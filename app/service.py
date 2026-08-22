"""
SafeRouteService: the single class that encapsulates the entire ML pipeline
and contextual/business logic. The FastAPI layer (app/main.py) and the CLI
(cli.py) are both thin wrappers around this class, so behavior is guaranteed
consistent across every entry point.
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd

from app.config import (
    AUDITS_STORE_PATH,
    CRIME_TABLE_PATH,
    MAX_AUDIT_ADJUSTMENT,
    MODERATE_UPPER_BOUND,
    POI_TABLE_PATH,
    RISK_LABELS,
    SAFE_UPPER_BOUND,
    TRAINING_DATA_PATH,
)
from app.data_gen import (
    area_code_for_point,
    build_crime_table,
    build_poi_table,
    generate_segment_attributes,
    generate_training_dataset,
    haversine_km,
    nearest_poi_distances,
)
from app.explain import compute_feature_contributions, generate_grouped_reasons
from app.external_apis import fetch_traffic_context, fetch_weather, traffic_adjustment
from app.features import build_feature_row
from app.model import load_model, predict_segment, save_model, train_model


def _label_for_score(score: float) -> str:
    if score <= SAFE_UPPER_BOUND:
        return RISK_LABELS[0]
    if score <= MODERATE_UPPER_BOUND:
        return RISK_LABELS[1]
    return RISK_LABELS[2]


class SafeRouteService:
    """
    Facade over: synthetic/lookup data, feature engineering, the ML model,
    external context enrichment, and explanation generation.

    A single instance is created at API/CLI startup and reused across
    requests (the model and lookup tables are loaded once).
    """

    def __init__(self, auto_bootstrap: bool = True):
        self.model = load_model()
        self.crime_table: pd.DataFrame | None = None
        self.poi_table: pd.DataFrame | None = None
        self.audits: pd.DataFrame = self._load_audits()
        # In-memory exponential moving average of audit sentiment per area,
        # used to nudge crime_risk_index between full model retrains.
        self._area_audit_adjustment: dict[str, float] = {}

        self._load_lookup_tables()
        if auto_bootstrap and self.model is None:
            self.bootstrap()

    # ------------------------------------------------------------------
    # Bootstrapping / training
    # ------------------------------------------------------------------
    def bootstrap(self) -> dict:
        """Generate synthetic data, train + calibrate the model, persist everything."""
        df = generate_training_dataset()
        df.to_csv(TRAINING_DATA_PATH, index=False)
        model, metrics = train_model(df)
        save_model(model)
        self.model = model
        return metrics

    def _load_lookup_tables(self) -> None:
        if CRIME_TABLE_PATH.exists():
            self.crime_table = pd.read_csv(CRIME_TABLE_PATH).set_index("area_code")
        else:
            self.crime_table = build_crime_table().set_index("area_code")
            self.crime_table.to_csv(CRIME_TABLE_PATH)

        if POI_TABLE_PATH.exists():
            self.poi_table = pd.read_csv(POI_TABLE_PATH)
        else:
            self.poi_table = build_poi_table()
            self.poi_table.to_csv(POI_TABLE_PATH, index=False)

    def _load_audits(self) -> pd.DataFrame:
        if AUDITS_STORE_PATH.exists():
            return pd.read_csv(AUDITS_STORE_PATH)
        # Explicit dtypes matter here: an all-object empty frame stays
        # object-typed after pd.concat with a real row, which later breaks
        # numpy ufuncs (e.g. np.radians) in haversine_km.
        return pd.DataFrame(
            {
                "audit_id": pd.Series(dtype="str"),
                "lat": pd.Series(dtype="float64"),
                "lon": pd.Series(dtype="float64"),
                "area_code": pd.Series(dtype="str"),
                "rating": pd.Series(dtype="int64"),
                "comment": pd.Series(dtype="str"),
                "timestamp": pd.Series(dtype="str"),
            }
        )

    def _persist_audits(self) -> None:
        self.audits.to_csv(AUDITS_STORE_PATH, index=False)

    # ------------------------------------------------------------------
    # Feature assembly for a single segment
    # ------------------------------------------------------------------
    def _segment_feature_row(self, segment, when: datetime) -> tuple[dict, dict]:
        """
        Build the engineered feature row for one segment, filling any
        attribute the caller didn't supply from our synthetic lookup
        tables. Returns (feature_row, context_meta) where context_meta
        carries extra info (area_code, crime stats) used for explanations.
        """
        lat, lon = segment.point.lat, segment.point.lon
        area_code = area_code_for_point(lat, lon)
        synthetic_attrs = generate_segment_attributes(lat, lon, area_code)

        lighting_score = segment.lighting_score if segment.lighting_score is not None else synthetic_attrs["lighting_score"]
        crowd_density = segment.crowd_density if segment.crowd_density is not None else synthetic_attrs["crowd_density"]
        cctv_coverage = segment.cctv_coverage if segment.cctv_coverage is not None else synthetic_attrs["cctv_coverage"]
        streetlight_density = (
            segment.streetlight_density if segment.streetlight_density is not None else synthetic_attrs["streetlight_density"]
        )
        footpath_quality = (
            segment.footpath_quality if segment.footpath_quality is not None else synthetic_attrs["footpath_quality"]
        )

        poi_distances = nearest_poi_distances(lat, lon, self.poi_table)

        if area_code in self.crime_table.index:
            crime_row = self.crime_table.loc[area_code]
            crime_risk_index = float(crime_row["crime_risk_index"])
            reported_incidents_month = int(crime_row["reported_incidents_month"])
        else:
            crime_risk_index, reported_incidents_month = 0.4, 5

        # Blend in crowdsourced audit feedback for this area, if any.
        audit_adj = self._area_audit_adjustment.get(area_code, 0.0)
        crime_risk_index = float(min(1.0, max(0.0, crime_risk_index + audit_adj)))

        recent_audit_count = int((self.audits["area_code"] == area_code).sum()) if not self.audits.empty else 0

        feature_row = build_feature_row(
            hour=when.hour,
            day_of_week=when.weekday(),
            lighting_score=lighting_score,
            crowd_density=crowd_density,
            cctv_coverage=cctv_coverage,
            streetlight_density=streetlight_density,
            footpath_quality=footpath_quality,
            crime_risk_index=crime_risk_index,
            poi_distances_km=poi_distances,
        )
        context_meta = {
            "area_code": area_code,
            "reported_incidents_month": reported_incidents_month,
            "recent_audit_count": recent_audit_count,
        }
        return feature_row, context_meta

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------
    def score_segment(self, segment, when: datetime, use_live_context: bool, weather_ctx: dict | None = None) -> dict:
        feature_row, context_meta = self._segment_feature_row(segment, when)
        prediction = predict_segment(self.model, feature_row)

        context_adjustments = []
        total_adjustment = 0.0

        if use_live_context:
            # Callers that already fan out weather fetches in parallel (see
            # score_route) pass the result in directly so we don't block on
            # a second sequential network call here.
            if weather_ctx is None:
                weather_ctx = fetch_weather(segment.point.lat, segment.point.lon)
            weather_adj = weather_ctx.get("adjustment", 0.0)
            total_adjustment += weather_adj
            context_adjustments.append(
                {
                    "source": "weather",
                    "description": (
                        "; ".join(weather_ctx["notes"]) if weather_ctx.get("notes") else "No significant weather risk detected"
                    )
                    if weather_ctx["data_available"]
                    else "Live weather data unavailable; no adjustment applied",
                    "adjustment": weather_adj,
                    "data_available": weather_ctx["data_available"],
                }
            )

        adjusted_score = float(min(1.0, max(0.0, prediction["risk_score"] + total_adjustment)))
        adjusted_label = _label_for_score(adjusted_score)

        contributions = compute_feature_contributions(self.model, feature_row)
        grouped_reasons = generate_grouped_reasons(feature_row, context_meta)

        return {
            "point": segment.point,
            "risk_score": adjusted_score,
            "label": adjusted_label,
            "confidence": prediction["confidence"],
            "context_adjustments": context_adjustments,
            "feature_contributions": contributions,
            "grouped_reasons": grouped_reasons,
            "feature_row": feature_row,
            "context_meta": context_meta,
        }

    def score_route(self, segments, timestamp: datetime | None, use_live_context: bool, route_id: str | None = None) -> dict:
        when = timestamp or datetime.now(timezone.utc)

        # All external calls (one weather lookup per segment + one traffic
        # lookup for the route) are fanned out in parallel via a thread
        # pool instead of sequentially, since each is a blocking network
        # call with its own timeout. Sequentially, N segments + traffic
        # could take up to (N+1) * EXTERNAL_API_TIMEOUT_SECONDS in the
        # worst case (e.g. an unreachable/slow network); in parallel it's
        # bounded by a single timeout window regardless of segment count.
        weather_by_index: dict[int, dict] = {}
        traffic_ctx: dict | None = None
        if use_live_context:
            with ThreadPoolExecutor(max_workers=max(len(segments) + 1, 1)) as pool:
                weather_futures = {
                    pool.submit(fetch_weather, seg.point.lat, seg.point.lon): i
                    for i, seg in enumerate(segments)
                }
                traffic_future = None
                if len(segments) >= 2:
                    origin = (segments[0].point.lat, segments[0].point.lon)
                    destination = (segments[-1].point.lat, segments[-1].point.lon)
                    traffic_future = pool.submit(fetch_traffic_context, origin, destination)

                for future, idx in weather_futures.items():
                    weather_by_index[idx] = future.result()
                if traffic_future is not None:
                    traffic_ctx = traffic_future.result()

        segment_results = [
            self.score_segment(seg, when, use_live_context, weather_ctx=weather_by_index.get(i))
            for i, seg in enumerate(segments)
        ]

        # Route-level risk = weighted blend of mean and worst segment, so a
        # single dangerous stretch meaningfully raises the whole route's
        # score without letting one outlier fully dominate.
        scores = [r["risk_score"] for r in segment_results]
        mean_score = sum(scores) / len(scores)
        worst_idx = max(range(len(scores)), key=lambda i: scores[i])
        worst_score = scores[worst_idx]
        overall_score = float(0.6 * worst_score + 0.4 * mean_score)
        overall_label = _label_for_score(overall_score)
        overall_confidence = float(sum(r["confidence"] for r in segment_results) / len(segment_results))

        # Route-level traffic adjustment, using the context fetched above
        # (already fanned out in parallel with the weather calls).
        if traffic_ctx is not None:
            tod_risk = segment_results[worst_idx]["feature_row"]["time_of_day_risk"]
            adj = traffic_adjustment(traffic_ctx, tod_risk)
            overall_score = float(min(1.0, max(0.0, overall_score + adj)))
            overall_label = _label_for_score(overall_score)
            route_level_adjustment = {
                "source": "traffic",
                "description": (
                    f"Estimated congestion along this route: {traffic_ctx.get('congestion_ratio')}"
                    if traffic_ctx.get("data_available")
                    else "Live traffic data unavailable; no adjustment applied"
                ),
                "adjustment": adj,
                "data_available": traffic_ctx.get("data_available", False),
            }
        else:
            route_level_adjustment = {
                "source": "traffic",
                "description": "Traffic context not requested or insufficient segments",
                "adjustment": 0.0,
                "data_available": False,
            }

        all_adjustments = list(route_level_adjustment for route_level_adjustment in [route_level_adjustment])
        for r in segment_results:
            all_adjustments.extend(r["context_adjustments"])

        # Explanations (top feature contributions + grouped reasons) are
        # meant to say *why this particular route* carries the risk it
        # does. But every alternative route between the same source and
        # destination shares its first and last sampled point (see
        # sampleRoutePoints() in frontend/api.js, which always keeps both
        # endpoints) -- so whenever that shared endpoint happens to be the
        # single riskiest point on the trip (a common case: a poorly-lit
        # destination, say), it becomes `worst_idx` for every alternative
        # route, and the explanation ends up identical across all of them
        # even though the paths themselves differ. That defeats the whole
        # point of comparing routes.
        #
        # Explanations are therefore drawn from the worst *interior*
        # segment (excluding the shared start/end points) whenever the
        # route has enough segments for "interior" to mean anything. The
        # score itself is unaffected -- it still legitimately accounts for
        # endpoint risk via worst_idx above, and worst_segment in the API
        # response still reports the true worst point, endpoint or not.
        if len(scores) > 2:
            explain_idx = max(range(1, len(scores) - 1), key=lambda i: scores[i])
        else:
            explain_idx = worst_idx

        # Aggregate top contributions across segments (favor the explanation segment).
        top_contributions = segment_results[explain_idx]["feature_contributions"]
        # Merge grouped reasons across all segments, de-duplicated, explanation segment first.
        merged_reasons = {"environment": [], "infrastructure": [], "history": [], "time": []}
        ordered = [segment_results[explain_idx]] + [r for i, r in enumerate(segment_results) if i != explain_idx]
        for r in ordered:
            for k, v in r["grouped_reasons"].items():
                for item in v:
                    if item not in merged_reasons[k]:
                        merged_reasons[k].append(item)

        return {
            "route_id": route_id,
            "overall_risk_score": overall_score,
            "label": overall_label,
            "confidence": overall_confidence,
            "worst_segment_index": worst_idx,
            "explain_segment_index": explain_idx,
            "segment_results": segment_results,
            "context_adjustments": all_adjustments,
            "top_feature_contributions": top_contributions,
            "grouped_reasons": merged_reasons,
            "evaluated_at": when,
        }

    def compare_routes(self, routes: dict, timestamp: datetime | None, use_live_context: bool) -> dict:
        results = {}
        for name, segments in routes.items():
            scored = self.score_route(segments, timestamp, use_live_context, route_id=name)
            results[name] = scored
        recommended = min(results.items(), key=lambda kv: kv[1]["overall_risk_score"])[0]
        return {"results": results, "recommended_route": recommended}

    # ------------------------------------------------------------------
    # Feedback / crowdsourced audits
    # ------------------------------------------------------------------
    def submit_feedback(self, lat: float, lon: float, rating: int, comment: str | None, when: datetime | None) -> dict:
        area_code = area_code_for_point(lat, lon)
        audit_id = str(uuid.uuid4())
        when = when or datetime.now(timezone.utc)

        new_row = pd.DataFrame(
            [
                {
                    "audit_id": audit_id,
                    "lat": lat,
                    "lon": lon,
                    "area_code": area_code,
                    "rating": rating,
                    "comment": comment or "",
                    "timestamp": when.isoformat(),
                }
            ]
        )
        self.audits = pd.concat([self.audits, new_row], ignore_index=True)
        self._persist_audits()

        # Update the area's audit-driven adjustment: rating 1 (very unsafe)
        # nudges crime_risk_index up, rating 5 (very safe) nudges it down.
        # Exponential moving average keeps this stable against single outliers.
        normalized = (3 - rating) / 2.0  # rating 1 -> +1.0, rating 5 -> -1.0, rating 3 -> 0
        delta = normalized * MAX_AUDIT_ADJUSTMENT
        prev = self._area_audit_adjustment.get(area_code, 0.0)
        alpha = 0.3
        self._area_audit_adjustment[area_code] = float(prev * (1 - alpha) + delta * alpha)

        base_crime = float(self.crime_table.loc[area_code, "crime_risk_index"]) if area_code in self.crime_table.index else 0.4
        updated_score = float(min(1.0, max(0.0, base_crime + self._area_audit_adjustment[area_code])))

        return {"audit_id": audit_id, "area_code": area_code, "updated_area_audit_score": updated_score}

    def nearby_audits(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        if self.audits.empty:
            return []
        dists = haversine_km(lat, lon, self.audits["lat"].to_numpy(), self.audits["lon"].to_numpy())
        mask = dists <= radius_km
        subset = self.audits[mask].copy()
        subset["distance_km"] = dists[mask]
        subset = subset.sort_values("distance_km")
        return subset.to_dict(orient="records")
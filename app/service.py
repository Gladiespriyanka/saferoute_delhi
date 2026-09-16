"""
SafeRouteService: the one class that encapsulates the whole pipeline.

The FastAPI layer (app/main.py) and the CLI (cli.py) are both thin wrappers
around it, so behaviour is identical whichever entry point you use. One
instance is created at startup and shared: the model, the city tables and
the spatial indices are loaded once, not per request.
"""
from __future__ import annotations

import logging
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.config import (
    AUDIT_EMA_ALPHA,
    AUDITS_STORE_PATH,
    ESCORT_TIMELINE_LIMIT,
    ESCORT_TTL_SECONDS,
    MAX_AUDIT_ADJUSTMENT,
    MODERATE_UPPER_BOUND,
    RISK_LABELS,
    SAFE_UPPER_BOUND,
)
from app.dataset import DelhiData, load_delhi_data, measure_points
from app.explain import (
    compute_feature_contributions,
    generate_reason_items,
    merge_reason_items,
)
from app.external_apis import fetch_traffic_context, fetch_weather, traffic_adjustment
from app.features import build_feature_row
from app.geo import area_code_for_point, haversine_km
from app.model import SafetyModel, load_model

log = logging.getLogger(__name__)

# Columns a caller may override per segment with their own observation.
OVERRIDABLE_ATTRIBUTES = (
    "lighting_score",
    "crowd_density",
    "cctv_coverage",
    "streetlight_density",
    "footpath_quality",
)

AUDIT_COLUMNS = (
    "audit_id", "lat", "lon", "area_code", "rating", "comment", "reasons", "timestamp"
)


def _audit_records(frame: pd.DataFrame) -> list[dict]:
    """DataFrame rows -> API-shaped dicts, with reasons split back into a list."""
    records = frame.to_dict(orient="records")
    for record in records:
        # Empty CSV cells come back as NaN, which is neither "" nor falsy.
        raw = record.get("reasons")
        raw = "" if raw is None or raw != raw else str(raw)
        record["reasons"] = [r for r in raw.split("|") if r]
        comment = record.get("comment")
        record["comment"] = "" if comment is None or comment != comment else str(comment)
    return records


class ModelNotTrainedError(RuntimeError):
    """Raised when a prediction is attempted before a model exists."""


class EscortNotFoundError(LookupError):
    """Raised when a trip id doesn't name a live (or not yet expired) escort session."""


class EscortAuthError(PermissionError):
    """Raised when a write is attempted with a missing or wrong owner token."""


def label_for_score(score: float) -> str:
    if score <= SAFE_UPPER_BOUND:
        return RISK_LABELS[0]
    if score <= MODERATE_UPPER_BOUND:
        return RISK_LABELS[1]
    return RISK_LABELS[2]


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return float(min(high, max(low, value)))


class SafeRouteService:
    """Facade over the city data, the model, live context and explanations."""

    def __init__(self, allow_synthetic: bool = True):
        self.data: DelhiData = load_delhi_data(allow_synthetic=allow_synthetic)
        self.model: SafetyModel | None = load_model()
        if self.model is None:
            log.warning(
                "No usable model artifact found. /predict will return 503 until "
                "`python train_model.py` has been run."
            )

        # Audit writes come in concurrently from the API's thread pool and
        # every one of them rewrites the whole CSV, so they are serialised.
        self._audit_lock = threading.Lock()
        self.audits = self._load_audits()
        # Per-area exponential moving average of audit sentiment, which nudges
        # crime risk between full retrains. Rebuilt from the persisted audits
        # at startup -- keeping it purely in memory meant every restart
        # silently discarded the community's accumulated feedback.
        self._area_audit_adjustment = self._rebuild_audit_adjustments()

        # Smart Escort Mode: in-memory only, see app/config.ESCORT_TTL_SECONDS.
        self._escort_lock = threading.Lock()
        self._escorts: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------
    def status(self) -> dict:
        """What this instance is actually running on -- surfaced by /health."""
        info = self.data.describe()
        info["model_loaded"] = self.model is not None
        if self.model is not None:
            test = self.model.metrics.get("test", {})
            info["model_trained_at"] = self.model.trained_at
            info["model_features"] = len(self.model.feature_columns)
            info["calibration_method"] = self.model.metrics.get("calibration_method")
            info["test_accuracy"] = test.get("accuracy")
            info["calibration_error"] = test.get("ece")
        info["audits_recorded"] = int(len(self.audits))
        return info

    # ------------------------------------------------------------------
    # Crowdsourced audits
    # ------------------------------------------------------------------
    def _load_audits(self) -> pd.DataFrame:
        if AUDITS_STORE_PATH.exists():
            try:
                audits = pd.read_csv(AUDITS_STORE_PATH)
                # Files written before structured reasons existed lack the column.
                if "reasons" not in audits.columns:
                    audits["reasons"] = ""
                audits["reasons"] = audits["reasons"].fillna("")
                audits["comment"] = audits["comment"].fillna("")
                return audits
            except Exception as exc:  # noqa: BLE001 - a bad CSV must not block startup
                log.warning("Could not read %s (%s); starting with no audits.",
                            AUDITS_STORE_PATH, exc)
        # Explicit dtypes matter: an all-object empty frame stays object-typed
        # after concat with a real row, which later breaks numpy ufuncs in
        # haversine_km.
        return pd.DataFrame(
            {
                "audit_id": pd.Series(dtype="str"),
                "lat": pd.Series(dtype="float64"),
                "lon": pd.Series(dtype="float64"),
                "area_code": pd.Series(dtype="str"),
                "rating": pd.Series(dtype="int64"),
                "comment": pd.Series(dtype="str"),
                "reasons": pd.Series(dtype="str"),
                "timestamp": pd.Series(dtype="str"),
            }
        )

    def _rebuild_audit_adjustments(self) -> dict[str, float]:
        """Replay persisted audits, oldest first, to restore each area's EMA."""
        adjustments: dict[str, float] = {}
        if self.audits.empty:
            return adjustments
        ordered = self.audits.sort_values("timestamp", kind="stable")
        for area_code, rating in zip(ordered["area_code"], ordered["rating"], strict=False):
            adjustments[area_code] = self._next_adjustment(
                adjustments.get(str(area_code), 0.0), int(rating)
            )
        log.info("Restored audit adjustments for %d area(s).", len(adjustments))
        return adjustments

    @staticmethod
    def _next_adjustment(previous: float, rating: int) -> float:
        """
        Fold one new rating into an area's running adjustment.

        Rating 1 ("felt very unsafe") pushes crime risk up by the full cap,
        rating 5 pushes it down by the same, rating 3 is neutral. The
        exponential moving average keeps a single outlier from swinging an
        area on its own.
        """
        normalised = (3 - rating) / 2.0
        delta = normalised * MAX_AUDIT_ADJUSTMENT
        return float(previous * (1 - AUDIT_EMA_ALPHA) + delta * AUDIT_EMA_ALPHA)

    # ------------------------------------------------------------------
    # Feature assembly
    # ------------------------------------------------------------------
    def _segment_features(self, segments, when: datetime) -> tuple[list[dict], list[dict]]:
        """
        Build the feature row and explanation context for every segment.

        Measurement is done for the whole route in one batched call -- the
        spatial indices answer a route's worth of queries about as cheaply as
        a single one.
        """
        lats = np.array([s.point.lat for s in segments], dtype=float)
        lons = np.array([s.point.lon for s in segments], dtype=float)
        measured = measure_points(self.data, lats, lons)

        feature_rows: list[dict] = []
        contexts: list[dict] = []
        for index, segment in enumerate(segments):
            row = measured.iloc[index]
            area_code = str(row["area_code"])

            attributes = {name: float(row[name]) for name in OVERRIDABLE_ATTRIBUTES}
            overridden = []
            for name in OVERRIDABLE_ATTRIBUTES:
                supplied = getattr(segment, name, None)
                if supplied is not None:
                    attributes[name] = float(supplied)
                    overridden.append(name)

            crime_risk = None
            if self.data.with_crime:
                adjustment = self._area_audit_adjustment.get(area_code, 0.0)
                crime_risk = _clamp(float(row["crime_risk_index"]) + adjustment)

            poi_distances = {
                "metro": float(row["dist_metro_km"]),
                "bus_stop": float(row["dist_bus_km"]),
                "hospital": float(row["dist_hospital_km"]),
                "police": float(row["dist_police_km"]),
            }

            # A value the caller supplied is a first-hand observation, so it
            # counts as observed even where OpenStreetMap has nothing.
            observed = {
                "streetlight_density": bool(row["streetlight_observed"])
                or "streetlight_density" in overridden,
                "cctv_coverage": bool(row["cctv_observed"]) or "cctv_coverage" in overridden,
                "footpath_quality": bool(row["footpath_observed"])
                or "footpath_quality" in overridden,
            }

            feature_rows.append(
                build_feature_row(
                    hour=when.hour,
                    day_of_week=when.weekday(),
                    poi_distances_km=poi_distances,
                    crime_risk_index=crime_risk,
                    observed=observed,
                    infra_prior=self.data.infra_prior,
                    **attributes,
                )
            )
            contexts.append(
                {
                    "area_code": area_code,
                    "osm_coverage": float(row["osm_coverage"]),
                    "lighting_observed": bool(row["lighting_observed"])
                    or "lighting_score" in overridden,
                    "infra_observed": {
                        name: bool(value) for name, value in observed.items()
                    },
                    "recent_audit_count": self._audit_count_for_area(area_code),
                    "overridden_attributes": overridden,
                    "overridden_lighting": "lighting_score" in overridden,
                    "crime_data_available": self.data.with_crime,
                }
            )
        return feature_rows, contexts

    def _audit_count_for_area(self, area_code: str) -> int:
        if self.audits.empty:
            return 0
        return int((self.audits["area_code"] == area_code).sum())

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------
    def score_route(
        self,
        segments,
        timestamp: datetime | None,
        use_live_context: bool,
        route_id: str | None = None,
    ) -> dict:
        if self.model is None:
            raise ModelNotTrainedError(
                "No model has been trained yet. Run `python train_model.py`."
            )
        if not segments:
            raise ValueError("A route needs at least one segment.")

        when = timestamp or datetime.now(timezone.utc)
        feature_rows, contexts = self._segment_features(segments, when)
        predictions = self.model.score_rows(feature_rows)

        weather_by_index, traffic_ctx = self._fetch_live_context(segments, use_live_context)

        segment_results = []
        for index, segment in enumerate(segments):
            segment_results.append(
                self._assemble_segment(
                    segment,
                    feature_rows[index],
                    contexts[index],
                    predictions[index],
                    weather_by_index.get(index),
                )
            )

        return self._assemble_route(segment_results, traffic_ctx, when, route_id)

    def _fetch_live_context(self, segments, use_live_context: bool):
        """
        Fan every external lookup out in parallel.

        Each is a blocking network call with its own timeout, so sequentially
        N segments plus traffic could take (N+1) x the timeout on a slow
        network. In parallel the whole thing is bounded by a single timeout
        window regardless of how many segments there are.
        """
        if not use_live_context:
            return {}, None

        weather_by_index: dict[int, dict] = {}
        traffic_ctx: dict | None = None
        with ThreadPoolExecutor(max_workers=len(segments) + 1) as pool:
            weather_futures = {
                pool.submit(fetch_weather, seg.point.lat, seg.point.lon): i
                for i, seg in enumerate(segments)
            }
            traffic_future = None
            if len(segments) >= 2:
                traffic_future = pool.submit(
                    fetch_traffic_context,
                    (segments[0].point.lat, segments[0].point.lon),
                    (segments[-1].point.lat, segments[-1].point.lon),
                )
            for future, index in weather_futures.items():
                weather_by_index[index] = future.result()
            if traffic_future is not None:
                traffic_ctx = traffic_future.result()
        return weather_by_index, traffic_ctx

    def _assemble_segment(
        self, segment, feature_row: dict, context: dict, prediction: dict, weather_ctx: dict | None
    ) -> dict:
        adjustments = []
        total_adjustment = 0.0

        if weather_ctx is not None:
            adjustment = weather_ctx.get("adjustment", 0.0)
            total_adjustment += adjustment
            if not weather_ctx.get("data_available"):
                description = "Live weather data unavailable; no adjustment applied"
            elif weather_ctx.get("notes"):
                description = "; ".join(weather_ctx["notes"])
            else:
                description = "No significant weather risk detected"
            adjustments.append(
                {
                    "source": "weather",
                    "description": description,
                    "adjustment": adjustment,
                    "data_available": bool(weather_ctx.get("data_available")),
                }
            )

        adjusted_score = _clamp(prediction["risk_score"] + total_adjustment)
        return {
            "point": segment.point,
            "risk_score": adjusted_score,
            "label": label_for_score(adjusted_score),
            "confidence": prediction["confidence"],
            "margin": prediction["margin"],
            "class_probabilities": prediction["class_probabilities"],
            "data_coverage": context["osm_coverage"],
            "area_code": context["area_code"],
            "context_adjustments": adjustments,
            "feature_contributions": compute_feature_contributions(self.model, feature_row),
            "grouped_reasons": merge_reason_items([generate_reason_items(feature_row, context)]),
            "reason_items": generate_reason_items(feature_row, context),
            "feature_row": feature_row,
            "context_meta": context,
        }

    def _assemble_route(
        self, segment_results: list[dict], traffic_ctx: dict | None, when: datetime, route_id
    ) -> dict:
        scores = [r["risk_score"] for r in segment_results]
        worst_index = int(np.argmax(scores))
        # Weighted toward the worst segment: one genuinely dangerous stretch
        # should raise the whole route, but not so much that a single outlier
        # makes every route look identical.
        overall_score = _clamp(0.6 * scores[worst_index] + 0.4 * float(np.mean(scores)))

        traffic_entry = self._traffic_adjustment_entry(
            traffic_ctx, segment_results[worst_index]["feature_row"]["time_of_day_risk"]
        )
        overall_score = _clamp(overall_score + traffic_entry["adjustment"])

        adjustments = [traffic_entry]
        for result in segment_results:
            adjustments.extend(result["context_adjustments"])

        # Merge the segments' reasons worst-first, keeping at most one
        # statement per topic so the route-level list leads with the most
        # relevant explanation instead of contradicting itself.
        ordered = [segment_results[worst_index]] + [
            r for i, r in enumerate(segment_results) if i != worst_index
        ]
        merged_reasons = merge_reason_items([r["reason_items"] for r in ordered])

        coverage = float(np.mean([r["data_coverage"] for r in segment_results]))
        return {
            "route_id": route_id,
            "overall_risk_score": overall_score,
            "label": label_for_score(overall_score),
            "confidence": float(np.mean([r["confidence"] for r in segment_results])),
            # How much OpenStreetMap evidence backs this route's measurements.
            # Reported separately from model confidence because they fail
            # differently: the model can be sure about a place we know little
            # about, and that is exactly what a user needs to be told.
            "data_coverage": coverage,
            "worst_segment_index": worst_index,
            "segment_results": segment_results,
            "context_adjustments": adjustments,
            "top_feature_contributions": segment_results[worst_index]["feature_contributions"],
            "grouped_reasons": merged_reasons,
            "evaluated_at": when,
        }

    @staticmethod
    def _traffic_adjustment_entry(traffic_ctx: dict | None, time_of_day_risk: float) -> dict:
        if traffic_ctx is None:
            return {
                "source": "traffic",
                "description": "Traffic context not requested, or route has a single point",
                "adjustment": 0.0,
                "data_available": False,
            }
        available = bool(traffic_ctx.get("data_available"))
        return {
            "source": "traffic",
            "description": (
                f"Estimated congestion along this route: {traffic_ctx.get('congestion_ratio')}"
                if available
                else "Live traffic data unavailable; no adjustment applied"
            ),
            "adjustment": traffic_adjustment(traffic_ctx, time_of_day_risk),
            "data_available": available,
        }

    def compare_routes(
        self, routes: dict, timestamp: datetime | None, use_live_context: bool
    ) -> dict:
        if len(routes) < 2:
            raise ValueError("Provide at least two routes to compare.")
        results = {
            name: self.score_route(segments, timestamp, use_live_context, route_id=name)
            for name, segments in routes.items()
        }
        recommended = min(results.items(), key=lambda item: item[1]["overall_risk_score"])[0]
        return {"results": results, "recommended_route": recommended}

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------
    def submit_feedback(
        self,
        lat: float,
        lon: float,
        rating: int,
        comment: str | None,
        when: datetime | None,
        reasons: list[str] | None = None,
    ) -> dict:
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
                    # Stored pipe-joined so the audit log stays a flat CSV.
                    "reasons": "|".join(reasons or []),
                    "timestamp": when.isoformat(),
                }
            ]
        )

        with self._audit_lock:
            self.audits = pd.concat([self.audits, new_row], ignore_index=True)
            self._area_audit_adjustment[area_code] = self._next_adjustment(
                self._area_audit_adjustment.get(area_code, 0.0), rating
            )
            adjustment = self._area_audit_adjustment[area_code]
            self.audits.to_csv(AUDITS_STORE_PATH, index=False)

        base_crime = self._base_crime_risk(area_code)
        return {
            "audit_id": audit_id,
            "area_code": area_code,
            "adjustment": round(adjustment, 4),
            "updated_area_audit_score": (
                round(_clamp(base_crime + adjustment), 4) if base_crime is not None else None
            ),
            "crime_data_available": self.data.with_crime,
        }

    def _base_crime_risk(self, area_code: str) -> float | None:
        if not self.data.with_crime:
            return None
        match = self.data.grid.loc[self.data.grid["area_code"] == area_code, "crime_risk_index"]
        return float(match.iloc[0]) if not match.empty else None

    def nearby_audits(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        if self.audits.empty:
            return []
        distances = haversine_km(
            lat, lon, self.audits["lat"].to_numpy(), self.audits["lon"].to_numpy()
        )
        mask = distances <= radius_km
        subset = self.audits[mask].copy()
        subset["distance_km"] = distances[mask]
        return _audit_records(subset.sort_values("distance_km"))

    def audits_along_route(
        self, points: list[tuple[float, float]], radius_km: float, limit: int
    ) -> list[dict]:
        """
        Every report within `radius_km` of any point on a route, newest first.

        One call per saved route is what the dashboard needs to say "two new
        reports on your walk home since yesterday" without hammering
        /audits/nearby once per stretch.
        """
        if self.audits.empty or not points:
            return []
        lats = self.audits["lat"].to_numpy()
        lons = self.audits["lon"].to_numpy()
        nearest = np.full(len(self.audits), np.inf)
        for lat, lon in points:
            nearest = np.minimum(nearest, haversine_km(lat, lon, lats, lons))
        mask = nearest <= radius_km
        subset = self.audits[mask].copy()
        subset["distance_km"] = nearest[mask]
        subset = subset.sort_values("timestamp", ascending=False, kind="stable").head(limit)
        return _audit_records(subset)

    # ------------------------------------------------------------------
    # Smart Escort Mode
    # ------------------------------------------------------------------
    def _prune_escorts_locked(self) -> None:
        """Caller must hold `_escort_lock`. Drops sessions untouched past the TTL."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=ESCORT_TTL_SECONDS)
        stale = [tid for tid, session in self._escorts.items() if session["last_update_at"] < cutoff]
        for tid in stale:
            del self._escorts[tid]

    def _push_event(self, session: dict, kind: str, text: str) -> None:
        now = datetime.now(timezone.utc)
        session["events"].append({"at": now, "kind": kind, "text": text})
        session["events"] = session["events"][-ESCORT_TIMELINE_LIMIT:]
        session["last_update_at"] = now

    def _get_escort_locked(self, trip_id: str) -> dict:
        session = self._escorts.get(trip_id)
        if session is None:
            raise EscortNotFoundError(trip_id)
        return session

    def _authorize_escort_locked(self, trip_id: str, owner_token: str | None) -> dict:
        session = self._get_escort_locked(trip_id)
        if not secrets.compare_digest(owner_token or "", session["owner_token"]):
            raise EscortAuthError("Wrong or missing escort token for this trip.")
        return session

    def start_escort(self, destination, check_in_interval_seconds: int, route_preview) -> dict:
        with self._escort_lock:
            self._prune_escorts_locked()
            now = datetime.now(timezone.utc)
            session = {
                "trip_id": uuid.uuid4().hex,
                "owner_token": uuid.uuid4().hex,
                "status": "active",
                "started_at": now,
                "last_update_at": now,
                "check_in_interval_seconds": check_in_interval_seconds,
                "next_check_in_due_at": now + timedelta(seconds=check_in_interval_seconds),
                "destination": destination,
                "route_preview": route_preview,
                "last_point": None,
                "risk_label": None,
                "risk_score": None,
                "progress_fraction": None,
                "events": [{"at": now, "kind": "started", "text": "Escort started."}],
            }
            self._escorts[session["trip_id"]] = session
            return session

    def update_escort_position(
        self, trip_id: str, owner_token: str | None, point, risk_label, risk_score, progress_fraction
    ) -> dict:
        with self._escort_lock:
            session = self._authorize_escort_locked(trip_id, owner_token)
            if session["status"] == "ended":
                raise EscortNotFoundError(trip_id)
            session["last_point"] = point
            if risk_label is not None:
                session["risk_label"] = risk_label
            if risk_score is not None:
                session["risk_score"] = risk_score
            if progress_fraction is not None:
                session["progress_fraction"] = progress_fraction
            session["last_update_at"] = datetime.now(timezone.utc)
            return session

    def check_in_escort(self, trip_id: str, owner_token: str | None, ok: bool) -> dict:
        with self._escort_lock:
            session = self._authorize_escort_locked(trip_id, owner_token)
            if ok:
                session["status"] = "active"
                session["next_check_in_due_at"] = datetime.now(timezone.utc) + timedelta(
                    seconds=session["check_in_interval_seconds"]
                )
                self._push_event(session, "checkin_ok", "Checked in — doing fine.")
            else:
                session["status"] = "alert"
                self._push_event(session, "checkin_missed", "Reported not okay during a check-in.")
            return session

    def missed_check_in_escort(self, trip_id: str, owner_token: str | None) -> dict:
        with self._escort_lock:
            session = self._authorize_escort_locked(trip_id, owner_token)
            session["status"] = "alert"
            self._push_event(session, "checkin_missed", "Missed a scheduled check-in.")
            return session

    def sos_escort(self, trip_id: str, owner_token: str | None, point=None) -> dict:
        with self._escort_lock:
            session = self._authorize_escort_locked(trip_id, owner_token)
            session["status"] = "alert"
            if point is not None:
                session["last_point"] = point
            self._push_event(session, "sos", "SOS triggered.")
            return session

    def end_escort(self, trip_id: str, owner_token: str | None) -> dict:
        with self._escort_lock:
            session = self._authorize_escort_locked(trip_id, owner_token)
            session["status"] = "ended"
            self._push_event(session, "ended", "Escort ended.")
            return session

    def get_escort_status(self, trip_id: str) -> dict:
        """Deliberately unauthenticated: the trip id itself is the capability
        a companion needs, so they can open the link with no account and no
        API key."""
        with self._escort_lock:
            self._prune_escorts_locked()
            return self._get_escort_locked(trip_id)
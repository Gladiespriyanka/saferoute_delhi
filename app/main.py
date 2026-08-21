"""
FastAPI backend for SafeRoute Delhi.

Run with:
    uvicorn app.main:app --reload

All routes except /health require the `x-api-key` header (see app/security.py).
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.schemas import (
    CompareRoutesRequest,
    CompareRoutesResponse,
    ContextAdjustment,
    FeatureContribution,
    FeedbackRequest,
    FeedbackResponse,
    GeoPoint,
    GroupedReasons,
    HealthResponse,
    NearbyAuditsResponse,
    AuditRecord,
    PredictRequest,
    PredictResponse,
    RouteComparisonResult,
    SegmentScore,
)
from app.security import require_api_key
from app.service import SafeRouteService

app = FastAPI(
    title="SafeRoute Delhi API",
    description="Context-aware route safety scoring for pedestrians in Delhi.",
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# A single shared service instance, loaded/bootstrapped once at startup.
service = SafeRouteService()


def _segment_score_to_schema(seg_result: dict) -> SegmentScore:
    return SegmentScore(
        point=seg_result["point"],
        risk_score=round(seg_result["risk_score"], 4),
        label=seg_result["label"],
        confidence=round(seg_result["confidence"], 4),
    )

def _build_predict_response(scored: dict) -> PredictResponse:
    worst = scored["segment_results"][scored["worst_segment_index"]]
    return PredictResponse(
        route_id=scored["route_id"],
        overall_risk_score=round(scored["overall_risk_score"], 4),
        label=scored["label"],
        confidence=round(scored["confidence"], 4),
        worst_segment=_segment_score_to_schema(worst),
        segment_scores=[_segment_score_to_schema(r) for r in scored["segment_results"]],
        context_adjustments=[ContextAdjustment(**a) for a in scored["context_adjustments"]],
        top_feature_contributions=[FeatureContribution(**c) for c in scored["top_feature_contributions"]],
        grouped_reasons=GroupedReasons(**scored["grouped_reasons"]),
        evaluated_at=scored["evaluated_at"],
    )


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Unauthenticated liveness/readiness check."""
    return HealthResponse(
        status="ok",
        model_loaded=service.model is not None,
        model_version=__version__,
        external_apis={"weather": "open-meteo", "traffic": "osrm-demo"},
    )


@app.post("/predict", response_model=PredictResponse, tags=["prediction"])
def predict(request: PredictRequest, _: str = Depends(require_api_key)) -> PredictResponse:
    if service.model is None:
        raise HTTPException(status_code=503, detail="Model not yet trained/loaded.")
    try:
        scored = service.score_route(
            request.segments,
            request.timestamp,
            request.use_live_context,
            route_id=request.route_id,
        )
    except Exception as exc:  # defensive: never leak stack traces to clients
        raise HTTPException(status_code=500, detail=f"Prediction failed: {exc}") from exc
    return _build_predict_response(scored)


@app.post("/compare-routes", response_model=CompareRoutesResponse, tags=["prediction"])
def compare_routes(request: CompareRoutesRequest, _: str = Depends(require_api_key)) -> CompareRoutesResponse:
    if service.model is None:
        raise HTTPException(status_code=503, detail="Model not yet trained/loaded.")
    if len(request.routes) < 2:
        raise HTTPException(status_code=400, detail="Provide at least two routes to compare.")
    try:
        comparison = service.compare_routes(request.routes, request.timestamp, request.use_live_context)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Comparison failed: {exc}") from exc

    results = [
        RouteComparisonResult(
            route_name=name,
            overall_risk_score=round(res["overall_risk_score"], 4),
            label=res["label"],
            confidence=round(res["confidence"], 4),
        )
        for name, res in comparison["results"].items()
    ]
    when = next(iter(comparison["results"].values()))["evaluated_at"]
    return CompareRoutesResponse(recommended_route=comparison["recommended_route"], results=results, evaluated_at=when)


@app.post("/feedback", response_model=FeedbackResponse, tags=["feedback"])
async def submit_feedback(request: FeedbackRequest, _: str = Depends(require_api_key)) -> FeedbackResponse:
    try:
        result = service.submit_feedback(
            request.point.lat, request.point.lon, request.rating, request.comment, request.timestamp
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Feedback submission failed: {exc}") from exc
    return FeedbackResponse(
        status="recorded",
        audit_id=result["audit_id"],
        area_code=result["area_code"],
        updated_area_audit_score=round(result["updated_area_audit_score"], 4),
    )


@app.get("/audits/nearby", response_model=NearbyAuditsResponse, tags=["feedback"])
async def nearby_audits(
    lat: float, lon: float, radius_km: float = 1.0, _: str = Depends(require_api_key)
) -> NearbyAuditsResponse:
    if radius_km <= 0 or radius_km > 50:
        raise HTTPException(status_code=400, detail="radius_km must be between 0 and 50.")
    records = service.nearby_audits(lat, lon, radius_km)
    audits = [
        AuditRecord(
            audit_id=r["audit_id"],
            point=GeoPoint(lat=r["lat"], lon=r["lon"]),
            area_code=r["area_code"],
            rating=int(r["rating"]),
            comment=r.get("comment") or None,
            timestamp=r["timestamp"],
            distance_km=round(float(r["distance_km"]), 3),
        )
        for r in records
    ]
    return NearbyAuditsResponse(
        query_point=GeoPoint(lat=lat, lon=lon), radius_km=radius_km, count=len(audits), audits=audits
    )

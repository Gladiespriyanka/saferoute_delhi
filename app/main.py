"""
FastAPI backend for SafeHerWay.

    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

Every route except /health requires the `x-api-key` header (app/security.py).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import CORS_ALLOW_ORIGINS
from app.schemas import (
    AuditRecord,
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
    PredictRequest,
    PredictResponse,
    RouteAuditsRequest,
    RouteAuditsResponse,
    RouteComparisonResult,
    SegmentScore,
)
from app.security import require_api_key
from app.service import ModelNotTrainedError, SafeRouteService

log = logging.getLogger(__name__)

# Populated by the lifespan handler below. Building the service loads the
# model and every spatial index, which takes seconds -- doing that at import
# time (as this module used to) meant `import app.main` had side effects,
# made the test suite pay the cost on collection, and gave a failure during
# startup no chance of being reported cleanly.
service: SafeRouteService | None = None


def get_service() -> SafeRouteService:
    if service is None:  # pragma: no cover - only before startup completes
        raise HTTPException(status_code=503, detail="Service is still starting up.")
    return service


@asynccontextmanager
async def lifespan(_: FastAPI):
    global service
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    service = SafeRouteService()
    status = service.status()
    log.info("SafeHerWay ready: %s", status)
    if status["feature_source"] != "openstreetmap":
        log.warning(
            "Running on SYNTHETIC city data. Run `python fetch_osm_data.py` and "
            "`python train_model.py` for real OpenStreetMap measurements."
        )
    yield
    service = None


app = FastAPI(
    title="SafeHerWay API",
    description=(
        "Context-aware walking-route safety scoring for Delhi, built on measured "
        "OpenStreetMap infrastructure data with calibrated confidence."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type", "x-api-key"],
)


@app.exception_handler(ModelNotTrainedError)
async def _model_not_trained(_: Request, exc: ModelNotTrainedError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _bad_value(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _segment_schema(result: dict) -> SegmentScore:
    return SegmentScore(
        point=result["point"],
        risk_score=round(result["risk_score"], 4),
        label=result["label"],
        confidence=round(result["confidence"], 4),
        margin=round(result["margin"], 4),
        data_coverage=round(result["data_coverage"], 4),
        area_code=result["area_code"],
        class_probabilities={k: round(v, 4) for k, v in result["class_probabilities"].items()},
    )


def _predict_schema(scored: dict) -> PredictResponse:
    worst = scored["segment_results"][scored["worst_segment_index"]]
    return PredictResponse(
        route_id=scored["route_id"],
        overall_risk_score=round(scored["overall_risk_score"], 4),
        label=scored["label"],
        confidence=round(scored["confidence"], 4),
        data_coverage=round(scored["data_coverage"], 4),
        worst_segment=_segment_schema(worst),
        segment_scores=[_segment_schema(r) for r in scored["segment_results"]],
        context_adjustments=[ContextAdjustment(**a) for a in scored["context_adjustments"]],
        top_feature_contributions=[
            FeatureContribution(**c) for c in scored["top_feature_contributions"]
        ],
        grouped_reasons=GroupedReasons(**scored["grouped_reasons"]),
        evaluated_at=scored["evaluated_at"],
    )


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Unauthenticated liveness check, plus what data and model are loaded."""
    if service is None:
        raise HTTPException(status_code=503, detail="Service is still starting up.")
    return HealthResponse(
        status="ok",
        model_version=__version__,
        external_apis={"weather": "open-meteo", "traffic": "osrm-demo (proxy)"},
        **service.status(),
    )


# These handlers are deliberately `def`, not `async def`. Scoring is
# CPU-bound (forest inference, SHAP) and blocking (pandas, network fanout);
# declaring them async would run that work directly on the event loop and
# stall every other in-flight request. Sync handlers are dispatched to
# FastAPI's thread pool instead.
@app.post("/predict", response_model=PredictResponse, tags=["prediction"])
def predict(
    request: PredictRequest,
    _: str = Depends(require_api_key),
    svc: SafeRouteService = Depends(get_service),
) -> PredictResponse:
    """Score one route, with per-segment detail and explanations."""
    return _predict_schema(
        svc.score_route(
            request.segments,
            request.timestamp,
            request.use_live_context,
            route_id=request.route_id,
        )
    )


@app.post("/compare-routes", response_model=CompareRoutesResponse, tags=["prediction"])
def compare_routes(
    request: CompareRoutesRequest,
    _: str = Depends(require_api_key),
    svc: SafeRouteService = Depends(get_service),
) -> CompareRoutesResponse:
    """Score 2-8 candidate routes and recommend the safest."""
    comparison = svc.compare_routes(
        request.routes, request.timestamp, request.use_live_context
    )
    results = [
        RouteComparisonResult(
            route_name=name,
            overall_risk_score=round(result["overall_risk_score"], 4),
            label=result["label"],
            confidence=round(result["confidence"], 4),
            data_coverage=round(result["data_coverage"], 4),
        )
        for name, result in comparison["results"].items()
    ]
    evaluated_at = next(iter(comparison["results"].values()))["evaluated_at"]
    return CompareRoutesResponse(
        recommended_route=comparison["recommended_route"],
        results=results,
        evaluated_at=evaluated_at,
    )


@app.post("/feedback", response_model=FeedbackResponse, tags=["feedback"])
def submit_feedback(
    request: FeedbackRequest,
    _: str = Depends(require_api_key),
    svc: SafeRouteService = Depends(get_service),
) -> FeedbackResponse:
    """Record a crowdsourced safety audit for a location."""
    result = svc.submit_feedback(
        request.point.lat,
        request.point.lon,
        request.rating,
        request.comment,
        request.timestamp,
        reasons=request.reasons,
    )
    return FeedbackResponse(status="recorded", **result)


@app.get("/audits/nearby", response_model=NearbyAuditsResponse, tags=["feedback"])
def nearby_audits(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(1.0, gt=0, le=50),
    _: str = Depends(require_api_key),
    svc: SafeRouteService = Depends(get_service),
) -> NearbyAuditsResponse:
    """List crowdsourced audits within a radius of a point."""
    records = svc.nearby_audits(lat, lon, radius_km)
    return NearbyAuditsResponse(
        query_point=GeoPoint(lat=lat, lon=lon),
        radius_km=radius_km,
        count=len(records),
        audits=[_audit_schema(record) for record in records],
    )


@app.post("/audits/along-route", response_model=RouteAuditsResponse, tags=["feedback"])
def audits_along_route(
    request: RouteAuditsRequest,
    _: str = Depends(require_api_key),
    svc: SafeRouteService = Depends(get_service),
) -> RouteAuditsResponse:
    """Reports within reach of any point on a route, newest first."""
    records = svc.audits_along_route(
        [(p.lat, p.lon) for p in request.points], request.radius_km, request.limit
    )
    audits = [_audit_schema(record) for record in records]
    return RouteAuditsResponse(
        count=len(audits),
        audits=audits,
        latest=max((a.timestamp for a in audits), default=None),
    )


def _audit_schema(record: dict) -> AuditRecord:
    return AuditRecord(
        audit_id=record["audit_id"],
        point=GeoPoint(lat=record["lat"], lon=record["lon"]),
        area_code=record["area_code"],
        rating=int(record["rating"]),
        comment=record.get("comment") or None,
        reasons=record.get("reasons") or [],
        timestamp=record["timestamp"],
        distance_km=round(float(record["distance_km"]), 3),
    )

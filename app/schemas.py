"""
Typed request/response models.

Strong typing on every payload buys automatic validation, generated OpenAPI
docs, and one unambiguous source of truth for the API contract.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class RiskLabel(str, Enum):
    SAFE = "Safe"
    MODERATE = "Moderate"
    UNSAFE = "Unsafe"


class GeoPoint(BaseModel):
    lat: float = Field(..., description="Latitude in decimal degrees")
    lon: float = Field(..., description="Longitude in decimal degrees")

    # Deliberately validated as a coordinate, not as "inside Delhi". Points
    # just outside the study bounding box are legitimate -- routes run to the
    # city edge and into the NCR -- and are handled by clamping in the grid
    # lookup. Only values that are not coordinates at all are rejected.
    @field_validator("lat")
    @classmethod
    def _check_lat(cls, value: float) -> float:
        if not -90 <= value <= 90:
            raise ValueError("lat must be between -90 and 90")
        return value

    @field_validator("lon")
    @classmethod
    def _check_lon(cls, value: float) -> float:
        if not -180 <= value <= 180:
            raise ValueError("lon must be between -180 and 180")
        return value


class RouteSegmentInput(BaseModel):
    """
    One point along a route to score.

    Only `point` is required. The optional fields let a caller override what
    we measured from OpenStreetMap with their own observation -- a user
    reporting that the streetlights on this stretch are out, say. Anything
    left as None is measured.
    """

    point: GeoPoint
    lighting_score: float | None = Field(None, ge=0, le=1)
    crowd_density: float | None = Field(None, ge=0, le=1)
    cctv_coverage: float | None = Field(None, ge=0, le=1)
    streetlight_density: float | None = Field(None, ge=0, le=1)
    footpath_quality: float | None = Field(None, ge=0, le=1)


class PredictRequest(BaseModel):
    route_id: str | None = Field(None, description="Client-side identifier for the route")
    segments: list[RouteSegmentInput] = Field(..., min_length=1, max_length=50)
    timestamp: datetime | None = Field(
        None, description="ISO timestamp to evaluate at; defaults to now"
    )
    use_live_context: bool = Field(
        True, description="Enrich with live weather and traffic context"
    )


class FeatureContribution(BaseModel):
    feature: str
    contribution: float
    direction: str = Field(..., description="'increases_risk' or 'decreases_risk'")


class GroupedReasons(BaseModel):
    environment: list[str] = []
    infrastructure: list[str] = []
    history: list[str] = []
    time: list[str] = []


class ContextAdjustment(BaseModel):
    source: str
    description: str
    adjustment: float = Field(..., description="Signed delta applied to the risk score")
    data_available: bool


class SegmentScore(BaseModel):
    point: GeoPoint
    risk_score: float = Field(..., ge=0, le=1)
    label: RiskLabel
    confidence: float = Field(..., ge=0, le=1, description="Calibrated probability of the label")
    margin: float = Field(..., description="Gap between the top and runner-up class probabilities")
    data_coverage: float = Field(
        ..., ge=0, le=1, description="How much OpenStreetMap evidence backs this point"
    )
    area_code: str
    class_probabilities: dict[str, float]


class PredictResponse(BaseModel):
    route_id: str | None
    overall_risk_score: float = Field(..., ge=0, le=1)
    label: RiskLabel
    confidence: float = Field(..., ge=0, le=1)
    data_coverage: float = Field(
        ...,
        ge=0,
        le=1,
        description=(
            "Mean OpenStreetMap evidence across the route. Reported separately from "
            "`confidence` because the two fail differently: the model can be confident "
            "about a place very little is mapped for."
        ),
    )
    worst_segment: SegmentScore
    segment_scores: list[SegmentScore]
    context_adjustments: list[ContextAdjustment]
    top_feature_contributions: list[FeatureContribution]
    grouped_reasons: GroupedReasons
    evaluated_at: datetime


class CompareRoutesRequest(BaseModel):
    routes: dict[str, list[RouteSegmentInput]] = Field(
        ..., description="Mapping of route name -> segments", min_length=2, max_length=8
    )
    timestamp: datetime | None = None
    use_live_context: bool = True


class RouteComparisonResult(BaseModel):
    route_name: str
    overall_risk_score: float
    label: RiskLabel
    confidence: float
    data_coverage: float


class CompareRoutesResponse(BaseModel):
    recommended_route: str
    results: list[RouteComparisonResult]
    evaluated_at: datetime


# Fixed vocabulary for *why* a place felt the way it did. A structured
# reason is what lets a report be summarised ("3 people mention poor
# lighting here") rather than only read one comment at a time.
REPORT_REASONS = (
    "poorly_lit",
    "well_lit",
    "isolated",
    "busy",
    "harassment",
    "followed",
    "police_presence",
    "broken_footpath",
    "construction",
    "stray_dogs",
    "other",
)


class FeedbackRequest(BaseModel):
    point: GeoPoint
    rating: int = Field(..., ge=1, le=5, description="1 = felt very unsafe, 5 = felt very safe")
    comment: str | None = Field(None, max_length=500)
    reasons: list[str] = Field(default_factory=list, max_length=5)
    timestamp: datetime | None = None

    @field_validator("reasons")
    @classmethod
    def _check_reasons(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(REPORT_REASONS))
        if unknown:
            raise ValueError(f"Unknown reason(s): {', '.join(unknown)}")
        return list(dict.fromkeys(value))  # de-duplicate, keep order


class RouteAuditsRequest(BaseModel):
    """Reports along a whole route, for the saved-routes dashboard."""

    points: list[GeoPoint] = Field(..., min_length=1, max_length=50)
    radius_km: float = Field(0.75, gt=0, le=5)
    limit: int = Field(50, ge=1, le=200)


class FeedbackResponse(BaseModel):
    status: str
    audit_id: str
    area_code: str
    adjustment: float = Field(..., description="This area's running audit adjustment")
    updated_area_audit_score: float | None = Field(
        None, description="Audit-adjusted crime risk; null when crime data is not configured"
    )
    crime_data_available: bool


class AuditRecord(BaseModel):
    audit_id: str
    point: GeoPoint
    area_code: str
    rating: int
    comment: str | None
    reasons: list[str] = []
    timestamp: datetime
    distance_km: float | None = None


class RouteAuditsResponse(BaseModel):
    count: int
    audits: list[AuditRecord]
    latest: datetime | None = Field(None, description="Newest report along the route")


class NearbyAuditsResponse(BaseModel):
    query_point: GeoPoint
    radius_km: float
    count: int
    audits: list[AuditRecord]


class HealthResponse(BaseModel):
    """Liveness plus full provenance, so a client can tell what it is talking to."""

    status: str
    model_loaded: bool
    model_version: str
    feature_source: str = Field(
        ..., description="'openstreetmap' or 'synthetic' -- what the features were measured from"
    )
    osm_snapshot: str
    crime_data: str
    grid_cells: int
    mapped_cells: int
    poi_counts: dict[str, int]
    audits_recorded: int
    model_trained_at: str | None = None
    model_features: int | None = None
    calibration_method: str | None = None
    test_accuracy: float | None = None
    calibration_error: float | None = None
    external_apis: dict[str, str]

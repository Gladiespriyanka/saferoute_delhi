"""
Typed request/response models for the SafeRoute Delhi API.

Keeping every payload strongly typed via Pydantic gives us automatic
validation, OpenAPI docs, and a single source of truth for the API contract.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.config import DELHI_LAT_RANGE, DELHI_LON_RANGE


class RiskLabel(str, Enum):
    SAFE = "Safe"
    MODERATE = "Moderate"
    UNSAFE = "Unsafe"


class GeoPoint(BaseModel):
    lat: float = Field(..., description="Latitude in decimal degrees")
    lon: float = Field(..., description="Longitude in decimal degrees")

    @field_validator("lat")
    @classmethod
    def validate_lat(cls, v: float) -> float:
        lo, hi = DELHI_LAT_RANGE
        # Soft validation: warn-worthy but we don't hard fail requests just
        # outside the box (users may be at the city edge), only reject
        # values that are clearly not latitudes at all.
        if not -90 <= v <= 90:
            raise ValueError("lat must be a valid latitude between -90 and 90")
        return v

    @field_validator("lon")
    @classmethod
    def validate_lon(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("lon must be a valid longitude between -180 and 180")
        return v


class RouteSegmentInput(BaseModel):
    """A single point/segment along a route the caller wants scored.

    Only `point` is mandatory. All other fields are optional signals — if
    the caller has better ground truth than our synthetic tables (e.g. a
    verified streetlight audit) they can supply it directly and it will
    override our internal lookups.
    """

    point: GeoPoint
    lighting_score: Optional[float] = Field(None, ge=0, le=1)
    crowd_density: Optional[float] = Field(None, ge=0, le=1)
    cctv_coverage: Optional[float] = Field(None, ge=0, le=1)
    streetlight_density: Optional[float] = Field(None, ge=0, le=1)
    footpath_quality: Optional[float] = Field(None, ge=0, le=1)


class PredictRequest(BaseModel):
    route_id: Optional[str] = Field(None, description="Client-side identifier for the route")
    segments: list[RouteSegmentInput] = Field(..., min_length=1)
    timestamp: Optional[datetime] = Field(
        None, description="ISO timestamp to evaluate the route at; defaults to now"
    )
    use_live_context: bool = Field(
        True, description="If true, enrich with live weather/traffic/POI context"
    )


class FeatureContribution(BaseModel):
    feature: str
    contribution: float
    direction: str  # "increases_risk" | "decreases_risk"


class GroupedReasons(BaseModel):
    environment: list[str] = []
    infrastructure: list[str] = []
    history: list[str] = []
    time: list[str] = []


class ContextAdjustment(BaseModel):
    source: str
    description: str
    adjustment: float  # signed delta applied to the risk score
    data_available: bool


class SegmentScore(BaseModel):
    point: GeoPoint
    risk_score: float
    label: RiskLabel
    confidence: float


class PredictResponse(BaseModel):
    route_id: Optional[str]
    overall_risk_score: float = Field(..., ge=0, le=1)
    label: RiskLabel
    confidence: float = Field(..., ge=0, le=1)
    worst_segment: SegmentScore
    segment_scores: list[SegmentScore]
    context_adjustments: list[ContextAdjustment]
    top_feature_contributions: list[FeatureContribution]
    grouped_reasons: GroupedReasons
    evaluated_at: datetime


class CompareRoutesRequest(BaseModel):
    routes: dict[str, list[RouteSegmentInput]] = Field(
        ..., description="Mapping of route_name -> list of segments", min_length=2
    )
    timestamp: Optional[datetime] = None
    use_live_context: bool = True


class RouteComparisonResult(BaseModel):
    route_name: str
    overall_risk_score: float
    label: RiskLabel
    confidence: float


class CompareRoutesResponse(BaseModel):
    recommended_route: str
    results: list[RouteComparisonResult]
    evaluated_at: datetime


class FeedbackRequest(BaseModel):
    point: GeoPoint
    rating: int = Field(..., ge=1, le=5, description="1 = felt very unsafe, 5 = felt very safe")
    comment: Optional[str] = Field(None, max_length=500)
    timestamp: Optional[datetime] = None


class FeedbackResponse(BaseModel):
    status: str
    audit_id: str
    area_code: str
    updated_area_audit_score: float


class AuditRecord(BaseModel):
    audit_id: str
    point: GeoPoint
    area_code: str
    rating: int
    comment: Optional[str]
    timestamp: datetime
    distance_km: Optional[float] = None


class NearbyAuditsResponse(BaseModel):
    query_point: GeoPoint
    radius_km: float
    count: int
    audits: list[AuditRecord]


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_version: str
    external_apis: dict[str, str]

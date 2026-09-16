"""
Central configuration for SafeHerWay (SafeRoute Delhi).

Every tunable constant lives here so the rest of the codebase never
hardcodes magic numbers for weights, thresholds, paths or endpoints.
Anything an operator might reasonably want to change without editing code
is also readable from an environment variable.
"""
from __future__ import annotations

import os
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
# Overridable so a deployment can put generated artifacts on a mounted
# volume, and so the test suite can run fully isolated from the checkout.
ARTIFACTS_DIR = Path(os.environ.get("SAFEROUTE_ARTIFACTS_DIR", BASE_DIR / "artifacts"))
DATA_DIR = Path(os.environ.get("SAFEROUTE_DATA_DIR", BASE_DIR / "data"))
OSM_CACHE_DIR = ARTIFACTS_DIR / "osm_cache"

for _d in (ARTIFACTS_DIR, DATA_DIR, OSM_CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

MODEL_PATH = ARTIFACTS_DIR / "safety_model.joblib"
MODEL_METRICS_PATH = ARTIFACTS_DIR / "model_metrics.json"
MODEL_CARD_PATH = ARTIFACTS_DIR / "model_card.md"
GRID_FEATURES_PATH = ARTIFACTS_DIR / "grid_features.csv"
POI_TABLE_PATH = ARTIFACTS_DIR / "poi_table.csv"
OSM_POINTS_PATH = ARTIFACTS_DIR / "osm_points.csv"
OSM_SCALING_PATH = ARTIFACTS_DIR / "osm_scaling.json"
AUDITS_STORE_PATH = ARTIFACTS_DIR / "audits_store.csv"
TRAINING_DATA_PATH = ARTIFACTS_DIR / "training_data.csv"

# District-level crime input. Checked into the repo as a documented,
# replaceable CSV rather than baked into code -- see data/README.md.
DISTRICT_CRIME_PATH = DATA_DIR / "delhi_district_crime.csv"

# ---------------------------------------------------------------------------
# API security
# ---------------------------------------------------------------------------
# In production these should come from a secrets manager. A comma separated
# list supports key rotation without a code change.
VALID_API_KEYS = frozenset(
    k.strip()
    for k in os.environ.get("SAFEROUTE_API_KEYS", "demo-key-123,dev-key-456").split(",")
    if k.strip()
)
API_KEY_HEADER_NAME = "x-api-key"

# Browser origins allowed to call the API. Default is permissive so the
# static frontend works when opened from a file:// URL or any LAN host;
# set SAFEROUTE_CORS_ORIGINS to a comma separated allowlist in production.
CORS_ALLOW_ORIGINS = [
    o.strip() for o in os.environ.get("SAFEROUTE_CORS_ORIGINS", "*").split(",") if o.strip()
]

# ---------------------------------------------------------------------------
# Study area: Delhi NCT bounding box, and the analysis grid laid over it.
#
# GRID_CELLS_PER_SIDE = 40 gives ~1.2 x 1.4 km cells. That is fine enough
# that the two ends of a typical walking route fall in different cells, and
# coarse enough that per-cell OpenStreetMap density estimates are stable.
# ---------------------------------------------------------------------------
DELHI_LAT_RANGE = (28.40, 28.90)
DELHI_LON_RANGE = (76.85, 77.35)
GRID_CELLS_PER_SIDE = _env_int("SAFEROUTE_GRID_CELLS", 40)

# ---------------------------------------------------------------------------
# Risk label thresholds on the composite risk score (0 = safest, 1 = least
# safe). These bound the *label bootstrapping* step, not model output.
# ---------------------------------------------------------------------------
RISK_LABELS = ("Safe", "Moderate", "Unsafe")
SAFE_UPPER_BOUND = 0.42
MODERATE_UPPER_BOUND = 0.58

# Weights of the transparent composite-risk formula used to bootstrap
# training labels from the measured features. They sum to 1.0 so the
# composite stays on the same [0, 1] scale as its inputs.
COMPOSITE_WEIGHTS = {
    "infra_risk": 0.20,
    "isolation_index": 0.20,
    "crime_risk_index": 0.25,
    "time_of_day_risk": 0.20,
    "lighting_risk": 0.15,
}

# Width of the ambiguous band around each label threshold when sampling
# training labels. Chosen at roughly half the standard deviation of the
# composite score across Delhi (~0.11), so a place clearly on one side of a
# threshold is labelled near-deterministically while a genuinely borderline
# one is close to a coin flip. That leaves a Bayes-optimal accuracy ceiling
# around 0.82 -- enough irreducible uncertainty for calibrated probabilities
# to carry real information, without drowning the signal.
# See app/dataset.py for the full rationale.
LABEL_SAMPLING_TEMPERATURE = _env_float("SAFEROUTE_LABEL_TEMPERATURE", 0.03)

# ---------------------------------------------------------------------------
# Contextual (post-model) adjustment caps. Real-time signals nudge the
# model's score; they must never dominate it.
# ---------------------------------------------------------------------------
MAX_WEATHER_ADJUSTMENT = 0.10
MAX_TRAFFIC_ADJUSTMENT = 0.07
MAX_AUDIT_ADJUSTMENT = 0.10

# Exponential-moving-average factor for folding a new crowdsourced audit
# into an area's running adjustment.
AUDIT_EMA_ALPHA = 0.3

# ---------------------------------------------------------------------------
# External APIs (free / keyless so the system works out of the box; every
# call is wrapped with a timeout and a fallback).
# ---------------------------------------------------------------------------
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OSRM_URL = "https://router.project-osrm.org"
EXTERNAL_API_TIMEOUT_SECONDS = _env_float("SAFEROUTE_EXT_TIMEOUT", 3.0)

# Overpass mirrors, tried in order. The public instances rate-limit and
# occasionally return 504 under load, so more than one is listed.
OVERPASS_ENDPOINTS = [
    e.strip()
    for e in os.environ.get(
        "SAFEROUTE_OVERPASS_ENDPOINTS",
        "https://overpass-api.de/api/interpreter,"
        "https://overpass.osm.ch/api/interpreter,"
        "https://overpass.private.coffee/api/interpreter",
    ).split(",")
    if e.strip()
]
OVERPASS_TIMEOUT_SECONDS = _env_float("SAFEROUTE_OVERPASS_TIMEOUT", 300.0)
OVERPASS_USER_AGENT = "SafeHerWay/1.0 (route-safety research; OSM data via Overpass)"
# The city bbox is downloaded in tiles; smaller tiles mean more, lighter
# requests that are far less likely to hit a mirror's memory/time limit.
OVERPASS_TILES_PER_SIDE = _env_int("SAFEROUTE_OVERPASS_TILES", 4)

# Points-of-interest categories used by the isolation index.
POI_CATEGORIES = ("metro", "bus_stop", "hospital", "police")

# Distance (km) past which being further from a POI stops meaningfully
# increasing isolation.
POI_DISTANCE_SOFT_CAP_KM = 3.0

RANDOM_SEED = _env_int("SAFEROUTE_SEED", 42)

#Smart Escort Mode: ephemeral live-tracking sessions a trusted contact can
# open via an unguessable link, with no account of their own. Kept in
# memory only -- a session is meant to outlive one walk, not a server
# restart, and persisting live-location data to disk would be a strange
# thing to do by default in a safety app.
ESCORT_TTL_SECONDS = _env_int("SAFEROUTE_ESCORT_TTL_SECONDS", 6 * 3600)
ESCORT_TIMELINE_LIMIT = 40
ESCORT_DEFAULT_CHECKIN_SECONDS = 600
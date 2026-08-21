"""
Central configuration for SafeRoute Delhi.

All tunable constants live here so the rest of the codebase never hardcodes
magic numbers for weights, thresholds, or external endpoints.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = BASE_DIR / "artifacts"
ARTIFACTS_DIR.mkdir(exist_ok=True)

MODEL_PATH = ARTIFACTS_DIR / "safety_model.joblib"
CALIBRATOR_LABELS_PATH = ARTIFACTS_DIR / "label_classes.joblib"
CRIME_TABLE_PATH = ARTIFACTS_DIR / "crime_table.csv"
POI_TABLE_PATH = ARTIFACTS_DIR / "poi_table.csv"
AUDITS_STORE_PATH = ARTIFACTS_DIR / "audits_store.csv"
TRAINING_DATA_PATH = ARTIFACTS_DIR / "training_data.csv"

# ---------------------------------------------------------------------------
# API security
# ---------------------------------------------------------------------------
# In production these keys should come from a secrets manager / env vars.
# A comma separated list lets us support key rotation without code changes.
VALID_API_KEYS = set(
    filter(None, os.environ.get("SAFEROUTE_API_KEYS", "demo-key-123,dev-key-456").split(","))
)
API_KEY_HEADER_NAME = "x-api-key"

# ---------------------------------------------------------------------------
# Delhi bounding box (rough) - used to validate incoming coordinates and to
# generate synthetic data that is geographically plausible.
# ---------------------------------------------------------------------------
DELHI_LAT_RANGE = (28.40, 28.90)
DELHI_LON_RANGE = (76.85, 77.35)

# ---------------------------------------------------------------------------
# Risk label thresholds on the final composite risk score (0 = perfectly
# safe, 1 = maximally unsafe).
# ---------------------------------------------------------------------------
RISK_LABELS = ["Safe", "Moderate", "Unsafe"]
SAFE_UPPER_BOUND = 0.42
MODERATE_UPPER_BOUND = 0.58

# ---------------------------------------------------------------------------
# Feature engineering weights (must sum to 1.0 for interpretability of the
# composite score used to *generate* synthetic training labels; the ML model
# itself learns its own weighting from data, these are only the "ground
# truth" generator + the rule-based contextual adjustment weights).
# ---------------------------------------------------------------------------
COMPOSITE_WEIGHTS = {
    "infra_risk": 0.20,
    "isolation_index": 0.20,
    "crime_risk_index": 0.25,
    "time_of_day_risk": 0.20,
    "lighting_risk": 0.15,
}

# Contextual (post-model) adjustment caps - real-time signals can nudge the
# model's score but never dominate it.
MAX_WEATHER_ADJUSTMENT = 0.10
MAX_TRAFFIC_ADJUSTMENT = 0.07
MAX_AUDIT_ADJUSTMENT = 0.10

# ---------------------------------------------------------------------------
# External API endpoints (free / keyless where possible so the demo works
# out of the box; all calls are wrapped with timeouts + fallbacks).
# ---------------------------------------------------------------------------
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSRM_URL = "https://router.project-osrm.org"
EXTERNAL_API_TIMEOUT_SECONDS = float(os.environ.get("SAFEROUTE_EXT_TIMEOUT", "3.0"))

# Points of interest categories we care about for the "isolation index"
POI_CATEGORIES = ["metro", "bus_stop", "hospital", "police"]

RANDOM_SEED = 42

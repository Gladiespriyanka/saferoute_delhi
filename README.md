# 🌸 SafeHerWay

Context-aware ML system that scores pedestrian route safety (**Safe / Moderate / Unsafe**)
using lighting, crowd density, infrastructure quality, time-of-day, historical crime risk,
and crowdsourced audits — enriched with live weather/traffic context and explained with
SHAP + rule-based reasoning.

> Built with synthetic core data (no real Delhi crime/infra dataset wired in) plus real,
> keyless external APIs for weather (Open-Meteo) and a traffic/congestion proxy (OSRM demo
> router). Every external call has a fallback path so the system degrades gracefully offline.

## Architecture

```
app/
  config.py        Central constants: weights, thresholds, API keys, endpoints
  schemas.py        Pydantic request/response models
  data_gen.py        Synthetic areas/crime table/POI table/training data generator
  features.py        Feature engineering: cyclic time encoding, risk indices
  model.py           RandomForest + isotonic calibration, train/save/load/predict
  explain.py         SHAP feature contributions (+ permutation fallback) and
                      rule-based grouped reasons (environment/infra/history/time)
  external_apis.py   Weather + traffic enrichment with graceful fallback
  service.py         SafeRouteService — the single class encapsulating the whole
                      pipeline; used identically by the API and the CLI
  security.py        API-key auth dependency
  main.py            FastAPI app and routes
cli.py                Typer CLI: train / predict / compare / feedback / nearby / interactive
train_model.py         Standalone training script
tests/                 pytest suite (features, service, API)
artifacts/              Generated model + lookup tables (created on first run)
```

## Setup

```bash
pip install -r requirements.txt
python train_model.py            # generates synthetic data + trains/calibrates the model
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The first `SafeRouteService()` instantiation will also auto-bootstrap (generate data +
train) if no model artifact exists yet, so `uvicorn app.main:app` alone works too — it's
just slower to start the first time.

### Running the frontend

`frontend/index.html` is a static page — open it directly, or serve the `frontend/`
folder with any static server (e.g. `python -m http.server 5500` or VS Code Live Server).

**Important:** `frontend/config.js` points at the backend using the same hostname the
page itself was loaded from (port 8000). This matters if you open the frontend via a
LAN/VM IP instead of `localhost` — e.g. `http://10.112.89.26:5500/...` — because a
browser call to `127.0.0.1:8000` always means *the browser's own machine*, not
wherever `uvicorn` happens to be running. Concretely:

- Frontend and backend on the same machine, opened via `localhost` → works out of the box.
- Frontend opened via a machine/VM IP (e.g. `10.112.89.26:5500`) → the backend must
  also be reachable at `10.112.89.26:8000` (bind it with `--host 0.0.0.0` as above).
- Backend on a different host/port than the auto-detected one → load the frontend with
  `?api=http://<backend-host>:<backend-port>` appended once; it's remembered after that
  (stored in `localStorage`).

If the backend isn't reachable, the page now fails fast with a clear message instead of
hanging — previously an unreachable `127.0.0.1:8000` from a remote browser would hang
until the browser's own TCP timeout kicked in (~60s), which is the
"Request timed out after 60s" error this exact setup used to produce.

## Authentication

All routes except `/health` require an `x-api-key` header. Demo keys (override via the
`SAFEROUTE_API_KEYS` env var, comma-separated):

```
demo-key-123
dev-key-456
```

## API

| Method | Path              | Description                                      |
|--------|-------------------|---------------------------------------------------|
| GET    | `/health`         | Liveness check (no auth)                          |
| POST   | `/predict`        | Score a single route                              |
| POST   | `/compare-routes` | Score 2+ named routes, get a recommendation       |
| POST   | `/feedback`       | Submit a crowdsourced safety audit                |
| GET    | `/audits/nearby`  | List audits within a radius of a point            |

### Example: `/predict`

```bash
curl -X POST http://localhost:8000/predict \
  -H "x-api-key: demo-key-123" \
  -H "Content-Type: application/json" \
  -d '{
        "route_id": "home-to-metro",
        "timestamp": "2026-07-05T23:30:00",
        "segments": [
          {"point": {"lat": 28.6139, "lon": 77.2090}},
          {"point": {"lat": 28.6200, "lon": 77.2150}}
        ]
      }'
```

Response includes `overall_risk_score`, `label`, `confidence`, per-segment scores,
`context_adjustments` (weather/traffic, each flagged `data_available`), top SHAP-style
`top_feature_contributions`, and `grouped_reasons` bucketed into environment /
infrastructure / history / time.

### Example: `/compare-routes`

```bash
curl -X POST http://localhost:8000/compare-routes \
  -H "x-api-key: demo-key-123" -H "Content-Type: application/json" \
  -d '{"routes": {"Route A": [{"point": {"lat": 28.61, "lon": 77.20}}],
                   "Route B": [{"point": {"lat": 28.55, "lon": 77.05}}]}}'
```

## CLI

```bash
python cli.py train
python cli.py predict --points "28.6139,77.2090" "28.6200,77.2150" --hour 23
python cli.py compare --route-a "28.61,77.20;28.62,77.21" --route-b "28.55,77.05"
python cli.py feedback --point "28.61,77.20" --rating 2 --comment "Poorly lit lane"
python cli.py nearby --point "28.61,77.20" --radius 1.5
python cli.py interactive
```

`predict` also supports `--api-url http://localhost:8000 --api-key demo-key-123` to
validate the live REST API instead of running the pipeline in-process.

## Tests

```bash
pytest tests/ -v
```

Covers feature-engineering invariants (cyclic wraparound, bounded indices), service-level
behavior (bad infra → higher risk, route comparison recommends the safer option, feedback
loop updates area risk), and full API round-trips (auth, validation, happy paths).

## Design notes

- **Labels are bootstrapped from a transparent composite-risk formula** (`app/config.py:
  COMPOSITE_WEIGHTS`) with noise added, then a RandomForest is trained on top and
  calibrated via isotonic regression so `predict_proba` is a trustworthy confidence
  measure — not just an overconfident tree-ensemble score. In production this composite
  formula would be replaced/supplemented by real incident and audit outcome labels.
- **Cyclic time encoding** (`sin`/`cos` of hour and day-of-week) avoids the classic bug
  where 23:00 and 00:00 look maximally distant to a model that only sees raw integers.
- **Isolation index** blends sparse crowd density with distance to the nearest
  metro/bus/hospital/police point using a mostly-additive-with-a-small-interaction-term
  formula, so being both empty *and* far from help compounds risk rather than just
  averaging.
- **Context adjustments never dominate the model.** Weather/traffic can only nudge the
  final score by small capped amounts (`MAX_WEATHER_ADJUSTMENT`,
  `MAX_TRAFFIC_ADJUSTMENT`), and every adjustment is returned with `data_available` so
  clients (and the CLI) can see exactly when live data was or wasn't used.
- **Explanations are model-faithful where possible** (SHAP `TreeExplainer` against the
  underlying RandomForest) but fall back to a permutation-based approximation if `shap`
  is missing or errors — the API never fails a request just because explainability
  tooling is unavailable.
- **Crowdsourced feedback updates future predictions** via a per-area exponential moving
  average adjustment to the crime risk index, without requiring a full model retrain for
  every new audit.

## Known limitations / next steps

- Synthetic crime/infrastructure data stands in for real datasets; swap `app/data_gen.py`
  lookups for real government/OSM sources when available.
- OSRM's public demo server has no live traffic layer; it's used here only as a
  congestion-shape proxy and is clearly labeled as such.
- Area lookup is a simple lat/lon grid, not real administrative wards — fine for a demo,
  should be replaced with a proper geocoder for production.

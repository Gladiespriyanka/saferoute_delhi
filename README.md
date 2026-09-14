# SafeHerWay

Walking-route safety scoring for Delhi. Given a start and a destination, it
finds the pedestrian routes you could actually take, scores every stretch of
each one **Safe / Moderate / Unsafe**, and tells you *why* — so you can pick
a safer way rather than only the fastest one.

The features are measured, not invented. Street lighting, surveillance
coverage, footpath provision, surrounding activity and distance to the
nearest metro, bus stop, hospital and police station all come from
OpenStreetMap, aggregated for the exact point you are standing on. Live
weather and a traffic estimate nudge the result within a hard cap. Every
prediction carries a calibrated confidence *and* a separate map-coverage
score, because the model can be perfectly sure about a place OSM barely
covers — and you deserve to know when that is the case.

---

## Quick start

```bash
# --- Backend (Python) ---
pip install -r requirements.txt
python fetch_osm_data.py      # download the real OSM extract (~10-25 min, cached)
python train_model.py         # build the dataset, fit + calibrate, write the model card
uvicorn app.main:app --host 0.0.0.0 --port 8000

# --- Web app (React) ---
cd web
npm install
cp .env.example .env.local    # add your OpenRouteService key
npm run dev                   # then open http://localhost:5173
```

For a production build, `npm run build` writes `web/dist/`, which any static
server can host (`npm run preview` serves it locally).

`fetch_osm_data.py` is the slow one — it pulls nine layers over sixteen
tiles from the public Overpass API, which rate-limits. Every tile is cached
under `artifacts/osm_cache/`, so re-running resumes where it left off.

**Without that step the system still starts**, on a clearly-labelled
synthetic stand-in (`app/synthetic.py`) so you can see it work offline.
`/health`, the CLI's `info` command and the app's own footer all say which
one is in use; a synthetic run can never be mistaken for a real one.

---

## Where the data comes from

| Signal | Source | How |
| --- | --- | --- |
| Street lighting | OSM | share of nearby road length tagged `lit=yes`, blended with mapped `highway=street_lamp` density |
| Surveillance coverage | OSM | `man_made=surveillance` node density within 400 m |
| Footpath provision | OSM | `highway=footway\|pedestrian\|path\|steps` length per km² |
| Surrounding activity | OSM | shop and civic-amenity density within 400 m, as a **footfall proxy** |
| Distance to help | OSM | true nearest `station=subway`, `highway=bus_stop`, `amenity=hospital\|clinic`, `amenity=police` |
| Time of day | — | a smooth, hand-specified 24 h risk curve, quoted directly in explanations |
| Weather | Open-Meteo | live, keyless; capped at ±0.10 on the score |
| Traffic | OSRM demo | congestion *estimate*, not live traffic; capped at ±0.07 |
| Community reports | this app | per-area exponential moving average over submitted audits |
| District crime | **you supply it** | see below |

### The two caveats that matter

**OpenStreetMap coverage is very uneven.** Central Delhi is mapped in far
more detail than the outskirts, and there are only a few hundred mapped
streetlights in the entire city. A cell with no mapped lamps is almost
always an unsurveyed cell, not an unlit one. The pipeline handles this
explicitly rather than pretending otherwise:

- densities are log-compressed against a high percentile, not the maximum,
  so a handful of dense market blocks don't squash the rest of the city;
- the `lit=yes` share is shrunk toward the city-wide share in proportion to
  how little tagged road length a cell actually has;
- the streetlight term's weight in the lighting score **scales with the
  number of lamps actually observed**, so an unsurveyed area rests on its
  `lit` tags instead of being dragged toward darkness by a zero;
- an `osm_coverage` score travels with every prediction and is displayed
  next to the model's confidence.

**District crime data is not shipped.** NCRB's district tables and the
Delhi Police annual reports are PDFs, and data.gov.in's equivalents need a
registered key — so `data/delhi_district_crime.csv` ships as a schema
template with every row marked `source=PLACEHOLDER`, and the system never
invents numbers to fill it. While it is unpopulated, `crime_risk_index` is
dropped from the feature set entirely and its composite weight redistributed
over the measured features. **The default model is therefore trained purely
on measured OpenStreetMap quantities.** Populating the file is a strict
upgrade with no code change — see [`data/README.md`](data/README.md).

---

## How the model earns its confidence

This system puts a percentage in front of someone deciding which street to
walk down at night. Three choices make that number mean something.

**Labels are sampled, not thresholded.** A transparent weighted composite of
the measured features (`app/config.COMPOSITE_WEIGHTS`) gives a continuous
risk score. Thresholding it into a hard label would make the target a
deterministic function of the very features the model is handed — training
error goes to ~zero, every prediction comes back 99% confident, and that
confidence is worthless. Instead the composite goes through an **ordered
logit**, and each label is drawn from the resulting probabilities. Places
near a boundary are genuinely ambiguous, exactly as real safety outcomes
are, and there is a measurable **Bayes-optimal accuracy ceiling** to judge
the model against.

**The split is spatial.** Two samples from the same ~1.2 × 1.4 km cell share
their infrastructure measurements. Splitting rows at random scatters near
duplicates across train and test and inflates the score badly. Whole cells
are held out instead, so the test number answers the question that matters:
how does this behave in a part of the city it has never seen?

**Calibration is chosen on data nothing else touched.** The split is
four-way — fit / calibrate / select / test. The forest is fitted on the
first, isotonic and sigmoid calibrators on the second, the winner chosen on
the third by log loss, and the reported metrics produced exactly once on the
fourth.

`artifacts/model_card.md` is regenerated on every training run and reports
accuracy, macro F1, log loss, Brier score and **expected calibration error**,
each against its best achievable value, plus a per-class breakdown, the
confusion matrix, permutation importances and both calibration methods'
scores. `python cli.py metrics` prints it.

Label quality remains the honest limitation: the model has learned a
smoothed version of a hand-specified formula *as applied to real geography*,
not real safety outcomes. It is a well-engineered prior over Delhi's walking
infrastructure, not an empirical crime predictor. Replacing the composite
with real incident and audit outcomes is the highest-value next step, and
the crowdsourced-audit loop already feeds that direction.

---

## Layout

```
app/
  config.py        every tunable constant, env-overridable
  geo.py           haversine, polyline length, the analysis grid
  osm_data.py      Overpass client + aggregation into per-cell features
  crime_data.py    district crime loader + OSM district boundaries
  synthetic.py     labelled stand-in for the OSM extract (tests, offline)
  poi_index.py     BallTree nearest-POI lookup + radius density kernels
  dataset.py       measurement, ordered-logit labels, dataset assembly
  features.py      feature contract, cyclic time, isolation, lighting blend
  model.py         fit, calibrate, evaluate; the versioned model bundle
  model_card.py    renders artifacts/model_card.md
  explain.py       SHAP contributions (+ fallback) and grouped reasons
  external_apis.py weather + traffic, each with a fallback path
  service.py       SafeRouteService — the whole pipeline behind one class
  security.py      constant-time API-key check
  main.py          FastAPI app
web/               React + Vite front end
  src/routes/      Landing, Planner
  src/components/  PlaceField, TimeChoice, RouteChoices, RouteDetail,
                   SegmentList, CommunityPanel, RouteMap, SafetyKit, …
  src/hooks/       useHealth, useRouteSearch
  src/lib/         api, routing, places, storage, config, format
  src/styles/      tokens, base, landing, planner
fetch_osm_data.py  offline: download + aggregate the OSM extract
train_model.py     offline: build dataset, train, write the model card
cli.py             info / predict / compare / feedback / nearby / metrics
tests/             158 tests, hermetic, ~10s
```

`SafeRouteService` is the single entry point to the pipeline; the API and
the CLI are both thin wrappers around it, so behaviour is identical whichever
you use.

---

## API

All routes except `/health` need an `x-api-key` header. Demo keys, overridable
via `SAFEROUTE_API_KEYS`:

```
demo-key-123
dev-key-456
```

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness **and full provenance** — data source, OSM snapshot, crime-data status, model accuracy and calibration error |
| POST | `/predict` | Score one route, with per-segment detail and explanations |
| POST | `/compare-routes` | Score 2–8 routes, get a recommendation |
| POST | `/feedback` | Submit a crowdsourced safety audit |
| GET | `/audits/nearby` | Audits within a radius of a point |
| POST | `/audits/along-route` | Audits within reach of any point on a route, newest first |

`/feedback` accepts an optional `reasons` list drawn from a fixed vocabulary
(`poorly_lit`, `well_lit`, `isolated`, `busy`, `harassment`, `followed`,
`police_presence`, `broken_footpath`, `construction`, `stray_dogs`, `other`).

```bash
curl -X POST http://localhost:8000/predict \
  -H "x-api-key: demo-key-123" -H "Content-Type: application/json" \
  -d '{
        "route_id": "home-to-metro",
        "timestamp": "2026-07-05T23:30:00",
        "segments": [
          {"point": {"lat": 28.6139, "lon": 77.2090}},
          {"point": {"lat": 28.6200, "lon": 77.2150}}
        ]
      }'
```

The response carries `overall_risk_score`, `label`, `confidence`,
`data_coverage`, per-segment scores with full class probabilities,
`context_adjustments` (each flagged `data_available`), SHAP-style
`top_feature_contributions`, and `grouped_reasons` bucketed into
environment / infrastructure / history / time.

Interactive docs at `http://localhost:8000/docs`.

---

## CLI

```bash
python cli.py info                         # what data and model are loaded
python cli.py metrics                      # print the model card
python cli.py predict -p "28.6139,77.2090" -p "28.6200,77.2150" --hour 23
python cli.py compare -r "Ring Rd=28.61,77.20;28.62,77.21" -r "Lane=28.55,77.05"
python cli.py feedback --point "28.61,77.20" --rating 2 --comment "Poorly lit lane"
python cli.py nearby --point "28.61,77.20" --radius 1.5
python cli.py interactive
```

`predict --api-url http://localhost:8000` hits the live REST API instead of
running in-process, which doubles as an API smoke test.

---

## The web app

Three screens: a landing page at `/`, the planner at `/plan`, and **My routes**
at `/routes`. Routes can be planned **on foot, by bike, or by cab/car** — each
uses the matching OpenRouteService profile; the safety score describes the
streets a route uses, so it applies whichever way you travel them.

**My routes** keeps the walks you do often. Each saved route is re-scored for
right now and checked for community reports along its whole length every
time you open the page; anything posted since you last looked is flagged.
Reports are structured — *felt safe / felt unsafe*, plus reasons such as
*poorly lit*, *was followed*, *well lit*, *busy, felt fine* — so a stretch
with three "poorly lit" reports says something a pile of free-text comments
can't. Saved routes live in `localStorage`; the reports themselves are shared.

**Designed for the moment it's needed** — late, on a phone, wanting to be
home. Save a home address once and "Take me home" is a single tap from your
current location. Departure defaults to *now*, with presets instead of a
fiddly dial. Emergency numbers are permanently on screen, never behind a
menu. Saved places and your trusted contact live in `localStorage` and never
reach the server.

**Every search is a URL.** `/plan?from=lat,lon&to=lat,lon&at=23:30` prefills
and runs itself, so a planned walk can be bookmarked or sent to someone else
exactly as you saw it.

**Light and dark.** A toggle in the top bar; the choice is remembered, and
the default follows the system setting. The map re-tones OpenStreetMap's
tiles for the dark theme rather than depending on a paid basemap.

**Location accuracy.** "Use my location" watches for a few seconds and keeps
the best fix rather than taking the first, coarse one, then says how accurate
it is. Two things make it look wrong: on a laptop the fix is a Wi-Fi estimate
(often 100 m+ out, and the app says so), and browsers refuse geolocation on a
plain `http://` page unless it's `localhost` — so over a LAN address use
`localhost`, an SSH tunnel, or https.

**Backend address.** Resolved in this order: `?api=http://host:port` in the
URL (remembered in `localStorage`), a previously saved value, `VITE_API_BASE_URL`,
then the page's own hostname on port 8000. That last default matters when the
page is opened over a LAN or VM IP: a browser call to `127.0.0.1:8000` always
means *the browser's own machine*, not wherever `uvicorn` is running.

**Routing key.** Pedestrian routing uses OpenRouteService, which needs a free
key. Put it in `web/.env.local` as `VITE_ORS_KEY`, or load the page once with
`?ors_key=YOUR_KEY` — handy on a phone.

---

## Tests

```bash
pytest -q        # 158 tests, ~10s
```

The suite runs against the synthetic city on a shrunken grid in a temporary
artifacts directory, so it is hermetic, fast, never touches your real model
or audit log, and never hits a public Overpass mirror — while still
exercising the production code paths end to end.

Coverage includes: geometry and grid invariants; feature bounds and the
cyclic-time wraparound; that dropping crime data redistributes its weight
rather than shrinking the scale; that the label distribution is monotone in
risk and genuinely ambiguous at the thresholds; that the spatial split never
shares a cell between parts; that ECE catches overconfidence; that log loss
and `predict_proba` respect our class order rather than sklearn's
alphabetical one; that the model stays within its noise ceiling and its mean
confidence tracks its accuracy; that a failing weather API can't break a
prediction; that audit adjustments survive a restart; and the full HTTP
contract including auth, validation and error codes.

---

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `SAFEROUTE_API_KEYS` | `demo-key-123,dev-key-456` | Comma-separated valid keys |
| `SAFEROUTE_CORS_ORIGINS` | `*` | Browser origin allowlist |
| `SAFEROUTE_ARTIFACTS_DIR` | `./artifacts` | Where generated artifacts live |
| `SAFEROUTE_DATA_DIR` | `./data` | Where input data lives |
| `SAFEROUTE_GRID_CELLS` | `40` | Analysis grid resolution per side |
| `SAFEROUTE_EXT_TIMEOUT` | `3.0` | Per-call timeout for weather/traffic |
| `SAFEROUTE_OVERPASS_ENDPOINTS` | three public mirrors | Overpass mirrors, tried in order |
| `SAFEROUTE_LABEL_TEMPERATURE` | `0.03` | Width of the ambiguous band when sampling labels |
| `SAFEROUTE_SEED` | `42` | Random seed |

Front-end variables live in `web/.env.local`:

| Variable | Purpose |
| --- | --- |
| `VITE_ORS_KEY` | OpenRouteService key for pedestrian routing |
| `VITE_API_BASE_URL` | Pin the backend instead of auto-detecting it |
| `VITE_API_KEY` | Backend API key (defaults to `demo-key-123`) |

---

## Limitations

- **Not a substitute for judgement.** The score is a prior derived from
  infrastructure data. It does not observe the street right now.
- **Commercial density is a proxy for footfall**, not a measurement of it. It
  understates residential streets that are busy with people but have no shops.
- **The traffic signal is an estimate.** OSRM's public demo carries no live
  traffic layer; it captures road-type and route-shape effects, not today's
  congestion. Swapping in a real traffic API means changing one function.
- **The grid is not administrative wards.** ~1.2 × 1.4 km cells are a
  convenience, not a civic boundary.
- **Labels are bootstrapped**, as described above. This is the big one.

/* ==========================================
   SafeRoute Delhi Configuration
========================================== */

// ---------- Backend ----------

// The backend must be reachable from whatever machine the BROWSER is
// running on -- not from wherever the frontend files happen to be served
// from. Hardcoding "127.0.0.1" breaks the moment the frontend is opened
// from a different host than the one running `uvicorn` (this is exactly
// what caused "Request timed out ... http://127.0.0.1:8000/predict":
// the browser tried to reach a server on ITS OWN machine, not the one
// actually running the API).
//
// Resolution order:
//   1. ?api=http://host:port in the page URL (handy for quick overrides)
//   2. A value saved earlier via localStorage (persists the override)
//   3. Same hostname the page itself was loaded from, port 8000
//      (covers the common case of frontend + backend on the same host,
//      reached over LAN/VM IP instead of localhost)
function resolveApiBaseUrl() {
  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get("api");
  if (fromQuery) {
    localStorage.setItem("saferoute_api_base_url", fromQuery);
    return fromQuery.replace(/\/$/, "");
  }

  const saved = localStorage.getItem("saferoute_api_base_url");
  if (saved) return saved.replace(/\/$/, "");

  const host = window.location.hostname || "127.0.0.1";
  return `http://${host}:8000`;
}

const API = {
  BASE_URL: resolveApiBaseUrl(),

  API_KEY: "demo-key-123",

  // How long the frontend waits for the backend before giving up and
  // showing a clear error instead of hanging indefinitely. /predict can
  // fan out weather/traffic lookups (each with its own few-second budget
  // server-side) plus model inference, so this has headroom above that.
  TIMEOUT_MS: 30000,
};

// ---------- Geocoding ----------

const NOMINATIM = {
  SEARCH: "https://nominatim.openstreetmap.org/search",

  REVERSE: "https://nominatim.openstreetmap.org/reverse",
};

// Delhi/NCR bounding box, matching DELHI_LAT_RANGE / DELHI_LON_RANGE in
// app/config.py on the backend. Used to restrict geocoding results to
// this area — without it, a plain free-text search for a common place
// name (e.g. "Nawada", which is both a Delhi Metro-area locality AND an
// entire district in Bihar) can resolve to the wrong city or state
// entirely. Nominatim then happily returns *a* result, ORS fails to
// find any routable point near it (since it's hundreds of km away), and
// the error message ("Could not find routable point...") gives no hint
// that the real problem was the place name resolving somewhere else in
// India altogether.
const DELHI_BOUNDS = {
  LAT_MIN: 28.4,
  LAT_MAX: 28.9,
  LON_MIN: 76.85,
  LON_MAX: 77.35,
};

// ---------- Routing ----------

// OpenRouteService key resolution, mirroring resolveApiBaseUrl() above:
//   1. ?ors_key=... in the URL (saved to localStorage for next time)
//   2. A value saved earlier via localStorage
//   3. window.SAFEROUTE_ORS_KEY, set by an optional config.local.js
//      (gitignored - copy config.local.example.js to create it)
//
// Previously the real key only ever lived in config.local.js, which was
// the ONLY file declaring API/ORS/DEFAULT_LOCATION/EMERGENCY at all and
// is gitignored. A fresh clone of this repo had no config.local.js, so
// app.html's hard <script src="config.local.js"> 404'd and every one
// of those constants was undefined — the app broke immediately, not just
// routing. Now config.js (committed) always defines everything with a
// safe fallback, and config.local.js's only job is to optionally supply
// the secret key.
function resolveOrsApiKey() {
  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get("ors_key");
  if (fromQuery) {
    localStorage.setItem("saferoute_ors_key", fromQuery);
    return fromQuery;
  }

  const saved = localStorage.getItem("saferoute_ors_key");
  if (saved) return saved;

  if (
    typeof window.SAFEROUTE_ORS_KEY === "string" &&
    window.SAFEROUTE_ORS_KEY.trim()
  ) {
    return window.SAFEROUTE_ORS_KEY.trim();
  }

  return "";
}

// OpenRouteService - pedestrian routing
const ORS = {
  API_KEY: resolveOrsApiKey(),

  ROUTE_URL:
    "https://api.openrouteservice.org/v2/directions/foot-walking/geojson",
};

if (!ORS.API_KEY) {
  console.warn(
    "[SafeRoute] No OpenRouteService API key configured — routing will fail. " +
      "Copy frontend/config.local.example.js to frontend/config.local.js and add your key, " +
      "or open this page with ?ors_key=YOUR_KEY appended to the URL.",
  );
}

// ---------- Route sampling / comparison ----------

const ROUTE_OPTIONS = {
  // Max number of alternative routes to request from OSRM and score.
  MAX_ALTERNATIVES: 3,
  // Each route's coordinates get thinned down to this many points before
  // being sent as scored "segments" to the backend - keeps the number of
  // /predict segment evaluations (and external weather calls) bounded
  // regardless of how long/detailed the raw route geometry is.
  MAX_SEGMENTS_PER_ROUTE: 6,
};

// ---------- Default Map ----------

const DEFAULT_LOCATION = {
  lat: 28.6139,

  lon: 77.209,

  zoom: 12,
};
// ---------- Delhi Emergency ----------

const EMERGENCY = {
  POLICE: "112",

  WOMEN_HELPLINE: "1091",

  TRUSTED_CONTACT: "+919999999999",
};
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

// ---------- Routing ----------

// OSRM - currently used by the existing project
// const OSRM = {
//   ROUTE: "https://router.project-osrm.org/route/v1",
// };

// OpenRouteService - pedestrian routing
const ORS = {
  API_KEY: "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjQ2NWIwYjcyMDk1MzQ1NDM4N2Q0Mjg2NTI3MmM3ZThiIiwiaCI6Im11cm11cjY0In0=",

  ROUTE_URL:
    "https://api.openrouteservice.org/v2/directions/foot-walking/geojson",
};

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

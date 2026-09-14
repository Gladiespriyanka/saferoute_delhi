/**
 * Runtime configuration.
 *
 * Both the backend address and the routing key are resolvable at runtime
 * rather than baked in at build time, because the same built bundle gets
 * opened from localhost, from a phone on the LAN, and from a VM IP — and a
 * browser call to 127.0.0.1 always means *the browser's own machine*, not
 * wherever uvicorn happens to be running.
 *
 * Resolution order for both: ?query param (remembered) → localStorage →
 * build-time env → sensible default.
 */

const STORAGE = {
  apiBase: "safeherway.apiBaseUrl",
  orsKey: "safeherway.orsKey",
};

function readStored(key) {
  try {
    return window.localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode, blocked storage — the query param still works this session */
  }
}

function resolve({ param, storageKey, envValue, fallback }) {
  const fromQuery = new URLSearchParams(window.location.search).get(param);
  if (fromQuery) {
    writeStored(storageKey, fromQuery);
    return fromQuery;
  }
  return readStored(storageKey) || envValue || fallback;
}

export const API_BASE_URL = resolve({
  param: "api",
  storageKey: STORAGE.apiBase,
  envValue: import.meta.env.VITE_API_BASE_URL,
  fallback: `${window.location.protocol}//${window.location.hostname || "127.0.0.1"}:8000`,
}).replace(/\/$/, "");

export const API_KEY = import.meta.env.VITE_API_KEY || "demo-key-123";

export const ORS_KEY = resolve({
  param: "ors_key",
  storageKey: STORAGE.orsKey,
  envValue: import.meta.env.VITE_ORS_KEY,
  fallback: "",
});

/** How long to wait on the backend before giving up with a clear message. */
export const REQUEST_TIMEOUT_MS = 30000;

/** Delhi, used to centre the map and bias place search. */
export const DEFAULT_CENTRE = { lat: 28.6139, lon: 77.209, zoom: 12 };

export const ROUTE_OPTIONS = {
  /** Alternatives to request from the router and score. */
  maxAlternatives: 3,
  /**
   * Points sampled per route. Each becomes one scored segment, so this
   * bounds both model inference and the per-segment weather lookups
   * regardless of how long the route is.
   */
  maxSegments: 6,
};

export const EMERGENCY_NUMBERS = [
  { label: "Police", number: "112", tone: "police" },
  { label: "Women's helpline", number: "1091", tone: "women" },
  { label: "Ambulance", number: "102", tone: "medical" },
  { label: "Fire", number: "101", tone: "fire" },
];

/**
 * How you're getting there. Each maps to an OpenRouteService profile. The
 * safety score describes the streets a route uses — lighting, how busy they
 * are, how far help is — so it applies whichever way you travel them,
 * though it was built with walking most in mind.
 */
export const MODES = [
  { id: "walk", label: "Walk", profile: "foot-walking", verb: "walk", noun: "on foot" },
  { id: "cycle", label: "Cycle", profile: "cycling-regular", verb: "ride", noun: "by bike" },
  { id: "drive", label: "Cab / drive", profile: "driving-car", verb: "drive", noun: "by road" },
];

export const DEFAULT_MODE = "walk";

export function modeById(id) {
  return MODES.find((m) => m.id === id) || MODES[0];
}

/**
 * Everything the app remembers about you, on this device only.
 *
 * Saved places are the feature that makes this usable at the moment it
 * matters: at 11pm you should be one tap from "take me home", not typing an
 * address into a phone in the dark. None of it leaves the browser.
 */

const KEYS = {
  home: "safeherway.home",
  work: "safeherway.work",
  contact: "safeherway.trustedContact",
  recents: "safeherway.recentPlaces",
};

function read(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    if (value === null || value === undefined) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode or blocked storage: the app still works, it just forgets */
  }
}

export const loadSavedPlace = (kind) => read(KEYS[kind], null);
export const saveSavedPlace = (kind, place) => write(KEYS[kind], place);

export const loadTrustedContact = () => read(KEYS.contact, "");
export const saveTrustedContact = (value) => write(KEYS.contact, value || null);

const MAX_RECENTS = 5;

export function loadRecentPlaces() {
  const list = read(KEYS.recents, []);
  return Array.isArray(list) ? list.slice(0, MAX_RECENTS) : [];
}

export function rememberPlace(place) {
  if (!place?.label) return loadRecentPlaces();
  const existing = loadRecentPlaces().filter(
    (item) => item.label.toLowerCase() !== place.label.toLowerCase(),
  );
  const next = [place, ...existing].slice(0, MAX_RECENTS);
  write(KEYS.recents, next);
  return next;
}

/* ---------------------------------------------------------------------
   Saved routes
   ---------------------------------------------------------------------
   A route you walk often — home from the office, home from the gym —
   saved with a name so it's one tap to re-check and so the dashboard can
   watch it for new community reports. Kept on the device, like everything
   else here.
   --------------------------------------------------------------------- */

const ROUTES_KEY = "safeherway.savedRoutes";

export function loadSavedRoutes() {
  const list = read(ROUTES_KEY, []);
  return Array.isArray(list) ? list : [];
}

function persistRoutes(list) {
  write(ROUTES_KEY, list);
  return list;
}

export function saveRoute({ name, from, to, timeMode, mode, points, score, label }) {
  const id = `r_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const route = {
    id,
    name: name.trim() || `${from.name || from.label} → ${to.name || to.label}`,
    from,
    to,
    timeMode: timeMode || { mode: "now", time: "" },
    mode: mode || "walk",
    points, // sampled [lat, lon] pairs, so reports can be looked up along it
    savedAt: new Date().toISOString(),
    lastSeenAt: new Date().toISOString(),
    lastScore: score ?? null,
    lastLabel: label ?? null,
  };
  persistRoutes([route, ...loadSavedRoutes()]);
  return route;
}

export function updateRoute(id, patch) {
  return persistRoutes(loadSavedRoutes().map((r) => (r.id === id ? { ...r, ...patch } : r)));
}

export function removeRoute(id) {
  return persistRoutes(loadSavedRoutes().filter((r) => r.id !== id));
}

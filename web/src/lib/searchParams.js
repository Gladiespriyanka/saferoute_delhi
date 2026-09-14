/**
 * A search encoded in the URL.
 *
 * This makes a planned walk shareable and bookmarkable — "home from the
 * office at 11pm" becomes a link you can keep on your phone — and it means
 * a result can be sent to someone else exactly as you saw it.
 */

const COORD = /^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/;

function parsePlace(value, label) {
  const match = String(value || "").match(COORD);
  if (!match) return null;
  const lat = Number(match[1]);
  const lon = Number(match[2]);
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  const fallback = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  return { lat, lon, label: label || fallback, name: label || fallback };
}

export function readSearchParams(params) {
  const from = parsePlace(params.get("from"), params.get("fromLabel"));
  const to = parsePlace(params.get("to"), params.get("toLabel"));
  const at = params.get("at");
  const time = /^\d{2}:\d{2}$/.test(at || "")
    ? { mode: "custom", time: at }
    : { mode: "now", time: "" };
  const mode = ["walk", "cycle", "drive"].includes(params.get("mode")) ? params.get("mode") : "walk";
  return { from, to, time, mode, complete: Boolean(from && to) };
}

export function writeSearchParams({ from, to, time, mode }) {
  const params = new URLSearchParams();
  if (mode && mode !== "walk") params.set("mode", mode);
  if (from) {
    params.set("from", `${from.lat.toFixed(5)},${from.lon.toFixed(5)}`);
    if (from.label) params.set("fromLabel", from.label);
  }
  if (to) {
    params.set("to", `${to.lat.toFixed(5)},${to.lon.toFixed(5)}`);
    if (to.label) params.set("toLabel", to.label);
  }
  if (time?.mode === "custom" && time.time) params.set("at", time.time);
  return params;
}

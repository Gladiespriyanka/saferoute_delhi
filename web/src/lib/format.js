/** Presentation helpers. Kept in one place so wording stays consistent. */

export const pct = (value) => Math.round((Number(value) || 0) * 100);

export function km(metres) {
  const value = (Number(metres) || 0) / 1000;
  return value < 10 ? value.toFixed(1) : Math.round(value).toString();
}

export function minutes(seconds) {
  return Math.max(1, Math.round((Number(seconds) || 0) / 60));
}

/** "1 h 12 m" reads better than "72 min" once a walk gets long. */
export function duration(seconds) {
  const total = minutes(seconds);
  if (total < 60) return `${total} min`;
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `${hours} h ${rest} m` : `${hours} h`;
}

export const RISK_LABELS = ["Safe", "Moderate", "Unsafe"];

export function labelTone(label) {
  return { Safe: "safe", Moderate: "moderate", Unsafe: "unsafe" }[label] || "neutral";
}

/**
 * The one-line answer. Everything else on the results screen is evidence
 * for this sentence, so it is worth getting right: it names the tradeoff
 * rather than just restating the score.
 */
export function verdictSentence(route, fastest) {
  const label = route.prediction.label;
  const risk = pct(route.prediction.overall_risk_score);

  if (!fastest || fastest.routeId === route.routeId) {
    if (label === "Safe") return "This is both the quickest and the safest way.";
    return `This is the safest of the routes we found, at ${risk}% risk.`;
  }

  const extra = minutes(route.route.duration) - minutes(fastest.route.duration);
  const saved = pct(fastest.prediction.overall_risk_score) - risk;

  if (extra <= 0) return `Safer than the quickest route, and no slower.`;
  if (saved <= 0) return `${extra} min longer, with no safety gain — take the quicker route.`;
  return `${extra} min longer than the quickest route, for ${saved} points less risk.`;
}

/** Clock time for a Date, in the 24h form the backend expects to echo back. */
export function clockTime(date) {
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

export function friendlyTime(date) {
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** Human wording for the structured report reasons the backend accepts. */
export const REPORT_REASONS = [
  { id: "poorly_lit", label: "Poorly lit", tone: "bad" },
  { id: "isolated", label: "Isolated", tone: "bad" },
  { id: "harassment", label: "Harassment", tone: "bad" },
  { id: "followed", label: "Was followed", tone: "bad" },
  { id: "broken_footpath", label: "Broken footpath", tone: "bad" },
  { id: "construction", label: "Construction", tone: "bad" },
  { id: "stray_dogs", label: "Stray dogs", tone: "bad" },
  { id: "well_lit", label: "Well lit", tone: "good" },
  { id: "busy", label: "Busy, felt fine", tone: "good" },
  { id: "police_presence", label: "Police nearby", tone: "good" },
  { id: "other", label: "Something else", tone: "neutral" },
];

export function reasonLabel(id) {
  return REPORT_REASONS.find((r) => r.id === id)?.label || id;
}

/** "2 hours ago", "yesterday", "3 days ago" — for the updates feed. */
export function relativeTime(iso) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  return new Date(iso).toLocaleDateString([], { day: "numeric", month: "short" });
}

/** Plain words for how sure the model is; a percentage here reads as jargon. */
export function confidenceWord(value) {
  const v = Number(value) || 0;
  if (v >= 0.8) return "High";
  if (v >= 0.6) return "Medium";
  return "Low";
}

export function coverageWord(value) {
  const v = pct(value);
  if (v >= 70) return "Good";
  if (v >= 30) return "Partial";
  return "Thin";
}

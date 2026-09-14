import { modeById } from "../lib/config.js";
import { duration, km, labelTone, pct, verdictSentence } from "../lib/format.js";

/**
 * The choice itself.
 *
 * Routes are listed safest-first, and the recommended one carries the
 * tradeoff in words — "7 min longer than the quickest route, for 22 points
 * less risk" — because that comparison is the actual decision. A column of
 * percentages leaves the user to do that arithmetic themselves.
 */
export default function RouteChoices({ routes, selectedId, onSelect }) {
  if (!routes.length) return null;

  const fastest = routes.reduce((best, entry) =>
    entry.route.duration < best.route.duration ? entry : best,
  );

  return (
    <section className="choices" aria-labelledby="choices-title">
      <h2 className="eyebrow" id="choices-title">
        {routes.length === 1 ? "Your route" : `${routes.length} ways to go`}
      </h2>

      <ul className="choices__list">
        {routes.map((entry, index) => {
          const tone = labelTone(entry.prediction.label);
          // One number, one direction, everywhere in the app: higher is
          // safer. Filling these bars by *risk* meant the safest route drew
          // an almost-empty bar, which reads as a bad result at a glance.
          const safety = 100 - pct(entry.prediction.overall_risk_score);
          const isSelected = entry.routeId === selectedId;
          const isFastest = entry.routeId === fastest.routeId;

          return (
            <li key={entry.routeId}>
              <button
                type="button"
                className={`choice${isSelected ? " is-selected" : ""}`}
                aria-pressed={isSelected}
                onClick={() => onSelect(entry.routeId)}
              >
                <span className="choice__head">
                  <span className="choice__name">
                    {entry.recommended ? "Safest route" : `Option ${index + 1}`}
                  </span>
                  <span className={`chip tone-${tone}`}>{entry.prediction.label}</span>
                </span>

                <span className="choice__meta tnum">
                  {km(entry.route.distance)} km · {duration(entry.route.duration)}{" "}
                  {modeById(entry.mode).noun} ·{" "}
                  {safety}% safe
                  {isFastest && routes.length > 1 && (
                    <span className="choice__flag">quickest</span>
                  )}
                </span>

                <span className="bar choice__bar">
                  <span
                    className={`bar__fill tone-${tone}`}
                    style={{ width: `${Math.max(safety, 2)}%` }}
                  />
                </span>

                {isSelected && (
                  <span className="choice__verdict">{verdictSentence(entry, fastest)}</span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

import { useEffect, useState } from "react";

import { reverseGeocode } from "../lib/places.js";
import { labelTone, pct } from "../lib/format.js";

/**
 * Stretch-by-stretch breakdown.
 *
 * Rows render immediately with their scores; the place names arrive
 * afterwards through a rate-limited queue. Awaiting each lookup inline —
 * as the previous build did — left the panel blank for seven seconds
 * before showing anything at all.
 */
export default function SegmentList({ segments }) {
  const [names, setNames] = useState({});

  useEffect(() => {
    let cancelled = false;
    setNames({});
    for (const [index, segment] of segments.entries()) {
      reverseGeocode(segment.point.lat, segment.point.lon).then((name) => {
        if (!cancelled && name) setNames((current) => ({ ...current, [index]: name }));
      });
    }
    return () => {
      cancelled = true;
    };
  }, [segments]);

  return (
    <ol className="segments">
      {segments.map((segment, index) => {
        const safety = 100 - pct(segment.risk_score);
        const tone = labelTone(segment.label);
        return (
          <li className="segment" key={`${segment.point.lat},${segment.point.lon},${index}`}>
            <div className="segment__head">
              <span className="segment__index eyebrow">Stretch {index + 1}</span>
              <span className="segment__name">
                {names[index] || <span className="segment__pending">Locating…</span>}
              </span>
              <span className={`chip chip--bare tone-${tone}`}>{segment.label}</span>
              <span className="segment__risk tnum">{safety}%</span>
            </div>
            <div className="bar">
              <span
                className={`bar__fill tone-${tone}`}
                style={{ width: `${Math.max(safety, 2)}%` }}
              />
            </div>
          </li>
        );
      })}
    </ol>
  );
}

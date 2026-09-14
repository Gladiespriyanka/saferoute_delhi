const FIELDS = [
  { key: "lighting_score", label: "Street lighting" },
  { key: "crowd_density", label: "How busy it is" },
  { key: "cctv_coverage", label: "CCTV you can see" },
  { key: "streetlight_density", label: "Streetlight density" },
  { key: "footpath_quality", label: "Footpath condition" },
];

export const DEFAULT_CONDITIONS = Object.fromEntries(FIELDS.map(({ key }) => [key, 50]));

export function toObserved(values) {
  return Object.fromEntries(Object.entries(values).map(([key, value]) => [key, value / 100]));
}

/**
 * Your own eyes, overriding the map.
 *
 * OpenStreetMap records a `lit` tag for roughly a tenth of Delhi, so for
 * most places the lighting figure is a city-wide prior rather than a
 * measurement. Someone standing on the street knows better, and anything
 * set here is treated as a first-hand observation by the backend.
 */
export default function ConditionSliders({ enabled, values, onToggle, onChange }) {
  return (
    <div className="conditions">
      <label className="conditions__switch">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => onToggle(event.target.checked)}
        />
        <span className="conditions__track" aria-hidden="true">
          <span className="conditions__thumb" />
        </span>
        <span className="conditions__switchText">
          <span className="conditions__switchTitle">I can see the street right now</span>
          <span className="conditions__switchHint">
            Override our estimates with what's actually in front of you.
          </span>
        </span>
      </label>

      {enabled && (
        <div className="conditions__sliders">
          {FIELDS.map((field) => (
            <div className="conditions__row" key={field.key}>
              <label htmlFor={`cond-${field.key}`}>
                {field.label}
                <span className="tnum muted">{values[field.key]}%</span>
              </label>
              <input
                id={`cond-${field.key}`}
                type="range"
                min="0"
                max="100"
                step="5"
                value={values[field.key]}
                onChange={(event) =>
                  onChange({ ...values, [field.key]: Number(event.target.value) })
                }
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

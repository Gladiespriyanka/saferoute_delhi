import { useId, useMemo } from "react";

import { clockTime, friendlyTime } from "../lib/format.js";

/**
 * When are you setting off?
 *
 * Time of day is the single strongest signal in the model, so this needs to
 * be quick and unambiguous. It replaces a hand-drawn circular dial that
 * could only express twelve hours, needed a separate AM/PM control to reach
 * the other twelve, took three taps for the common case, and was unusable
 * with a keyboard.
 *
 * The common case — "I'm leaving now" — is one tap and the default.
 * Everything else is a preset, and the escape hatch is the platform's own
 * time input, which is already accessible, localised and familiar.
 */

const PRESETS = [
  { id: "now", label: "Now", minutes: 0 },
  { id: "30", label: "In 30 min", minutes: 30 },
  { id: "60", label: "In 1 hour", minutes: 60 },
];

export function resolveDeparture(choice) {
  if (choice.mode === "custom" && /^\d{2}:\d{2}$/.test(choice.time)) {
    const [hours, mins] = choice.time.split(":").map(Number);
    const when = new Date();
    when.setHours(hours, mins, 0, 0);
    // A time already past today means tonight has turned into tomorrow.
    if (when.getTime() < Date.now() - 60_000) when.setDate(when.getDate() + 1);
    return when;
  }
  const preset = PRESETS.find((item) => item.id === choice.mode) || PRESETS[0];
  return new Date(Date.now() + preset.minutes * 60_000);
}

export default function TimeChoice({ value, onChange }) {
  const customId = useId();
  const departure = useMemo(() => resolveDeparture(value), [value]);

  const summary =
    value.mode === "now"
      ? `Leaving now · ${friendlyTime(departure)}`
      : `Leaving at ${friendlyTime(departure)}`;

  return (
    <fieldset className="timeChoice">
      <legend className="eyebrow">When are you setting off?</legend>

      <div className="timeChoice__options" role="group">
        {PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            className="timeChoice__option"
            aria-pressed={value.mode === preset.id}
            onClick={() => onChange({ mode: preset.id, time: value.time })}
          >
            {preset.label}
          </button>
        ))}
        <button
          type="button"
          className="timeChoice__option"
          aria-pressed={value.mode === "custom"}
          onClick={() =>
            onChange({ mode: "custom", time: value.time || clockTime(new Date()) })
          }
        >
          Pick a time
        </button>
      </div>

      {value.mode === "custom" && (
        <div className="timeChoice__custom">
          <label className="visuallyHidden" htmlFor={customId}>
            Departure time
          </label>
          <input
            id={customId}
            type="time"
            className="timeChoice__input tnum"
            value={value.time}
            onChange={(event) => onChange({ mode: "custom", time: event.target.value })}
          />
        </div>
      )}

      <p className="timeChoice__summary" aria-live="polite">
        {summary}
        {departure.toDateString() !== new Date().toDateString() && " (tomorrow)"}
      </p>
    </fieldset>
  );
}

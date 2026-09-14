import { MODES } from "../lib/config.js";

const ICONS = {
  walk: (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="13.5" cy="4.5" r="1.8" fill="currentColor" />
      <path d="M10.5 21l2-6 2.5 2.2V21M9 13l1.5-4.5L14 7.5l1.5 3.5H18M10.5 8.5 7 11l1 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  cycle: (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="6" cy="16.5" r="3.5" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="18" cy="16.5" r="3.5" stroke="currentColor" strokeWidth="1.6" />
      <path d="M6 16.5 9.5 9h5l3.5 7.5M9.5 9l3 7.5M13 6h2.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  drive: (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4 13l1.6-4.5A2 2 0 0 1 7.5 7h9a2 2 0 0 1 1.9 1.5L20 13v5H4v-5Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <circle cx="7.5" cy="15.5" r="1.2" fill="currentColor" />
      <circle cx="16.5" cy="15.5" r="1.2" fill="currentColor" />
    </svg>
  ),
};

/** Walk, cycle, or a cab — a segmented control, one tap. */
export default function ModeChoice({ value, onChange }) {
  return (
    <div className="modeChoice" role="group" aria-label="How are you travelling?">
      {MODES.map((mode) => (
        <button
          key={mode.id}
          type="button"
          className="modeChoice__option"
          aria-pressed={value === mode.id}
          onClick={() => onChange(mode.id)}
        >
          <span className="modeChoice__icon">{ICONS[mode.id]}</span>
          {mode.label}
        </button>
      ))}
    </div>
  );
}

import { useCountUp } from "../hooks/useCountUp.js";

/** The score as a ring: reads at a glance, and animates in as the result lands. */
export default function ScoreRing({ value, tone, size = 132, label = "safe" }) {
  const shown = useCountUp(value);
  const r = (size - 14) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - Math.max(0, Math.min(100, shown)) / 100);
  return (
    <div className={`ring tone-${tone}`} style={{ width: size, height: size }} role="img" aria-label={`${value}% ${label}`}>
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size}>
        <circle className="ring__track" cx={size / 2} cy={size / 2} r={r} />
        <circle
          className="ring__fill"
          cx={size / 2}
          cy={size / 2}
          r={r}
          strokeDasharray={c}
          strokeDashoffset={offset}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <div className="ring__center">
        <span className="ring__value tnum">{shown}</span>
        <span className="ring__label">{label}</span>
      </div>
    </div>
  );
}

import Alert from "./Alert.jsx";
import CompanionShare from "./CompanionShare.jsx";
import { duration, km, labelTone, pct } from "../lib/format.js";

export default function WalkingHUD({
  walking,
  onEnd,
  onAcceptReroute,
  escort,
  onStartEscort,
  onSos,
  escortBusy,
}) {
  const {
    onRouteState,
    currentScore,
    alerts,
    dismissAlert,
    reroute,
    rerouting,
    declineReroute,
  } = walking;

  const tone = currentScore ? labelTone(currentScore.label) : "neutral";
  const offRoute = onRouteState && onRouteState.offRouteM > 35;

  return (
    <div className="walkingHud panelCard reveal" style={{ "--i": 0 }}>
      <div className="walkingHud__top">
        <span className={`chip tone-${tone}`}>
          {currentScore
            ? `${currentScore.label} · ${pct(currentScore.overall_risk_score)}% risk here`
            : "Reading your surroundings…"}
        </span>

        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onEnd}
        >
          End walk
        </button>
      </div>

      {onRouteState && (
        <div className="walkingHud__progress">
          <div className="bar">
            <div
              className={`bar__fill tone-${offRoute ? "unsafe" : "safe"}`}
              style={{
                width: `${Math.round(
                  (onRouteState.progressFraction || 0) * 100
                )}%`,
              }}
            />
          </div>

          <span className="walkingHud__progressLabel">
            {offRoute
              ? `${Math.round(onRouteState.offRouteM)} m off the planned route`
              : `${km(onRouteState.progressM)} of ${km(
                  onRouteState.totalM
                )} km walked`}
          </span>
        </div>
      )}

      {alerts.map((alert) => (
        <Alert
          key={alert.id}
          tone={
            alert.tone === "error"
              ? "error"
              : alert.tone === "warn"
                ? "warn"
                : "info"
          }
          onDismiss={() => dismissAlert(alert.id)}
        >
          {alert.text}
        </Alert>
      ))}

      {rerouting && (
        <p className="walkingHud__checking">
          <span className="spinner" aria-hidden="true" />
          Checking for a safer way from here…
        </p>
      )}

      {reroute && (
        <div className="walkingHud__reroute">
          <p>
            Safer from here: <strong>{reroute.prediction.label}</strong>,{" "}
            {pct(reroute.prediction.overall_risk_score)}% risk ·{" "}
            {duration(reroute.route.duration)} ·{" "}
            {km(reroute.route.distance)} km
          </p>

          <div className="walkingHud__rerouteActions">
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={onAcceptReroute}
            >
              Switch to this route
            </button>

            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={declineReroute}
            >
              Keep walking as planned
            </button>
          </div>
        </div>
      )}

      <CompanionShare
        escort={escort}
        onStart={onStartEscort}
        onSos={onSos}
        busy={escortBusy}
      />
    </div>
  );
}
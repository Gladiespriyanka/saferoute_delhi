import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import RouteMap from "../components/RouteMap.jsx";
import { EMERGENCY_NUMBERS } from "../lib/config.js";
import { pct } from "../lib/format.js";
import { getEscortStatus } from "../lib/api.js";
import { useAlertNotice } from "../hooks/useAlertNotice.js";

const POLL_MS = 8000;

/** "3 minutes ago", not a raw timestamp — what matters here is how stale this is. */
function timeAgo(iso) {
  if (!iso) return null;
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.round(minutes / 60)} hr ago`;
}

const STATUS_COPY = {
  active: { title: "On the way", tone: "safe" },
  alert: { title: "Needs attention", tone: "unsafe" },
  ended: { title: "Walk ended", tone: "neutral" },
};

/**
 * The page a trusted contact opens from a shared link — no account, no
 * app, no API key, just this trip's unguessable id. It only ever reads:
 * `getEscortStatus` is the one endpoint in this whole API that doesn't
 * need x-api-key, precisely so this page can exist.
 */
export default function EscortView() {
  const { tripId } = useParams();
  const [status, setStatus] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [stale, setStale] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function poll() {
      try {
        const result = await getEscortStatus({ tripId, signal: controller.signal });
        if (cancelled) return;
        setStatus(result);
        setNotFound(false);
        setStale(false);
      } catch (e) {
        if (cancelled) return;
        if (e?.status === 404) setNotFound(true);
        else setStale(true);
      } finally {
        if (!cancelled) timerRef.current = setTimeout(poll, POLL_MS);
      }
    }
    poll();

    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timerRef.current);
    };
  }, [tripId]);

  useAlertNotice(status?.status, status?.destination?.label);

  if (notFound) {
    return (
      <div className="escortView escortView--empty">
        <h1>This link isn't active</h1>
        <p>The walk it was tracking has ended, or the link has expired. There's nothing more to see here.</p>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="escortView escortView--empty">
        <p>Loading…</p>
      </div>
    );
  }

  const copy = STATUS_COPY[status.status] || STATUS_COPY.active;
  const lastPoint = status.last_point ? { lat: status.last_point.lat, lon: status.last_point.lon } : null;
  const destination = status.destination?.point || null;
  const preview = (status.route_preview || []).map((p) => [p.lat, p.lon]);

  return (
    <div className="escortView">
      <header className={`escortView__banner tone-${copy.tone}`}>
        <h1>{copy.title}</h1>
        {status.status === "alert" && (
          <p>This person may need help — they missed a check-in, reported not okay, or sent an SOS. Consider calling them.</p>
        )}
        {status.status === "ended" && <p>This walk has finished. The link will stop updating.</p>}
        {status.status === "active" && <p>Following this walk live. This page refreshes on its own.</p>}
      </header>

      <div className="escortView__map">
        <RouteMap
          routes={[]}
          from={null}
          to={destination}
          live={lastPoint}
          previewCoordinates={preview}
          offRoute={status.status === "alert"}
        />
      </div>

      <div className="escortView__body">
        <dl className="escortView__facts">
          <div>
            <dt>Last update</dt>
            <dd>{timeAgo(status.last_update_at)}{stale && " — having trouble reaching the server"}</dd>
          </div>
          {status.risk_label && (
            <div>
              <dt>Ground they're on right now</dt>
              <dd>{status.risk_label}{status.risk_score != null ? ` · ${pct(status.risk_score)}% risk` : ""}</dd>
            </div>
          )}
          {status.destination?.label && (
            <div>
              <dt>Heading to</dt>
              <dd>{status.destination.label}</dd>
            </div>
          )}
          {status.progress_fraction != null && (
            <div>
              <dt>Progress</dt>
              <dd>{Math.round(status.progress_fraction * 100)}% of the way</dd>
            </div>
          )}
        </dl>

        <h2>Timeline</h2>
        <ul className="escortView__timeline">
          {[...status.events].reverse().map((event, i) => (
            <li key={i} className={`escortView__event escortView__event--${event.kind}`}>
              <span className="escortView__eventTime">{timeAgo(event.at)}</span>
              <span>{event.text}</span>
            </li>
          ))}
        </ul>

        {status.status === "alert" && (
          <div className="escortView__emergency">
            <h2>If you can't reach them</h2>
            <div className="sos__grid">
              {EMERGENCY_NUMBERS.map((item) => (
                <a key={item.number} href={`tel:${item.number}`} className={`sos__tile sos__tile--${item.tone}`}>
                  <span className="sos__tileNumber tnum">{item.number}</span>
                  <span className="sos__tileLabel">{item.label}</span>
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
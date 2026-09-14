import { useCallback, useEffect, useState } from "react";

import ReportComposer from "./ReportComposer.jsx";
import ReportList from "./ReportList.jsx";
import { getNearbyAudits } from "../lib/api.js";

/**
 * What people who walked here said, and a way to add to it. Reports are
 * pinned to the riskiest stretch of the route — where a first-hand account
 * is worth most.
 */
export default function CommunityPanel({ point }) {
  const [audits, setAudits] = useState(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(
    (signal) =>
      getNearbyAudits({ lat: point.lat, lon: point.lon, signal })
        .then(setAudits)
        .catch(() => setAudits({ count: 0, audits: [], failed: true })),
    [point.lat, point.lon],
  );

  useEffect(() => {
    const controller = new AbortController();
    setAudits(null);
    setOpen(false);
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return (
    <section className="panelCard community">
      <h3>From people who walked here</h3>
      {audits === null && <p className="muted">Checking reports…</p>}
      {audits?.failed && <p className="muted">Couldn't load reports right now.</p>}
      {audits && !audits.failed && (
        <ReportList
          audits={audits.audits}
          emptyText="No reports within 1.5 km of the riskiest stretch yet. Yours would be the first."
        />
      )}

      <button
        type="button"
        className="community__toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "Close" : "Add your report"}
      </button>

      {open && (
        <div className="community__form">
          <ReportComposer point={point} onSubmitted={() => load()} />
        </div>
      )}
    </section>
  );
}

import { reasonLabel, relativeTime } from "../lib/format.js";

/** Community reports, newest first, with reasons as chips. */
export default function ReportList({ audits, newSince, limit = 5, emptyText, withRoute = false }) {
  if (!audits?.length) {
    return <p className="muted">{emptyText || "No reports here yet."}</p>;
  }

  return (
    <ul className="reports">
      {audits.slice(0, limit).map((audit) => {
        const good = audit.rating >= 4;
        const since = newSince ?? audit.newSince;
        const isNew = since && new Date(audit.timestamp) > new Date(since);
        return (
          <li key={audit.audit_id} className={`reportItem${isNew ? " is-new" : ""}`}>
            <div className="reportItem__head">
              <span className={`chip chip--bare ${good ? "tone-safe" : "tone-unsafe"}`}>
                {good ? "Felt safe" : "Felt unsafe"}
              </span>
              <span className="muted tnum">{relativeTime(audit.timestamp)}</span>
              {isNew && <span className="reportItem__new">New</span>}
            </div>
            {withRoute && audit.routeName && (
              <p className="reportItem__route">on {audit.routeName}</p>
            )}
            {audit.reasons?.length > 0 && (
              <p className="reportItem__reasons">
                {audit.reasons.map((r) => (
                  <span key={r} className="reportItem__reason">
                    {reasonLabel(r)}
                  </span>
                ))}
              </p>
            )}
            {audit.comment && <p className="reportItem__comment">{audit.comment}</p>}
          </li>
        );
      })}
    </ul>
  );
}

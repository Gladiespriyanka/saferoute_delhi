import { useState } from "react";

import { submitFeedback } from "../lib/api.js";
import { REPORT_REASONS } from "../lib/format.js";

/**
 * "This was good / this was bad, because…"
 *
 * Two big verdict buttons, then reasons as chips, then an optional line of
 * text. Structured reasons are what make reports useful to anyone but the
 * writer: a stretch with three "poorly lit" reports says something a pile
 * of free-text comments can't be summarised into.
 */
export default function ReportComposer({ point, onSubmitted, compact = false }) {
  const [verdict, setVerdict] = useState(null); // "good" | "bad"
  const [reasons, setReasons] = useState([]);
  const [comment, setComment] = useState("");
  const [status, setStatus] = useState(null);
  const [sending, setSending] = useState(false);

  const visibleReasons = REPORT_REASONS.filter(
    (r) => !verdict || r.tone === "neutral" || r.tone === verdict,
  );

  function toggleReason(id) {
    setReasons((current) =>
      current.includes(id) ? current.filter((r) => r !== id) : [...current, id].slice(0, 5),
    );
  }

  async function send(event) {
    event.preventDefault();
    if (!verdict) {
      setStatus({ tone: "error", text: "Was it good or bad?" });
      return;
    }
    setSending(true);
    setStatus(null);
    try {
      const result = await submitFeedback({
        lat: point.lat,
        lon: point.lon,
        // Good/bad collapses onto the 1–5 scale the model already uses,
        // with reasons carrying the detail.
        rating: verdict === "good" ? 5 : 1,
        reasons,
        comment: comment.trim(),
      });
      setStatus({ tone: "ok", text: "Thank you. Your report is live for everyone walking here." });
      setVerdict(null);
      setReasons([]);
      setComment("");
      onSubmitted?.(result);
    } catch (error) {
      setStatus({ tone: "error", text: error.message });
    } finally {
      setSending(false);
    }
  }

  return (
    <form className={`report${compact ? " report--compact" : ""}`} onSubmit={send}>
      <div className="report__verdicts" role="group" aria-label="How did it feel?">
        <button
          type="button"
          className="report__verdict report__verdict--good"
          aria-pressed={verdict === "good"}
          onClick={() => setVerdict("good")}
        >
          Felt safe
        </button>
        <button
          type="button"
          className="report__verdict report__verdict--bad"
          aria-pressed={verdict === "bad"}
          onClick={() => setVerdict("bad")}
        >
          Felt unsafe
        </button>
      </div>

      {verdict && (
        <>
          <p className="eyebrow report__label">Because…</p>
          <div className="report__reasons">
            {visibleReasons.map((reason) => (
              <button
                key={reason.id}
                type="button"
                className="report__reason"
                aria-pressed={reasons.includes(reason.id)}
                onClick={() => toggleReason(reason.id)}
              >
                {reason.label}
              </button>
            ))}
          </div>

          <textarea
            className="report__comment"
            rows={2}
            maxLength={500}
            placeholder="Anything else worth knowing? (optional)"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />

          <button type="submit" className="btn btn--primary" disabled={sending}>
            {sending ? "Sending…" : "Post report"}
          </button>
        </>
      )}

      {status && (
        <p className={`report__status is-${status.tone}`} role="status">
          {status.text}
        </p>
      )}
    </form>
  );
}

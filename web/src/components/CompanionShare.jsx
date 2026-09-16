import { useState } from "react";

import { loadTrustedContact } from "../lib/storage.js";

/**
 * The walker's controls for Smart Escort Mode: start a session, hand the
 * link to someone, answer check-ins, and a one-tap SOS. The companion's
 * side of this is `routes/EscortView.jsx` — a page this panel's link
 * points at, nothing more.
 */
export default function CompanionShare({ escort, onStart, onSos, busy }) {
  const [copied, setCopied] = useState(false);
  const contact = loadTrustedContact();

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(escort.shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked — the link is still shown to select by hand */
    }
  }

  async function shareLink() {
    const message = `I'm walking and sharing my live location with you: ${escort.shareUrl}`;
    if (navigator.share) {
      try {
        await navigator.share({ title: "Follow my walk", text: message });
        return;
      } catch {
        /* cancelled — fall through to a direct text */
      }
    }
    if (contact) {
      window.location.href = `sms:${contact.replace(/[^\d+]/g, "")}?body=${encodeURIComponent(message)}`;
    } else {
      copyLink();
    }
  }

  if (!escort.active) {
    return (
      <button type="button" className="btn btn--ghost btn--sm companionShare__start" onClick={onStart} disabled={busy}>
        {busy ? "Starting…" : "Share this walk with someone you trust"}
      </button>
    );
  }

  return (
    <div className="companionShare">
      <div className="companionShare__linkRow">
        <input readOnly value={escort.shareUrl} onFocus={(e) => e.target.select()} aria-label="Share link" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={copyLink}>
          {copied ? "Copied" : "Copy"}
        </button>
        <button type="button" className="btn btn--primary btn--sm" onClick={shareLink}>
          {contact ? "Text my contact" : "Share"}
        </button>
      </div>

      {escort.checkInDue && (
        <div className="companionShare__checkin" role="alert">
          <p>Still doing okay?</p>
          <div className="companionShare__checkinActions">
            <button type="button" className="btn btn--primary btn--sm" onClick={() => escort.checkIn(true)}>
              I'm fine
            </button>
            <button type="button" className="btn btn--danger btn--sm" onClick={() => escort.checkIn(false)}>
              Not okay
            </button>
          </div>
        </div>
      )}

      <button type="button" className="companionShare__sos" onClick={onSos}>
        Send SOS to whoever's watching this link
      </button>
    </div>
  );
}
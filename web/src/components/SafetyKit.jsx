import { useEffect, useRef, useState } from "react";

import { EMERGENCY_NUMBERS } from "../lib/config.js";
import { loadTrustedContact, saveTrustedContact } from "../lib/storage.js";

const PHONE = (
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path
      d="M6.5 3.5h3l1.5 4-2 1.5a12 12 0 0 0 6 6l1.5-2 4 1.5v3c0 1-.9 1.8-1.9 1.6-7-1.3-12.4-6.7-13.7-13.7-.2-1 .6-1.9 1.6-1.9Z"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinejoin="round"
    />
  </svg>
);

/**
 * Help, one tap away.
 *
 * `placement="bar"` renders it as a compact control inside the app bar (the
 * dashboard); the default floats it over the map (the planner). Either way
 * it opens the same panel: numbers that dial directly, live-location
 * sharing, and a trusted contact kept on this device.
 */
export default function SafetyKit({ placement = "float" }) {
  const [open, setOpen] = useState(false);
  const [contact, setContact] = useState(() => loadTrustedContact());
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [shareState, setShareState] = useState(null);
  const panelRef = useRef(null);
  const buttonRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function onPointerDown(event) {
      if (panelRef.current?.contains(event.target) || buttonRef.current?.contains(event.target)) return;
      setOpen(false);
    }
    function onKeyDown(event) {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function shareLocation() {
    if (!navigator.geolocation) {
      setShareState({ tone: "error", text: "Location isn't available on this device." });
      return;
    }
    setShareState({ tone: "info", text: "Getting your location…" });
    navigator.geolocation.getCurrentPosition(
      async (position) => {
        const lat = position.coords.latitude.toFixed(6);
        const lon = position.coords.longitude.toFixed(6);
        const url = `https://maps.google.com/?q=${lat},${lon}`;
        const message = `I'm sharing my live location: ${url}`;
        const base = { tone: "ok", url };
        if (navigator.share) {
          try {
            await navigator.share({ title: "My live location", text: message });
            setShareState({ ...base, text: "Shared. The link is below too." });
            return;
          } catch {
            /* cancelled or unsupported — fall through */
          }
        }
        if (contact) {
          window.location.href = `sms:${contact.replace(/[^\d+]/g, "")}?body=${encodeURIComponent(message)}`;
          setShareState({ ...base, text: "Opening a text to your trusted contact." });
          return;
        }
        try {
          await navigator.clipboard.writeText(message);
          setShareState({ ...base, text: "Link copied — paste it to whoever you need." });
        } catch {
          setShareState({ ...base, text: "Here's your location link:" });
        }
      },
      () => setShareState({ tone: "error", text: "Couldn't get your location. Check permissions." }),
      { enableHighAccuracy: true, timeout: 10000 },
    );
  }

  function persistContact() {
    const value = draft.trim();
    saveTrustedContact(value);
    setContact(value);
    setEditing(false);
  }

  return (
    <div className={`safetyKit safetyKit--${placement}`}>
      <button
        ref={buttonRef}
        type="button"
        className={`safetyKit__button${open ? " is-open" : ""}`}
        aria-expanded={open}
        aria-label="Emergency help"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="safetyKit__text">SOS</span>
      </button>

      {open && (
        <div className="sos onDark" ref={panelRef} role="dialog" aria-label="Emergency help">
          <div className="sos__head">
            <h2 className="sos__title">Emergency</h2>
            <button type="button" className="sos__close" aria-label="Close" onClick={() => setOpen(false)}>
              ×
            </button>
          </div>

          <div className="sos__grid">
            {EMERGENCY_NUMBERS.map((item) => (
              <a key={item.number} href={`tel:${item.number}`} className={`sos__tile sos__tile--${item.tone}`}>
                <span className="sos__tileNumber tnum">{item.number}</span>
                <span className="sos__tileLabel">{item.label}</span>
              </a>
            ))}
          </div>

          <button type="button" className="sos__share" onClick={shareLocation}>
            Share my live location
          </button>

          {shareState && (
            <p className={`sos__status is-${shareState.tone}`} role="status">
              {shareState.text}
              {shareState.url && (
                <a className="sos__link" href={shareState.url} target="_blank" rel="noreferrer">{shareState.url}</a>
              )}
            </p>
          )}

          <div className="sos__contact">
            {contact && !editing ? (
              <>
                <a href={`tel:${contact.replace(/[^\d+]/g, "")}`} className="sos__contactLink">
                  <span className="sos__contactLabel">Trusted contact</span>
                  <span className="tnum">{contact}</span>
                </a>
                <button type="button" className="sos__edit" onClick={() => { setDraft(contact); setEditing(true); }}>Edit</button>
              </>
            ) : (
              <form className="sos__contactForm" onSubmit={(e) => { e.preventDefault(); persistContact(); }}>
                <label className="visuallyHidden" htmlFor="trusted-contact">Trusted contact number</label>
                <input id="trusted-contact" type="tel" placeholder="Add a trusted contact" value={draft} onChange={(e) => setDraft(e.target.value)} />
                <button type="submit">Save</button>
              </form>
            )}
          </div>
          <p className="sos__note">Kept on this device. Never sent to us.</p>
        </div>
      )}
    </div>
  );
}

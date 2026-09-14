/**
 * Inline, dismissible status. Never window.alert(): a modal dialog blocks
 * the page, can't be styled, and on a phone lands on top of the map you
 * were trying to read.
 */
export default function Alert({ tone = "error", title, children, onDismiss, action }) {
  return (
    <div className={`alert alert--${tone}`} role={tone === "error" ? "alert" : "status"}>
      <div className="alert__body">
        {title && <strong className="alert__title">{title}</strong>}
        <div className="alert__text">{children}</div>
        {action}
      </div>
      {onDismiss && (
        <button type="button" className="alert__dismiss" onClick={onDismiss} aria-label="Dismiss">
          ×
        </button>
      )}
    </div>
  );
}

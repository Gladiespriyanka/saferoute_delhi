import { NavLink } from "react-router-dom";

import SafetyKit from "./SafetyKit.jsx";
import { useTheme } from "../hooks/useTheme.js";
import { loadSavedRoutes } from "../lib/storage.js";

const SUN = (
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.7" />
    <path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
  </svg>
);
const MOON = (
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
  </svg>
);

/** Shared chrome: brand, sections, theme, and — on scrolling pages — SOS. */
export default function AppShell({ children, emergencyInBar = false }) {
  const savedCount = loadSavedRoutes().length;
  const { isDark, toggle } = useTheme();

  return (
    <div className="shell">
      <header className="shell__bar onDark">
        <NavLink to="/" className="shell__brand">
          <svg className="shell__mark" viewBox="0 0 32 32" aria-hidden="true">
            <path d="M7 23c2.8-9.5 8.2-4.2 11.6-13.4L23 5" stroke="currentColor" strokeWidth="2.4" fill="none" strokeLinecap="round" />
            <circle cx="23.4" cy="4.6" r="2.6" fill="var(--saffron)" />
          </svg>
          <span>SafeHerWay</span>
        </NavLink>
        <nav className="shell__nav" aria-label="Sections">
          <NavLink to="/plan" className="shell__link">Plan</NavLink>
          <NavLink to="/routes" className="shell__link">
            My routes
            {savedCount > 0 && <span className="shell__count tnum">{savedCount}</span>}
          </NavLink>
        </nav>
        <div className="shell__tools">
          <button
            type="button"
            className="shell__theme"
            onClick={toggle}
            aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
            title={isDark ? "Light theme" : "Dark theme"}
          >
            {isDark ? SUN : MOON}
          </button>
          {emergencyInBar && <SafetyKit placement="bar" />}
        </div>
      </header>
      <div className="shell__body">{children}</div>
    </div>
  );
}

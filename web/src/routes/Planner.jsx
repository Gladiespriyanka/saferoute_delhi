import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import Alert from "../components/Alert.jsx";
import AppShell from "../components/AppShell.jsx";
import ModeChoice from "../components/ModeChoice.jsx";
import ConditionSliders, {
  DEFAULT_CONDITIONS,
  toObserved,
} from "../components/ConditionSliders.jsx";
import PlaceField from "../components/PlaceField.jsx";
import Provenance from "../components/Provenance.jsx";
import RouteChoices from "../components/RouteChoices.jsx";
import RouteDetail from "../components/RouteDetail.jsx";
import RouteMap from "../components/RouteMap.jsx";
import SafetyKit from "../components/SafetyKit.jsx";
import TimeChoice, { resolveDeparture } from "../components/TimeChoice.jsx";
import { useHealth } from "../hooks/useHealth.js";
import { useRouteSearch } from "../hooks/useRouteSearch.js";
import { USABLE_ACCURACY_M, describeAccuracy, getAccurateLocation, watchLocation } from "../lib/geolocation.js";
import { geocode } from "../lib/places.js";
import { hasRoutingKey } from "../lib/routing.js";
import { readSearchParams, writeSearchParams } from "../lib/searchParams.js";
import { sampleRoutePoints } from "../lib/routing.js";
import {
  loadRecentPlaces,
  loadSavedPlace,
  rememberPlace,
  saveRoute,
  saveSavedPlace,
} from "../lib/storage.js";

const PIN_ICON = (
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <circle cx="12" cy="12" r="3.2" stroke="currentColor" strokeWidth="1.6" />
    <path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
);

const FLAG_ICON = (
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M5 21V4.5M5 4.5h11.5L14 8l2.5 3.5H5" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
  </svg>
);

/** Departure, weather and traffic for the selected route, at a glance. */
function TonightStrip({ entry, departure }) {
  const bySource = {};
  for (const a of entry.prediction.context_adjustments || []) {
    const k = String(a.source || "").toLowerCase();
    if (!bySource[k]) bySource[k] = a;
  }
  const weather = bySource.weather;
  const traffic = bySource.traffic;
  const hour = departure.getHours();
  const period = hour >= 21 || hour < 5 ? "Late night" : hour >= 18 ? "Evening" : hour >= 12 ? "Afternoon" : "Morning";
  return (
    <ul className="tonight reveal" style={{ "--i": 0 }}>
      <li>
        <span className="tonight__label">Leaving</span>
        <span className="tonight__value">{period}</span>
        <span className="tonight__sub tnum">{departure.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
      </li>
      <li>
        <span className="tonight__label">Weather</span>
        <span className="tonight__value">
          {weather?.data_available ? (weather.adjustment > 0 ? "Adverse" : "Clear") : "—"}
        </span>
        <span className="tonight__sub">{weather?.data_available ? "live" : "unavailable"}</span>
      </li>
      <li>
        <span className="tonight__label">Traffic</span>
        <span className="tonight__value">
          {traffic?.data_available ? (traffic.adjustment < 0 ? "Busy" : "Quiet") : "—"}
        </span>
        <span className="tonight__sub">{traffic?.data_available ? "live" : "unavailable"}</span>
      </li>
    </ul>
  );
}

export default function Planner() {
  const { status: healthStatus, health } = useHealth();
  const { search, routes, progress, error, busy } = useRouteSearch();
  const [searchParams, setSearchParams] = useSearchParams();
  const initial = useMemo(() => readSearchParams(searchParams), []); // first render only

  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);
  const [time, setTime] = useState(initial.time);
  const [mode, setMode] = useState(initial.mode);
  const [useConditions, setUseConditions] = useState(false);
  const [conditions, setConditions] = useState(DEFAULT_CONDITIONS);
  const [selectedId, setSelectedId] = useState(null);
  const [notice, setNotice] = useState(null);
  const [live, setLive] = useState(null);
  const [focusFix, setFocusFix] = useState(null);
  const [home, setHome] = useState(() => loadSavedPlace("home"));
  const [recents, setRecents] = useState(() => loadRecentPlaces());
  const [saveState, setSaveState] = useState({ open: false, name: "", done: null });
  const resultsRef = useRef(null);

  // Keep a live position for the map and for "use my location", but never
  // block on it — the permission prompt shouldn't stand between someone and
  // a route.
  useEffect(() => watchLocation(setLive), []);

  useEffect(() => {
    if (routes.length) {
      setSelectedId(routes[0].routeId);
      setSaveState({ open: false, name: "", done: null });
      resultsRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }, [routes]);

  function saveSelectedRoute(event) {
    event.preventDefault();
    if (!selected || !from || !to) return;
    const saved = saveRoute({
      name: saveState.name,
      from,
      to,
      timeMode: time,
      mode,
      points: sampleRoutePoints(selected.route.coordinates),
      score: 100 - Math.round(selected.prediction.overall_risk_score * 100),
      label: selected.prediction.label,
    });
    setSaveState({ open: false, name: "", done: saved.name });
  }

  // A link that already names both ends runs itself, so a shared or
  // bookmarked route opens straight onto its result.
  const autoRunRef = useRef(false);
  useEffect(() => {
    if (autoRunRef.current || !initial.complete || !hasRoutingKey()) return;
    autoRunRef.current = true;
    search({
      from: initial.from,
      to: initial.to,
      timestamp: resolveDeparture(initial.time).toISOString(),
      observed: null,
      mode: initial.mode,
    });
  }, [initial, search]);

  const selected = useMemo(
    () => routes.find((entry) => entry.routeId === selectedId) || routes[0] || null,
    [routes, selectedId],
  );

  const fastest = useMemo(
    () =>
      routes.length
        ? routes.reduce((best, entry) =>
            entry.route.duration < best.route.duration ? entry : best,
          )
        : null,
    [routes],
  );

  const useMyLocation = useCallback(async (apply) => {
    setNotice({ tone: "info", text: "Waiting for a precise fix…" });
    try {
      const fix = await getAccurateLocation();
      const place = {
        lat: fix.lat,
        lon: fix.lon,
        accuracy: fix.accuracy,
        label: "My current location",
        name: "My current location",
      };
      setLive(fix);
      setFocusFix({ ...fix, requestedAt: Date.now() }); // pan the map there either way
      if (fix.accuracy > USABLE_ACCURACY_M) {
        // A kilometres-wide guess would put the start point in the wrong
        // neighbourhood, so it's shown on the map rather than planned from
        // — unless you say so.
        setNotice({
          tone: "warn",
          text: `That's roughly where you are — ${describeAccuracy(fix.accuracy)}. Type your starting point for something exact, or use this anyway.`,
          action: { label: "Use it anyway", onClick: () => { apply(place); setNotice(null); } },
        });
        return;
      }
      apply(place);
      setNotice(
        fix.accuracy > 100
          ? { tone: "warn", text: `Location is ${describeAccuracy(fix.accuracy)}. A phone outdoors will be much closer.` }
          : { tone: "info", text: `Location set — ${describeAccuracy(fix.accuracy)}.` },
      );
    } catch (error) {
      setNotice({ tone: "error", text: error.message });
    }
  }, []);

  /** Resolve whatever is in a field into real coordinates. */
  const resolve = useCallback(async (place, text, which) => {
    if (place) return place;
    if (!text?.trim()) throw new Error(`Enter a ${which}.`);
    return geocode(text.trim());
  }, []);

  async function runSearch(event) {
    event?.preventDefault();
    setNotice(null);

    const form = event?.currentTarget;
    const typedFrom = form?.elements?.["from"]?.value ?? "";
    const typedTo = form?.elements?.["to"]?.value ?? "";

    let origin;
    let destination;
    try {
      origin = await resolve(from, typedFrom, "starting point");
      destination = await resolve(to, typedTo, "destination");
    } catch (resolveError) {
      setNotice({ tone: "error", text: resolveError.message });
      return;
    }

    setFrom(origin);
    setTo(destination);
    setRecents(rememberPlace(destination));
    setSearchParams(writeSearchParams({ from: origin, to: destination, time, mode }), {
      replace: true,
    });

    await search({
      from: origin,
      to: destination,
      timestamp: resolveDeparture(time).toISOString(),
      observed: useConditions ? toObserved(conditions) : null,
      mode,
    });
  }

  async function takeMeHome() {
    if (!home) return;
    setNotice(null);
    useMyLocation(async (place) => {
      setFrom(place);
      setTo(home);
      setTime({ mode: "now", time: "" });
      await search({
        from: place,
        to: home,
        timestamp: new Date().toISOString(),
        observed: useConditions ? toObserved(conditions) : null,
        mode,
      });
    });
  }

  function saveHome() {
    if (!to) return;
    saveSavedPlace("home", to);
    setHome(to);
    setNotice({ tone: "info", text: `Saved ${to.label} as home, on this device.` });
  }

  const routingReady = hasRoutingKey();
  const backendDown = healthStatus === "error";
  const noModel = health && !health.model_loaded;

  const destinationShortcuts = [
    home && {
      key: "home",
      label: "Home",
      icon: "⌂",
      onSelect: (choose) => choose(home),
    },
    ...recents.slice(0, 3).map((place, index) => ({
      key: `recent-${index}`,
      label: place.name || place.label,
      onSelect: (choose) => choose(place),
    })),
  ].filter(Boolean);

  return (
    <AppShell>
    <div className="planner">
      <div className="planner__panel">
        <div className="planner__body">
          {backendDown && (
            <Alert title="Backend unreachable">
              Start it with <code>uvicorn app.main:app --host 0.0.0.0</code>, or open this
              page with <code>?api=http://host:port</code> to point somewhere else.
            </Alert>
          )}

          {!backendDown && noModel && (
            <Alert title="No model trained yet">
              The backend is running but has nothing to score with. Run{" "}
              <code>python train_model.py</code> and reload.
            </Alert>
          )}

          {!routingReady && (
            <Alert tone="warn" title="Routing key missing">
              Walking routes come from OpenRouteService. Add <code>VITE_ORS_KEY</code> to{" "}
              <code>web/.env.local</code>, or open this page once with{" "}
              <code>?ors_key=YOUR_KEY</code>.
            </Alert>
          )}

          {home && (
            <button
              type="button"
              className="homeCta"
              onClick={takeMeHome}
              disabled={busy || !routingReady}
            >
              <span className="homeCta__title">Take me home</span>
              <span className="homeCta__sub">From where I am now, to {home.name || home.label}</span>
            </button>
          )}

          <form className="planner__form" onSubmit={runSearch}>
            <PlaceField
              id="from"
              label="Starting from"
              placeholder="Where are you now?"
              value={from}
              onChange={setFrom}
              icon={PIN_ICON}
              shortcuts={[
                { key: "live", label: "Use my location", icon: "◎", onSelect: useMyLocation },
              ]}
            />

            <PlaceField
              id="to"
              label="Going to"
              placeholder="Where are you headed?"
              value={to}
              onChange={setTo}
              icon={FLAG_ICON}
              shortcuts={destinationShortcuts}
            />

            {to && (!home || home.label !== to.label) && (
              <button type="button" className="linkButton" onClick={saveHome}>
                Save this as home
              </button>
            )}

            <ModeChoice value={mode} onChange={setMode} />

            <TimeChoice value={time} onChange={setTime} />

            <ConditionSliders
              enabled={useConditions}
              values={conditions}
              onToggle={setUseConditions}
              onChange={setConditions}
            />

            <button
              type="submit"
              className="btn btn--primary btn--lg"
              disabled={busy || backendDown || !routingReady}
            >
              {busy ? "Checking routes…" : "Find the safest way"}
            </button>
          </form>

          {busy && (
            <div className="skeleton" role="status" aria-live="polite">
              <p className="planner__progress">
                <span className="spinner" aria-hidden="true" />
                {progress.message}
              </p>
              <div className="skeleton__card" />
              <div className="skeleton__card skeleton__card--tall" />
            </div>
          )}

          {notice && (
            <Alert
              tone={notice.tone}
              onDismiss={() => setNotice(null)}
              action={
                notice.action && (
                  <button type="button" className="alert__action" onClick={notice.action.onClick}>
                    {notice.action.label}
                  </button>
                )
              }
            >
              {notice.text}
            </Alert>
          )}

          {error && <Alert title="Couldn't plan that route">{error.message}</Alert>}

          <div ref={resultsRef}>
            {routes.length > 0 && selected && (
              <>
                <TonightStrip entry={selected} departure={resolveDeparture(time)} />
                <RouteChoices
                  routes={routes}
                  selectedId={selected?.routeId}
                  onSelect={setSelectedId}
                />

                {/* Keep this walk: it then shows up on My routes with its
                    current score and any new reports along it. */}
                <div className="saveRoute">
                  {saveState.done ? (
                    <p className="saveRoute__done">
                      Saved as <strong>{saveState.done}</strong>.{" "}
                      <Link to="/routes">See my routes</Link>
                    </p>
                  ) : saveState.open ? (
                    <form className="saveRoute__form" onSubmit={saveSelectedRoute}>
                      <label className="visuallyHidden" htmlFor="save-route-name">
                        Route name
                      </label>
                      <input
                        id="save-route-name"
                        autoFocus
                        placeholder="Name it — e.g. Home from the office"
                        value={saveState.name}
                        onChange={(e) => setSaveState({ ...saveState, name: e.target.value })}
                      />
                      <button type="submit" className="btn btn--primary btn--sm">
                        Save
                      </button>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => setSaveState({ open: false, name: "", done: null })}
                      >
                        Cancel
                      </button>
                    </form>
                  ) : (
                    <button
                      type="button"
                      className="btn btn--ghost"
                      onClick={() => setSaveState({ open: true, name: "", done: null })}
                    >
                      Save this route
                    </button>
                  )}
                </div>

                {selected && <RouteDetail entry={selected} fastest={fastest} />}
              </>
            )}
          </div>
        </div>

        <Provenance status={healthStatus} health={health} />
      </div>

      <div className="planner__map">
        <RouteMap
          routes={routes}
          selectedId={selected?.routeId}
          onSelect={setSelectedId}
          from={from}
          to={to}
          live={live}
          focusFix={focusFix}
          layoutKey={`${routes.length}-${selected?.routeId ?? ""}`}
        />
        <SafetyKit />
      </div>
    </div>
    </AppShell>
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import AppShell from "../components/AppShell.jsx";
import ReportComposer from "../components/ReportComposer.jsx";
import ReportList from "../components/ReportList.jsx";
import { getAuditsAlongRoute, predict } from "../lib/api.js";
import { modeById } from "../lib/config.js";
import { labelTone, pct, relativeTime } from "../lib/format.js";
import { writeSearchParams } from "../lib/searchParams.js";
import { loadSavedRoutes, removeRoute, updateRoute } from "../lib/storage.js";

/**
 * My routes.
 *
 * The walks you actually do, watched for you. Each card re-scores its route
 * for right now and pulls every community report along it, and anything
 * posted since you last looked is flagged — so "someone said this stretch
 * is bad tonight" reaches you without you going looking for it.
 */
export default function Dashboard() {
  const [routes, setRoutes] = useState(() => loadSavedRoutes());
  const [live, setLive] = useState({}); // id -> { score, label, audits, latest, loading, error }
  const [reporting, setReporting] = useState(null); // route id with the composer open

  const refresh = useCallback(async (route, signal) => {
    setLive((s) => ({ ...s, [route.id]: { ...(s[route.id] || {}), loading: true, error: null } }));
    try {
      const segments = route.points.map(([lat, lon]) => ({ point: { lat, lon } }));
      const [scored, reports] = await Promise.all([
        predict({ routeId: route.id, segments, signal }).catch(() => null),
        getAuditsAlongRoute({ points: route.points, signal }),
      ]);
      setLive((s) => ({
        ...s,
        [route.id]: {
          loading: false,
          error: null,
          score: scored ? 100 - pct(scored.overall_risk_score) : route.lastScore,
          label: scored?.label || route.lastLabel,
          worst: scored?.worst_segment?.point || null,
          audits: reports.audits,
          latest: reports.latest,
        },
      }));
      if (scored) {
        updateRoute(route.id, {
          lastScore: 100 - pct(scored.overall_risk_score),
          lastLabel: scored.label,
        });
      }
    } catch (error) {
      if (signal?.aborted) return;
      setLive((s) => ({ ...s, [route.id]: { ...(s[route.id] || {}), loading: false, error } }));
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    routes.forEach((route) => refresh(route, controller.signal));
    return () => controller.abort();
    // Only on mount / when the set of routes changes, not on every live update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routes.map((r) => r.id).join(",")]);

  const totalNew = useMemo(
    () =>
      routes.reduce((sum, route) => {
        const since = new Date(route.lastSeenAt);
        const audits = live[route.id]?.audits || [];
        return sum + audits.filter((a) => new Date(a.timestamp) > since).length;
      }, 0),
    [routes, live],
  );

  function markSeen(route) {
    setRoutes(updateRoute(route.id, { lastSeenAt: new Date().toISOString() }));
  }

  function remove(route) {
    setRoutes(removeRoute(route.id));
  }

  const allReports = useMemo(() => {
    const seen = new Set();
    const merged = [];
    for (const route of routes) {
      for (const audit of live[route.id]?.audits || []) {
        if (seen.has(audit.audit_id)) continue;
        seen.add(audit.audit_id);
        merged.push({ ...audit, routeName: route.name, newSince: route.lastSeenAt });
      }
    }
    return merged.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
  }, [routes, live]);

  const weakest = useMemo(() => {
    const scored = routes
      .map((r) => ({ route: r, score: live[r.id]?.score ?? r.lastScore }))
      .filter((x) => x.score != null);
    return scored.length ? scored.reduce((a, b) => (b.score < a.score ? b : a)) : null;
  }, [routes, live]);

  return (
    <AppShell emergencyInBar>
      <div className="dash">
        <header className="dash__head">
          <div>
            <p className="eyebrow">My routes</p>
            <h1 className="dash__title">
              {routes.length === 0
                ? "Nothing saved yet"
                : totalNew > 0
                  ? `${totalNew} new report${totalNew === 1 ? "" : "s"} since you last looked`
                  : "All quiet on your routes"}
            </h1>
          </div>
          <Link to="/plan" className="btn btn--primary">
            Plan a route
          </Link>
        </header>

        {routes.length > 0 && (
          <dl className="dash__stats">
            <div>
              <dt className="eyebrow">Saved routes</dt>
              <dd className="tnum">{routes.length}</dd>
            </div>
            <div>
              <dt className="eyebrow">Reports along them</dt>
              <dd className="tnum">{allReports.length}</dd>
            </div>
            <div>
              <dt className="eyebrow">Needs attention</dt>
              <dd>
                {weakest && weakest.score < 60 ? (
                  <span className={`tone-${labelTone(live[weakest.route.id]?.label || weakest.route.lastLabel)}`}>
                    {weakest.route.name}
                  </span>
                ) : (
                  "None right now"
                )}
              </dd>
            </div>
          </dl>
        )}

        {routes.length === 0 && (
          <div className="dash__empty">
            <p>
              Plan a route, then choose <strong>Save this route</strong>. It'll be re-checked every
              time you open this page, and any new report along it will be waiting here.
            </p>
            <Link to="/plan" className="btn btn--primary">
              Plan your first route
            </Link>
          </div>
        )}

        {routes.length > 0 && (
          <div className="dash__grid">
            <ul className="routeCards">
              {routes.map((route) => {
                const state = live[route.id] || {};
                const since = new Date(route.lastSeenAt);
                const audits = state.audits || [];
                const fresh = audits.filter((a) => new Date(a.timestamp) > since);
                const negatives = audits.filter((a) => a.rating <= 2).length;
                const tone = labelTone(state.label || route.lastLabel);
                const score = state.score ?? route.lastScore;
                const planHref = `/plan?${writeSearchParams({
                  from: route.from,
                  to: route.to,
                  time: route.timeMode,
                  mode: route.mode,
                })}`;

                return (
                  <li key={route.id} className={`routeCard${fresh.length ? " has-new" : ""}`}>
                    <div className="routeCard__head">
                      <div className="routeCard__titles">
                        <h2 className="routeCard__name">{route.name}</h2>
                        <p className="routeCard__path muted">
                          {route.from.name || route.from.label} → {route.to.name || route.to.label}
                          <span className="routeCard__mode">{modeById(route.mode).label}</span>
                        </p>
                      </div>
                      {score != null && (
                        <div className={`routeCard__score tone-${tone}`}>
                          <span className="routeCard__scoreNum tnum">{score}</span>
                          <span className="routeCard__scoreLabel">{state.label || route.lastLabel}</span>
                        </div>
                      )}
                    </div>

                    <div className="routeCard__stats">
                      <span className="tnum">
                        {state.loading
                          ? "Checking…"
                          : `${audits.length} report${audits.length === 1 ? "" : "s"}`}
                      </span>
                      {negatives > 0 && <span className="routeCard__warn tnum">{negatives} unsafe</span>}
                      {fresh.length > 0 && <span className="routeCard__fresh tnum">{fresh.length} new</span>}
                      {state.latest && <span className="muted tnum">latest {relativeTime(state.latest)}</span>}
                    </div>

                    {state.error && <p className="routeCard__error">Couldn't check this route right now.</p>}

                    <div className="routeCard__actions">
                      <Link to={planHref} className="btn btn--primary btn--sm">
                        Check now
                      </Link>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => setReporting(reporting === route.id ? null : route.id)}
                        aria-expanded={reporting === route.id}
                      >
                        {reporting === route.id ? "Cancel" : "Report"}
                      </button>
                      {fresh.length > 0 && (
                        <button type="button" className="btn btn--ghost btn--sm" onClick={() => markSeen(route)}>
                          Mark seen
                        </button>
                      )}
                      <button
                        type="button"
                        className="routeCard__remove"
                        onClick={() => remove(route)}
                        aria-label={`Remove ${route.name}`}
                      >
                        Remove
                      </button>
                    </div>

                    {reporting === route.id && (
                      <div className="routeCard__report">
                        <p className="muted">Pinned to the riskiest stretch of this route.</p>
                        <ReportComposer
                          point={state.worst || { lat: route.points[0][0], lon: route.points[0][1] }}
                          onSubmitted={() => {
                            setReporting(null);
                            refresh(route);
                          }}
                        />
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>

            <aside className="dash__feed">
              <h2 className="dash__feedTitle">Latest reports</h2>
              <p className="muted dash__feedSub">Across all your routes, newest first.</p>
              <ReportList
                audits={allReports}
                newSince={null}
                limit={8}
                withRoute
                emptyText="No reports along your routes yet."
              />
            </aside>
          </div>
        )}
      </div>
    </AppShell>
  );
}

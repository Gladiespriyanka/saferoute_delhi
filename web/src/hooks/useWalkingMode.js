import { useCallback, useEffect, useRef, useState } from "react";

import { compareRoutes, predict } from "../lib/api.js";
import { watchLocation } from "../lib/geolocation.js";
import { buildSegments, fetchRoutes } from "../lib/routing.js";
import { haversineMeters, nearestPointOnRoute } from "../lib/walking.js";

/** How far off the planned line, in metres, counts as "not on this route anymore".
 *  Wider than GPS noise (typically 5-20m) so a phone in a pocket doesn't cry wolf. */
const OFF_ROUTE_M = 35;

/** Off-route has to persist this long before we act on it — a single bad
 *  fix from a tall building shouldn't trigger a reroute. */
const OFF_ROUTE_CONFIRM_MS = 15000;

/** Rescore the ground under your feet at most this often either by time or
 *  by distance moved, whichever comes first — keeps the backend call rate
 *  sane on a multi-kilometre walk without ever going more than ~30s stale. */
const RESCORE_MIN_MS = 20000;
const RESCORE_MIN_M = 40;

/** A jump this big versus what the planned route was scored for is worth a
 *  heads-up even if you're still on the line — conditions change. */
const RISK_JUMP_ALERT = 0.15;

let nextAlertId = 1;

/**
 * Turns a planned route into a live walk: watches the device's position,
 * rescoring the ground you're actually on, flags when you've drifted off
 * the planned line for long enough to mean it, and computes a safer
 * alternative from wherever you are.
 *
 * `entry` is the currently selected `{ route, prediction }` from
 * useRouteSearch. Swapping it (e.g. after accepting a reroute) updates the
 * line this hook measures drift against without restarting tracking.
 */
export function useWalkingMode({ entry, to, mode = "walk" }) {
  const [active, setActive] = useState(false);
  const [position, setPosition] = useState(null);
  const [walkedPath, setWalkedPath] = useState([]);
  const [onRouteState, setOnRouteState] = useState(null); // { offRouteM, progressM, totalM, progressFraction }
  const [currentScore, setCurrentScore] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [reroute, setReroute] = useState(null);
  const [rerouting, setRerouting] = useState(false);

  const routeRef = useRef(entry?.route || null);
  const baselineRef = useRef(entry?.prediction || null);
  useEffect(() => {
    routeRef.current = entry?.route || null;
    baselineRef.current = entry?.prediction || null;
  }, [entry]);

  const offRouteSinceRef = useRef(null);
  const lastScoredRef = useRef({ at: 0, lat: null, lon: null });
  const rerouteRef = useRef(null);
  const reroutingRef = useRef(false);
  useEffect(() => {
    rerouteRef.current = reroute;
  }, [reroute]);
  useEffect(() => {
    reroutingRef.current = rerouting;
  }, [rerouting]);

  const pushAlert = useCallback((tone, text) => {
    const alert = { id: nextAlertId++, tone, text, at: Date.now() };
    setAlerts((prev) => [...prev.slice(-4), alert]);
  }, []);

  const dismissAlert = useCallback((id) => {
    setAlerts((prev) => prev.filter((a) => a.id !== id));
  }, []);

  /** Score exactly where you're standing, and flag it if it's worse than
   *  the planned route promised at the nearest point. */
  const scoreCurrentPoint = useCallback(
    async (lat, lon) => {
      try {
        const result = await predict({ segments: [{ point: { lat, lon } }] });
        setCurrentScore(result);

        const segments = baselineRef.current?.segment_scores;
        const baseline = segments?.length
          ? segments.reduce((best, s) => {
              const d = haversineMeters({ lat, lon }, { lat: s.point.lat, lon: s.point.lon });
              return d < best.d ? { d, s } : best;
            }, { d: Infinity, s: null }).s
          : null;

        if (result.label === "Unsafe") {
          pushAlert("error", "This stretch is scoring Unsafe right now — stay aware of your surroundings.");
        } else if (baseline && result.overall_risk_score - baseline.risk_score >= RISK_JUMP_ALERT) {
          pushAlert("warn", "It's reading riskier here than the route was scored for.");
        }
      } catch {
        // A missed rescore shouldn't interrupt a walk; the next fix tries again.
      }
    },
    [pushAlert],
  );

  /** Find, score, and offer the best walk from wherever you are now to the destination. */
  const computeReroute = useCallback(
    async (fromPos) => {
      if (!to || reroutingRef.current) return;
      setRerouting(true);
      try {
        const found = await fetchRoutes(fromPos, to, { mode });
        if (!found.length) return;

        const ids = found.map((_, i) => `reroute_${i + 1}`);
        const segmentsByRoute = found.map((r) => buildSegments(r.coordinates));

        const scored = await Promise.all(
          found.map(async (route, i) => ({
            routeId: ids[i],
            route,
            prediction: await predict({ routeId: ids[i], segments: segmentsByRoute[i] }),
          })),
        );

        let best = scored.reduce((a, b) => (a.prediction.overall_risk_score <= b.prediction.overall_risk_score ? a : b));
        if (scored.length > 1) {
          try {
            const comparison = await compareRoutes({
              routes: Object.fromEntries(ids.map((id, i) => [id, segmentsByRoute[i]])),
            });
            best = scored.find((s) => s.routeId === comparison.recommended_route) || best;
          } catch {
            // fall back to the locally-lowest score computed above
          }
        }

        setReroute(best);
        pushAlert("info", "Found a safer way from here — see the suggestion below.");
      } catch {
        // No usable alternative right now; try again on the next drift.
      } finally {
        setRerouting(false);
      }
    },
    [to, mode, pushAlert],
  );

  useEffect(() => {
    if (!active) return undefined;

    const stop = watchLocation((fix) => {
      setPosition(fix);

      setWalkedPath((prev) => {
        const last = prev[prev.length - 1];
        if (last && haversineMeters({ lat: last[0], lon: last[1] }, fix) < 5) return prev;
        return [...prev, [fix.lat, fix.lon]];
      });

      const route = routeRef.current;
      if (route?.coordinates?.length) {
        const proj = nearestPointOnRoute(fix, route.coordinates);
        setOnRouteState({
          offRouteM: proj.distanceM,
          progressM: proj.progressM,
          totalM: proj.totalM,
          progressFraction: proj.progressFraction,
        });

        if (proj.distanceM > OFF_ROUTE_M) {
          if (!offRouteSinceRef.current) {
            offRouteSinceRef.current = Date.now();
          } else if (
            Date.now() - offRouteSinceRef.current > OFF_ROUTE_CONFIRM_MS &&
            !rerouteRef.current &&
            !reroutingRef.current
          ) {
            pushAlert("warn", "You've drifted off the planned route — checking for a safer way from here.");
            computeReroute({ lat: fix.lat, lon: fix.lon });
          }
        } else {
          offRouteSinceRef.current = null;
        }
      }

      const last = lastScoredRef.current;
      const movedM = last.lat != null ? haversineMeters(fix, { lat: last.lat, lon: last.lon }) : Infinity;
      if (Date.now() - last.at > RESCORE_MIN_MS || movedM > RESCORE_MIN_M) {
        lastScoredRef.current = { at: Date.now(), lat: fix.lat, lon: fix.lon };
        scoreCurrentPoint(fix.lat, fix.lon);
      }
    });

    return stop;
  }, [active, scoreCurrentPoint, computeReroute, pushAlert]);

  const start = useCallback(() => {
    setWalkedPath([]);
    setAlerts([]);
    setReroute(null);
    setCurrentScore(null);
    setOnRouteState(null);
    offRouteSinceRef.current = null;
    lastScoredRef.current = { at: 0, lat: null, lon: null };
    setActive(true);
  }, []);

  const stop = useCallback(() => {
    setActive(false);
  }, []);

  /** Adopt the suggested reroute as the route being walked; returns it so
   *  the caller can update whatever it shows on the map / results panel. */
  const acceptReroute = useCallback(() => {
    const accepted = rerouteRef.current;
    if (!accepted) return null;
    routeRef.current = accepted.route;
    baselineRef.current = accepted.prediction;
    offRouteSinceRef.current = null;
    setReroute(null);
    pushAlert("info", "Switched to the safer route from here.");
    return accepted;
  }, [pushAlert]);

  const declineReroute = useCallback(() => setReroute(null), []);

  return {
    active,
    start,
    stop,
    position,
    walkedPath,
    onRouteState,
    currentScore,
    alerts,
    dismissAlert,
    reroute,
    rerouting,
    acceptReroute,
    declineReroute,
  };
}
import { useCallback, useRef, useState } from "react";

import { compareRoutes, predict } from "../lib/api.js";
import { buildSegments, fetchRoutes } from "../lib/routing.js";

const IDLE = { phase: "idle", message: "" };

/** The router sometimes returns the same road twice as "alternatives". */
function dedupeRoutes(routes) {
  const seen = new Set();
  return routes.filter((route) => {
    const key = `${Math.round(route.distance / 50)}|${Math.round(route.duration / 30)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/**
 * The whole search: find walking routes, score every one, and let the
 * backend pick the recommendation.
 *
 * Each run gets its own AbortController and a monotonic id. Without that, a
 * slow first search can land after a quick second one and overwrite the
 * results the user is actually looking at — the kind of bug that shows up
 * only on a bad connection, which is exactly when this app matters.
 */
export function useRouteSearch() {
  const [progress, setProgress] = useState(IDLE);
  const [routes, setRoutes] = useState([]);
  const [error, setError] = useState(null);
  const runIdRef = useRef(0);
  const abortRef = useRef(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setProgress(IDLE);
  }, []);

  const search = useCallback(async ({ from, to, timestamp, observed, mode = "walk" }) => {
    const runId = ++runIdRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;
    const current = () => runId === runIdRef.current && !signal.aborted;

    setError(null);
    setRoutes([]);

    try {
      setProgress({ phase: "routing", message: "Finding routes…" });
      const found = dedupeRoutes(await fetchRoutes(from, to, { signal, mode }));
      if (!current()) return null;

      setProgress({
        phase: "scoring",
        message:
          found.length === 1
            ? "Scoring the route against lighting, isolation and live conditions…"
            : `Scoring ${found.length} routes against lighting, isolation and live conditions…`,
      });

      const ids = found.map((_, index) => `route_${index + 1}`);
      const segmentsByRoute = found.map((route) => buildSegments(route.coordinates, observed));

      const scored = await Promise.all(
        found.map(async (route, index) => ({
          routeId: ids[index],
          route,
          prediction: await predict({
            routeId: ids[index],
            segments: segmentsByRoute[index],
            timestamp,
            signal,
          }),
          recommended: false,
          mode,
        })),
      );
      if (!current()) return null;

      if (found.length > 1) {
        try {
          const comparison = await compareRoutes({
            routes: Object.fromEntries(ids.map((id, i) => [id, segmentsByRoute[i]])),
            timestamp,
            signal,
          });
          for (const entry of scored) {
            entry.recommended = entry.routeId === comparison.recommended_route;
          }
        } catch (comparisonError) {
          // Falling back to the lowest local score is a slightly worse
          // answer than the backend's, but a far better outcome than
          // failing the whole search.
          if (!signal.aborted) console.warn("Comparison unavailable:", comparisonError.message);
        }
      }
      if (!current()) return null;

      scored.sort(
        (a, b) => a.prediction.overall_risk_score - b.prediction.overall_risk_score,
      );
      if (!scored.some((entry) => entry.recommended) && scored.length) {
        scored[0].recommended = true;
      }

      setRoutes(scored);
      setProgress(IDLE);
      return scored;
    } catch (searchError) {
      if (signal.aborted || !current()) return null;
      setError(searchError);
      setProgress(IDLE);
      return null;
    }
  }, []);

  return { search, cancel, routes, progress, error, busy: progress.phase !== "idle" };
}

/** Pedestrian routing via OpenRouteService. */

import { ORS_KEY, ROUTE_OPTIONS, modeById } from "./config.js";

const routeUrl = (mode) =>
  `https://api.openrouteservice.org/v2/directions/${modeById(mode).profile}/geojson`;

export class RoutingError extends Error {
  constructor(message) {
    super(message);
    this.name = "RoutingError";
  }
}

export const hasRoutingKey = () => Boolean(ORS_KEY);

function toRoute(feature) {
  const summary = feature.properties?.summary || {};
  return {
    distance: summary.distance || 0,
    duration: summary.duration || 0,
    // ORS returns [lon, lat]; Leaflet wants [lat, lon].
    coordinates: feature.geometry.coordinates.map(([lon, lat]) => [lat, lon]),
  };
}

async function post(body, signal, mode) {
  return fetch(routeUrl(mode), {
    method: "POST",
    headers: { Authorization: ORS_KEY, "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
}

/**
 * Asks for alternatives, not just the single fastest path — comparing
 * safety across different ways to the same place is the whole point. ORS
 * refuses alternatives on some distances and route shapes, so a
 * single-route retry is the fallback rather than a failure.
 */
export async function fetchRoutes(from, to, { signal, mode = "walk" } = {}) {
  if (!ORS_KEY) {
    throw new RoutingError(
      "No OpenRouteService key configured. Add VITE_ORS_KEY to web/.env.local, " +
        "or open this page once with ?ors_key=YOUR_KEY.",
    );
  }

  const coordinates = [
    [from.lon, from.lat],
    [to.lon, to.lat],
  ];

  let response = await post(
    {
      coordinates,
      alternative_routes: {
        target_count: ROUTE_OPTIONS.maxAlternatives,
        weight_factor: 1.4,
        share_factor: 0.6,
      },
    },
    signal,
    mode,
  );

  if (!response.ok) {
    const detail = await response.text();
    if (response.status === 400 && /alternative/i.test(detail)) {
      response = await post({ coordinates }, signal, mode);
      if (!response.ok) {
        throw new RoutingError(`Routing failed (${response.status}): ${await response.text()}`);
      }
    } else if (response.status === 403 || response.status === 401) {
      throw new RoutingError("OpenRouteService rejected the key. Check VITE_ORS_KEY.");
    } else {
      throw new RoutingError(`Routing failed (${response.status}): ${detail}`);
    }
  }

  const data = await response.json();
  if (!data.features?.length) {
    throw new RoutingError(`No ${modeById(mode).verb === "drive" ? "road" : modeById(mode).verb + "ing"} route found between those two points.`);
  }
  return data.features.map(toRoute);
}

/**
 * Thin a route's geometry to a small, evenly spaced set of points. Each
 * becomes one scored segment, bounding the work a very long route creates.
 */
export function sampleRoutePoints(coordinates, maxPoints = ROUTE_OPTIONS.maxSegments) {
  if (coordinates.length <= maxPoints) return coordinates;
  const step = (coordinates.length - 1) / (maxPoints - 1);
  return Array.from({ length: maxPoints }, (_, i) => coordinates[Math.round(i * step)]);
}

const OVERRIDABLE = [
  "lighting_score",
  "crowd_density",
  "cctv_coverage",
  "streetlight_density",
  "footpath_quality",
];

export function buildSegments(coordinates, observed) {
  return sampleRoutePoints(coordinates).map(([lat, lon]) => {
    const segment = { point: { lat, lon } };
    if (observed) {
      for (const key of OVERRIDABLE) {
        if (observed[key] !== undefined) segment[key] = observed[key];
      }
    }
    return segment;
  });
}

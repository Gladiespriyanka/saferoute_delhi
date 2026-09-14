/** SafeHerWay backend client. */

import { API_BASE_URL, API_KEY, REQUEST_TIMEOUT_MS } from "./config.js";

export class ApiError extends Error {
  constructor(message, { status } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * fetch() has no timeout of its own, so an unreachable backend hangs until
 * the browser's own TCP timeout — about a minute of spinner and no
 * explanation.
 */
async function request(path, { method = "GET", body, signal, timeoutMs = REQUEST_TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException("timeout", "TimeoutError")), timeoutMs);
  const onAbort = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", onAbort, { once: true });

  try {
    const response = await fetch(API_BASE_URL + path, {
      method,
      headers: {
        "x-api-key": API_KEY,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new ApiError(await describe(response), { status: response.status });
    }
    return await response.json();
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (signal?.aborted) throw error;
    if (error.name === "TimeoutError" || error.name === "AbortError") {
      throw new ApiError(
        `The backend at ${API_BASE_URL} didn't answer within ${Math.round(timeoutMs / 1000)}s.`,
      );
    }
    throw new ApiError(`Can't reach the backend at ${API_BASE_URL}.`);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

/** Pull the most useful message out of an error body, including FastAPI's validation shape. */
async function describe(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail
        .map((item) => `${(item.loc || []).slice(1).join(".")}: ${item.msg}`)
        .join("; ");
    }
    return JSON.stringify(body);
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}

/** Liveness plus provenance: which data and model the backend is actually running. */
export function getHealth({ signal } = {}) {
  return request("/health", { signal, timeoutMs: 8000 });
}

export function predict({ routeId, segments, timestamp, signal }) {
  return request("/predict", {
    method: "POST",
    signal,
    body: {
      route_id: routeId,
      timestamp: timestamp || new Date().toISOString(),
      use_live_context: true,
      segments,
    },
  });
}

/**
 * The backend owns the recommendation. Re-deriving it in the browser by
 * sorting /predict results would duplicate logic that already lives — and
 * is tested — in SafeRouteService.compare_routes().
 */
export function compareRoutes({ routes, timestamp, signal }) {
  return request("/compare-routes", {
    method: "POST",
    signal,
    body: { routes, timestamp: timestamp || new Date().toISOString(), use_live_context: true },
  });
}

export function getNearbyAudits({ lat, lon, radiusKm = 1.5, signal }) {
  const params = new URLSearchParams({
    lat: String(lat),
    lon: String(lon),
    radius_km: String(radiusKm),
  });
  return request(`/audits/nearby?${params}`, { signal });
}

export function submitFeedback({ lat, lon, rating, comment, reasons = [], signal }) {
  return request("/feedback", {
    method: "POST",
    signal,
    body: {
      point: { lat, lon },
      rating,
      comment: comment || null,
      reasons,
      timestamp: new Date().toISOString(),
    },
  });
}

/** Every report within reach of any point on a route, newest first. */
export function getAuditsAlongRoute({ points, radiusKm = 0.75, limit = 50, signal }) {
  return request("/audits/along-route", {
    method: "POST",
    signal,
    body: {
      points: points.map(([lat, lon]) => ({ lat, lon })),
      radius_km: radiusKm,
      limit,
    },
  });
}

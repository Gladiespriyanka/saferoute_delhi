/* ==========================================
   SafeRoute Delhi - API Functions
========================================== */

/* -----------------------------------------
   fetch() with a real timeout instead of
   hanging forever when a host is unreachable
------------------------------------------*/

async function fetchWithTimeout(url, options = {}, timeoutMs = API.TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error(
        `Request to ${url} timed out after ${timeoutMs / 1000}s. ` +
          `Is the backend running and reachable at that address?`,
      );
    }
    throw new Error(`Could not reach ${url}: ${err.message}`);
  } finally {
    clearTimeout(timer);
  }
}

/* -----------------------------------------
   Convert Place Name -> Latitude & Longitude
------------------------------------------*/

async function geocode(place) {
  // If the caller already handed us "lat,lon" (e.g. from "Use My Current
  // Location"), skip the geocoder entirely - it's both unnecessary and a
  // less reliable round trip through Nominatim's free-text search.
  const coordMatch = place
    .trim()
    .match(/^(-?\d+(\.\d+)?)\s*,\s*(-?\d+(\.\d+)?)$/);
  if (coordMatch) {
    return { lat: parseFloat(coordMatch[1]), lon: parseFloat(coordMatch[3]) };
  }

  // viewbox + bounded=1 restricts results to Delhi/NCR instead of a plain
  // global search — see the DELHI_BOUNDS comment in config.js for why
  // this matters (a common place name can otherwise silently resolve to
  // a same-named place in a totally different state).
  const viewbox = [
    DELHI_BOUNDS.LON_MIN,
    DELHI_BOUNDS.LAT_MAX,
    DELHI_BOUNDS.LON_MAX,
    DELHI_BOUNDS.LAT_MIN,
  ].join(",");

  const url =
    `${NOMINATIM.SEARCH}?format=jsonv2&q=${encodeURIComponent(place)}` +
    `&limit=1&addressdetails=1&countrycodes=in&viewbox=${viewbox}&bounded=1`;

  const response = await fetchWithTimeout(url);

  if (!response.ok) {
    throw new Error(
      `Geocoding service error (${response.status}) for "${place}"`,
    );
  }

  const data = await response.json();

  if (data.length === 0) {
    throw new Error(
      `Could not find "${place}" in Delhi/NCR. Try a more specific name ` +
        `(e.g. add the neighbourhood or landmark), or use "Use my current location".`,
    );
  }

  return {
    lat: parseFloat(data[0].lat),
    lon: parseFloat(data[0].lon),
  };
}

/* -----------------------------------------
   Get Route(s) from OSRM

   Requests alternative routes (not just the single fastest one) so the
   app can actually compare safety across different paths between the
   same source/destination, instead of only ever scoring one option.
------------------------------------------*/

async function getRoutes(source, destination) {
  if (!ORS.API_KEY) {
    throw new Error(
      "OpenRouteService API key is not configured. Copy " +
        "frontend/config.local.example.js to frontend/config.local.js and add your key, " +
        "or reload this page with ?ors_key=YOUR_KEY appended to the URL.",
    );
  }

  const response = await fetch(ORS.ROUTE_URL, {
    method: "POST",

    headers: {
      Authorization: ORS.API_KEY,
      "Content-Type": "application/json",
    },

    body: JSON.stringify({
      coordinates: [
        [source.lon, source.lat],
        [destination.lon, destination.lat],
      ],

      // Ask for alternative routes
      alternative_routes: {
        target_count: 3,
        weight_factor: 1.4,
        share_factor: 0.6,
      },
    }),
  });

  if (!response.ok) {
    const errorText = await response.text();

    // If ORS rejects alternative routes because the
    // route is too long, try a normal route instead.
    if (response.status === 400 && errorText.includes("alternative Routes")) {
      console.warn(
        "Alternative routes unavailable for this distance. Trying normal route...",
      );

      const fallbackResponse = await fetch(ORS.ROUTE_URL, {
        method: "POST",

        headers: {
          Authorization: ORS.API_KEY,
          "Content-Type": "application/json",
        },

        body: JSON.stringify({
          coordinates: [
            [source.lon, source.lat],
            [destination.lon, destination.lat],
          ],
        }),
      });

      if (!fallbackResponse.ok) {
        const fallbackError = await fallbackResponse.text();

        throw new Error(
          `Pedestrian routing failed (${fallbackResponse.status}): ${fallbackError}`,
        );
      }

      const fallbackData = await fallbackResponse.json();

      if (!fallbackData.features || fallbackData.features.length === 0) {
        throw new Error("No pedestrian route found");
      }

      return fallbackData.features.map((feature) => {
        const summary = feature.properties.summary;

        return {
          distance: summary.distance,
          duration: summary.duration,

          coordinates: feature.geometry.coordinates.map(([lon, lat]) => [
            lat,
            lon,
          ]),
        };
      });
    }

    throw new Error(
      `Pedestrian routing failed (${response.status}): ${errorText}`,
    );
  }

  const data = await response.json();

  console.log("Pedestrian routes:", data);

  if (!data.features || data.features.length === 0) {
    throw new Error("No pedestrian routes found");
  }

  return data.features.map((feature) => {
    const summary = feature.properties.summary;

    return {
      distance: summary.distance,
      duration: summary.duration,

      coordinates: feature.geometry.coordinates.map(([lon, lat]) => [lat, lon]),
    };
  });
}

/* -----------------------------------------
   Thin a route's coordinate list down to a
   small, evenly-spaced set of points to send
   as scored "segments" to the backend.
------------------------------------------*/

function sampleRoutePoints(
  coordinates,
  maxPoints = ROUTE_OPTIONS.MAX_SEGMENTS_PER_ROUTE,
) {
  if (coordinates.length <= maxPoints) return coordinates;

  const step = (coordinates.length - 1) / (maxPoints - 1);
  const picked = [];
  for (let i = 0; i < maxPoints; i++) {
    picked.push(coordinates[Math.round(i * step)]);
  }
  return picked;
}

/* -----------------------------------------
   Build the /predict (or /compare-routes)
   segment payload for one route, applying
   any user-reported live conditions as
   overrides on every sampled point.
------------------------------------------*/

function buildSegments(coordinates, userContext) {
  const points = sampleRoutePoints(coordinates);

  return points.map(([lat, lon]) => {
    const segment = { point: { lat, lon } };
    if (userContext) {
      if (userContext.lighting_score !== undefined)
        segment.lighting_score = userContext.lighting_score;
      if (userContext.crowd_density !== undefined)
        segment.crowd_density = userContext.crowd_density;
      if (userContext.cctv_coverage !== undefined)
        segment.cctv_coverage = userContext.cctv_coverage;
      if (userContext.streetlight_density !== undefined)
        segment.streetlight_density = userContext.streetlight_density;
      if (userContext.footpath_quality !== undefined)
        segment.footpath_quality = userContext.footpath_quality;
    }
    return segment;
  });
}

/* -----------------------------------------
   Predict Safety for a single route (full
   detail: segment scores, live-context
   adjustments, feature contributions,
   grouped reasons, confidence).

   Takes already-built `segments` (rather than raw coordinates) so the
   exact same segment list can be reused for the /compare-routes call
   below without re-sampling/re-applying user context twice.
------------------------------------------*/

async function predictSafety(routeId, segments, timestamp) {
  const payload = {
    route_id: routeId,
    timestamp: timestamp || new Date().toISOString(),
    use_live_context: true,
    segments,
  };

  const response = await fetchWithTimeout(API.BASE_URL + "/predict", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": API.API_KEY,
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const error = await response.json();
      detail = error.detail
        ? JSON.stringify(error.detail)
        : JSON.stringify(error);
    } catch (_) {
      // response body wasn't JSON; fall back to statusText
    }
    throw new Error(
      `Prediction failed for ${routeId} (${response.status}): ${detail}`,
    );
  }

  return await response.json();
}

/* -----------------------------------------
   Ask the backend which route it recommends
   across >=2 alternatives in one call. This
   is what /compare-routes is actually for —
   previously the frontend never called it
   and instead re-derived "recommended" by
   sorting the per-route /predict results by
   overall_risk_score itself, duplicating
   logic that already lives (and is tested)
   server-side in SafeRouteService.compare_routes().

   Note: /compare-routes only returns summary
   scores, not segment-level detail, so this
   does NOT replace the per-route /predict
   calls above (the route-tab UI needs their
   segment scores/reasons/contributions) — it
   runs alongside them purely to source the
   recommendation from a single source of
   truth instead of two.
------------------------------------------*/

async function compareRoutes(routesMap, timestamp) {
  const payload = {
    routes: routesMap,
    timestamp: timestamp || new Date().toISOString(),
    use_live_context: true,
  };

  const response = await fetchWithTimeout(API.BASE_URL + "/compare-routes", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": API.API_KEY,
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const error = await response.json();
      detail = error.detail
        ? JSON.stringify(error.detail)
        : JSON.stringify(error);
    } catch (_) {
      // response body wasn't JSON; fall back to statusText
    }
    throw new Error(`Route comparison failed (${response.status}): ${detail}`);
  }

  return await response.json();
}

/* -----------------------------------------
   Score every alternative route in parallel
   and return them sorted safest-first, each
   tagged with its own map polyline data.
------------------------------------------*/
async function analyzeRoutes(routes, timestamp, userContext) {
  const routeIds = routes.map((_, i) => `route_${i + 1}`);

  // Build the exact segment format expected by the backend
  const segmentsByRoute = routes.map((route) =>
    buildSegments(route.coordinates, userContext)
  );

  // -----------------------------------------
  // A. Get detailed prediction for each route
  // -----------------------------------------
  const scored = await Promise.all(
    routes.map((route, i) => {
      const routeId = routeIds[i];

      return predictSafety(
        routeId,
        segmentsByRoute[i],
        timestamp
      ).then((prediction) => ({
        routeId,
        route,
        prediction,
        recommended: false
      }));
    })
  );

  // -----------------------------------------
  // B. Ask backend to compare all routes
  // -----------------------------------------
  let comparison = null;

  try {
    const routesMap = {};

    routeIds.forEach((routeId, i) => {
      routesMap[routeId] = segmentsByRoute[i];
    });

    comparison = await compareRoutes(
      routesMap,
      timestamp
    );

    console.log("COMPARE ROUTES RESULT:", comparison);

    // Backend recommendation
    const recommendedRoute =
      comparison.recommended_route;

    scored.forEach((item) => {
      item.recommended =
        item.routeId === recommendedRoute;
    });

  } catch (error) {
    console.warn(
      "Compare-routes unavailable:",
      error.message
    );

    // Fallback — don't break the application
    // Lowest risk score remains recommended.
    scored.sort(
      (a, b) =>
        a.prediction.overall_risk_score -
        b.prediction.overall_risk_score
    );

    scored.forEach((item, index) => {
      item.recommended = index === 0;
    });
  }

  // Keep safest/recommended route first
  scored.sort(
    (a, b) =>
      a.prediction.overall_risk_score -
      b.prediction.overall_risk_score
  );

  return scored;
}

/* -----------------------------------------
   Health Check
------------------------------------------*/

async function checkBackend() {
  try {
    const response = await fetchWithTimeout(API.BASE_URL + "/health", {}, 6000);

    if (!response.ok) throw new Error(`status ${response.status}`);

    const data = await response.json();
    console.log("Backend Connected:", API.BASE_URL, data);
    return true;
  } catch (e) {
    console.log(`Backend unreachable at ${API.BASE_URL}:`, e.message);
    return false;
  }
}

/* -----------------------------------------
   Nearby Audits
------------------------------------------*/

async function getNearbyAudits(lat, lon, radiusKm = 1) {
  const url = `${API.BASE_URL}/audits/nearby?lat=${lat}&lon=${lon}&radius_km=${radiusKm}`;

  const response = await fetchWithTimeout(url, {
    headers: { "x-api-key": API.API_KEY },
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch nearby audits (${response.status})`);
  }

  return await response.json();
}

/* -----------------------------------------
   Feedback (crowdsourced safety audit) -
   this is what actually "stores" a user's
   real-world report on the backend and
   nudges that area's risk going forward.
------------------------------------------*/

async function submitFeedback(lat, lon, rating, comment) {
  const body = {
    point: { lat, lon },
    rating: rating,
    comment: comment,
    timestamp: new Date().toISOString(),
  };

  const response = await fetchWithTimeout(API.BASE_URL + "/feedback", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": API.API_KEY,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new Error(`Failed to submit feedback (${response.status})`);
  }

  return await response.json();
}
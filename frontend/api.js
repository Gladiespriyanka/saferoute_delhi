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

  const url = `${NOMINATIM.SEARCH}?format=jsonv2&q=${encodeURIComponent(place)}&limit=1&addressdetails=1`;

  const response = await fetchWithTimeout(url);

  if (!response.ok) {
    throw new Error(
      `Geocoding service error (${response.status}) for "${place}"`,
    );
  }

  const data = await response.json();

  if (data.length === 0) {
    throw new Error("Location not found : " + place);
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

      alternative_routes: {
        target_count: 3,
        weight_factor: 1.4,
        share_factor: 0.6,
      },
    }),
  });

  if (!response.ok) {
    const errorText = await response.text();

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
------------------------------------------*/

async function predictSafety(routeId, coordinates, timestamp, userContext) {
  const payload = {
    route_id: routeId,
    timestamp: timestamp || new Date().toISOString(),
    use_live_context: true,
    segments: buildSegments(coordinates, userContext),
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
   Score every alternative route in parallel
   and return them sorted safest-first, each
   tagged with its own map polyline data.
------------------------------------------*/

async function analyzeRoutes(routes, timestamp, userContext) {
  const scored = await Promise.all(
    routes.map((route, i) =>
      predictSafety(
        `route_${i + 1}`,
        route.coordinates,
        timestamp,
        userContext,
      ).then((prediction) => ({
        routeId: `route_${i + 1}`,
        route,
        prediction,
      })),
    ),
  );

  scored.sort(
    (a, b) => a.prediction.overall_risk_score - b.prediction.overall_risk_score,
  );
  scored.forEach((s, i) => (s.recommended = i === 0));
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

/* ==========================================
   SafeRoute Delhi - Map Functions
========================================== */

let map;

// Markers
let sourceMarker = null;
let destinationMarker = null;
let liveMarker = null;

// Route Polylines - one per alternative route, keyed by routeId, plus a
// list so we can clear them all between searches.
let routeLines = {};

const ROUTE_COLORS = ["#16a34a", "#2563eb", "#f59e0b", "#a855f7", "#0891b2"];
const RISK_COLORS = { Safe: "#16a34a", Moderate: "#f59e0b", Unsafe: "#dc2626" };

/* ------------------------------
   Initialize Map
------------------------------ */

function initMap() {
  map = L.map("map").setView(
    [DEFAULT_LOCATION.lat, DEFAULT_LOCATION.lon],
    DEFAULT_LOCATION.zoom,
  );

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap Contributors",
  }).addTo(map);
}

window.onload = initMap;

/* ------------------------------
   Source Marker
------------------------------ */

function setSourceMarker(lat, lon) {
  if (sourceMarker) map.removeLayer(sourceMarker);
  sourceMarker = L.marker([lat, lon])
    .addTo(map)
    .bindPopup("📍 Source")
    .openPopup();
}

/* ------------------------------
   Destination Marker
------------------------------ */

function setDestinationMarker(lat, lon) {
  if (destinationMarker) map.removeLayer(destinationMarker);
  destinationMarker = L.marker([lat, lon])
    .addTo(map)
    .bindPopup("🏁 Destination");
}

/* ------------------------------
   Live User Location
------------------------------ */

function showLiveLocation(lat, lon) {
  if (liveMarker) map.removeLayer(liveMarker);
  liveMarker = L.circleMarker([lat, lon], {
    radius: 8,
    color: "#2563eb",
    fillColor: "#3b82f6",
    fillOpacity: 1,
  }).addTo(map);
}

/* ------------------------------
   Clear all route polylines
------------------------------ */

function clearRoutes() {
  Object.values(routeLines).forEach((line) => map.removeLayer(line));
  routeLines = {};
}

/* ------------------------------
   Draw every scored route at once,
   colored by its own safety label,
   with the currently-selected one
   drawn thicker/opaque and the rest
   dimmed underneath.
------------------------------ */

function drawScoredRoutes(scoredRoutes, selectedRouteId) {
  clearRoutes();

  scoredRoutes.forEach((s) => {
    const isSelected = s.routeId === selectedRouteId;
    const routeIndex = scoredRoutes.findIndex(
      (route) => route.routeId === s.routeId,
    );

    const color = ROUTE_COLORS[routeIndex % ROUTE_COLORS.length];

    const line = L.polyline(s.route.coordinates, {
      color,
      weight: isSelected ? 7 : 4,
      opacity: isSelected ? 0.95 : 0.7,
    }).addTo(map);

    line.bindTooltip(
      `${s.recommended ? "⭐ Recommended — " : ""}${s.routeId}: ${s.prediction.label} (${Math.round(s.prediction.overall_risk_score * 100)}% risk)`,
    );
    line.on("click", () => window.selectRoute && window.selectRoute(s.routeId));

    routeLines[s.routeId] = line;
  });

  const selectedLine = routeLines[selectedRouteId];
  if (selectedLine) {
    map.fitBounds(selectedLine.getBounds(), { padding: [40, 40] });
  } else {
    const allBounds = L.featureGroup(Object.values(routeLines)).getBounds();
    if (allBounds.isValid()) map.fitBounds(allBounds, { padding: [40, 40] });
  }
}

/* ------------------------------
   Re-emphasize one route without
   re-fetching anything (used when
   the user clicks a route tab).
------------------------------ */

function highlightRoute(routeId) {
  Object.entries(routeLines).forEach(([id, line]) => {
    const isSelected = id === routeId;
    line.setStyle({
      weight: isSelected ? 7 : 4,
      opacity: isSelected ? 0.95 : 0.35,
    });
    if (isSelected) map.fitBounds(line.getBounds(), { padding: [40, 40] });
  });
}

/* ------------------------------
   Get Current Location
------------------------------ */

function useCurrentLocation() {
  if (!navigator.geolocation) {
    alert("Geolocation not supported");
    return;
  }

  navigator.geolocation.getCurrentPosition(
    function (position) {
      const lat = position.coords.latitude;
      const lon = position.coords.longitude;

      document.getElementById("origin").value = lat + "," + lon;

      showLiveLocation(lat, lon);
      setSourceMarker(lat, lon);
      map.setView([lat, lon], 15);
    },
    function () {
      alert("Unable to get your location.");
    },
    { enableHighAccuracy: true },
  );
}

/* ------------------------------
   GPS Tracking
------------------------------ */

function startTracking() {
  if (!navigator.geolocation) return;

  navigator.geolocation.watchPosition(
    function (position) {
      showLiveLocation(position.coords.latitude, position.coords.longitude);
    },
    function (error) {
      console.log(error);
    },
    { enableHighAccuracy: true, maximumAge: 1000 },
  );
}

/* ------------------------------
   Button Events
------------------------------ */

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("locationBtn");
  if (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      useCurrentLocation();
    });
  }
});

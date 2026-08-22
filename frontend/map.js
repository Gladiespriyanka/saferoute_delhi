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

const ROUTE_COLORS = ["#d6336c", "#2563eb", "#f59e0b", "#8a1c46", "#0891b2"];
const RISK_COLORS = { Safe: "#0e7c7b", Moderate: "#b54708", Unsafe: "#b42318" };

/* ------------------------------
   Custom pin icons

   Source and destination previously used Leaflet's identical default
   blue teardrop for both, so the two were impossible to tell apart at a
   glance — and the live-location dot (also blue) landed right on top of
   whichever one was closest, turning that corner of the map into a
   cluster of same-colored blobs. These are shaped + colored differently
   on purpose: a filled pin for source (brand pink), an outlined flag pin
   for destination (dark), and a small pulsing dot (blue, defined in
   style.css) for live GPS position.
------------------------------ */

function pinIcon(fillColor) {
  const svg = `
    <svg width="30" height="40" viewBox="0 0 30 40" xmlns="http://www.w3.org/2000/svg">
      <path d="M15 1C7.3 1 1 7.1 1 14.6 1 24.9 15 39 15 39s14-14.1 14-24.4C29 7.1 22.7 1 15 1Z"
            fill="${fillColor}" stroke="#ffffff" stroke-width="2"/>
      <circle cx="15" cy="14.5" r="5.2" fill="#ffffff"/>
    </svg>
  `;
  return L.divIcon({
    className: "srPin",
    html: svg,
    iconSize: [30, 40],
    iconAnchor: [15, 39],
    popupAnchor: [0, -36],
  });
}

function flagIcon(fillColor) {
  const svg = `
    <svg width="30" height="40" viewBox="0 0 30 40" xmlns="http://www.w3.org/2000/svg">
      <path d="M15 1C7.3 1 1 7.1 1 14.6 1 24.9 15 39 15 39s14-14.1 14-24.4C29 7.1 22.7 1 15 1Z"
            fill="${fillColor}" stroke="#ffffff" stroke-width="2"/>
      <path d="M11.5 9v11" stroke="#ffffff" stroke-width="1.6" stroke-linecap="round"/>
      <path d="M11.5 9.3h6.3l-1.7 2.6 1.7 2.6h-6.3Z" fill="#ffffff"/>
    </svg>
  `;
  return L.divIcon({
    className: "srPin",
    html: svg,
    iconSize: [30, 40],
    iconAnchor: [15, 39],
    popupAnchor: [0, -36],
  });
}

function liveDotIcon() {
  return L.divIcon({
    className: "srLiveDotWrap",
    html: `<div class="srLiveDot"></div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
}

const SOURCE_ICON = pinIcon("#d6336c"); // brand pink
const DESTINATION_ICON = flagIcon("#1b0e14"); // near-black, reads as "end point"

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
  sourceMarker = L.marker([lat, lon], { icon: SOURCE_ICON, zIndexOffset: 500 })
    .addTo(map)
    .bindPopup("📍 Source")
    .openPopup();
}

/* ------------------------------
   Destination Marker
------------------------------ */

function setDestinationMarker(lat, lon) {
  if (destinationMarker) map.removeLayer(destinationMarker);
  destinationMarker = L.marker([lat, lon], {
    icon: DESTINATION_ICON,
    zIndexOffset: 500,
  })
    .addTo(map)
    .bindPopup("🏁 Destination");
}

/* ------------------------------
   Live User Location

   Kept as a small pulsing dot (not a full pin) specifically so it reads
   as "you are here" rather than competing with the source/destination
   pins when they land close together.
------------------------------ */

function showLiveLocation(lat, lon) {
  if (liveMarker) map.removeLayer(liveMarker);
  liveMarker = L.marker([lat, lon], {
    icon: liveDotIcon(),
    zIndexOffset: 1000,
    interactive: false,
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
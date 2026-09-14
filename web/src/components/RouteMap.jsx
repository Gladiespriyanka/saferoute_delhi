import L from "leaflet";
import { useEffect } from "react";
import { Circle, MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap } from "react-leaflet";


import { DEFAULT_CENTRE } from "../lib/config.js";
import { labelTone, pct } from "../lib/format.js";

import "leaflet/dist/leaflet.css";

/*
 * Start and destination differ in both shape and colour on purpose. Leaflet's
 * default blue teardrop for both, plus a blue "you are here" dot, turns any
 * corner of the map where they land close together into a cluster of
 * indistinguishable blobs.
 */
function pin(fill, inner) {
  return L.divIcon({
    className: "mapPin",
    html: `<svg width="30" height="40" viewBox="0 0 30 40" xmlns="http://www.w3.org/2000/svg">
      <path d="M15 1C7.3 1 1 7.1 1 14.6 1 24.9 15 39 15 39s14-14.1 14-24.4C29 7.1 22.7 1 15 1Z"
            fill="${fill}" stroke="#ffffff" stroke-width="2"/>${inner}</svg>`,
    iconSize: [30, 40],
    iconAnchor: [15, 39],
    popupAnchor: [0, -36],
  });
}

const START_ICON = pin("#e9a23b", '<circle cx="15" cy="14.5" r="5.2" fill="#ffffff"/>');
const END_ICON = pin(
  "#2a1a3f",
  '<path d="M11.5 9v11" stroke="#ffffff" stroke-width="1.6" stroke-linecap="round"/>' +
    '<path d="M11.5 9.3h6.3l-1.7 2.6 1.7 2.6h-6.3Z" fill="#ffffff"/>',
);
const LIVE_ICON = L.divIcon({
  className: "mapLive",
  html: '<span class="mapLive__dot"></span>',
  iconSize: [16, 16],
  iconAnchor: [8, 8],
});

const ROUTE_COLORS = {
  safe: "#1f8f72",
  moderate: "#cf8a2a",
  unsafe: "#cf3f3f",
  neutral: "#2a1a3f",
};

/* One keyless tile source; the dark theme re-tones the tile pane in CSS. */
const TILES = {
  url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
};

/** Keep the viewport on whatever is currently worth looking at. */
function Viewport({ routes, selectedId, from, to }) {
  const map = useMap();

  useEffect(() => {
    const selected = routes.find((entry) => entry.routeId === selectedId);
    if (selected?.route.coordinates.length) {
      map.fitBounds(L.latLngBounds(selected.route.coordinates), {
        paddingTopLeft: [32, 32],
        paddingBottomRight: [32, 32],
      });
      return;
    }
    const pins = [from, to].filter(Boolean).map((place) => [place.lat, place.lon]);
    if (pins.length === 2) map.fitBounds(L.latLngBounds(pins), { padding: [64, 64] });
    else if (pins.length === 1) map.setView(pins[0], 15);
  }, [map, routes, selectedId, from, to]);

  return null;
}

/** Centre on a location the user just asked for, zoomed to show how sure it is. */
function FlyToFix({ fix }) {
  const map = useMap();
  useEffect(() => {
    if (!fix) return;
    const radius = Number.isFinite(fix.accuracy) ? Math.max(fix.accuracy, 60) : 300;
    const bounds = L.latLng(fix.lat, fix.lon).toBounds(radius * 2.4);
    map.flyToBounds(bounds, { duration: 0.8, padding: [24, 24] });
  }, [map, fix]);
  return null;
}

/** Leaflet measures the container on creation; the panel resizing invalidates that. */
function ResizeOnLayout({ dependency }) {
  const map = useMap();
  useEffect(() => {
    const timer = setTimeout(() => map.invalidateSize(), 260);
    return () => clearTimeout(timer);
  }, [map, dependency]);
  return null;
}

export default function RouteMap({ routes, selectedId, onSelect, from, to, live, focusFix, layoutKey }) {
  return (
    <MapContainer
      className="routeMap"
      center={[DEFAULT_CENTRE.lat, DEFAULT_CENTRE.lon]}
      zoom={DEFAULT_CENTRE.zoom}
      zoomControl={false}
      scrollWheelZoom
    >
      <TileLayer url={TILES.url} maxZoom={19} attribution={TILES.attribution} />

      {routes.map((entry) => {
        const isSelected = entry.routeId === selectedId;
        const tone = labelTone(entry.prediction.label);
        return (
          <Polyline
            key={entry.routeId}
            positions={entry.route.coordinates}
            pathOptions={{
              color: ROUTE_COLORS[tone] || ROUTE_COLORS.neutral,
              weight: isSelected ? 7 : 4,
              opacity: isSelected ? 0.95 : 0.4,
            }}
            eventHandlers={{ click: () => onSelect(entry.routeId) }}
          >
            <Tooltip sticky>
              {entry.recommended ? "Recommended — " : ""}
              {entry.prediction.label} · {pct(entry.prediction.overall_risk_score)}% risk
            </Tooltip>
          </Polyline>
        );
      })}

      {from && <Marker position={[from.lat, from.lon]} icon={START_ICON} />}
      {to && <Marker position={[to.lat, to.lon]} icon={END_ICON} />}
      {live && (
        <>
          {/* How far out the fix could be — honest about a Wi-Fi estimate. */}
          {Number.isFinite(live.accuracy) && live.accuracy > 15 && (
            <Circle
              center={[live.lat, live.lon]}
              radius={live.accuracy}
              pathOptions={{ color: "#2f7de1", weight: 1.5, opacity: 0.6, fillOpacity: live.accuracy > 1500 ? 0.06 : 0.1 }}
              interactive={false}
            />
          )}
          <Marker position={[live.lat, live.lon]} icon={LIVE_ICON} interactive={false} />
        </>
      )}

      <Viewport routes={routes} selectedId={selectedId} from={from} to={to} />
      <FlyToFix fix={focusFix} />
      <ResizeOnLayout dependency={layoutKey} />
    </MapContainer>
  );
}

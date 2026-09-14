/**
 * Geocoding.
 *
 * Photon handles typeahead — Nominatim's public instance explicitly
 * disallows autocomplete and is reserved for one-shot lookups, which is
 * what we use it for when someone types a full address and hits search.
 */

import { DEFAULT_CENTRE } from "./config.js";

const PHOTON = "https://photon.komoot.io/api/";
const NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search";
const NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse";

const COORD_PATTERN = /^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/;

export function parseCoordinates(text) {
  const match = String(text || "").match(COORD_PATTERN);
  if (!match) return null;
  const lat = Number(match[1]);
  const lon = Number(match[2]);
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { lat, lon, label: `${lat.toFixed(5)}, ${lon.toFixed(5)}` };
}

function formatPhoton(feature) {
  const props = feature.properties || {};
  const coords = feature.geometry?.coordinates;
  if (!coords || coords.length < 2) return null;

  const name =
    props.name ||
    [props.housenumber, props.street].filter(Boolean).join(" ") ||
    props.locality ||
    props.city ||
    "Unknown place";

  const seen = new Set([name.toLowerCase()]);
  const context = [];
  for (const part of [
    props.street && props.name ? props.street : null,
    props.district,
    props.locality,
    props.city,
    props.county,
    props.state,
  ]) {
    if (!part || seen.has(part.toLowerCase())) continue;
    seen.add(part.toLowerCase());
    context.push(part);
    if (context.length === 2) break;
  }

  return {
    name,
    context: context.join(", "),
    label: context.length ? `${name}, ${context.join(", ")}` : name,
    lat: coords[1],
    lon: coords[0],
  };
}

export async function searchPlaces(query, { signal } = {}) {
  const text = query.trim();
  if (text.length < 2) return [];

  const coords = parseCoordinates(text);
  if (coords) return [{ ...coords, name: coords.label, context: "Exact coordinates" }];

  const params = new URLSearchParams({
    q: text,
    limit: "6",
    lang: "en",
    lat: String(DEFAULT_CENTRE.lat),
    lon: String(DEFAULT_CENTRE.lon),
    location_bias_scale: "0.5",
  });

  const response = await fetch(`${PHOTON}?${params}`, { signal });
  if (!response.ok) throw new Error(`Place search failed (${response.status})`);

  // Deduplicate on the label alone. Including coordinates in the key let
  // two points a few hundred metres apart that render identically — the
  // village and the district both called "Hauz Khas, South Delhi" — sit in
  // the list as indistinguishable rows.
  const seen = new Set();
  const places = [];
  for (const feature of (await response.json()).features || []) {
    const place = formatPhoton(feature);
    if (!place) continue;
    const key = place.label.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    places.push(place);
  }
  return places;
}

/** One-shot lookup for free text the user never picked a suggestion for. */
export async function geocode(text, { signal } = {}) {
  const coords = parseCoordinates(text);
  if (coords) return coords;

  const params = new URLSearchParams({
    format: "jsonv2",
    limit: "1",
    addressdetails: "1",
    q: text,
  });
  const response = await fetch(`${NOMINATIM_SEARCH}?${params}`, { signal });
  if (!response.ok) throw new Error(`Geocoding failed (${response.status})`);

  const data = await response.json();
  if (!data.length) throw new Error(`Couldn't find "${text}". Try a more specific name.`);

  return {
    lat: Number(data[0].lat),
    lon: Number(data[0].lon),
    label: data[0].display_name?.split(",").slice(0, 3).join(", ") || text,
  };
}

/**
 * Neighbourhood name for a point, used to label route segments.
 *
 * Nominatim's policy allows one request per second, so calls are pushed
 * through a single queue and cached. Callers never await this on the
 * render path — segments appear immediately and names fill in behind them.
 */
const areaCache = new Map();
let queue = Promise.resolve();

export function reverseGeocode(lat, lon) {
  const key = `${lat.toFixed(4)},${lon.toFixed(4)}`;
  if (areaCache.has(key)) return Promise.resolve(areaCache.get(key));

  const result = queue.then(async () => {
    if (areaCache.has(key)) return areaCache.get(key);
    try {
      const params = new URLSearchParams({
        format: "json",
        zoom: "18",
        addressdetails: "1",
        lat: String(lat),
        lon: String(lon),
      });
      const response = await fetch(`${NOMINATIM_REVERSE}?${params}`, {
        headers: { "Accept-Language": "en" },
      });
      if (!response.ok) return null;
      const address = (await response.json()).address || {};
      const name =
        address.neighbourhood ||
        address.suburb ||
        address.quarter ||
        address.city_district ||
        address.town ||
        address.city ||
        null;
      areaCache.set(key, name);
      return name;
    } catch {
      return null;
    }
  });

  // Space out network hits without making cached reads wait.
  queue = result.then(() => new Promise((resolve) => setTimeout(resolve, 1100)));
  return result;
}

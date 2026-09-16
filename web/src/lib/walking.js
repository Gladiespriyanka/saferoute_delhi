/**
 * Geometry for turning a raw GPS fix into "how far off the planned route
 * are you, and how far along it have you got" — the two numbers everything
 * in walking mode is built on.
 *
 * Distances are computed with a flat-earth projection centred on the live
 * fix itself. That keeps the projection numerically simple (no shared
 * reference frame to get wrong) and is accurate to well under a metre of
 * error at the city-block scale this app cares about.
 */

export function haversineMeters(a, b) {
  const R = 6371000;
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

/**
 * Where a point sits relative to a route polyline: the closest distance to
 * it, and how far along the route (in metres, and as a 0-1 fraction) that
 * closest point falls.
 *
 * `coordinates` is an array of [lat, lon] pairs, the same shape
 * `routing.js` hands back from ORS.
 */
export function nearestPointOnRoute(point, coordinates) {
  if (!coordinates?.length || coordinates.length < 2) {
    return { distanceM: Infinity, index: 0, fraction: 0, progressM: 0, totalM: 0, progressFraction: 0 };
  }

  const metersPerDegLat = 111320;
  const metersPerDegLon = 111320 * Math.cos((point.lat * Math.PI) / 180);
  const toXY = ([lat, lon]) => ({
    x: (lon - point.lon) * metersPerDegLon,
    y: (lat - point.lat) * metersPerDegLat,
  });

  let best = { distanceM: Infinity, index: 0, fraction: 0 };
  const segLens = [];

  for (let i = 0; i < coordinates.length - 1; i++) {
    const a = coordinates[i];
    const b = coordinates[i + 1];
    segLens.push(haversineMeters({ lat: a[0], lon: a[1] }, { lat: b[0], lon: b[1] }));

    const A = toXY(a);
    const B = toXY(b);
    const abx = B.x - A.x;
    const aby = B.y - A.y;
    const lenSq = abx * abx + aby * aby;
    let t = lenSq > 0 ? (-A.x * abx - A.y * aby) / lenSq : 0;
    t = Math.max(0, Math.min(1, t));
    const d = Math.hypot(A.x + t * abx, A.y + t * aby);

    if (d < best.distanceM) best = { distanceM: d, index: i, fraction: t };
  }

  const totalM = segLens.reduce((sum, len) => sum + len, 0);
  let progressM = 0;
  for (let i = 0; i < best.index; i++) progressM += segLens[i];
  progressM += (segLens[best.index] || 0) * best.fraction;

  return {
    distanceM: best.distanceM,
    index: best.index,
    fraction: best.fraction,
    progressM,
    totalM,
    progressFraction: totalM > 0 ? Math.max(0, Math.min(1, progressM / totalM)) : 0,
  };
}
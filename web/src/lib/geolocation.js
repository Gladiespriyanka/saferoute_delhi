/**
 * A location fix worth trusting.
 *
 * The first answer from the Geolocation API is usually a coarse one — a
 * cell tower or Wi-Fi estimate, hundreds of metres out — and the GPS fix
 * that follows a few seconds later is the accurate one. `getCurrentPosition`
 * hands back that first answer and stops. So this watches instead, keeps
 * the best fix it sees, and resolves early only once the accuracy is good,
 * or otherwise after a short window with the best it has.
 *
 * It also explains the most common reason location "doesn't work" at all:
 * browsers refuse geolocation on plain http:// unless the page is on
 * localhost, which bites the moment the app is opened over a LAN address.
 */

export class LocationError extends Error {
  constructor(message, code) {
    super(message);
    this.name = "LocationError";
    this.code = code;
  }
}

export function explainLocationSupport() {
  if (!("geolocation" in navigator)) return "Location isn't available in this browser.";
  if (!window.isSecureContext) {
    return (
      "Browsers only share location on secure pages. This one was opened over http:// on " +
      "a network address — open it as localhost, or serve it over https."
    );
  }
  return null;
}

export function getAccurateLocation({ goodEnoughM = 40, maxWaitMs = 12000 } = {}) {
  return new Promise((resolve, reject) => {
    const unsupported = explainLocationSupport();
    if (unsupported) {
      reject(new LocationError(unsupported, "unsupported"));
      return;
    }

    let best = null;
    let watchId = null;
    let timer = null;
    let settled = false;

    const consider = (position) => {
      const fix = {
        lat: position.coords.latitude,
        lon: position.coords.longitude,
        accuracy: position.coords.accuracy ?? Infinity,
        at: position.timestamp,
      };
      if (!best || fix.accuracy < best.accuracy) best = fix;
      return fix;
    };

    const finish = () => {
      if (settled) return;
      settled = true;
      if (watchId !== null) navigator.geolocation.clearWatch(watchId);
      clearTimeout(timer);
      if (best) resolve(best);
      else reject(new LocationError("Couldn't get a location fix. Try again, ideally outdoors.", "timeout"));
    };

    // 1. Take whatever the device already knows, immediately. On a laptop
    //    this is usually an IP-based guess several kilometres wide — but it
    //    is a starting point, and the caller is told how wide it is.
    navigator.geolocation.getCurrentPosition(
      (position) => consider(position),
      () => {},
      { enableHighAccuracy: false, maximumAge: 120000, timeout: 4000 },
    );

    // 2. Then watch for something better, and stop as soon as it's good.
    watchId = navigator.geolocation.watchPosition(
      (position) => {
        consider(position);
        if (best.accuracy <= goodEnoughM) finish();
      },
      (error) => {
        if (best || error.code === 3) return; // keep waiting on the timer; a fix may still land
        clearTimeout(timer);
        if (watchId !== null) navigator.geolocation.clearWatch(watchId);
        settled = true;
        const messages = {
          1: "Location permission was denied. Allow it in the browser's site settings, then try again.",
          2: "Your device couldn't determine its position. Try again, ideally outdoors.",
        };
        reject(new LocationError(messages[error.code] || "Couldn't get your location.", error.code));
      },
      { enableHighAccuracy: true, maximumAge: 0, timeout: maxWaitMs },
    );

    timer = setTimeout(finish, maxWaitMs);
  });
}

/** Continuous tracking for the map dot; cheap to keep running. */
export function watchLocation(onFix) {
  if (explainLocationSupport()) return () => {};
  const id = navigator.geolocation.watchPosition(
    (p) => onFix({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy }),
    () => {},
    { enableHighAccuracy: true, maximumAge: 5000 },
  );
  return () => navigator.geolocation.clearWatch(id);
}

export function describeAccuracy(metres) {
  if (!Number.isFinite(metres)) return "";
  if (metres <= 25) return "accurate to a few metres";
  if (metres <= 150) return `accurate to about ${Math.round(metres / 10) * 10} m`;
  if (metres <= 1500) return `only accurate to about ${Math.round(metres / 100) * 100} m — a Wi-Fi estimate`;
  return `only accurate to about ${(metres / 1000).toFixed(1)} km — an estimate from your internet connection, not GPS`;
}

/** Past this, a fix is a guess at the neighbourhood, not a position. */
export const USABLE_ACCURACY_M = 1500;

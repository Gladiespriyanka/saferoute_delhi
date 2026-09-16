import { useCallback, useEffect, useRef, useState } from "react";

import {
  checkInEscort,
  endEscort,
  reportMissedCheckIn,
  sosEscort,
  startEscort,
  updateEscortPosition,
} from "../lib/api.js";
import { haversineMeters } from "../lib/walking.js";

/** How long an unanswered "still okay?" prompt waits before it counts as missed. */
const CHECKIN_GRACE_MS = 90000;

/** Same throttle shape as the rescoring in useWalkingMode — enough to keep
 *  a companion's view current without hammering the backend. */
const POSITION_MIN_MS = 20000;
const POSITION_MIN_M = 40;

/** A dropped position update isn't worth chasing — another fix is coming
 *  in ~20s anyway. A dropped SOS or check-in is a different story: nothing
 *  else will resend it, so those get real retries with backoff before we
 *  give up and just surface the failure. */
const CRITICAL_RETRY_DELAYS_MS = [2000, 5000, 12000, 25000];

async function withRetry(fn, delays = CRITICAL_RETRY_DELAYS_MS) {
  let lastErr;
  for (let attempt = 0; attempt <= delays.length; attempt += 1) {
    try {
      return await fn();
    } catch (e) {
      lastErr = e;
      if (attempt < delays.length) await new Promise((resolve) => setTimeout(resolve, delays[attempt]));
    }
  }
  throw lastErr;
}

/**
 * A Smart Escort session: a trusted contact follows a live, unguessable
 * link while you walk. This hook owns the session's lifecycle on the
 * walker's side — the companion's side is `routes/EscortView.jsx`, which
 * only ever reads.
 */
export function useEscort() {
  const [session, setSession] = useState(null); // { tripId, ownerToken, checkInIntervalSeconds, createdAt }
  const [status, setStatus] = useState(null);
  const [checkInDue, setCheckInDue] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // Reflects the *last write*, not a live socket — with an 8s poll on the
  // companion side, this is the walker's only early warning that updates
  // aren't landing, so it stays visible in the UI rather than silently
  // logged. See CompanionShare's "Reconnecting…" pill.
  const [connected, setConnected] = useState(true);
  const [lastSyncedAt, setLastSyncedAt] = useState(null);

  const checkInTimerRef = useRef(null);
  const graceTimerRef = useRef(null);
  const lastPositionRef = useRef({ at: 0, lat: null, lon: null });

  const clearTimers = useCallback(() => {
    clearTimeout(checkInTimerRef.current);
    clearTimeout(graceTimerRef.current);
  }, []);

  useEffect(() => clearTimers, [clearTimers]);

  const scheduleCheckIn = useCallback((intervalSeconds) => {
    clearTimeout(checkInTimerRef.current);
    checkInTimerRef.current = setTimeout(() => setCheckInDue(true), intervalSeconds * 1000);
  }, []);

  // An unanswered prompt escalates on its own — a companion shouldn't have
  // to wonder whether "no reply" meant "fine, just didn't see it". But a
  // miss isn't the end of the session: the walker might just have had their
  // phone locked or a dead signal, so the cycle keeps going afterwards —
  // otherwise the very first missed prompt would permanently strand the
  // session in "alert" with no way for the walker to ever check in again.
  useEffect(() => {
    if (!checkInDue || !session) return undefined;
    graceTimerRef.current = setTimeout(async () => {
      setCheckInDue(false);
      try {
        const result = await reportMissedCheckIn({ tripId: session.tripId, ownerToken: session.ownerToken });
        setStatus(result);
      } catch {
        // Best-effort; the companion's own view will also go stale, which is itself a signal.
      } finally {
        scheduleCheckIn(session.checkInIntervalSeconds);
      }
    }, CHECKIN_GRACE_MS);
    return () => clearTimeout(graceTimerRef.current);
  }, [checkInDue, session, scheduleCheckIn]);

  const start = useCallback(
    async ({ destination, routePreview, checkInIntervalSeconds = 600 }) => {
      setBusy(true);
      setError(null);
      try {
        const result = await startEscort({ destination, routePreview, checkInIntervalSeconds });
        const next = {
          tripId: result.trip_id,
          ownerToken: result.owner_token,
          checkInIntervalSeconds: result.check_in_interval_seconds,
          createdAt: result.created_at,
        };
        setSession(next);
        setStatus(null);
        setCheckInDue(false);
        setConnected(true);
        setLastSyncedAt(Date.now());
        lastPositionRef.current = { at: 0, lat: null, lon: null };
        scheduleCheckIn(next.checkInIntervalSeconds);
        return next;
      } catch (e) {
        setError(e);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [scheduleCheckIn],
  );

  const stop = useCallback(async () => {
    clearTimers();
    if (session) {
      try {
        await endEscort({ tripId: session.tripId, ownerToken: session.ownerToken });
      } catch {
        // The session will simply expire on its own if this doesn't land.
      }
    }
    setSession(null);
    setStatus(null);
    setCheckInDue(false);
    setConnected(true);
    setLastSyncedAt(null);
  }, [session, clearTimers]);

  const reportPosition = useCallback(
    (lat, lon, { riskLabel, riskScore, progressFraction, force = false } = {}) => {
      if (!session) return;
      const last = lastPositionRef.current;
      const movedM = last.lat != null ? haversineMeters({ lat, lon }, { lat: last.lat, lon: last.lon }) : Infinity;
      if (!force && Date.now() - last.at < POSITION_MIN_MS && movedM < POSITION_MIN_M) return;
      lastPositionRef.current = { at: Date.now(), lat, lon };
      // Not retried on failure — another fix (and another call here) is
      // coming within POSITION_MIN_MS anyway, so a retry queue would just
      // duplicate work the normal cadence already does. `connected` still
      // flips false so the walker isn't left thinking this one landed.
      updateEscortPosition({ tripId: session.tripId, ownerToken: session.ownerToken, lat, lon, riskLabel, riskScore, progressFraction })
        .then((result) => {
          setStatus(result);
          setConnected(true);
          setLastSyncedAt(Date.now());
        })
        .catch(() => setConnected(false));
    },
    [session],
  );

  const checkIn = useCallback(
    async (ok) => {
      if (!session) return;
      clearTimeout(graceTimerRef.current);
      setCheckInDue(false);
      try {
        // A "still okay?" answer only ever gets sent once by the person
        // tapping it — worth a few retries on a flaky signal rather than
        // silently dropping the one honest "I'm fine" they gave.
        const result = await withRetry(() =>
          checkInEscort({ tripId: session.tripId, ownerToken: session.ownerToken, ok }),
        );
        setStatus(result);
        setConnected(true);
        setLastSyncedAt(Date.now());
        if (ok) scheduleCheckIn(session.checkInIntervalSeconds);
      } catch (e) {
        setConnected(false);
        setError(e);
      }
    },
    [session, scheduleCheckIn],
  );

  const sos = useCallback(
    async (lat, lon) => {
      if (!session) return null;
      clearTimeout(checkInTimerRef.current);
      clearTimeout(graceTimerRef.current);
      setCheckInDue(false);
      try {
        // The single most important call this hook makes — retried harder
        // than anything else before giving up on it.
        const result = await withRetry(() =>
          sosEscort({ tripId: session.tripId, ownerToken: session.ownerToken, lat, lon }),
        );
        setStatus(result);
        setConnected(true);
        setLastSyncedAt(Date.now());
        return result;
      } catch (e) {
        setConnected(false);
        setError(e);
        return null;
      }
    },
    [session],
  );

  const shareUrl = session ? `${window.location.origin}/escort/${session.tripId}` : null;

  return {
    active: Boolean(session),
    session,
    status,
    checkInDue,
    busy,
    error,
    connected,
    lastSyncedAt,
    shareUrl,
    start,
    stop,
    reportPosition,
    checkIn,
    sos,
  };
}
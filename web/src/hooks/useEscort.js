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
  // to wonder whether "no reply" meant "fine, just didn't see it".
  useEffect(() => {
    if (!checkInDue || !session) return undefined;
    graceTimerRef.current = setTimeout(async () => {
      setCheckInDue(false);
      try {
        const result = await reportMissedCheckIn({ tripId: session.tripId, ownerToken: session.ownerToken });
        setStatus(result);
      } catch {
        // Best-effort; the companion's own view will also go stale, which is itself a signal.
      }
    }, CHECKIN_GRACE_MS);
    return () => clearTimeout(graceTimerRef.current);
  }, [checkInDue, session]);

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
  }, [session, clearTimers]);

  const reportPosition = useCallback(
    (lat, lon, { riskLabel, riskScore, progressFraction, force = false } = {}) => {
      if (!session) return;
      const last = lastPositionRef.current;
      const movedM = last.lat != null ? haversineMeters({ lat, lon }, { lat: last.lat, lon: last.lon }) : Infinity;
      if (!force && Date.now() - last.at < POSITION_MIN_MS && movedM < POSITION_MIN_M) return;
      lastPositionRef.current = { at: Date.now(), lat, lon };
      updateEscortPosition({ tripId: session.tripId, ownerToken: session.ownerToken, lat, lon, riskLabel, riskScore, progressFraction })
        .then(setStatus)
        .catch(() => {});
    },
    [session],
  );

  const checkIn = useCallback(
    async (ok) => {
      if (!session) return;
      clearTimeout(graceTimerRef.current);
      setCheckInDue(false);
      try {
        const result = await checkInEscort({ tripId: session.tripId, ownerToken: session.ownerToken, ok });
        setStatus(result);
        if (ok) scheduleCheckIn(session.checkInIntervalSeconds);
      } catch (e) {
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
        const result = await sosEscort({ tripId: session.tripId, ownerToken: session.ownerToken, lat, lon });
        setStatus(result);
        return result;
      } catch (e) {
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
    shareUrl,
    start,
    stop,
    reportPosition,
    checkIn,
    sos,
  };
}

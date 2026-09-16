import { useEffect, useRef } from "react";

/**
 * A companion watching `EscortView` only notices "Needs attention" if
 * they're actually looking at the tab — the page itself has no way to
 * reach them otherwise. This makes the transition into `alert` hard to
 * miss: a system notification (works even if the tab is backgrounded, as
 * long as the browser process is still running), an audible beep, and a
 * flashing document title so a glance at the tab bar is enough.
 *
 * None of this can reach someone with the tab or browser fully closed —
 * that would need a server-driven push subscription, which this project
 * doesn't have yet. This is the best a plain polled tab can do.
 */
export function useAlertNotice(status, walkerLabel) {
  const prevStatusRef = useRef(null);
  const flashTimerRef = useRef(null);
  const originalTitleRef = useRef(typeof document !== "undefined" ? document.title : "");
  // One AudioContext, reused for every alert. Creating (and closing) a
  // fresh one per alert is what broke repeat sounds: browsers suspend a
  // brand-new context until a user gesture unlocks it, so only the first
  // one -- riding along on whatever gesture was already in flight -- ever
  // actually played. A single persisted context stays unlocked once
  // resumed, so every later alert can just resume + play on it.
  const audioCtxRef = useRef(null);

  // Ask once, up front, while we still have a click/interaction-adjacent
  // context — asking for the first time *during* an actual alert is too
  // late for a permission prompt to be useful.
  useEffect(() => {
  const unlockAudio = async () => {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;

      if (!audioCtxRef.current) {
        audioCtxRef.current = new Ctx();
      }

      if (audioCtxRef.current.state === "suspended") {
        await audioCtxRef.current.resume();
      }

      console.log("🔊 Companion audio unlocked");
    } catch (err) {
      console.error("Audio unlock failed:", err);
    }
  };

  window.addEventListener("click", unlockAudio, { once: true });

  return () => {
    window.removeEventListener("click", unlockAudio);
  };
}, []);
  useEffect(() => {
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  }, []);

  // Torn down once, when the page itself unmounts -- not per alert.
  useEffect(() => {
    return () => {
      audioCtxRef.current?.close().catch(() => {});
    };
  }, []);

  useEffect(() => {
    const prev = prevStatusRef.current;
    prevStatusRef.current = status;
    if (status !== "alert" || prev === "alert") return undefined;

    // System notification, if the browser will show one without another prompt.
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      try {
        new Notification("Needs attention", {
          body: `${walkerLabel || "The walk you're following"} may need help — missed a check-in, reported not okay, or sent an SOS.`,
          tag: "escort-alert",
          renotify: true,
        });
      } catch {
        // Notification construction can throw on some mobile browsers; the
        // beep and title flash below still carry the alert either way.
      }
    }

    // Short audible cue via WebAudio — no asset to fetch, works offline.
    // Reuses the one persisted context (see audioCtxRef above) and
    // explicitly resumes it, since a context can end up suspended again
    // between alerts on some browsers even after its first successful use.
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) {
        if (!audioCtxRef.current || audioCtxRef.current.state === "closed") {
          audioCtxRef.current = new Ctx();
        }
        const ctx = audioCtxRef.current;
        const playTone = () => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = "sine";
          osc.frequency.value = 880;
          gain.gain.setValueAtTime(0.0001, ctx.currentTime);
          gain.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.01);
          gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.4);
          osc.connect(gain).connect(ctx.destination);
          osc.start(ctx.currentTime);
          osc.stop(ctx.currentTime + 0.42);
        };
        if (ctx.state === "suspended") {
          ctx.resume().then(playTone).catch(() => {});
        } else {
          playTone();
        }
      }
    } catch {
      // Audio can fail (autoplay policy, no output device) — the
      // notification and title flash still get the point across.
    }

    // Flash the tab title so it's visible without switching to the tab.
    let on = true;
    clearInterval(flashTimerRef.current);
    flashTimerRef.current = setInterval(() => {
      document.title = on ? "⚠ Needs attention" : originalTitleRef.current;
      on = !on;
    }, 1000);

    return () => {
      clearInterval(flashTimerRef.current);
      document.title = originalTitleRef.current;
    };
  }, [status, walkerLabel]);

  // Stop flashing and restore the title once the alert clears or the page unmounts.
  useEffect(() => {
    if (status !== "alert") {
      clearInterval(flashTimerRef.current);
      document.title = originalTitleRef.current;
    }
    return () => {
      clearInterval(flashTimerRef.current);
      document.title = originalTitleRef.current;
    };
  }, [status]);
}
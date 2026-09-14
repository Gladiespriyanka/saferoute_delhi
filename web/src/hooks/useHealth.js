import { useEffect, useState } from "react";

import { getHealth } from "../lib/api.js";

/**
 * What the backend is actually running on. Surfaced in the footer so a
 * synthetic-data or untrained-model deployment can never be mistaken for a
 * real one — the number on screen is a safety judgement, and the user is
 * entitled to know what produced it.
 */
export function useHealth() {
  const [state, setState] = useState({ status: "loading", health: null, error: null });

  useEffect(() => {
    const controller = new AbortController();
    getHealth({ signal: controller.signal })
      .then((health) => setState({ status: "ready", health, error: null }))
      .catch((error) => {
        if (controller.signal.aborted) return;
        setState({ status: "error", health: null, error });
      });
    return () => controller.abort();
  }, []);

  return state;
}

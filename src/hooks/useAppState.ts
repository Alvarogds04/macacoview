import { useState, useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";

import type { GetState } from "../types";

const POLL_INTERVAL_MS = 1000;

export interface UseAppStateReturn {
  snapshot: GetState["snapshot"];
  history: GetState["history"];
  error: boolean;
  loading: boolean;
}

/**
 * Polls the Tauri `get_state` command once per second.
 *
 * - Never allows overlapping calls (skips a tick when a call is in-flight).
 * - On failure keeps the last-good snapshot and sets `error: true`.
 * - First-call failure does NOT crash the app — UI shows a loading state.
 * - Cancels cleanly on unmount (no state update after unmount, no leaked timer).
 */
export function useAppState(): UseAppStateReturn {
  const [state, setState] = useState<{
    snapshot: GetState["snapshot"];
    history: GetState["history"];
    error: boolean;
    loading: boolean;
  }>({
    snapshot: null,
    history: [],
    error: false,
    loading: true,
  });

  const inFlight = useRef(false);
  const cancelled = useRef(false);

  useEffect(() => {
    cancelled.current = false;

    const tick = async () => {
      if (inFlight.current || cancelled.current) {
        return;
      }

      inFlight.current = true;

      try {
        const data: GetState = await invoke<GetState>("get_state");

        if (!cancelled.current) {
          setState({
            snapshot: data.snapshot,
            history: data.history,
            error: false,
            loading: false,
          });
        }
      } catch {
        if (!cancelled.current) {
          setState((prev) => ({ ...prev, error: true, loading: false }));
        }
      } finally {
        inFlight.current = false;
      }
    };

    // Start immediately, then on the interval.
    tick();

    const id = setInterval(tick, POLL_INTERVAL_MS);

    return () => {
      cancelled.current = true;
      clearInterval(id);
    };
  }, []);

  return state;
}

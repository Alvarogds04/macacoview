import { useState, useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";

import type { GetState } from "../types";

const POLL_INTERVAL_MS = 1000;

/**
 * True when the frontend runs inside a Tauri webview.
 *
 * Detection method: a synchronous `in` check for `__TAURI_INTERNALS__` on
 * `window`. Tauri v2 injects that object into the webview before any page
 * script runs, so the check is deterministic at startup, costs nothing (no
 * network round-trip, no thrown exception) and cannot produce a false
 * positive in a plain browser served by the daemon. A try/catch around an
 * initial `invoke` would also work, but it burns a real IPC round-trip and
 * conflates "not in Tauri" with "the command failed".
 */
function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/**
 * Fetches one `GetState` payload from whichever backend is available:
 *
 * - Inside Tauri: the `get_state` command, exactly as before.
 * - Served by the daemon over plain HTTP: `GET /api/state`, which returns
 *   the same JSON shape as the Tauri command.
 *
 * If the HTTP fetch fails (daemon down, non-2xx status, malformed JSON), it
 * throws like `invoke` does, so the caller's existing error handling applies
 * unchanged: the hook keeps the last-good snapshot, sets `error: true` and
 * simply retries on the next tick. The app never crashes on backend failure.
 */
async function fetchGetState(): Promise<GetState> {
  if (isTauri()) {
    return invoke<GetState>("get_state");
  }

  // `fetch` only rejects on network-level errors; a 404/500 resolves fine,
  // so the status must be checked explicitly to keep failures loud.
  const res = await fetch("/api/state");
  if (!res.ok) {
    throw new Error(`GET /api/state failed with status ${res.status}`);
  }
  return (await res.json()) as GetState;
}

export interface UseAppStateReturn {
  snapshot: GetState["snapshot"];
  history: GetState["history"];
  error: boolean;
  loading: boolean;
}

/**
 * Polls the backend `get_state` payload once per second.
 *
 * - Inside Tauri it calls the `get_state` command; served by the daemon in a
 *   plain browser it fetches `GET /api/state` (same JSON contract).
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
        const data: GetState = await fetchGetState();

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

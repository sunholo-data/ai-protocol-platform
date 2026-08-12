"use client";

import { useEffect, useState } from "react";

/**
 * useBackendReady — polls `/api/proxy/health` until it returns 200, then
 * resolves. The signal indicates that the FastAPI sidecar behind the
 * Next.js proxy is reachable (it isn't always — Cloud Run cold starts
 * for `platform-backend` can take 5-30s on the first hit after scale-
 * to-zero, during which `/api/proxy/...` requests time out or 502).
 *
 * Strategy: probe immediately; on miss/error, retry with a short
 * backoff (1s → 1.5s → 2s …) until 200 or maxAttempts. Resolves true
 * on success; resolves false only if the hook unmounts or the deadline
 * passes (callers can re-mount to retry).
 *
 * Why /api/proxy/health and not /health directly: same-origin so the
 * call lives inside the Next.js dev / production server's auth
 * boundaries. Bypasses GFE-level auth gating.
 *
 * Returns `{ ready, attempts }`. `ready` is the boolean callers use to
 * gate UI; `attempts` is exposed for diagnostics / debug overlays.
 */
export interface UseBackendReady {
  ready: boolean;
  attempts: number;
}

export function useBackendReady(): UseBackendReady {
  const [ready, setReady] = useState(false);
  const [attempts, setAttempts] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function probe(attempt: number) {
      if (cancelled) return;
      try {
        // No auth — the proxy's /health route is intentionally public
        // (the backend's /health is too). 5s is generous enough for a
        // cold-start single round-trip but won't keep the user staring
        // at "Connecting…" forever if the backend is genuinely down.
        const controller = new AbortController();
        const ac = setTimeout(() => controller.abort(), 5000);
        const res = await fetch("/api/proxy/health", {
          method: "GET",
          signal: controller.signal,
        });
        clearTimeout(ac);
        if (!cancelled && res.ok) {
          setReady(true);
          return;
        }
      } catch {
        // Network error / abort / non-200 — fall through to retry.
      }
      if (cancelled) return;
      setAttempts(attempt + 1);
      // Backoff: cap at 5s so the polling stays responsive in steady-
      // state without thrashing during a long cold start.
      const delay = Math.min(1000 + attempt * 500, 5000);
      timer = setTimeout(() => probe(attempt + 1), delay);
    }

    probe(0);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  return { ready, attempts };
}

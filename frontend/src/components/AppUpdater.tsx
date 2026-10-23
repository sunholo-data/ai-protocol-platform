"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** The build this bundle came from (next.config.mjs bakes it in). */
const BUILD_ID = process.env.NEXT_PUBLIC_BUILD_ID || "";

/** How often a visible tab re-checks for a newer deploy. */
const CHECK_INTERVAL_MS = 5 * 60 * 1000;

/** Don't reload-loop on a chunk that is genuinely missing. */
const CHUNK_RELOAD_KEY = "platform.chunkReloadAt";
const CHUNK_RELOAD_COOLDOWN_MS = 60 * 1000;

function serviceWorkerEnabled(): boolean {
  // G20: `||` — unset and "" both mean the default (on).
  const raw = (process.env.NEXT_PUBLIC_SERVICE_WORKER || "").trim().toLowerCase();
  return !["0", "false", "no", "off"].includes(raw);
}

/**
 * AppUpdater — keeps an installed (home-screen) app on the current build.
 *
 * A home-screen app is rarely reloaded: iOS resumes it from memory, so without
 * this a user can sit on last week's bundle indefinitely. Three jobs:
 *
 *  1. Registers /sw.js keyed on this build (`?v=<build>`), so every deploy
 *     installs a fresh worker that drops the previous build's caches. The
 *     worker never serves HTML from cache (see public/sw.js). With
 *     NEXT_PUBLIC_SERVICE_WORKER=0 it unregisters instead.
 *  2. Asks the server which build it is serving (/api/version) whenever the
 *     app comes back to the foreground, comes back online, and every few
 *     minutes while visible. A mismatch shows a "new version" bar — NEVER
 *     SILENT, and never a surprise reload that eats a half-typed message.
 *  3. A lazy chunk that 404s after a deploy (ChunkLoadError) means the page is
 *     already broken, so it reloads straight away (once per minute at most).
 *
 * Production builds only: in `next dev` a worker caching /_next/static fights
 * hot reload.
 */
export function AppUpdater() {
  const [updateReady, setUpdateReady] = useState(false);
  const registrationRef = useRef<ServiceWorkerRegistration | null>(null);

  const checkForUpdate = useCallback(async () => {
    if (!BUILD_ID || document.visibilityState !== "visible") return;
    void registrationRef.current?.update().catch(() => {});
    try {
      const res = await fetch("/api/version", { cache: "no-store" });
      if (!res.ok) return;
      const { buildId } = (await res.json()) as { buildId?: string };
      if (buildId && buildId !== "unknown" && buildId !== BUILD_ID) setUpdateReady(true);
    } catch {
      // Offline or mid-deploy: the next check will catch it.
    }
  }, []);

  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;

    if ("serviceWorker" in navigator) {
      if (serviceWorkerEnabled()) {
        navigator.serviceWorker
          .register(`/sw.js?v=${encodeURIComponent(BUILD_ID)}`, { updateViaCache: "none" })
          .then((reg) => {
            registrationRef.current = reg;
          })
          .catch(() => {
            // Progressive enhancement: private browsing or a locked-down device
            // refusing a worker must still get a fully working app.
          });
      } else {
        void navigator.serviceWorker.getRegistrations().then((regs) => {
          for (const reg of regs) void reg.unregister();
        });
        if ("caches" in window) {
          void caches.keys().then((keys) => {
            for (const key of keys) if (key.startsWith("platform-")) void caches.delete(key);
          });
        }
      }
    }

    const onVisible = () => {
      if (document.visibilityState === "visible") void checkForUpdate();
    };
    const onChunkError = (reason: unknown) => {
      const name = (reason as { name?: string } | null)?.name;
      const message = String((reason as { message?: string } | null)?.message ?? "");
      if (name !== "ChunkLoadError" && !/Loading chunk [\w-]+ failed/.test(message)) return;
      let last = 0;
      try {
        last = Number(sessionStorage.getItem(CHUNK_RELOAD_KEY) || 0);
        sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now()));
      } catch {
        // Storage blocked — fall through to the bar rather than risk a loop.
        setUpdateReady(true);
        return;
      }
      if (Date.now() - last > CHUNK_RELOAD_COOLDOWN_MS) window.location.reload();
      else setUpdateReady(true);
    };
    const onError = (e: ErrorEvent) => onChunkError(e.error);
    const onRejection = (e: PromiseRejectionEvent) => onChunkError(e.reason);

    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", checkForUpdate);
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    const timer = window.setInterval(() => void checkForUpdate(), CHECK_INTERVAL_MS);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", checkForUpdate);
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
      window.clearInterval(timer);
    };
  }, [checkForUpdate]);

  if (!updateReady) return null;

  return (
    <div
      role="status"
      className="flex shrink-0 items-center justify-center gap-3 border-b bg-primary px-4 py-2 text-sm text-primary-foreground"
    >
      <span>A new version is available.</span>
      <button
        type="button"
        onClick={() => window.location.reload()}
        className="min-h-[36px] rounded-md bg-primary-foreground px-3 text-sm font-medium text-primary"
      >
        Reload
      </button>
    </div>
  );
}

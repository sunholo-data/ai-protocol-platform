"use client";

import { useEffect, useState } from "react";
import { isLocalMode } from "@/lib/localMode";

/**
 * LocalModeBanner — soft-yellow strip mounted at the top of every page when
 * `NEXT_PUBLIC_LOCAL_MODE=1`. Three jobs:
 *
 *  1. Trust calibration — attendees see at a glance that data is ephemeral
 *     and auth is stubbed.
 *  2. Discoverability — the "Connect to your own GCP →" link is the single
 *     funnel from LOCAL_MODE to a real backend.
 *  3. Safety — if anyone ever deploys a build with NEXT_PUBLIC_LOCAL_MODE=1
 *     by mistake, the banner is loud enough to catch before harm.
 *
 * Renders nothing when LOCAL_MODE is off. Fetches /api/local-mode-status to
 * surface the exact list of stubbed GCP services (defensive: if the fetch
 * fails the banner still renders with a generic message).
 */
export function LocalModeBanner() {
  const [disabledServices, setDisabledServices] = useState<string[]>([]);
  const [fetchError, setFetchError] = useState<boolean>(false);

  useEffect(() => {
    if (!isLocalMode()) return;
    // Public endpoint — no auth header required.
    fetch("/api/proxy/api/local-mode-status", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then((body: { local_mode: boolean; disabled_services: string[] }) => {
        setDisabledServices(body.disabled_services ?? []);
      })
      .catch(() => setFetchError(true));
  }, []);

  if (!isLocalMode()) return null;

  return (
    <div
      role="status"
      aria-label="LOCAL_MODE active"
      className="w-full bg-yellow-100 border-b border-yellow-300 text-yellow-900 text-xs px-4 py-2 flex items-center gap-4 flex-wrap"
    >
      <span className="font-medium">
        🛠️ LOCAL_MODE — All data is in-memory and ephemeral. Auth is stubbed.
      </span>
      {disabledServices.length > 0 && !fetchError && (
        <span className="text-yellow-700 hidden md:inline">
          Disabled: {disabledServices.join(", ")}
        </span>
      )}
      <a
        href="/workshop#graduating-from-local-mode"
        className="ml-auto underline hover:no-underline"
      >
        Connect to your own GCP →
      </a>
    </div>
  );
}

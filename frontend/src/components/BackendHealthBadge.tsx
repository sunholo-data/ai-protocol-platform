"use client";

import { useEffect, useState } from "react";

type Status = "loading" | "ok" | "down";

export function BackendHealthBadge() {
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    let cancelled = false;

    async function check() {
      try {
        const res = await fetch("/api/proxy/health", { cache: "no-store" });
        if (cancelled) return;
        setStatus(res.ok ? "ok" : "down");
      } catch {
        if (!cancelled) setStatus("down");
      }
    }

    check();
    const id = setInterval(check, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // Quiet when healthy: a "backend ok" pill is internal plumbing, not news.
  // Only an outage is worth the user's attention.
  if (status !== "down") return null;

  return (
    <span
      role="status"
      className="inline-flex items-center gap-2 rounded-full border border-destructive/30 bg-destructive/10 px-3 py-1 text-xs font-medium text-destructive"
      data-testid="backend-health-badge"
      data-status={status}
    >
      <span aria-hidden className="h-2 w-2 rounded-full bg-destructive" />
      Can&apos;t reach the service
    </span>
  );
}

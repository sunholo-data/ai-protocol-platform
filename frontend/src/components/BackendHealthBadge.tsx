"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

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

  const label =
    status === "loading"
      ? "checking backend…"
      : status === "ok"
        ? "backend ok"
        : "backend down";

  const color =
    status === "loading"
      ? "bg-muted text-muted-foreground"
      : status === "ok"
        ? "bg-green-100 text-green-800"
        : "bg-red-100 text-red-800";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium",
        color,
      )}
      data-testid="backend-health-badge"
      data-status={status}
    >
      <span
        className={cn(
          "h-2 w-2 rounded-full",
          status === "loading" && "bg-muted-foreground animate-pulse",
          status === "ok" && "bg-green-500",
          status === "down" && "bg-red-500",
        )}
      />
      {label}
    </span>
  );
}

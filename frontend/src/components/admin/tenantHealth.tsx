// Presentational health bits for the tenant-admin overview (v6.9.x richer-UI
// quick-wins): a bucket-reachability dot + an at-a-glance config-health badge,
// both driven by the /api/admin/tenants/{domain}/validate response. Kept out of
// page.tsx so the logic is unit-testable without rendering the whole page.
"use client";

import type { ReactNode } from "react";
import type { TenantValidation, VerdictLevel } from "./tenantAdmin";

/** Per-tenant validate result, fetched lazily (one call per row, in parallel). */
export type HealthState =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ready"; validation: TenantValidation };

/** The documents_bucket check's level, or null if unresolved / no such check. */
export function bucketLevel(state?: HealthState): VerdictLevel | null {
  if (!state || state.kind !== "ready") return null;
  return state.validation.checks.find((c) => c.field === "documents_bucket")?.level ?? null;
}

/** Count of non-ok checks, split by severity — drives the health badge. */
export function healthSummary(validation: TenantValidation): { warnings: number; errors: number } {
  return {
    warnings: validation.checks.filter((c) => c.level === "warning").length,
    errors: validation.checks.filter((c) => c.level === "error").length,
  };
}

/** A small dot next to the bucket name showing SA reachability (the object-level
 * probe result), so a misconfigured grant is visible without opening the editor. */
export function ReachDot({ state }: { state?: HealthState }) {
  const loading = !state || state.kind === "loading";
  const level = bucketLevel(state);
  const { color, title } = loading
    ? { color: "bg-muted-foreground/40 animate-pulse", title: "checking reachability…" }
    : level === "ok"
      ? { color: "bg-green-500", title: "Reachable by the service account" }
      : level === "warning"
        ? { color: "bg-amber-500", title: "Not reachable — check the SA's grant on this bucket" }
        : level === "error"
          ? { color: "bg-red-500", title: "Error probing this bucket" }
          : { color: "bg-muted-foreground/40", title: "Reachability not checked" };
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${color}`}
      title={title}
      aria-label={title}
    />
  );
}

/** At-a-glance per-tenant config health: healthy / N warnings (e.g. an
 * unreachable bucket) / needs-fix (a hard error). */
export function HealthBadge({ state }: { state?: HealthState }) {
  if (!state || state.kind === "loading") {
    return <span className="text-xs text-muted-foreground">checking…</span>;
  }
  if (state.kind === "error") {
    return (
      <span className="text-xs text-muted-foreground" title="Couldn't validate this tenant">
        —
      </span>
    );
  }
  const { warnings, errors } = healthSummary(state.validation);
  if (!state.validation.ok || errors > 0) {
    return <HealthPill className="border-red-300 bg-red-50 text-red-700">✕ needs fix</HealthPill>;
  }
  if (warnings > 0) {
    return (
      <HealthPill className="border-amber-300 bg-amber-50 text-amber-700">
        ! {warnings} warning{warnings > 1 ? "s" : ""}
      </HealthPill>
    );
  }
  return <HealthPill className="border-green-300 bg-green-50 text-green-700">✓ healthy</HealthPill>;
}

function HealthPill({ className, children }: { className: string; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${className}`}>
      {children}
    </span>
  );
}

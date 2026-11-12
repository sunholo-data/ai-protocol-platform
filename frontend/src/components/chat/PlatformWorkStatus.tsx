"use client";

import type { PlatformRunStatus } from "@/hooks/useSkillAgent";

const LABELS: Record<PlatformRunStatus, string> = {
  idle: "No platform run in this view",
  pending: "Pending — waiting for the platform",
  running: "Started — platform skill is working",
  completed: "Platform run finished",
  failed: "Platform run failed",
  stopped: "Stopped listening to the platform run",
  unknown: "Platform completion not confirmed",
};

/** Generic workbench chrome over real run events, shared by every skill. */
export function PlatformWorkStatus({
  status,
  stageLabel,
  error,
}: {
  status: PlatformRunStatus;
  stageLabel?: string | null;
  error?: string | null;
}) {
  if (status === "idle") return null;
  const busy = status === "pending" || status === "running";
  return (
    <div
      role="status"
      aria-live="polite"
      className="shrink-0 border-b border-border px-4 py-3"
      data-testid="platform-work-status"
    >
      <div className="flex items-center gap-2 text-sm font-medium">
        <span
          aria-hidden="true"
          className={`h-2 w-2 shrink-0 rounded-full ${status === "failed" ? "bg-destructive" : busy ? "bg-primary" : "bg-muted-foreground"}`}
        />
        {LABELS[status]}
      </div>
      {status === "running" && stageLabel && (
        <p className="mt-1 text-xs text-muted-foreground">{stageLabel}</p>
      )}
      {(status === "failed" || status === "unknown") && error && <p className="mt-1 text-xs text-destructive">{error}</p>}
      <p className="mt-1 text-xs text-muted-foreground">
        This tracks the platform skill. Work handed to another agent needs that agent&apos;s status.
      </p>
    </div>
  );
}

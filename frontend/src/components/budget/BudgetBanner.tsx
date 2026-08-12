"use client";

import { useEffect, useState } from "react";
import type { StreamError } from "@/hooks/useSkillAgent";

/**
 * Renders a typed banner when the backend's budget enforcer refused
 * the current turn. Reads ``error`` off ``useSkillAgent`` and only
 * surfaces when ``error.kind === "budget_exceeded"`` — other error
 * kinds (HTTP, run_error, network) keep their existing rendering.
 *
 * Sprint 2.12 M3. Pairs with the backend translation in
 * ``skill_processor.py`` which catches ``BudgetExceededError`` and
 * emits ``RUN_ERROR{code: "BUDGET_EXCEEDED", message, retry_after_seconds}``.
 *
 * Behaviour:
 * - Renders the backend ``message`` verbatim (no client-side
 *   interpolation — the message text is policy and lives server-side).
 * - Shows a live countdown from ``retryAfterSeconds`` that ticks
 *   down once per second. Hidden when ``retryAfterSeconds`` is
 *   absent (some enforcers may decline to project a recovery time).
 * - "Got it" dismiss button calls ``onDismiss`` (typically
 *   ``useSkillAgent.clearError``); accessibility uses
 *   ``role="alert"`` + ``aria-live="assertive"`` because hard-block
 *   is a state the user MUST notice — they can't just keep typing.
 */
export interface BudgetBannerProps {
  error: StreamError | null;
  onDismiss: () => void;
}

export function BudgetBanner({ error, onDismiss }: BudgetBannerProps) {
  if (!error || error.kind !== "budget_exceeded") return null;

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="w-full bg-rose-50 border border-rose-300 text-rose-900 rounded-md px-4 py-3 flex items-start gap-4"
      data-testid="budget-banner"
    >
      <div className="flex-1">
        <p className="font-medium text-sm">{error.message}</p>
        {error.retryAfterSeconds !== undefined && (
          <Countdown initialSeconds={error.retryAfterSeconds} />
        )}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="text-rose-700 hover:text-rose-900 underline text-sm font-medium focus:outline-none focus:ring-2 focus:ring-rose-400 rounded"
        data-testid="budget-banner-dismiss"
      >
        Got it
      </button>
    </div>
  );
}

/**
 * Live countdown helper. Ticks once per second. Renders nothing once
 * the countdown reaches zero (the underlying error state is cleared
 * by the next successful run or the user dismissing).
 */
function Countdown({ initialSeconds }: { initialSeconds: number }) {
  const [secondsLeft, setSecondsLeft] = useState(initialSeconds);

  useEffect(() => {
    setSecondsLeft(initialSeconds);
  }, [initialSeconds]);

  useEffect(() => {
    if (secondsLeft <= 0) return;
    const handle = setTimeout(() => setSecondsLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearTimeout(handle);
  }, [secondsLeft]);

  if (secondsLeft <= 0) return null;

  return (
    <p className="text-xs text-rose-700 mt-1" data-testid="budget-countdown">
      Resets in {formatDuration(secondsLeft)}.
    </p>
  );
}

/**
 * Human-readable duration string. Days for ≥ 86400s, hours for
 * ≥ 3600s, minutes for ≥ 60s, seconds otherwise. Avoids precision
 * the UX doesn't benefit from — "23h" reads cleaner than
 * "23h 42m 19s" for a recovery countdown.
 */
function formatDuration(seconds: number): string {
  if (seconds >= 86400) {
    const days = Math.floor(seconds / 86400);
    return `${days} day${days === 1 ? "" : "s"}`;
  }
  if (seconds >= 3600) {
    const hours = Math.floor(seconds / 3600);
    return `${hours} hour${hours === 1 ? "" : "s"}`;
  }
  if (seconds >= 60) {
    const minutes = Math.floor(seconds / 60);
    return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  }
  return `${seconds} second${seconds === 1 ? "" : "s"}`;
}

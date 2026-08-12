"use client";

import type { ReactNode } from "react";

/**
 * Yellow-bordered wrapper for the warn variant of ArtefactDecision.
 * The artefact still renders below the stripe — warn is informational.
 *
 * Sprint 2.13 M2. ``role="status"`` + ``aria-live="polite"`` so
 * screen readers announce the message but do not interrupt
 * (contrast with ArtefactRefused which uses ``assertive``).
 */
export interface ArtefactWarningStripeProps {
  message: string;
  reasonCode: string;
  children: ReactNode;
}

export function ArtefactWarningStripe({
  message,
  reasonCode,
  children,
}: ArtefactWarningStripeProps) {
  return (
    <div data-testid="artefact-warning-stripe-wrapper" className="w-full">
      <div
        role="status"
        aria-live="polite"
        className="w-full bg-yellow-50 border border-yellow-300 text-yellow-900 rounded-t-md px-3 py-2 text-xs flex items-center gap-3"
        data-testid="artefact-warning-stripe"
      >
        <span className="font-medium">{message}</span>
        <span
          className="ml-auto font-mono text-yellow-700"
          data-testid="artefact-warning-reason"
        >
          {reasonCode}
        </span>
      </div>
      {children}
    </div>
  );
}

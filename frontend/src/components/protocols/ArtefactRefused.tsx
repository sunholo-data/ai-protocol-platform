"use client";

import { useEffect, useRef } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import type { ArtefactDecision } from "./ArtefactReviewer";

/**
 * Renders the refusal panel when an ArtefactReviewer returns
 * ``{action: "block"}``. Sprint 2.13 M2.
 *
 * The user MUST notice this state — they can't just keep typing
 * expecting the artefact to load. ``role="alert"`` +
 * ``aria-live="assertive"`` so screen readers announce the refusal.
 *
 * On mount, fires a best-effort audit POST so the block path is
 * never silent — the backend gets the only record of what was
 * refused. The POST is fire-and-forget; failures are logged in
 * dev only and never bubbled to the user (no point cascading the
 * failure).
 */
interface BlockDecision {
  action: "block";
  message: string;
  reasonCode: string;
  appealUrl?: string;
}

export interface ArtefactRefusedProps {
  decision: BlockDecision;
  toolName: string;
  serverId: string;
  invocationId: string;
  sessionId?: string | null;
}

export function ArtefactRefused({
  decision,
  toolName,
  serverId,
  invocationId,
  sessionId,
}: ArtefactRefusedProps) {
  // Fire the audit POST exactly once per mount. React 18 Strict-Mode
  // double-mounts in dev; the ref guard makes the POST idempotent
  // without depending on the dev-only behaviour.
  const auditFired = useRef(false);

  useEffect(() => {
    if (auditFired.current) return;
    auditFired.current = true;
    if (!sessionId) return;
    void fetchWithAuth(
      `/api/proxy/api/sessions/${encodeURIComponent(sessionId)}/artefact-blocked`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tool_name: toolName,
          server_id: serverId,
          reason_code: decision.reasonCode,
          invocation_id: invocationId,
        }),
      },
    ).catch((err: unknown) => {
      if (process.env.NODE_ENV !== "production") {
        console.warn("[ArtefactRefused] audit POST failed", err);
      }
    });
  }, [
    decision.reasonCode,
    invocationId,
    sessionId,
    serverId,
    toolName,
  ]);

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="w-full bg-rose-50 border border-rose-300 text-rose-900 rounded-md px-4 py-3 flex items-start gap-4"
      data-testid="artefact-refused"
    >
      <div className="flex-1">
        <p className="font-medium text-sm">{decision.message}</p>
        <p
          className="text-xs text-rose-700 mt-1 font-mono"
          data-testid="artefact-refused-reason"
        >
          {decision.reasonCode}
        </p>
      </div>
      {decision.appealUrl && (
        <a
          href={decision.appealUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-rose-700 hover:text-rose-900 underline text-sm font-medium focus:outline-none focus:ring-2 focus:ring-rose-400 rounded"
          data-testid="artefact-refused-appeal"
        >
          Appeal →
        </a>
      )}
    </div>
  );
}

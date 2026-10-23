// v6.34.0 DOC-WORKBENCH — document edits show up in Activity.
//
// The workbench panels call REST endpoints directly rather than going through
// the agent, which is what makes them fast — but it also meant they were
// INVISIBLE: rewriting a passage, restyling, or restoring a version produced no
// trace in the Activity tab, so a run that took a while looked like nothing
// happening (principle #8 asks for live progress, not just a terminal state).
//
// These push the same `ToolCallState` shape the agent's own tool calls use, so
// they appear in the one feed, time-ordered, with no bespoke rendering.

import type { ToolCallState } from "@/hooks/useSkillAgent";

export interface DocumentActivitySink {
  upsertToolCall?: (toolCall: ToolCallState) => void;
}

/**
 * Report a document action to Activity: running now, settled later.
 *
 * Returns a `settle` callback. Fire-and-forget by design — activity reporting
 * must never be able to fail the action it is describing.
 */
export function reportDocumentAction(
  sink: DocumentActivitySink | undefined,
  name: string,
  detail?: string,
): (outcome: { ok: boolean; message?: string }) => void {
  if (!sink?.upsertToolCall) return () => {};

  const id = `doc-action-${name}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const ts = Date.now();
  sink.upsertToolCall({ id, name, status: "running", ts, resultContent: detail });

  return ({ ok, message }) => {
    sink.upsertToolCall?.({
      id,
      name,
      status: ok ? "success" : "error",
      ts,
      resultContent: message ?? detail,
    });
  };
}

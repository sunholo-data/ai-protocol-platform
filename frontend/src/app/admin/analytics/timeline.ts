// Chronological trace timeline — merges a session trace's messages, tool calls
// and delegations into the order they actually happened, so the admin reads one
// conversation stream instead of three disjoint lists.

export type TraceMsg = {
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  agent_label?: string | null;
};

export type TraceTool = {
  id: string;
  name: string;
  status: string;
  ts: number;
  argsJson?: string | null;
  resultContent?: string | null;
};

export type TraceDeleg = { id: string; target: string; targetDisplay: string; mode: string; ts: number };

export type TimelineItem =
  | { kind: "message"; key: string; ts: number; role: TraceMsg["role"]; content: string; agentLabel?: string | null }
  | { kind: "tool"; key: string; ts: number; name: string; status: string; argsJson?: string; resultContent?: string }
  | { kind: "delegation"; key: string; ts: number; targetDisplay: string; mode: string };

/** Merge messages, tool calls and delegations into conversation order (oldest
 * first). The sort is stable, so items sharing a timestamp keep source order
 * (messages, then tools, then delegations). */
export function buildTimeline(trace: {
  messages: TraceMsg[];
  tools: TraceTool[];
  delegations: TraceDeleg[];
}): TimelineItem[] {
  const items: TimelineItem[] = [
    ...trace.messages.map(
      (m, i): TimelineItem => ({
        kind: "message",
        key: `m-${i}`,
        ts: m.timestamp || 0,
        role: m.role,
        content: m.content,
        agentLabel: m.agent_label,
      }),
    ),
    ...trace.tools.map(
      (t): TimelineItem => ({
        kind: "tool",
        key: `t-${t.id}`,
        ts: t.ts || 0,
        name: t.name,
        status: t.status,
        argsJson: t.argsJson ?? undefined,
        resultContent: t.resultContent ?? undefined,
      }),
    ),
    ...trace.delegations.map(
      (d): TimelineItem => ({
        kind: "delegation",
        key: `d-${d.id}`,
        ts: d.ts || 0,
        targetDisplay: d.targetDisplay || d.target,
        mode: d.mode,
      }),
    ),
  ];
  return items.sort((a, b) => a.ts - b.ts);
}

// SKILL-DELEGATION M3b — Activity panel.
//
// The general "activity transparency" surface: a recede-able, time-ordered
// feed of what the assistant is doing this session — tool calls, skill
// handoffs, and reasoning — aggregated from events the frontend already
// receives (thin client; no new backend emission). Lives as a Workbench tab
// whose badge lights up on new activity and fades when idle. Deeper,
// structured detail a skill wants to show is pushed separately via the A2UI
// "activity" surface (see A2UISurfaceMount below).
//
// Each row carries a human-friendly, self-updating relative timestamp
// ("just now", "12s ago") with the exact clock time on hover, and tool rows
// expand to reveal what the tool was called with (its arguments) and its result.
//
// The rendering primitives (JSON tree, args key→value list, copy buttons,
// status dots, timestamp formatting) are shared with the admin analytics
// trace — they live in `@/components/activity/`.

"use client";

import { useEffect, useState, type ReactNode } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { DocIcon } from "@/components/icons";
import type { ToolCallState, DelegationMarkerItem, ModelEventItem, ModelWaitState } from "@/hooks/useSkillAgent";
import { A2UISurfaceMount } from "@/components/protocols/A2UISurfaceMount";
import { useSurfaceState } from "@/providers/SurfaceRegistry";
import { ArrowIcon, StatusDot, WrenchIcon } from "@/components/activity/bits";
import { ToolCallDetails, hasToolDetail } from "@/components/activity/ToolCallDetails";
import { absoluteTime, activityTime, useNow } from "@/components/activity/format";
import { useToolLabels } from "@/hooks/useToolLabels";
import { useFormat } from "@/i18n";

/** Session context shown in the pinned header row (model + voice config). */
export interface ActivityContext {
  modelTier?: string;
  voice?: { enabled: boolean; language: string | null } | null;
}

/** A document in the session, surfaced as a "document added" entry. */
export interface ActivityDoc {
  id: string;
  name: string;
  ts?: number;
}

interface ActivityPanelProps {
  toolCalls: ToolCallState[];
  delegations: DelegationMarkerItem[];
  isThinking: boolean;
  sessionId: string | null;
  /** Model + voice config for the pinned context row. */
  context?: ActivityContext;
  /** Documents in the session → "document added" entries. */
  documents?: ActivityDoc[];
  /** When the session began → a "Session started" marker. */
  sessionStartTs?: number;
  /** ACTIVITY-OBS — an action-triggered run (launcher "Compare contracts" /
   * "Analyze obligations") is in flight. Shows a live "Running…" row so the
   * user always sees the run is happening, even before its first tool call
   * lands — never a silent grey button. */
  isRunning?: boolean;
  /** Server-authored stage label for the in-flight action run, shown on the
   * running row when present (e.g. "Reading 2 documents…"). */
  runStageLabel?: string | null;
  /** An action-triggered run failed — surfaced as an error row so the failure
   * is visible in Activity, not just a console.warn. */
  runError?: string | null;
  /** COMPACTION-WIRE M4 — history compactions in this session. Compaction
   * silently rewrites what the assistant can remember; this is the only place
   * the user (or a triager) can see it happened. */
  compactions?: { id: string; ts: number; eventsCompacted: number; summaryChars: number }[];
  /** Model retries and fallbacks — each one is time the turn spent not
   * answering, so they belong in the feed beside the tools. */
  modelEvents?: ModelEventItem[];
  /** The model request the turn is waiting on → a live "Waiting for…" row. */
  modelWait?: ModelWaitState | null;
  /** When the streaming chat turn began, or null when none is. Only a tool that
   * started in THIS turn and has no result yet is running — tools loaded from
   * history carry no result time and must not count up. */
  turnStartedAt?: number | null;
}

type Entry =
  | {
      id: string;
      ts: number;
      kind: "tool";
      name: string;
      status: ToolCallState["status"];
      argsJson?: string;
      resultContent?: string;
      endTs?: number;
    }
  | { id: string; ts: number; kind: "delegation"; name: string; mode: DelegationMarkerItem["mode"] }
  | { id: string; ts: number; kind: "document"; name: string }
  | { id: string; ts: number; kind: "compaction"; eventsCompacted: number; summaryChars: number }
  | { id: string; ts: number; kind: "model"; event: ModelEventItem }
  | { id: string; ts: number; kind: "session" };

// v6.11.0 — the curated workbench hides pure orchestration plumbing from the
// Home digest; the Activity feed keeps it but collapses it under a disclosure so
// the debug feed stays scannable. This mirrors the backend `_INTERNAL_TOOLS`
// set (adk/notability.py) — a small, fixed display heuristic for the live tool
// calls, which (unlike history rows) don't carry a server-assigned tier.
const _INTERNAL_TOOL_NAMES = new Set(["transfer_to_agent", "request_handoff"]);
function isInternalToolName(name: string): boolean {
  return _INTERNAL_TOOL_NAMES.has(name);
}

/** "12s", "1m 05s" — elapsed wall-clock for a step. */
export function formatDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

const _MODEL_ERROR_LABELS: Record<string, string> = {
  MODEL_RATE_LIMITED: "rate limited",
  MODEL_UNAVAILABLE: "unavailable",
  MODEL_AUTH_FAILED: "auth failed",
  MODEL_REQUEST_INVALID: "rejected the request",
  MODEL_TOOL_CALL_INVALID: "wrote a broken tool call",
};

function displayModel(model: string): string {
  return model.includes("/") ? model.split("/").pop()! : model;
}

/** One sentence per retry/fallback: what failed, how long it cost, what's next. */
function modelEventText(e: ModelEventItem): ReactNode {
  const from = <span className="font-medium text-foreground/80">{displayModel(e.model)}</span>;
  const to = e.toModel ? <span className="font-medium text-foreground/80">{displayModel(e.toModel)}</span> : null;
  const waited = e.waitedS !== undefined ? `${e.waitedS.toFixed(e.waitedS < 10 ? 1 : 0)}s` : null;
  const next =
    e.kind === "fallback" ? (
      <>switched to {to}</>
    ) : (
      <>retrying{e.delayS !== undefined ? ` in ${e.delayS.toFixed(1)}s` : ""}</>
    );
  if (e.reason === "provider_cooldown" || e.reason === "model_cooldown")
    return (
      <>
        Skipped {from} after repeated failures — using {to}
      </>
    );
  if (e.code === "MODEL_SILENT_STALL")
    return (
      <>
        {from} sent nothing{waited ? ` for ${waited}` : ""} — {next}
      </>
    );
  const what = _MODEL_ERROR_LABELS[e.code] ?? e.code.toLowerCase().replace(/^model_/, "").replace(/_/g, " ");
  return (
    <>
      {from} {what}
      {waited ? ` after ${waited}` : ""} — {next}
    </>
  );
}

// Resolve a model tier (e.g. "smart") → the actual model id via /api/models,
// cached per page. Falls back to the tier name when the fetch fails.
interface ModelsResp {
  models: { id: string }[];
  tier_defaults: Record<string, string>;
}
let _modelsCache: ModelsResp | null = null;

function useModelLabel(tier?: string): string {
  const [label, setLabel] = useState<string>(tier ?? "");
  useEffect(() => {
    if (!tier) {
      setLabel("");
      return;
    }
    let cancelled = false;
    const apply = (d: ModelsResp) => {
      const id = d.tier_defaults?.[tier];
      if (!cancelled) setLabel(id || tier);
    };
    if (_modelsCache) {
      apply(_modelsCache);
      return;
    }
    fetchWithAuth("/api/proxy/api/models")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: ModelsResp) => {
        _modelsCache = d;
        apply(d);
      })
      .catch(() => {
        if (!cancelled) setLabel(tier);
      });
    return () => {
      cancelled = true;
    };
  }, [tier]);
  return label;
}

/** Pinned header showing the model + read-aloud config the session runs with. */
function ContextRow({ context }: { context: ActivityContext }) {
  const modelLabel = useModelLabel(context.modelTier);
  const voice = context.voice;
  return (
    <div className="shrink-0 border-b border-border bg-muted/20 px-3 py-2 text-[11px]">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {context.modelTier && (
          <span>
            <span className="text-muted-foreground/60">Model </span>
            <span className="font-medium text-foreground/80">{modelLabel || context.modelTier}</span>
            {modelLabel && modelLabel !== context.modelTier && (
              <span className="text-muted-foreground/50"> · {context.modelTier}</span>
            )}
          </span>
        )}
        {voice && (
          <span>
            <span className="text-muted-foreground/60">Read-aloud </span>
            <span className="font-medium text-foreground/80">{voice.enabled ? "on" : "off"}</span>
            {voice.enabled && voice.language ? (
              <span className="text-muted-foreground/50"> · {voice.language}</span>
            ) : null}
          </span>
        )}
      </div>
    </div>
  );
}

function ToolRow({ entry, now, live }: { entry: Extract<Entry, { kind: "tool" }>; now: number; live: boolean }) {
  const [open, setOpen] = useState(false);
  const hasDetail = hasToolDetail(entry.argsJson, entry.resultContent);
  // CLAUDE.md #9 — friendly label first; the raw tool name is secondary
  // detail (row tooltip + the expanded view).
  const labelFor = useToolLabels();
  const label = labelFor(entry.name);
  const fmt = useFormat();

  return (
    <li className="rounded-md text-xs text-muted-foreground">
      <button
        type="button"
        disabled={!hasDetail}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted/40 disabled:cursor-default disabled:hover:bg-transparent"
        aria-expanded={hasDetail ? open : undefined}
        title={entry.name}
        data-tool-name={entry.name}
      >
        <WrenchIcon />
        <span className="flex-1 truncate font-medium text-foreground/80">{label}</span>
        {entry.ts && (entry.endTs || live) ? (
          <span
            data-testid="activity-tool-duration"
            title={entry.endTs ? "How long the tool took" : "Running for"}
            className={`shrink-0 tabular-nums text-[11px] ${entry.endTs ? "text-muted-foreground/70" : "text-orange-500/80"}`}
          >
            {formatDuration((entry.endTs ?? now) - entry.ts)}
          </span>
        ) : null}
        {entry.ts ? (
          <time
            dateTime={new Date(entry.ts).toISOString()}
            title={absoluteTime(entry.ts, fmt)}
            className="shrink-0 tabular-nums text-[11px] text-muted-foreground/70"
          >
            {activityTime(entry.ts, now, fmt)}
          </time>
        ) : null}
        <StatusDot status={entry.status} />
        {hasDetail && (
          <span aria-hidden className={`shrink-0 text-muted-foreground/60 transition-transform ${open ? "rotate-90" : ""}`}>
            ›
          </span>
        )}
      </button>
      {open && hasDetail && (
        <div className="ml-6 mb-1.5 mr-2">
          {label !== entry.name && (
            <p data-testid="activity-tool-raw-name" className="mb-1 font-mono text-[11px] text-muted-foreground/70">
              {entry.name}
            </p>
          )}
          <ToolCallDetails argsJson={entry.argsJson} resultContent={entry.resultContent} />
        </div>
      )}
    </li>
  );
}

/** A simple icon + label + time row (delegation / document / session). */
function SimpleRow({ icon, children, ts, now }: { icon: ReactNode; children: ReactNode; ts: number; now: number }) {
  const fmt = useFormat();
  return (
    <li className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted/40">
      {icon}
      <span className="flex-1 truncate">{children}</span>
      {ts ? (
        <time
          dateTime={new Date(ts).toISOString()}
          title={absoluteTime(ts, fmt)}
          className="shrink-0 tabular-nums text-[11px] text-muted-foreground/70"
        >
          {activityTime(ts, now, fmt)}
        </time>
      ) : null}
    </li>
  );
}

export function ActivityPanel({
  toolCalls,
  delegations,
  isThinking,
  sessionId,
  context,
  documents,
  sessionStartTs,
  isRunning = false,
  runStageLabel = null,
  runError = null,
  compactions,
  modelEvents,
  modelWait = null,
  turnStartedAt = null,
}: ActivityPanelProps) {
  const isLive = (tc: { ts?: number; endTs?: number; status: ToolCallState["status"] }) =>
    turnStartedAt !== null && !tc.endTs && tc.status !== "error" && (tc.ts ?? 0) >= turnStartedAt;
  // Tick every second while something is counting up, else every 10s.
  const liveTool = toolCalls.some(isLive);
  const now = useNow(modelWait || liveTool ? 1000 : 10_000);
  const fmt = useFormat();
  const [showInternal, setShowInternal] = useState(false);
  // A skill may push structured rich detail into the same tab via the A2UI
  // "activity" surface — rendered above the timeline when present.
  const activitySurface = useSurfaceState("activity");

  const entries: Entry[] = [
    ...toolCalls.map(
      (tc): Entry => ({
        id: `tool-${tc.id}`,
        ts: tc.ts ?? 0,
        kind: "tool",
        name: tc.name,
        status: tc.status,
        argsJson: tc.argsJson,
        resultContent: tc.resultContent,
        endTs: tc.endTs,
      }),
    ),
    ...(modelEvents ?? []).map((m): Entry => ({ id: m.id, ts: m.ts, kind: "model", event: m })),
    ...delegations.map(
      (d): Entry => ({ id: d.id, ts: d.ts, kind: "delegation", name: d.targetDisplay, mode: d.mode }),
    ),
    // Session documents → "document added" entries. No per-doc timestamp yet,
    // so fall back to the session start time for ordering.
    ...(documents ?? []).map(
      (d): Entry => ({ id: `doc-${d.id}`, ts: d.ts ?? sessionStartTs ?? 0, kind: "document", name: d.name }),
    ),
    ...(compactions ?? []).map(
      (c): Entry => ({
        id: c.id,
        ts: c.ts,
        kind: "compaction",
        eventsCompacted: c.eventsCompacted,
        summaryChars: c.summaryChars,
      }),
    ),
    ...(sessionStartTs ? [{ id: "session-start", ts: sessionStartTs, kind: "session" } as Entry] : []),
  ].sort((a, b) => b.ts - a.ts); // newest first — latest activity draws the eye

  const internalToolCount = entries.filter((e) => e.kind === "tool" && isInternalToolName(e.name)).length;
  const hasContext = Boolean(context && (context.modelTier || context.voice));
  const bodyEmpty =
    entries.length === 0 &&
    !isThinking &&
    !isRunning &&
    !runError &&
    !modelWait &&
    !activitySurface?.surface;

  // Nothing at all to show — the full-panel invitation (no context row either).
  if (bodyEmpty && !hasContext) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center">
        <p className="max-w-xs text-xs text-muted-foreground">
          The assistant&apos;s activity — tools it calls, specialists it hands off to, and its reasoning —
          shows up here as it works, then quietly stays available.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {hasContext && context && <ContextRow context={context} />}
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3">
        {activitySurface?.surface && (
          <div className="rounded-md border border-border p-2">
            <A2UISurfaceMount surfaceId="activity" sessionId={sessionId} />
          </div>
        )}

        {bodyEmpty ? (
          <p className="px-2 py-1 text-xs text-muted-foreground">
            No activity yet — tools, hand-offs and documents show up here as the assistant works.
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {/* ACTIVITY-OBS — an action-triggered run is streaming. The pulse
                mirrors a "running" tool-call dot so a launcher run always reads
                as live in the Activity tab, even before its first tool lands. */}
            {isRunning && (
              <li
                data-testid="activity-running-row"
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-foreground/80"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-orange-400 animate-pulse" />
                <span className="font-medium">{runStageLabel || "Running…"}</span>
              </li>
            )}
            {runError && (
              <li
                data-testid="activity-error-row"
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-red-600 dark:text-red-400"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500" />
                <span className="min-w-0 flex-1 break-words">{runError}</span>
              </li>
            )}
            {modelWait && !isThinking && (
              <li
                data-testid="activity-model-wait-row"
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-muted-foreground"
                title={
                  modelWait.deadlineS
                    ? `If it sends nothing for ${Math.round(modelWait.deadlineS)}s the backup model takes over`
                    : undefined
                }
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-orange-400 animate-pulse" />
                <span className="flex-1 truncate">
                  Waiting for <span className="font-medium text-foreground/80">{displayModel(modelWait.model)}</span>
                </span>
                <span className="shrink-0 tabular-nums text-[11px] text-orange-500/80">
                  {formatDuration(now - modelWait.since)}
                </span>
              </li>
            )}
            {isThinking && (
              <li className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-muted-foreground">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-orange-400 animate-pulse" />
                <span>Reasoning…</span>
              </li>
            )}
            {internalToolCount > 0 && (
              <li>
                <button
                  type="button"
                  onClick={() => setShowInternal((v) => !v)}
                  aria-expanded={showInternal}
                  data-testid="activity-internal-toggle"
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs text-muted-foreground/70 transition-colors hover:text-foreground"
                >
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/30" />
                  <span>
                    {showInternal ? "Hide" : "Show"} {internalToolCount} internal step
                    {internalToolCount === 1 ? "" : "s"}
                  </span>
                </button>
              </li>
            )}
            {entries.map((e) => {
              if (e.kind === "tool") {
                if (isInternalToolName(e.name) && !showInternal) return null;
                return <ToolRow key={e.id} entry={e} now={now} live={isLive(e)} />;
              }
              if (e.kind === "delegation")
                return (
                  <SimpleRow key={e.id} icon={<ArrowIcon />} ts={e.ts} now={now}>
                    {e.mode === "suggest" ? "Suggested " : "Delegated to "}
                    <span className="font-medium text-foreground/80">{e.name}</span>
                  </SimpleRow>
                );
              // Wraps rather than truncates: the sentence IS the detail.
              if (e.kind === "model")
                return (
                  <li
                    key={e.id}
                    data-testid="activity-model-event"
                    className="flex items-start gap-2 rounded-md px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted/40"
                    title={`${e.event.model}${e.event.toModel ? ` → ${e.event.toModel}` : ""} (${e.event.code}${e.event.reason ? `, ${e.event.reason}` : ""})`}
                  >
                    <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500/70" />
                    <span className="min-w-0 flex-1 break-words">{modelEventText(e.event)}</span>
                    <time
                      dateTime={new Date(e.ts).toISOString()}
                      title={absoluteTime(e.ts, fmt)}
                      className="shrink-0 tabular-nums text-[11px] text-muted-foreground/70"
                    >
                      {activityTime(e.ts, now, fmt)}
                    </time>
                  </li>
                );
              if (e.kind === "document")
                return (
                  <SimpleRow key={e.id} icon={<DocIcon className="h-3.5 w-3.5 text-muted-foreground" />} ts={e.ts} now={now}>
                    Added <span className="font-medium text-foreground/80">{e.name}</span>
                  </SimpleRow>
                );
              // COMPACTION-WIRE M4 — history was summarised. The user keeps
              // seeing the full transcript while the model now sees a summary,
              // so without this row a degraded answer is indistinguishable
              // from a good one (CLAUDE.md #8 applied to context).
              if (e.kind === "compaction")
                return (
                  <SimpleRow
                    key={e.id}
                    icon={<span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500/70" />}
                    ts={e.ts}
                    now={now}
                  >
                    <span className="font-medium text-foreground/80">History summarised</span>
                    {" — "}
                    {e.eventsCompacted} earlier {e.eventsCompacted === 1 ? "entry" : "entries"} condensed to keep
                    the conversation within its context limit
                  </SimpleRow>
                );
              return (
                <SimpleRow key={e.id} icon={<span className="h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/40" />} ts={e.ts} now={now}>
                  Session started
                </SimpleRow>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

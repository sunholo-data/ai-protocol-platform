// Workshop W5d — AG-UI: Subscribing to the Stream
// The AG-UI subscription is the `agent.subscribe(...)` block: four callbacks map
// lifecycle events (RUN_STARTED, TEXT_MESSAGE_*, RUN_FINISHED) to React state.
// `sendMessage` is the full round-trip: add message → runAgent() → await. The
// subscription fires streaming updates while we wait. No polling, no EventSource.

"use client";

import type { Message } from "@ag-ui/client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useAGUIAgent } from "@/providers/AGUIProvider";
import { useOptionalSurfaceRegistry } from "@/providers/SurfaceRegistry";
import {
  recordFirstEvent,
  recordFirstStageLabel,
  recordFirstTextChunk,
  recordServerReport,
  startMark,
} from "@/stores/latencyStore";

export interface SkillMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** Resume-time per-delegate attribution (6.11): the producing agent's avatar
   * + name for a history message. Undefined for live messages (attributed via
   * delegation events) and root-skill/user messages. */
  avatar?: string | null;
  agentLabel?: string | null;
  /** Original send time (epoch ms) for a HISTORY message, from the backend
   * event timestamp. Undefined for live messages — the transcript stamps those
   * with their stable client first-seen time instead, so a resumed thread shows
   * each bubble at when it was sent, not when the session was loaded. */
  createdAt?: number;
}

export interface StreamError {
  kind: "http" | "run_error" | "network" | "budget_exceeded" | "rate_limited";
  status?: number;
  message: string;
  retryable: boolean;
  rawMessage: string;
  /**
   * Seconds until budget recovery (period rollover). Present only on
   * ``kind === "budget_exceeded"`` — backend pulls this off the
   * BudgetDecision and rides it as a passthrough field on the AG-UI
   * RUN_ERROR event. The BudgetBanner renders a live countdown.
   */
  retryAfterSeconds?: number;
}

export interface ToolCallState {
  id: string;
  name: string;
  status: "running" | "success" | "error";
  /** Wall-clock creation time (ms). Used to time-order the Activity panel
   * alongside delegation markers (SKILL-DELEGATION M3b). */
  ts?: number;
  parentMessageId?: string;
  resultContent?: string;
  /** Concatenated TOOL_CALL_ARGS deltas (the agent's tool input as a JSON
   * string). Set as soon as the first ARGS chunk arrives; appended on
   * subsequent chunks; stable by TOOL_CALL_END. Consumers (e.g. the MCP
   * App router for AppRenderer.toolInput) JSON.parse it themselves. */
  argsJson?: string;
}

/**
 * One skill→skill delegation event (SKILL-DELEGATION M3), surfaced from the
 * backend `AGENT_DELEGATION` AG-UI Custom event. Drives both the transient
 * "Handing off…" indicator (via stageLabel) and a persistent transcript chip.
 */
export interface DelegationMarkerItem {
  id: string;
  /** Id of the last message present when the handoff fired; the chip renders
   * right after that message. Null if it fired before any message. */
  afterMessageId: string | null;
  parent: string;
  target: string;
  targetDisplay: string;
  /** The delegate skill's avatar — lets the transcript attribute each message
   * to the agent that produced it (the bot bubble mark changes on handoff).
   * Null when the delegate has no avatar. */
  avatar: string | null;
  mode: "auto" | "suggest";
  /** Wall-clock time (ms) the handoff fired — orders the Activity feed. */
  ts: number;
}

/**
 * One model-fallback event (MODEL-RELIABILITY M3), surfaced from the backend
 * `MODEL_FALLBACK` AG-UI Custom event. Rendered as a persistent transcript
 * notice — degradation is announced, never hidden (axiom #2/#5).
 */
/** A `HISTORY_COMPACTED` CUSTOM event: earlier turns were summarised away.
 * METADATA ONLY — the backend never puts summary text on the wire, because a
 * summary derives from customer conversation content. */
export interface CompactionNoticeItem {
  id: string;
  ts: number;
  eventsCompacted: number;
  summaryChars: number;
}

export interface FallbackNoticeItem {
  id: string;
  /** Id of the last message present when the fallback fired. */
  afterMessageId: string | null;
  fromModel: string;
  toModel: string;
  code: string;
  /** e.g. "provider_cooldown" when the primary was skipped, not tried. */
  reason?: string;
  ts: number;
}

export interface UseSkillAgentReturn {
  /** The HttpAgent's threadId — equal to the backend ChatSessionIndex id. */
  sessionId: string;
  messages: SkillMessage[];
  toolCalls: ToolCallState[];
  thinkingContent: string;
  isThinking: boolean;
  /**
   * Latest server-authored stage label ("Reading 2 documents…",
   * "Thinking…", "Calling search…") delivered via AG-UI STAGE_PROGRESS
   * Custom events from the backend LatencyTracker. Null between runs
   * and after the first model token has landed (the streaming bubble
   * takes over the UX). Decouples perceived TTFT from real TTFT —
   * see docs/design/v6.1.0/ttft-instrumentation.md.
   */
  stageLabel: string | null;
  /**
   * Persistent skill→skill delegation markers for this conversation, in the
   * order they fired (SKILL-DELEGATION M3). Each is rendered as a small inline
   * chip in the transcript after `afterMessageId`. `mode` distinguishes an
   * `auto` handoff ("Delegated to X") from a `suggest` proposal ("Proposed X").
   */
  delegations: DelegationMarkerItem[];
  /** Persistent model-fallback notices for this conversation (M3). */
  fallbacks: FallbackNoticeItem[];
  /** COMPACTION-WIRE M4 — history compactions this session. Compaction
   * silently rewrites what the model can remember, so it must be visible. */
  compactions: CompactionNoticeItem[];
  /** COMPACTION-LATENCY M2 — a compaction is running AFTER the answer finished.
   * The turn's output is complete; only housekeeping remains, so the composer is
   * re-enabled and this drives a quiet notice instead of the typing indicator. */
  tidyingUp: boolean;
  /** Model that ran the most recent turn (MODEL_RESOLVED), or null before the
   * first turn. Drives the Activity header so it reflects a router tier /
   * delegate model rather than the skill's static config (8.2). */
  resolvedModel: string | null;
  sendMessage: (
    text: string,
    opts?: { documentIds?: string[]; resumedSession?: boolean },
  ) => Promise<void>;
  isLoading: boolean;
  error: StreamError | null;
  clearError: () => void;
  stop: () => void;
}

function toSkillMessage(m: Message): SkillMessage | null {
  const role = (m as { role?: string }).role;
  if (!role || !["user", "assistant"].includes(role)) return null;
  const content = (m as { content?: unknown }).content;
  if (typeof content === "string") {
    return { id: m.id, role: role as SkillMessage["role"], content };
  }
  // Tool-only assistant turns (Gemini sometimes emits send_a2ui_json_to_client
  // with no text in chat). AG-UI sets content to undefined; without a
  // SkillMessage, the MessageBubble never renders and its tool-call
  // dispatchers never fire — so the workspace surface stays empty.
  // Render the bubble with empty text; tool calls render via their own slots.
  if (role === "assistant") {
    return { id: m.id, role: "assistant", content: "" };
  }
  return null;
}

function classifyError(err: unknown): StreamError {
  const msg = err instanceof Error ? err.message : String(err);
  const httpMatch = msg.match(/HTTP (\d+)/);
  if (httpMatch) {
    const status = parseInt(httpMatch[1]);
    if (status === 401)
      return { kind: "http", status, message: "Session expired — please refresh the page", retryable: false, rawMessage: msg };
    if (status === 404)
      return { kind: "http", status, message: "Skill not found", retryable: false, rawMessage: msg };
    if (status === 502)
      return { kind: "http", status, message: "Can't reach the server. Try again.", retryable: true, rawMessage: msg };
    if (status >= 500)
      return { kind: "http", status, message: "Something went wrong on our end. Try again.", retryable: true, rawMessage: msg };
    return { kind: "http", status, message: "Request failed. Try again.", retryable: true, rawMessage: msg };
  }
  return { kind: "network", message: "Connection lost. Try again.", retryable: true, rawMessage: msg };
}

function classifyRunError(event: unknown): StreamError {
  const msg =
    event && typeof event === "object" && "message" in event
      ? String((event as { message: unknown }).message)
      : "Agent run failed";
  // Sprint 2.12 — typed budget-exceeded branch. The backend's
  // skill_processor catches BudgetExceededError and emits a RUN_ERROR
  // with code="BUDGET_EXCEEDED" + the BudgetDecision's message +
  // retry_after_seconds as a passthrough field. The BudgetBanner
  // component renders the typed branch as a countdown banner instead
  // of the generic "Something went wrong" fallback.
  if (event && typeof event === "object" && "code" in event &&
      (event as { code: unknown }).code === "BUDGET_EXCEEDED") {
    const rawRetry = (event as { retry_after_seconds?: unknown }).retry_after_seconds;
    const retryAfterSeconds = typeof rawRetry === "number" ? rawRetry : undefined;
    return {
      kind: "budget_exceeded",
      message: msg,
      retryable: retryAfterSeconds !== undefined,
      rawMessage: msg,
      retryAfterSeconds,
    };
  }
  // MODEL-RELIABILITY M2 — typed model-provider error codes. The backend's
  // classifier (adk/model_errors.py) guarantees these four codes for any
  // Gemini/Claude/OpenAI failure that exhausts its options; copy is honest
  // about what happened and whether retrying can help.
  if (event && typeof event === "object" && "code" in event) {
    const code = (event as { code: unknown }).code;
    if (code === "MODEL_RATE_LIMITED") {
      const rawRetry = (event as { retry_after_seconds?: unknown }).retry_after_seconds;
      const retryAfterSeconds = typeof rawRetry === "number" ? Math.ceil(rawRetry) : undefined;
      return {
        kind: "rate_limited",
        message: retryAfterSeconds
          ? `The AI model is busy — retry in about ${retryAfterSeconds}s.`
          : "The AI model is busy right now. Try again shortly.",
        retryable: true,
        rawMessage: msg,
        retryAfterSeconds,
      };
    }
    if (code === "MODEL_UNAVAILABLE") {
      return {
        kind: "run_error",
        message: "The AI model for this skill is temporarily unavailable. Try again in a minute.",
        retryable: true,
        rawMessage: msg,
      };
    }
    if (code === "MODEL_AUTH_FAILED") {
      return {
        kind: "run_error",
        message: "This deployment's AI credentials were rejected — an operator needs to fix the configuration.",
        retryable: false,
        rawMessage: msg,
      };
    }
    if (code === "MODEL_REQUEST_INVALID") {
      return {
        kind: "run_error",
        message: "The AI model couldn't accept this request — it may be too large. Try a shorter message or fewer documents.",
        retryable: false,
        rawMessage: msg,
      };
    }
    // EMPTY_RUN — the model ended a turn with no reply and no tool call. Transient
    // (flash-lite does this intermittently). The hook auto-retries once; this
    // message only shows if the retry ALSO came back empty.
    if (code === "EMPTY_RUN") {
      return {
        kind: "run_error",
        message: "The assistant didn't reply — this is usually a transient model hiccup. Please try again.",
        retryable: true,
        rawMessage: msg,
      };
    }
  }
  // Gemini quota / rate limit. The model backend emits a RUN_ERROR whose
  // message carries the raw 429 (RESOURCE_EXHAUSTED / "Too Many Requests" /
  // "exceeded your current quota"). Surface it as its own clearly-worded branch
  // so a KEY/QUOTA problem is never mistaken for a broken skill — during the
  // workshop, free-tier keys routinely hit the per-minute cap.
  if (/429|RESOURCE_EXHAUSTED|Too Many Requests|exceeded your current quota|rate limit/i.test(msg)) {
    const retryMatch = msg.match(/retry(?:Delay)?["\s:]*(?:in\s*)?"?(\d+(?:\.\d+)?)\s*s/i);
    const retryAfterSeconds = retryMatch ? Math.ceil(parseFloat(retryMatch[1])) : undefined;
    const wait = retryAfterSeconds
      ? ` Wait ~${retryAfterSeconds}s and try again.`
      : " Wait a moment and try again.";
    return {
      kind: "rate_limited",
      message: `⏳ Rate limited — the Gemini API key hit its quota, not a problem with the demo.${wait}`,
      retryable: true,
      rawMessage: msg,
      retryAfterSeconds,
    };
  }
  return { kind: "run_error", message: "The agent encountered an error. Try again.", retryable: true, rawMessage: msg };
}

/**
 * Subscribe to the AG-UI `HttpAgent` from `AGUIProvider` and expose a
 * chat-shaped API. Streaming text deltas land via `onTextMessageContentEvent`;
 * `onRunFinalized` flips `isLoading` off.
 *
 * We mirror `agent.messages` to React state on every change so consumers see
 * fresh renders. The agent keeps the canonical list; we just copy it.
 */

// #12: search runs as an AgentTool / native grounding — no handoff chip — so a
// stage label is how it stays visible in the main chat. Keyed by the tool /
// sub-agent name AG-UI reports on TOOL_CALL_START. Non-search tools set no label
// (they show in the Activity tab).
const SEARCH_TOOL_LABELS: Record<string, string> = {
  ai_search: "Searching the library…",
  enterprise_search_agent: "Searching the library…",
  google_search: "Searching the web…",
  web_search_agent: "Searching the web…",
};
export function useSkillAgent(options?: {
  _hangTimeoutMs?: number;
  _midStreamTimeoutMs?: number;
}): UseSkillAgentReturn {
  const hangTimeoutMs = options?._hangTimeoutMs ?? 30_000;
  // MODEL-RELIABILITY M1: mid-stream stall detection. 90s of ZERO subscriber
  // traffic mid-run means the transport is dead — the backend heartbeats
  // every 20s of model silence, so a healthy-but-quiet stream always
  // produces events well inside this window.
  const midStreamTimeoutMs = options?._midStreamTimeoutMs ?? 90_000;
  const lastActivityRef = useRef(0);
  const agent = useAGUIAgent();
  // Sprint 2.10: read every active A2UI surface's snapshot at sendMessage
  // time and ride it back on `forwardedProps.a2ui_surface_state`. Optional
  // because useSkillAgent is also used in surface-registry-less contexts
  // (some tests, isolated embeds).
  const surfaceRegistry = useOptionalSurfaceRegistry();
  const [messages, setMessages] = useState<SkillMessage[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolCallState[]>([]);
  const [thinkingContent, setThinkingContent] = useState("");
  const [isThinking, setIsThinking] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [runStarted, setRunStarted] = useState(false);
  const [error, setError] = useState<StreamError | null>(null);
  const [stageLabel, setStageLabel] = useState<string | null>(null);
  const [delegations, setDelegations] = useState<DelegationMarkerItem[]>([]);
  const delegSeqRef = useRef(0);
  const [fallbacks, setFallbacks] = useState<FallbackNoticeItem[]>([]);
  const [compactions, setCompactions] = useState<CompactionNoticeItem[]>([]);
  const [tidyingUp, setTidyingUp] = useState(false);
  const fallbackSeqRef = useRef(0);
  // Resolved model for the most recent turn (MODEL_RESOLVED custom event). Lets
  // the Activity header show the model that ACTUALLY ran — a router thinking
  // tier, a fallback, or the front door's configured model — rather than the
  // static skill config. Null until the first turn reports it (8.2).
  const [resolvedModel, setResolvedModel] = useState<string | null>(null);

  const clearError = useCallback(() => setError(null), []);

  // Set to true by onRunFailed so the sendMessage catch block doesn't overwrite
  // the real run_error with a spurious "protocol violation" network error.
  // The backend sometimes emits RUN_FINISHED after RUN_ERROR; AG-UI's state
  // machine throws on that sequence, but the error is already handled.
  const runFailedRef = useRef(false);
  // Empty-run auto-retry (flash-lite intermittently ends a turn with no reply).
  // `pending` signals sendMessage to re-run; `count` caps it at one auto-retry
  // per user message. See onRunErrorEvent + sendMessage.
  const emptyRunPendingRef = useRef(false);
  const emptyRunRetryRef = useRef(0);
  // NEVER SILENT: a healthy run ALWAYS ends with a terminal event (RUN_FINISHED
  // or RUN_ERROR). If the stream is truncated — the serving instance dies, the
  // proxy fails to pipe (ECONNRESET), the network drops, the laptop sleeps — the
  // client just sees the stream end, `onRunFinalized` fires, isLoading clears and
  // NOTHING is shown. That's the silent dead-end (observed live 2026-07-21: an
  // instance was replaced mid-turn right after a web-search tool call). These
  // refs let onRunFinalized tell "clean finish" from "truncated".
  const sawTerminalRef = useRef(false);
  const abortedRef = useRef(false);

  // Snapshot of agent.messages.length taken at RUN_STARTED. Used by
  // onToolCallStartEvent to scope the assistant-text-message search to the
  // current run only — messages before this index belong to prior turns and
  // must not capture new tool calls (each turn's tools should land in that
  // turn's bubble via back-attribution at TEXT_MESSAGE_START).
  const runStartMessageCountRef = useRef<number>(0);

  // Track the agent instance so we re-subscribe when AGUIProvider rebuilds it
  // (e.g. after the Firebase ID token refreshes).
  const lastAgentRef = useRef(agent);

  useEffect(() => {
    // chat-history-deep-fixes v6.1.0: track whether `agent` is the same
    // reference as the previous render. F1's monotonic guard must yield
    // on agent-identity changes (AGUIProvider rebuild after URL writeback,
    // "+ New conversation", thread select) so the message list correctly
    // resets to the new agent's state. Without this, the OLD agent's
    // messages stay pinned in the UI and the user sees stale or empty
    // chats — exactly Bugs A, B, and C from chat-history-deep-fixes.md.
    const agentChanged = lastAgentRef.current !== agent;
    lastAgentRef.current = agent;

    const sync = (allowReset = false) => {
      const next = agent.messages
        .map(toSkillMessage)
        .filter((m): m is SkillMessage => m !== null);
      // F1 (chat-history-fixes v6.1.0): never shrink the rendered list
      // *while the agent is the same instance* — AG-UI's HttpAgent state
      // machine resets its internal `messages` array on certain
      // protocol-violation paths (e.g. RUN_FINISHED arriving after
      // RUN_ERROR), and we don't want those stutters to wipe the UI.
      // But when the agent itself was replaced, the shrink is legitimate
      // (new conversation, new session, token refresh) — yield then.
      setMessages((prev) => {
        if (!allowReset && next.length < prev.length) {
          console.warn(
            "useSkillAgent: agent.messages shrunk from",
            prev.length,
            "to",
            next.length,
            "— holding previous list (AG-UI protocol stutter, F1 guard).",
          );
          return prev;
        }
        return next;
      });
    };
    // First sync after agent change must allow a reset to the new
    // agent's (possibly empty) message list.
    sync(agentChanged);

    // Any subscriber event counts as stream traffic for the mid-stream
    // watchdog — including no-op HEARTBEATs, which exist for exactly this.
    const markActivity = () => {
      lastActivityRef.current = Date.now();
    };

    const sub = agent.subscribe({
      onMessagesChanged: () => {
        markActivity();
        sync();
      },
      onRunStartedEvent: () => {
        markActivity();
        setIsLoading(true);
        setRunStarted(true);
        // Per-run truncation tracking (see sawTerminalRef).
        sawTerminalRef.current = false;
        abortedRef.current = false;
        // Preserve completed tool calls from prior turns so their iframes
        // survive into the next turn. Only drop orphaned "running" entries
        // left over from an aborted previous run.
        setToolCalls((prev) => prev.filter((tc) => tc.status !== "running"));
        setThinkingContent("");
        setIsThinking(false);
        runStartMessageCountRef.current = agent.messages.length;
        recordFirstEvent(performance.now());
        // Don't reset stageLabel here — STAGE_PROGRESS for
        // before_agent_done/before_model_done can arrive *before*
        // RUN_STARTED on a slow loader, and we want the label to
        // survive the handshake so the user keeps seeing progress.
      },
      onCustomEvent: ({ event }: { event: { name?: unknown; value?: unknown } }) => {
        markActivity();
        // HEARTBEAT — no-op keep-alive emitted by the backend every ~20s of
        // model silence (MODEL-RELIABILITY M1). Its only job is the
        // markActivity() above; it must never touch visible state.
        if (event.name === "HEARTBEAT") return;
        // MODEL_RETRY — transient: the backend is backing off and retrying.
        // Reuses the stageLabel tier (auto-fades like stage progress).
        if (event.name === "MODEL_RETRY") {
          setStageLabel("Model busy — retrying…");
          return;
        }
        // MODEL_RESOLVED — the model that actually ran this turn. Drives the
        // Activity header so it reflects a router tier / delegate model, not the
        // front door's static config (8.2).
        if (event.name === "MODEL_RESOLVED" && event.value && typeof event.value === "object") {
          const v = event.value as { model?: unknown };
          if (typeof v.model === "string" && v.model) setResolvedModel(v.model);
          return;
        }
        // COMPACTION_STARTED — the answer is COMPLETE and a compaction is now
        // running. Measured at ~37s median (up to 47s) during which the answer
        // was already fully rendered while the composer sat disabled and the
        // typing indicator span. Release the user here: the run has not
        // finished, but their turn has.
        //
        // HISTORY_COMPACTED can't do this job — it fires when summarisation
        // RETURNS, i.e. ~35s later, at roughly the same moment as RUN_FINISHED.
        if (event.name === "COMPACTION_STARTED") {
          setIsLoading(false);
          setStageLabel(null);
          setTidyingUp(true);
          return;
        }
        // HISTORY_COMPACTED — earlier turns were summarised away to stay
        // within the context limit. Surfaced in Activity because the user
        // still sees the FULL transcript while the model now sees a summary;
        // without this, a degraded answer looks identical to a good one.
        if (event.name === "HISTORY_COMPACTED" && event.value && typeof event.value === "object") {
          const v = event.value as { events_compacted?: unknown; summary_chars?: unknown };
          setCompactions((prev) => [
            ...prev,
            {
              id: `compaction-${prev.length}`,
              ts: Date.now(),
              eventsCompacted: typeof v.events_compacted === "number" ? v.events_compacted : 0,
              summaryChars: typeof v.summary_chars === "number" ? v.summary_chars : 0,
            },
          ]);
          setTidyingUp(false);
          return;
        }
        // MODEL_FALLBACK — persistent transcript notice: a backup model is
        // answering. Honest degradation per axiom #5; mirrors AGENT_DELEGATION.
        if (event.name === "MODEL_FALLBACK" && event.value && typeof event.value === "object") {
          const v = event.value as { from_model?: unknown; to_model?: unknown; code?: unknown; reason?: unknown };
          setStageLabel("Switching to backup model…");
          const msgs = agent.messages;
          const afterMessageId = msgs.length ? (msgs[msgs.length - 1]?.id ?? null) : null;
          const seq = fallbackSeqRef.current++;
          setFallbacks((prev) => [
            ...prev,
            {
              id: `fb-${seq}`,
              afterMessageId,
              fromModel: String(v.from_model ?? "primary model"),
              toModel: String(v.to_model ?? "backup model"),
              code: String(v.code ?? "MODEL_UNAVAILABLE"),
              reason: typeof v.reason === "string" ? v.reason : undefined,
              ts: Date.now(),
            },
          ]);
          return;
        }
        // Server-authored Custom event types of interest:
        //   STAGE_PROGRESS  — per-stage label for the TypingIndicator
        //   LATENCY_REPORT  — final per-stage timings (only when ?probe=1)
        // Backend definitions in observability/timing.py.
        if (event.name === "STAGE_PROGRESS") {
          const value = event.value as { label?: unknown } | null | undefined;
          if (!value || typeof value.label !== "string") return;
          setStageLabel(value.label);
          recordFirstStageLabel(performance.now());
          return;
        }
        if (event.name === "LATENCY_REPORT" && event.value && typeof event.value === "object") {
          recordServerReport(event.value as Record<string, unknown>);
          return;
        }
        // AGENT_DELEGATION — a skill handed off (auto) or proposed a handoff
        // (suggest). Drives the transient "Handing off…" indicator (reusing the
        // stageLabel tier) plus a persistent transcript chip. See
        // observability/timing.py + docs/design/v6.7.0/skill-delegation.md.
        if (event.name === "AGENT_DELEGATION" && event.value && typeof event.value === "object") {
          const v = event.value as {
            parent?: unknown;
            target?: unknown;
            target_display?: unknown;
            mode?: unknown;
            avatar?: unknown;
          };
          const targetDisplay =
            typeof v.target_display === "string" && v.target_display
              ? v.target_display
              : String(v.target ?? "specialist");
          const mode = v.mode === "suggest" ? "suggest" : "auto";
          if (mode === "auto") setStageLabel(`Handing off to ${targetDisplay}…`);
          // Anchor the chip to the CURRENT TURN'S USER message, not the last
          // message of any role. The transparent front door hands off via
          // `transfer_to_agent`, so by the time this fires (the delegate's
          // before_agent callback) an ASSISTANT message already exists — the
          // front-door's transfer message — and the specialist's answer streams
          // into that same/next assistant message. Snapshotting the last
          // message of any role therefore drifts the chip BELOW the specialist's
          // answer, or misses the (still-streaming) assistant id entirely and
          // drops the chip into ChatMessageList's trailing group at the very
          // bottom. The user's request is stable and always precedes the
          // specialist's output, so anchoring there renders the marker ABOVE the
          // answer — "PPA Expert is now answering" — which is where the handoff
          // actually happened in the transcript.
          const msgs = agent.messages;
          let afterMessageId: string | null = null;
          for (let i = msgs.length - 1; i >= 0; i--) {
            if ((msgs[i] as { role?: string }).role === "user") {
              afterMessageId = msgs[i]?.id ?? null;
              break;
            }
          }
          const seq = delegSeqRef.current++;
          setDelegations((prev) => [
            ...prev,
            {
              id: `deleg-${seq}`,
              afterMessageId,
              parent: String(v.parent ?? ""),
              target: String(v.target ?? ""),
              targetDisplay,
              avatar: typeof v.avatar === "string" && v.avatar ? v.avatar : null,
              mode,
              ts: Date.now(),
            },
          ]);
        }
        // A2UI_SURFACE (tool-results-as-a2ui / 7.3, Model B) is handled by
        // `WorkspaceA2uiEventRouter` in ChatShell, NOT here: this hook runs
        // ABOVE the SurfaceRegistryProvider in the tree, so its
        // `useOptionalSurfaceRegistry()` is null and can't reach the registry.
        // The router subscribes to the same agent from inside the provider.
      },
      onTextMessageStartEvent: ({ event }: { event: { messageId?: string } }) => {
        markActivity();
        // First model token reached the wire — clear the stage label so
        // the UI handoff (TypingIndicator → StreamingBubble) is clean.
        setStageLabel(null);
        recordFirstTextChunk(performance.now());
        // F2a fix (part 2): back-attribute any tool calls whose parentMessageId
        // was deferred (tools-before-text ADK pattern). TOOL_CALL_START fires
        // before TEXT_MESSAGE_START, so the fallback snapshot in
        // onToolCallStartEvent can only find a text-content assistant message if
        // one already exists from a prior turn. For tool calls in the current
        // turn (where no prior text message existed at TOOL_CALL_START time),
        // parentMessageId is undefined — fix it now that we have the real id.
        const msgId = event.messageId;
        if (msgId) {
          setToolCalls((prev) =>
            prev.map((tc) =>
              tc.parentMessageId === undefined ? { ...tc, parentMessageId: msgId } : tc,
            ),
          );
        }
      },
      onReasoningStartEvent: () => {
        markActivity();
        setThinkingContent("");
        setIsThinking(true);
      },
      onReasoningMessageContentEvent: ({ reasoningMessageBuffer }: { reasoningMessageBuffer: string }) => {
        markActivity();
        setThinkingContent(reasoningMessageBuffer);
      },
      onReasoningEndEvent: () => {
        setIsThinking(false);
      },
      // A clean end-of-run terminal. Marks the run as properly terminated so
      // onRunFinalized can tell it apart from a truncated stream.
      onRunFinishedEvent: () => {
        sawTerminalRef.current = true;
      },
      onRunFinalized: () => {
        // NEVER SILENT: the run ended without any terminal event and without an
        // error already shown → the stream was cut (instance replaced, proxy pipe
        // failure/ECONNRESET, network drop). Surface it instead of just clearing
        // the spinner and leaving a dead turn on screen.
        if (!sawTerminalRef.current && !runFailedRef.current && !abortedRef.current) {
          console.warn("stream_truncated_no_terminal");
          setError({
            kind: "network",
            message: "The connection dropped before the assistant finished. Please try again.",
            retryable: true,
            rawMessage: "stream_truncated_no_terminal",
          });
          // A still-"running" tool call did NOT succeed — don't mark it success below.
          setToolCalls((prev) => prev.map((tc) => (tc.status === "running" ? { ...tc, status: "error" } : tc)));
          setIsLoading(false);
          setRunStarted(false);
          setStageLabel(null);
          return;
        }
        setIsLoading(false);
        setRunStarted(false);
        setStageLabel(null);
        // Resolve any still-running tool calls as success on clean finish
        setToolCalls((prev) =>
          prev.map((tc) => tc.status === "running" ? { ...tc, status: "success" } : tc),
        );
      },
      // A backend-emitted RUN_ERROR *event* (the common case: a Gemini 429,
      // a tool failure, a model error) is dispatched by the AG-UI client
      // (0.0.52) to `onRunErrorEvent` — NOT `onRunFailed`. The RUN_ERROR case
      // in the client runs the callback then completes the stream cleanly; it
      // never throws, so `onRunFailed`/`onError` don't fire and `runAgent`
      // resolves normally. Combined with the backend's terminal-dedup dropping
      // the trailing RUN_FINISHED, that meant a RUN_ERROR was SILENTLY EATEN —
      // the UI showed "thinking…" then went blank. Subscribing here surfaces
      // it. The payload wraps the raw event as `{ event }`; classify off its
      // own `message`/`code` (where the 429 text lives).
      onRunErrorEvent: ({ event }: { event: unknown }) => {
        // A RUN_ERROR IS a terminal event — the stream ended properly, so the
        // truncation guard in onRunFinalized must not also fire.
        sawTerminalRef.current = true;
        // EMPTY_RUN is a transient no-reply from the model. Auto-retry ONCE
        // (a fresh run) before surfacing anything — sendMessage drives the
        // re-run when it sees `emptyRunPendingRef`. Keep the loading state so
        // the user sees continuous progress, not a flash of error.
        const code = event && typeof event === "object" && "code" in event ? (event as { code: unknown }).code : null;
        if (code === "EMPTY_RUN" && emptyRunRetryRef.current < 1) {
          emptyRunRetryRef.current += 1;
          emptyRunPendingRef.current = true;
          console.warn("stream_empty_run_auto_retry", emptyRunRetryRef.current);
          return; // do NOT setError / stop loading — the retry handles it
        }
        runFailedRef.current = true;
        const streamErr = classifyRunError(event);
        console.warn("stream_run_error_event", streamErr);
        setError(streamErr);
        setIsLoading(false);
        setRunStarted(false);
        setStageLabel(null);
        setToolCalls((prev) =>
          prev.map((tc) => tc.status === "running" ? { ...tc, status: "error" } : tc),
        );
      },
      // Pipeline-level failure (an actual throw/reject inside the run — e.g. a
      // subscriber error). The client passes `{ error }` here, not a RUN_ERROR
      // event, so classify off `error` (an Error with `.message`), not the
      // wrapper. Genuine stream RUN_ERRORs come through onRunErrorEvent above.
      onRunFailed: ({ error }: { error: unknown }) => {
        runFailedRef.current = true;
        const streamErr = classifyRunError(error);
        console.warn("stream_run_failed", streamErr);
        setError(streamErr);
        setIsLoading(false);
        setRunStarted(false);
        setStageLabel(null);
        setToolCalls((prev) =>
          prev.map((tc) => tc.status === "running" ? { ...tc, status: "error" } : tc),
        );
      },
      onToolCallStartEvent: ({ event }: { event: { toolCallId: string; toolCallName: string; parentMessageId?: string } }) => {
        markActivity();
        // #12: surface web / library search in the MAIN chat status, not just the
        // Activity tab. Search runs as an AgentTool (or native grounding), so it
        // never emitted a handoff chip and read as "silent" — a stage label (like
        // "Handing off…") is the never-silent signal. Clears on text-start / run-end
        // via the existing setStageLabel(null) transitions.
        const searchLabel = SEARCH_TOOL_LABELS[event.toolCallName];
        if (searchLabel) setStageLabel(searchLabel);
        // F2a (2026-05-01): ADK doesn't emit parentMessageId on AG-UI
        // TOOL_CALL_START events. Without snapshotting at start time, every
        // unparented tool call inherits "latest assistant at render time" via
        // ChatMessageList's lastAssistantId fallback — so when turn 2 finalises,
        // turn 1's tool calls jump to turn 2's bubble and turn 1 loses its
        // iframe.
        // Scope to (a) string-content messages only — tool-call messages have
        // content:[] not a string — AND (b) messages added in the current run
        // (sliced at runStartMessageCountRef). Without the run-scope, prior
        // turns' text messages would capture this turn's tool calls.
        // When no current-run text message exists yet (ADK tools-before-text
        // pattern) the snapshot returns undefined; onTextMessageStartEvent
        // back-attributes all undefined-parent tool calls to the real id.
        const currentRunMessages = agent.messages.slice(runStartMessageCountRef.current);
        const fallbackParentId =
          event.parentMessageId ??
          [...currentRunMessages]
            .reverse()
            .find((m) => {
              const role = (m as { role?: string }).role;
              const content = (m as { content?: unknown }).content;
              return role === "assistant" && typeof content === "string";
            })?.id;
        setToolCalls((prev) => [
          ...prev,
          {
            id: event.toolCallId,
            name: event.toolCallName,
            status: "running",
            ts: Date.now(),
            parentMessageId: fallbackParentId,
          },
        ]);
      },
      onToolCallArgsEvent: ({ event }: { event: { toolCallId: string; delta: string } }) => {
        markActivity();
        // AG-UI emits ARGS as streaming deltas — concatenate into argsJson
        // so the final string is the complete JSON-encoded tool input by
        // the time TOOL_CALL_END fires. Consumers parse on read.
        setToolCalls((prev) =>
          prev.map((tc) =>
            tc.id === event.toolCallId
              ? { ...tc, argsJson: (tc.argsJson ?? "") + event.delta }
              : tc,
          ),
        );
      },
      onToolCallEndEvent: ({ event }: { event: { toolCallId: string } }) => {
        setToolCalls((prev) =>
          prev.map((tc) =>
            tc.id === event.toolCallId ? { ...tc, status: "success" } : tc,
          ),
        );
      },
      onToolCallResultEvent: ({ event }: { event: { toolCallId: string; content: string } }) => {
        setToolCalls((prev) =>
          prev.map((tc) =>
            tc.id === event.toolCallId ? { ...tc, resultContent: event.content } : tc,
          ),
        );
      },
    });
    return () => sub.unsubscribe();
    // surfaceRegistry is intentionally NOT a dependency: onCustomEvent reads it
    // as a stable (provider-memoized) reference. This effect calls
    // setMessages(newArray) on every run, so re-running it whenever a captured
    // value changes identity is a render loop — adding surfaceRegistry here OOMed
    // useSkillAgent.test under CI's singleFork pool. Keep the subscription keyed
    // on `agent` only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent]);

  // Delegation markers are per-conversation; reset when the thread changes
  // (new chat / switching sessions). Resumed history doesn't replay these
  // runtime events, so a clean slate per thread is correct.
  useEffect(() => {
    setDelegations([]);
    delegSeqRef.current = 0;
    setFallbacks([]);
    fallbackSeqRef.current = 0;
    // Compaction markers are per-conversation too — without this reset a new
    // chat would show 'History summarised' from the PREVIOUS thread, which is
    // worse than no marker: it implies context was lost when none was.
    setCompactions([]);
    setTidyingUp(false);
    // Tool calls are per-conversation too — this reset covered delegations and
    // fallbacks but NOT toolCalls, so starting a new chat carried the previous
    // conversation's tool calls into the Activity tab (a "web_search_agent"
    // from the last thread showing under a brand-new, empty conversation).
    setToolCalls([]);
    // Fall back to the skill's static config until the new thread's first turn
    // reports its model (ChatShell uses `resolvedModel ?? skillModel`).
    setResolvedModel(null);
  }, [agent.threadId]);

  // 30s watchdog: if loading starts but RUN_STARTED never fires, abort and surface error.
  useEffect(() => {
    if (!isLoading || runStarted) return;
    const timer = setTimeout(() => {
      agent.abortRun();
      setError({ kind: "network", message: "Connection lost. Try again.", retryable: true, rawMessage: "stream_hang_timeout_30s" });
      setIsLoading(false);
    }, hangTimeoutMs);
    return () => clearTimeout(timer);
  }, [isLoading, runStarted, agent, hangTimeoutMs]);

  // Mid-stream inactivity watchdog (MODEL-RELIABILITY M1): once the run has
  // started, sustained silence means the transport died — the backend emits
  // HEARTBEAT customs every ~20s of model silence, so a healthy stream always
  // produces *some* subscriber traffic well inside this window. Poll rather
  // than reset-a-timeout so subscriber callbacks stay cheap (a ref write).
  useEffect(() => {
    if (!isLoading || !runStarted) return;
    lastActivityRef.current = Date.now();
    const tickMs = Math.max(250, Math.floor(midStreamTimeoutMs / 6));
    const timer = setInterval(() => {
      if (Date.now() - lastActivityRef.current <= midStreamTimeoutMs) return;
      agent.abortRun();
      setError({
        kind: "network",
        message: "Connection stalled — no data received for a while. Try again.",
        retryable: true,
        rawMessage: `stream_stall_timeout_${Math.round(midStreamTimeoutMs / 1000)}s`,
      });
      setIsLoading(false);
    }, tickMs);
    return () => clearInterval(timer);
  }, [isLoading, runStarted, agent, midStreamTimeoutMs]);

  const sendMessage = useCallback(
    async (
      text: string,
      opts?: { documentIds?: string[]; resumedSession?: boolean },
    ) => {
      clearError();
      setRunStarted(false);
      setStageLabel(null);
      runFailedRef.current = false;
      emptyRunPendingRef.current = false;
      emptyRunRetryRef.current = 0;
      const userMessageId = crypto.randomUUID();
      // Latency mark t_send: anchored before agent.addMessage so
      // perceived TTFT (t_send → first DOM paint) measures the full
      // submit→render cycle, not just the network call. The HUD reads
      // these marks via the latencyStore.
      startMark(agent.threadId, userMessageId, performance.now());
      agent.addMessage({
        id: userMessageId,
        role: "user",
        content: text,
      } as Message);
      setIsLoading(true);
      try {
        const forwardedProps: Record<string, unknown> = {};
        if (opts?.documentIds && opts.documentIds.length > 0) {
          forwardedProps.document_ids = opts.documentIds;
        }
        if (opts?.resumedSession) {
          forwardedProps.resumed_session = true;
        }
        // Sprint 2.10: attach per-turn A2UI surface snapshot when any
        // surface is active. Omit the slot entirely when empty so the
        // wire stays clean and the backend extractor's `if isinstance
        // and raw` short-circuits without work.
        const surfaceSnapshot = surfaceRegistry?.readA2uiSurfaceState();
        if (surfaceSnapshot && Object.keys(surfaceSnapshot).length > 0) {
          forwardedProps.a2ui_surface_state = surfaceSnapshot;
        }
        const runInput = Object.keys(forwardedProps).length > 0
          ? { forwardedProps }
          : undefined;
        await agent.runAgent(runInput);
        // Empty-run auto-retry: an EMPTY_RUN deferred in onRunErrorEvent asks us
        // to re-run once. A fresh runAgent() is a new, protocol-valid run (no
        // duplicate user message — we don't re-addMessage). The retry cap lives
        // in onRunErrorEvent (emptyRunRetryRef < 1), so this loops at most once;
        // a second empty run falls through to a real error there.
        while (emptyRunPendingRef.current) {
          emptyRunPendingRef.current = false;
          await agent.runAgent(runInput);
        }
      } catch (err) {
        // If onRunFailed already fired, the real error is already set — don't
        // overwrite it with the AG-UI state-machine protocol exception that
        // the backend triggers by emitting RUN_FINISHED after RUN_ERROR.
        if (!runFailedRef.current) {
          const streamErr = classifyError(err);
          console.warn("stream_error", streamErr);
          setError(streamErr);
        }
      } finally {
        setIsLoading(false);
        setRunStarted(false);
      }
    },
    [agent, clearError, surfaceRegistry],
  );

  const stop = useCallback(() => {
    // A user-initiated stop ends the stream without a terminal event — that's
    // intentional, not a dropped connection, so suppress the truncation guard.
    abortedRef.current = true;
    agent.abortRun();
  }, [agent]);

  return {
    sessionId: agent.threadId,
    messages,
    toolCalls,
    thinkingContent,
    isThinking,
    stageLabel,
    delegations,
    fallbacks,
    compactions,
    tidyingUp,
    resolvedModel,
    sendMessage,
    isLoading,
    error,
    clearError,
    stop,
  };
}

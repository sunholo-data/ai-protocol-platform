// Workshop W5b — AG-UI: text events → chat bubbles
// ChatMessageList maps AG-UI messages[] from useSkillAgent to MessageBubble /
// StreamingBubble. All state transitions are driven by TEXT_MESSAGE_START /
// CONTENT / END events — no custom event types, no polling.
// Auto-scroll tracks whether the user is near the bottom; if they've scrolled
// up, a "↓ New message" badge appears instead of forcing them back down.
// See: docs/talks/workshop.md §W5

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { StreamError, SkillMessage, ToolCallState, DelegationMarkerItem, FallbackNoticeItem } from "@/hooks/useSkillAgent";
import { buildAgentMap } from "@/lib/messageAgent";
import { DelegationMarker } from "./DelegationMarker";
import { FallbackNotice } from "./FallbackNotice";
import type { ActiveDocumentContext } from "@/components/chat/ContextBanner";
import { ContextBanner } from "@/components/chat/ContextBanner";
import { MessageBubble } from "./MessageBubble";
import { StreamingBubble } from "./StreamingBubble";
import { TypingIndicator } from "./TypingIndicator";
import { AssistantIntroBubble } from "./AssistantIntroBubble";
import { PinnedWelcome } from "./PinnedWelcome";
import { ChatPlacementForm, useChatSurfaces, type ChatSurfaceItem } from "./ChatPlacementForms";
import type React from "react";

interface ChatMessageListProps {
  messages: SkillMessage[];
  initialMessages?: SkillMessage[];
  /** v6.4.0 4.5 SKILL-ONBOARDING M3 — synthetic first-turn assistant
   * intro shown when chat is fresh. Pure presentation; never serialised
   * into AG-UI stream nor session history. Naturally falls off once the
   * user sends their first real message (`messages.length > 0`). */
  introMessage?: string | null;
  /** Skill display name shown in the intro bubble headline. */
  skillDisplayName?: string;
  historyError?: string | null;
  /** Conversation is listed and had turns, but its transcript is gone from the
   * session store. Renders an explicit notice — resuming it would otherwise
   * show an unexplained blank thread (CLAUDE.md #8, NEVER SILENT). */
  transcriptUnavailable?: boolean;
  toolCalls: ToolCallState[];
  thinkingContent: string;
  isThinking: boolean;
  isLoading: boolean;
  error: StreamError | null;
  skillId: string;
  userInitial: string;
  userDisplayName: string;
  /** G34 (template-chat-surface-defaults.md): pass-through from chat page
   * to MessageBubble. Renders the user's Google profile photo when
   * present, falls back to userInitial chip otherwise. */
  userPhotoURL?: string | null;
  /** The active skill's avatar — rendered on each bot bubble so the speaking
   * skill matches its switcher logo (6.11). Null → brand mark. */
  botAvatarUrl?: string | null;
  activeDocumentContext?: ActiveDocumentContext | null;
  navigateToBlock?: (docId: string, blockId: string) => void;
  onAction: (event: { actionName: string; context: Record<string, unknown> }) => void;
  errorBanner?: React.ReactNode;
  /**
   * Server-authored stage label from AG-UI STAGE_PROGRESS Custom events,
   * surfaced inside the TypingIndicator. Decouples perceived TTFT from
   * real model TTFT — see docs/design/v6.1.0/ttft-instrumentation.md.
   */
  stageLabel?: string | null;
  /** Persistent skill→skill delegation markers (SKILL-DELEGATION M3),
   * rendered inline in the transcript after their `afterMessageId`. */
  delegations?: DelegationMarkerItem[];
  fallbacks?: FallbackNoticeItem[];
  /** MCP server IDs configured for the current skill (from
   * useSkillMeta.mcpServerIds) — passed to MessageBubble so
   * MCPAppToolCallRouter can attribute tool calls to a server and decide
   * which have a UI surface. Empty array if the skill has no MCP servers. */
  mcpServerIds?: readonly string[];
  /** Active iframe → host bridge: when an MCP App iframe sends a
   * notification, the adapter translates it to a chat string and this
   * callback (typically wired to useSkillAgent.sendMessage) appends it as
   * the next user turn. */
  onChatMessage?: (text: string) => void;
  /** Current chat session id — threaded to MessageBubble →
   * MCPAppToolCallRouter so iframe `ui/update-model-context` pushes can
   * POST to /api/proxy/api/sessions/{id}/iframe-context (sprint 1.25). */
  sessionId?: string | null;
  /**
   * Content rendered as the LAST item INSIDE the scrollable transcript — after
   * the messages/streaming bubble, so it scrolls WITH the conversation and the
   * history stays reachable above it.
   */
  trailingSlot?: React.ReactNode;
  /**
   * Chat-placement A2UI surfaces (obligation elicitation forms / result cards)
   * INTERLEAVED into the transcript BY CREATION TIME (7.8): each renders after
   * the last message whose `createdAt` ≤ the surface's; surfaces older than the
   * first message render before it. So a form created before a later message
   * appears ABOVE that message — true chronological order, not pinned to the
   * bottom. Omitted by non-registry consumers (rich-media/drawer) → no surfaces.
   */
  chatSurfaces?: ChatSurfaceItem[];
  /** REAL skill id (slug/uuid) the interleaved chat forms' surface-action-run is
   * scoped to. Distinct from `skillId` above, which is the display label passed
   * to MessageBubble. Falls back to `skillId` when omitted. */
  formSkillId?: string;
}

const SCROLL_THRESHOLD = 100;

// Stable empty tool-call array. MessageBubble is React.memo'd; passing a fresh
// `[]` literal (or a freshly-reduced array) for every message on every render
// breaks the shallow prop compare, so every finalized bubble re-runs its full
// markdown parse on every SSE token during streaming. Sharing one frozen
// reference keeps memo intact for the (common) no-tool-call case.
const EMPTY_TOOL_CALLS: ToolCallState[] = [];

// Window in which a live chat surface (elicitation/confirm form) re-anchors AFTER
// a same-turn assistant message that arrived just after its emit `createdAt`, so
// the form renders below the intro text rather than above it. Restricted to
// assistant messages, so a later USER turn never captures a stale form; generous
// enough to cover a slow narration streaming in after the tool result.
const SURFACE_TURN_GRACE_MS = 30_000;

// v6.4.0 4.5 SKILL-ONBOARDING M3: intro bubble shows only when chat is
// truly fresh (no resumed history + no live messages yet). Centralised
// gate so any consumer that supplies `introMessage` gets the right
// behaviour without duplicating the predicate.
function shouldShowIntro(
  introMessage: string | null | undefined,
  messages: SkillMessage[],
  initialMessages?: SkillMessage[],
): boolean {
  return Boolean(
    introMessage &&
      messages.length === 0 &&
      !(initialMessages && initialMessages.length > 0),
  );
}

export function ChatMessageList({
  messages,
  initialMessages,
  introMessage,
  skillDisplayName,
  historyError,
  transcriptUnavailable = false,
  toolCalls,
  thinkingContent,
  isThinking,
  isLoading,
  error,
  skillId,
  userInitial,
  userDisplayName,
  userPhotoURL,
  botAvatarUrl,
  activeDocumentContext,
  navigateToBlock,
  onAction,
  errorBanner,
  stageLabel,
  delegations,
  fallbacks,
  mcpServerIds,
  onChatMessage,
  sessionId,
  trailingSlot,
  chatSurfaces,
  formSkillId,
}: ChatMessageListProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  // First-seen client arrival time per message id — SkillMessage has no
  // timestamp, so this lets chat surfaces interleave chronologically (7.8).
  const messageSeenAtRef = useRef<Map<string, number>>(new Map());
  const [showScrollBadge, setShowScrollBadge] = useState(false);

  const noopNavigate = useCallback((_docId: string, _blockId: string) => {
    // stub: file-browser.md implements real navigation
  }, []);
  const navigate = navigateToBlock ?? noopNavigate;

  // "Stick to bottom" INTENT — the source of truth for auto-follow, rather than
  // recomputing isNearBottom() at each resize. Two bugs came from the latter:
  // (1) during fast streaming a `smooth` scroll leaves scrollTop lagging behind
  //     the growing content, so isNearBottom() reads false mid-stream and the
  //     view detaches; (2) on a long session load, scrollTop starts at 0 so
  //     isNearBottom() is false and the view never jumps to the latest message.
  // We instead track intent: true until the user scrolls up, re-armed when they
  // return to the bottom or a new session loads. Follow scrolls are INSTANT so
  // scrollTop always pins to the tail and stays attached.
  const stickToBottomRef = useRef(true);

  const isNearBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return true;
    return el.scrollTop + el.clientHeight >= el.scrollHeight - SCROLL_THRESHOLD;
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const el = scrollRef.current;
    if (el && typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight, behavior });
    }
    stickToBottomRef.current = true;
    setShowScrollBadge(false);
  }, []);

  // ResizeObserver: follow the tail whenever the user is stuck to the bottom
  // (streaming tokens, new messages, thinking content, late-loading history) —
  // no dependence on message count. Instant follow so it never falls behind.
  useEffect(() => {
    const inner = innerRef.current;
    if (!inner) return;
    const observer = new ResizeObserver(() => {
      if (stickToBottomRef.current) {
        scrollToBottom("auto");
      } else {
        setShowScrollBadge(true);
      }
    });
    observer.observe(inner);
    return () => observer.disconnect();
  }, [scrollToBottom]);

  // Jump to the latest message on session load / switch / reload. Keyed on
  // sessionId so it fires on first mount AND whenever a different session loads;
  // re-arms stick-to-bottom in case the user had scrolled up in the prior one.
  // rAF lets the loaded transcript paint before we pin to the bottom; the
  // ResizeObserver above then keeps up as async history finishes rendering.
  useEffect(() => {
    stickToBottomRef.current = true;
    const raf = requestAnimationFrame(() => scrollToBottom("auto"));
    return () => cancelAnimationFrame(raf);
  }, [sessionId, scrollToBottom]);

  const handleScroll = useCallback(() => {
    // The user's scroll position is the intent: near the bottom → keep
    // following; scrolled up → detach (and let the badge offer a way back).
    const near = isNearBottom();
    stickToBottomRef.current = near;
    if (near) setShowScrollBadge(false);
  }, [isNearBottom]);

  // Determine what to render as the last item
  const lastMessage = messages[messages.length - 1];
  const isStreaming =
    isLoading && lastMessage?.role === "assistant" && lastMessage.content.length > 0;
  const isTyping =
    isLoading && (!lastMessage || lastMessage.role !== "assistant" || lastMessage.content.length === 0);

  // Stable messages: all finalised (when streaming, exclude last assistant msg)
  const stableMessages = isStreaming ? messages.slice(0, -1) : messages;

  // Delegation markers (SKILL-DELEGATION M3) grouped by the message they follow.
  // Markers whose anchor isn't among the current messages (fired before any
  // message, or anchored to the still-streaming last message) render as a
  // trailing group just before the streaming bubble so none are lost.
  const delegList = delegations ?? [];
  // Per-message agent attribution (6.11): as the conversation hands off, each
  // bot message shows the avatar + name of the agent that produced it. The root
  // (session skill) is the default; auto delegations override for messages after
  // their anchor. History (initialMessages) keeps the root — live delegations
  // aren't replayed on resume.
  const rootAgent = { avatar: botAvatarUrl ?? null, label: null as string | null };
  const agentMap = buildAgentMap(
    messages.map((m) => m.id),
    delegList,
    rootAgent,
  );
  const attrFor = (id: string) => agentMap.get(id) ?? rootAgent;
  const stableIds = new Set(stableMessages.map((m) => m.id));
  const delegationsByAfter = new Map<string, DelegationMarkerItem[]>();
  const trailingDelegations: DelegationMarkerItem[] = [];
  for (const d of delegList) {
    if (d.afterMessageId && stableIds.has(d.afterMessageId)) {
      const arr = delegationsByAfter.get(d.afterMessageId) ?? [];
      arr.push(d);
      delegationsByAfter.set(d.afterMessageId, arr);
    } else {
      trailingDelegations.push(d);
    }
  }

  // Fallback notices (MODEL-RELIABILITY M3) — same anchoring strategy as
  // delegation markers: after their anchor message, else trailing.
  const fallbackList = fallbacks ?? [];
  const fallbacksByAfter = new Map<string, FallbackNoticeItem[]>();
  const trailingFallbacks: FallbackNoticeItem[] = [];
  for (const f of fallbackList) {
    if (f.afterMessageId && stableIds.has(f.afterMessageId)) {
      const arr = fallbacksByAfter.get(f.afterMessageId) ?? [];
      arr.push(f);
      fallbacksByAfter.set(f.afterMessageId, arr);
    } else {
      trailingFallbacks.push(f);
    }
  }

  // Chat-placement A2UI surfaces (7.8) interleaved BY arrival time: each surface
  // anchors after the LAST message that arrived at-or-before the surface's
  // createdAt; surfaces older than every message are "leading" (rendered before
  // the transcript). So a form created before a later message appears ABOVE it —
  // chronological, not pinned to the bottom. SkillMessage carries no timestamp,
  // so we record each message's client arrival time (first-seen) in a ref; both
  // that and surface.createdAt are client Date.now() values → comparable.
  for (const m of messages) {
    if (!messageSeenAtRef.current.has(m.id)) messageSeenAtRef.current.set(m.id, Date.now());
  }
  const messageArrival = (id: string) => messageSeenAtRef.current.get(id) ?? 0;
  const surfaceList = chatSurfaces ?? [];
  const surfacesByAfter = new Map<string, ChatSurfaceItem[]>();
  const leadingSurfaces: ChatSurfaceItem[] = [];
  for (const s of surfaceList) {
    let anchorId: string | null = null;
    for (const m of stableMessages) {
      if (messageArrival(m.id) <= s.createdAt) anchorId = m.id;
    }
    // Pull-forward (form-after-text): an elicitation/confirm surface is the TAIL
    // of an assistant turn, but its emit `createdAt` can beat the same-turn
    // assistant text (tool result emitted before the narration finishes
    // streaming) — so the raw createdAt anchor lands it after the USER message,
    // i.e. ABOVE the AI text. For a LIVE surface only (replayed history is
    // already chronological), re-anchor after the same-turn assistant message
    // that arrived just after it, so the card sits BELOW the intro text.
    if (!s.replayed) {
      for (const m of stableMessages) {
        if (
          m.role === "assistant" &&
          messageArrival(m.id) > s.createdAt &&
          messageArrival(m.id) <= s.createdAt + SURFACE_TURN_GRACE_MS
        ) {
          anchorId = m.id;
        }
      }
    }
    if (anchorId) {
      const arr = surfacesByAfter.get(anchorId) ?? [];
      arr.push(s);
      surfacesByAfter.set(anchorId, arr);
    } else {
      leadingSurfaces.push(s);
    }
  }
  // `mt-4` gives the card a consistent gap above it. It's load-bearing for the
  // INTERLEAVED case: those surfaces render as grandchildren inside a per-message
  // `display:contents` wrapper (see stableMessages.map), so the parent's
  // `space-y-4` never reaches them and the card would otherwise butt flush
  // against the bubble above. For `leadingSurfaces` (direct children) `mt-4`
  // matches the `space-y-4` value, so there's no double margin.
  const renderChatSurface = (s: ChatSurfaceItem) => (
    <div key={`surface-${s.surfaceId}`} className="mt-4 flex justify-start">
      <ChatPlacementForm
        surfaceId={s.surfaceId}
        submitted={s.submitted}
        sessionId={sessionId ?? null}
        skillId={formSkillId ?? skillId}
        isConfirm={s.isConfirm}
        title={s.title}
      />
    </div>
  );

  // Tool calls grouped by parentMessageId for use in MessageBubble.
  // chat-history-deep-fixes-3 / Bug G: when AG-UI emits a tool call without
  // a parentMessageId, attribute it to the most recent assistant message
  // rather than fall back to a shared "__unparented__" key — otherwise
  // every assistant bubble's lookup misses and lands on the same array,
  // and the chip renders inside every prior turn.
  // Memoized so an unchanged tool-call set yields stable per-message array
  // identities across renders — otherwise every SSE token rebuilds this map,
  // hands each MessageBubble a fresh array, and defeats React.memo (see
  // EMPTY_TOOL_CALLS). Recomputes only when the tool calls or the assistant
  // set actually change, not on every streamed token.
  const lastAssistantId = useMemo(
    () => [...stableMessages].reverse().find((m) => m.role === "assistant")?.id,
    [stableMessages],
  );
  const toolCallsByParent = useMemo(
    () =>
      toolCalls.reduce<Record<string, ToolCallState[]>>((acc, tc) => {
        const key = tc.parentMessageId ?? lastAssistantId ?? "__unparented__";
        acc[key] = [...(acc[key] ?? []), tc];
        return acc;
      }, {}),
    [toolCalls, lastAssistantId],
  );

  // Show the most recent running tool name in the TypingIndicator
  const activeToolName = toolCalls.find((tc) => tc.status === "running")?.name ?? null;

  return (
    <div className="relative flex flex-col flex-1 overflow-hidden">
      {activeDocumentContext !== undefined && (
        <ContextBanner context={activeDocumentContext ?? null} />
      )}

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto"
      >
        <div ref={innerRef} className="space-y-4 p-4">
          {historyError && (
            <p className="text-xs text-muted-foreground italic">{historyError}</p>
          )}

          {transcriptUnavailable && (
            <div
              role="status"
              data-testid="transcript-unavailable"
              className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-foreground"
            >
              <p className="font-medium">This conversation&rsquo;s messages are no longer available.</p>
              <p className="mt-1 text-xs text-muted-foreground">
                The conversation is still listed, but its transcript was removed from the
                session store and can&rsquo;t be recovered. You can keep chatting here — new
                messages will be saved.
              </p>
            </div>
          )}

          {/* v6.4.0 4.5 SKILL-ONBOARDING M3 / 2026-06-11 polish:
              Pinned foldable intro panel — always visible until the user
              collapses it, regardless of message count. Replaces the
              earlier AssistantIntroBubble-on-fresh-chat pattern; the
              bubble is kept as a fallback for legacy callers that pass
              `introMessage` but DON'T pass `skillId` (the pinned panel
              scopes its collapse-state key per skillId). */}
          {introMessage && skillId && (
            <PinnedWelcome
              content={introMessage}
              skillId={skillId}
              skillDisplayName={skillDisplayName}
            />
          )}
          {introMessage && !skillId && shouldShowIntro(introMessage, messages, initialMessages) && (
            <AssistantIntroBubble
              content={introMessage}
              skillName={skillDisplayName}
            />
          )}

          {initialMessages && initialMessages.length > 0 && (
            <>
              {initialMessages.map((m) => (
                <MessageBubble
                  key={m.id}
                  message={m}
                  skillId={skillId}
                  userInitial={userInitial}
                  userDisplayName={userDisplayName}
                  userPhotoURL={userPhotoURL}
                  botAvatarUrl={m.avatar ?? botAvatarUrl}
                  botLabel={m.agentLabel}
                  toolCalls={EMPTY_TOOL_CALLS}
                  navigateToBlock={navigate}
                  onAction={onAction}
                  mcpServerIds={mcpServerIds}
                  onChatMessage={onChatMessage}
                  sessionId={sessionId}
                  timestamp={m.createdAt}
                />
              ))}
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <div className="flex-1 border-t" />
                <span>Earlier in this conversation</span>
                <div className="flex-1 border-t" />
              </div>
            </>
          )}

          {messages.length === 0 &&
            !initialMessages?.length &&
            !introMessage &&
            !error &&
            !isLoading && (
              <p className="text-sm text-muted-foreground">
                Send a message to start the conversation.
              </p>
            )}

          {/* Chat surfaces created before the first message (7.8) — e.g. an
              analysis started from the launcher before any chat text. */}
          {leadingSurfaces.map(renderChatSurface)}

          {stableMessages.map((m) => {
            const markers = delegationsByAfter.get(m.id);
            return (
              <div key={m.id} className="contents">
                <MessageBubble
                  message={m}
                  skillId={skillId}
                  userInitial={userInitial}
                  userDisplayName={userDisplayName}
                  userPhotoURL={userPhotoURL}
                  botAvatarUrl={attrFor(m.id).avatar}
                  botLabel={attrFor(m.id).label}
                  toolCalls={toolCallsByParent[m.id] ?? EMPTY_TOOL_CALLS}
                  navigateToBlock={navigate}
                  onAction={onAction}
                  mcpServerIds={mcpServerIds}
                  onChatMessage={onChatMessage}
                  sessionId={sessionId}
                  // History carries createdAt; live messages fall back to their
                  // stable client first-seen (so the time doesn't tick forward).
                  timestamp={m.createdAt ?? messageArrival(m.id)}
                />
                {markers?.map((d) => (
                  <DelegationMarker key={d.id} targetDisplay={d.targetDisplay} mode={d.mode} />
                ))}
                {fallbacksByAfter.get(m.id)?.map((f) => (
                  <FallbackNotice key={f.id} fromModel={f.fromModel} toModel={f.toModel} reason={f.reason} />
                ))}
                {/* Chat surfaces created after this message but before the next
                    (7.8) — interleaved so the timeline is chronological. */}
                {surfacesByAfter.get(m.id)?.map(renderChatSurface)}
              </div>
            );
          })}

          {trailingDelegations.map((d) => (
            <DelegationMarker key={d.id} targetDisplay={d.targetDisplay} mode={d.mode} />
          ))}

          {trailingFallbacks.map((f) => (
            <FallbackNotice key={f.id} fromModel={f.fromModel} toModel={f.toModel} reason={f.reason} />
          ))}

          {isStreaming && lastMessage && (
            <StreamingBubble
              message={lastMessage}
              skillId={skillId}
              botAvatarUrl={attrFor(lastMessage.id).avatar}
              botLabel={attrFor(lastMessage.id).label}
              thinkingContent={thinkingContent}
              isThinking={isThinking}
            />
          )}

          {isTyping && (
            <TypingIndicator stageLabel={stageLabel} activeToolName={activeToolName} />
          )}

          {errorBanner && <div className="text-left">{errorBanner}</div>}

          {/* Trailing in-flow content (obligation elicitation form, 7.8) — the
              last item in the transcript, so it scrolls with the conversation
              and the ResizeObserver auto-scrolls it into view on arrival. */}
          {trailingSlot}
        </div>
      </div>

      {showScrollBadge && (
        <button
          type="button"
          onClick={() => scrollToBottom("smooth")}
          className="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full border border-border bg-background px-3 py-1 text-xs font-medium shadow-md hover:bg-muted"
        >
          ↓ New message
        </button>
      )}
    </div>
  );
}

/**
 * `ChatMessageList` wired to the SurfaceRegistry — reads chat-placement surfaces
 * and feeds them as `chatSurfaces` so they interleave into the transcript by
 * time (7.8). MUST be rendered inside `SurfaceRegistryProvider`. Use this in the
 * chat page instead of `ChatMessageList` directly; `ChatMessageList` stays
 * registry-agnostic for consumers (rich-media / drawer) that lack the provider.
 */
export function ChatMessageListWithSurfaces(props: ChatMessageListProps) {
  const chatSurfaces = useChatSurfaces();
  return <ChatMessageList {...props} chatSurfaces={chatSurfaces} />;
}

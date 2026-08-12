// Workshop W5b — AG-UI: text events → chat bubbles
// Each finalised TEXT_MESSAGE_END produces one immutable MessageBubble. The bubble
// is keyed by message.id (AG-UI messageId) so React never re-renders stable messages
// when a new streaming token arrives elsewhere.
// Bot path:  ChatMarkdown for text | TOOL_CALL_END routes to A2UIRenderer / MCPAppToolCallRouter / ToolCallChip
// User path: plain <p>
// A2UI arrives via TOOL_CALL_* events (send_a2ui_json_to_client tool) — not fenced blocks.
// MCP App UI surfaces are decided by the router from the tool DEFINITION's
// _meta.ui block (UI-by-reference), not the tool result text.
// See: docs/talks/workshop.md §W6c, docs/design/v6.1.0/a2ui-tool-delivery.md
// See: docs/design/v6.1.0/mcp-app-integrations.md

"use client";

import React, { useEffect } from "react";
import { A2UIRenderer } from "@/components/protocols/A2UIRenderer";
import { MCPAppToolCallRouter } from "@/components/protocols/MCPAppToolCallRouter";
import { BrandAvatar } from "@/components/chat/BrandAvatar";
import { parseA2uiSubmission } from "@/lib/a2uiSubmission";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";
import { InlineCitation } from "@/components/chat/InlineCitation";
import { ReadAloudButton } from "@/components/chat/ReadAloudButton";
import { ToolCallChip } from "@/components/chat/ToolCallChip";
import { useSurfaceRegistry } from "@/providers/SurfaceRegistry";
import { useVoiceConfig } from "@/hooks/useVoiceConfig";
import type { SkillMessage, ToolCallState } from "@/hooks/useSkillAgent";

/** Read-aloud is opt-in per deployment. Read once at module scope
 * (matches the NEXT_PUBLIC_* flag pattern used elsewhere, e.g.
 * NEXT_PUBLIC_SHOW_DEV_PROBES). When false the button never renders and
 * useVoiceConfig never fires a network call. */
const READ_ALOUD_ENABLED = process.env.NEXT_PUBLIC_ENABLE_READ_ALOUD === "true";

interface MessageBubbleProps {
  message: SkillMessage;
  skillId: string;
  userInitial: string;
  userDisplayName: string;
  /** G34 (template-chat-surface-defaults.md): Google-signed-in users
   * have a `photoURL` on the Firebase `User` object. When present we
   * render their avatar photo instead of the initial chip. Falls back
   * to the initial chip when null/undefined (anonymous-group users,
   * email/password signups without a photo, etc.). Pass-through from
   * the chat page; see `useAuth()`. */
  userPhotoURL?: string | null;
  /** The speaking skill's avatar — rendered as the bot bubble avatar so it
   * matches the skill switcher and users can tell which agent produced a
   * message. Falls back to the brand mark when null. */
  botAvatarUrl?: string | null;
  /** The speaking agent's display name for the bubble header — the delegate's
   * name for a handed-off message (6.11). Falls back to `skillId` when null. */
  botLabel?: string | null;
  toolCalls: ToolCallState[];
  navigateToBlock: (docId: string, blockId: string) => void;
  onAction: (event: { actionName: string; context: Record<string, unknown> }) => void;
  /** MCP server IDs configured for this skill — passed to the
   * MCPAppToolCallRouter so it can attribute tool calls to the right
   * server and decide whether they have a UI surface. Optional / empty
   * by default; until the chat page wires actual server IDs the router
   * is a no-op for everyone. */
  mcpServerIds?: readonly string[];
  /** Active iframe -> host bridge: chat strings produced by guest
   * notifications flow here. The chat page wires this to
   * useSkillAgent.sendMessage. */
  onChatMessage?: (text: string) => void;
  /** Current chat session id — threaded to MCPAppToolCallRouter so
   * iframe `ui/update-model-context` pushes can POST to
   * /api/proxy/api/sessions/{id}/iframe-context (sprint 1.25). */
  sessionId?: string | null;
  /** Bubble time (epoch ms) — the message's ORIGINAL send time. The transcript
   * resolves it (history createdAt, else the message's stable client first-seen)
   * so a resumed thread shows real send times, not the load time. Falls back to
   * now only when absent. */
  timestamp?: number;
}

/**
 * Parse the `send_a2ui_json_to_client` tool result envelope produced by
 * `backend/adk/a2ui.py::SurfaceAwareA2uiToolset`. The envelope shape is:
 *
 *   {
 *     "validated_a2ui_json": A2uiMessage[],  // v0.9 message array (createSurface | updateComponents | updateDataModel | deleteSurface)
 *     "surface_id":  "chat" | "workspace" | "sidebar" | "modal" | <custom>,  // optional
 *     "update_mode": "replace" | "patch",                                     // optional, surface routing hint (advisory)
 *   }
 *
 * `validated_a2ui_json` is whatever the SDK validator accepted. We don't
 * inspect message contents here — the SDK has already done structural
 * validation; downstream consumers feed the array to a v0.9 MessageProcessor.
 *
 * Returns `null` when the result is missing/malformed.
 */
export interface ParsedA2UIResult {
  messages: Record<string, unknown>[];
  surfaceId: string | undefined;
  updateMode: "replace" | "patch" | undefined;
}

export function parseA2UIResult(
  resultContent: string | undefined,
): ParsedA2UIResult | null {
  if (!resultContent) return null;
  try {
    const parsed = JSON.parse(resultContent) as Record<string, unknown>;
    const raw = parsed.validated_a2ui_json;
    if (raw === undefined || raw === null) return null;
    // SDK's parse_and_fix wraps a single message in a list before validation,
    // so we expect an array here. Accept a bare object defensively (treat as
    // a one-message array) so older envelopes don't crash the chat.
    const messages = Array.isArray(raw)
      ? (raw as Record<string, unknown>[])
      : [raw as Record<string, unknown>];
    if (messages.length === 0) return null;
    const surfaceId =
      typeof parsed.surface_id === "string" ? parsed.surface_id : undefined;
    const updateMode =
      parsed.update_mode === "patch" || parsed.update_mode === "replace"
        ? parsed.update_mode
        : undefined;
    return { messages, surfaceId, updateMode };
  } catch {
    return null;
  }
}

function formatTime(timestamp?: number): string {
  const when = typeof timestamp === "number" && Number.isFinite(timestamp) ? new Date(timestamp) : new Date();
  return new Intl.DateTimeFormat("en", {
    hour: "numeric",
    minute: "2-digit",
  }).format(when);
}

export const MessageBubble = React.memo(function MessageBubble({
  message,
  skillId,
  userInitial,
  userDisplayName,
  userPhotoURL,
  botAvatarUrl,
  botLabel,
  toolCalls,
  navigateToBlock,
  onAction,
  mcpServerIds,
  onChatMessage,
  sessionId,
  timestamp,
}: MessageBubbleProps) {
  const isBot = message.role === "assistant";
  const time = formatTime(timestamp);
  // A2UI form submissions arrive as `[a2ui:action] {json}` chat messages —
  // render them as a clean "Submitted …" summary, not raw JSON (6.11 polish).
  const submission = !isBot ? parseA2uiSubmission(message.content) : null;

  // Read-aloud voice config. The hook is called unconditionally (rules of
  // hooks); when the feature flag is off we pass null so no fetch fires.
  const voiceConfig = useVoiceConfig(READ_ALOUD_ENABLED ? skillId : null);
  // Per-skill opt-out: the skill's voice config can disable read-aloud even
  // when the global flag is on.
  const showReadAloud =
    READ_ALOUD_ENABLED && isBot && !!message.content && voiceConfig.tts.enabled;

  const A2UI_TOOL_NAME = "send_a2ui_json_to_client";

  if (isBot) {
    const a2uiCalls = toolCalls.filter((tc) => tc.name === A2UI_TOOL_NAME);
    const nonA2uiCalls = toolCalls.filter((tc) => tc.name !== A2UI_TOOL_NAME);
    // The router is a no-op for tool calls that don't match a UI binding,
    // so passing the full non-A2UI list is safe — anything without
    // _meta.ui simply renders nothing. ToolCallChip still shows for the
    // rest below.
    const mcpAppCandidates = nonA2uiCalls.filter((tc) => tc.resultContent);

    // Suppress the bubble entirely when the assistant turn has nothing to
    // show INLINE: no text, no inline A2UI (only surface-routed calls),
    // no MCP app candidates, no chip-rendered tool calls. The
    // A2UISurfaceDispatcher inside still ran when the SkillMessage existed
    // (its useEffect fired during render commit), so the workspace surface
    // is already populated by the time we return null. Without this, every
    // tool-only assistant turn renders an empty avatar+name+timestamp row.
    const inlineA2uiCalls = a2uiCalls.filter((tc) => {
      const parsed = parseA2UIResult(tc.resultContent);
      // Inline = no surface_id, or surface_id === "chat".
      return !parsed || !parsed.surfaceId || parsed.surfaceId === "chat";
    });
    const hasInlineContent =
      !!message.content ||
      inlineA2uiCalls.length > 0 ||
      mcpAppCandidates.length > 0 ||
      nonA2uiCalls.length > 0;
    if (!hasInlineContent) {
      // Still render the dispatcher so it fires the registry effect.
      return (
        <>
          {a2uiCalls.map((tc) => {
            const parsed = parseA2UIResult(tc.resultContent);
            if (!parsed || !parsed.surfaceId || parsed.surfaceId === "chat") return null;
            return (
              <A2UISurfaceDispatcher
                key={tc.id}
                surfaceId={parsed.surfaceId}
                messages={parsed.messages}
                sourceToolCallId={tc.id}
              />
            );
          })}
        </>
      );
    }

    return (
      <div className="flex items-start gap-3">
        {botAvatarUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={botAvatarUrl}
            alt={skillId}
            className="h-7 w-7 shrink-0 rounded-full border border-border bg-background object-cover"
          />
        ) : (
          <BrandAvatar />
        )}
        <div className="flex max-w-[80%] flex-col gap-1">
          <div className="flex items-baseline gap-2">
            <span className="text-xs font-medium text-orange-600">{botLabel || skillId}</span>
            <span className="text-xs text-muted-foreground">{time}</span>
            {showReadAloud && (
              <ReadAloudButton
                text={message.content}
                provider={voiceConfig.tts.provider}
                voice={voiceConfig.tts.voice}
                lang={voiceConfig.tts.language ?? "es"}
                skillId={skillId}
              />
            )}
          </div>
          <div className="space-y-2 rounded-[2px_8px_8px_8px] border-l-[3px] border-orange-400 bg-[hsl(0,0%,98%)] px-3 py-2 text-sm">
            {message.content && (
              <ChatMarkdown content={message.content} navigateToBlock={navigateToBlock} />
            )}
            {a2uiCalls.map((tc) => {
              const parsed = parseA2UIResult(tc.resultContent);
              if (!parsed) return null;
              // Routed-surface path: skills with surface_id != 'chat' push
              // their v0.9 messages into SurfaceRegistry; an
              // <A2UISurfaceMount> elsewhere in the layout owns the render.
              // The dispatcher is effect-only — the chat bubble shows
              // nothing for routed surfaces.
              if (parsed.surfaceId && parsed.surfaceId !== "chat") {
                return (
                  <A2UISurfaceDispatcher
                    key={tc.id}
                    surfaceId={parsed.surfaceId}
                    messages={parsed.messages}
                    sourceToolCallId={tc.id}
                  />
                );
              }
              // Inline-in-chat path — the renderer owns its own per-bubble
              // MessageProcessor + SurfaceModel; messages flow straight in.
              return (
                <A2UIRenderer
                  key={tc.id}
                  messages={parsed.messages}
                  fallbackSurfaceId={`inline-${tc.id}`}
                  onAction={(a) =>
                    onAction({ actionName: a.name, context: a.context })
                  }
                />
              );
            })}
            {mcpAppCandidates.length > 0 && (
              <MCPAppToolCallRouter
                toolCalls={mcpAppCandidates}
                mcpServerIds={mcpServerIds ?? []}
                onChatMessage={onChatMessage}
                sessionId={sessionId}
              />
            )}
            {nonA2uiCalls.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {nonA2uiCalls.map((tc) => (
                  <ToolCallChip key={tc.id} toolCall={tc} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // User bubble
  return (
    <div className="flex items-start justify-end gap-3">
      <div className="flex max-w-[80%] flex-col items-end gap-1">
        <div className="flex items-baseline gap-2">
          <span className="text-xs text-muted-foreground">{time}</span>
          <span className="text-xs font-medium text-foreground">{userDisplayName}</span>
        </div>
        <div className="rounded-[2px_8px_8px_8px] border-l-[3px] border-primary bg-muted/50 px-3 py-2 text-sm">
          {submission ? (
            <div className="flex flex-col gap-1.5" data-testid="a2ui-submission">
              <span className="flex items-center gap-1.5 text-xs font-medium text-primary">
                {/* check glyph (SVG, no emoji) */}
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M20 6 9 17l-5-5" />
                </svg>
                Submitted
              </span>
              {submission.fields.length > 0 && (
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                  {submission.fields.map((f) => (
                    <div key={f.key} className="contents">
                      <dt className="text-xs text-muted-foreground">{f.key}</dt>
                      <dd className="text-xs font-medium text-foreground">{f.value}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          ) : (
            <p className="whitespace-pre-wrap">{message.content}</p>
          )}
        </div>
      </div>
      {/* G34: prefer user.photoURL when present; fallback to initial chip.
          Both use theme tokens so a rebrand doesn't require touching this file. */}
      {userPhotoURL ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={userPhotoURL}
          alt={userDisplayName}
          className="h-7 w-7 shrink-0 rounded-full border border-border object-cover"
        />
      ) : (
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary ring-1 ring-primary/20">
          {userInitial}
        </div>
      )}
    </div>
  );
});


// ─── A2UI Surface Dispatcher ────────────────────────────────────────────────
// Effect-only React component — pushes a surface-targeted A2UI v0.9 message
// array into the SurfaceRegistry so the matching <A2UISurfaceMount> renders
// it. Returns null because the surface mount owns the rendering.
//
// Why a component rather than a hook inline in the .map()? React's Rules of
// Hooks forbid calling hooks inside .map() callbacks conditionally. Wrapping
// the dispatch in its own component keeps hook usage strictly at the top of
// the dispatcher's body.
//
// `update_mode` is no longer interpreted here — the v0.9 message stream
// encodes the mode itself (`updateComponents` replaces, `updateDataModel`
// patches the data model). The `update_mode` envelope key remains an
// advisory hint for surface authors / forks but does not change dispatch.

interface A2UISurfaceDispatcherProps {
  surfaceId: string;
  messages: Record<string, unknown>[];
  sourceToolCallId: string;
}

function A2UISurfaceDispatcher({
  surfaceId,
  messages,
  sourceToolCallId,
}: A2UISurfaceDispatcherProps) {
  const registry = useSurfaceRegistry();

  useEffect(() => {
    registry.appendMessages(surfaceId, messages, sourceToolCallId);
  }, [registry, surfaceId, messages, sourceToolCallId]);

  return null;
}

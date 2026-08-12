"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { User } from "@/lib/firebase";
import { useSkillAgent } from "@/hooks/useSkillAgent";
import { useSkillMeta } from "@/hooks/useSkillMeta";
import { useSessionMessages } from "@/hooks/useSessionMessages";
import { fetchWithAuth } from "@/lib/apiClient";
import { ChatMessageList } from "@/components/chat/ChatMessageList";

export interface DrawerChatPaneProps {
  skillId: string;
  pathPrefix: string;
  user: User;
  /** Composer placeholder — lets each shell phrase the prompt for its context. */
  placeholder?: string;
}

/**
 * v6.4.0 SHELL-MODES — focused chat for the non-chat-primary shells' drawers.
 *
 * Message list + composer wired to the shared agent, plus the URL session-pin
 * and best-effort bootstrap that keep a fresh chat resumable. Deliberately
 * lighter than ChatShell: no sidebar, doc-tabs, or workbench — in doc-compare
 * and workbench-primary modes the primary surface lives outside the drawer.
 * Shared by DocCompareShell (right drawer) and WorkbenchShell (left drawer).
 */
export function DrawerChatPane({
  skillId,
  pathPrefix,
  user,
  placeholder = "Message…",
}: DrawerChatPaneProps) {
  const {
    sessionId: agentSessionId,
    messages,
    toolCalls,
    thinkingContent,
    isThinking,
    stageLabel,
    sendMessage,
    isLoading,
    error,
    clearError,
    stop,
  } = useSkillAgent();
  const { displayName, mcpServerIds } = useSkillMeta(skillId);
  const searchParams = useSearchParams();
  const router = useRouter();
  const [draft, setDraft] = useState("");
  // Auto-grow the composer with its content (mirrors ChatShell). Reset to
  // `auto` first or scrollHeight can only ever grow, and the box never shrinks
  // back after a send.
  const draftRef = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const el = draftRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft]);

  const sessionId = searchParams.get("session");
  const enteredViaResume = useRef<boolean>(sessionId !== null).current;
  const { initialMessages, historyError } = useSessionMessages(sessionId);

  const navigateToSession = useCallback(
    (sid: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("session", sid);
      router.replace(`${pathPrefix}?${params.toString()}`);
    },
    [router, pathPrefix, searchParams],
  );
  useEffect(() => {
    if (!sessionId && agentSessionId && messages.length > 0) {
      navigateToSession(agentSessionId);
    }
  }, [sessionId, agentSessionId, messages.length, navigateToSession]);

  const bootstrappedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!agentSessionId || bootstrappedRef.current === agentSessionId) return;
    bootstrappedRef.current = agentSessionId;
    void fetchWithAuth(`/api/proxy/api/sessions/${agentSessionId}/bootstrap`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_id: skillId }),
    }).catch(() => {
      // best-effort; the chat still works if bootstrap is slow
    });
  }, [agentSessionId, skillId]);

  const userInitial = (user.displayName ?? user.email ?? "U").charAt(0).toUpperCase();
  const userDisplayName = user.displayName ?? user.email ?? "You";

  const handleAction = useCallback(
    (event: { actionName: string; context: Record<string, unknown> }) => {
      void sendMessage(`[a2ui:${event.actionName}] ${JSON.stringify(event.context)}`, {
        resumedSession: enteredViaResume,
      });
    },
    [sendMessage, enteredViaResume],
  );

  async function handleSend() {
    const text = draft.trim();
    if (!text || isLoading || error) return;
    setDraft("");
    await sendMessage(text, { resumedSession: enteredViaResume });
  }

  return (
    <div className="flex h-full flex-col">
      <ChatMessageList
        messages={messages}
        initialMessages={enteredViaResume ? initialMessages : undefined}
        historyError={historyError}
        skillDisplayName={displayName}
        toolCalls={toolCalls}
        thinkingContent={thinkingContent}
        isThinking={isThinking}
        isLoading={isLoading}
        error={error}
        skillId={displayName}
        userInitial={userInitial}
        userDisplayName={userDisplayName}
        userPhotoURL={user.photoURL}
        stageLabel={stageLabel}
        onAction={handleAction}
        mcpServerIds={mcpServerIds}
        sessionId={sessionId ?? agentSessionId}
        errorBanner={
          error ? (
            <div className="space-y-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <p>{error.message}</p>
              <button
                type="button"
                onClick={clearError}
                className="rounded border border-destructive/20 px-2 py-0.5 text-xs text-destructive/70 hover:bg-destructive/10"
              >
                Dismiss
              </button>
            </div>
          ) : undefined
        }
      />
      <footer className="border-t p-3">
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void handleSend();
          }}
        >
          {/* 2026-08-06 UAT (ONE): same single-line-<input> wrap bug as the
              main ChatShell composer — a long prompt scrolled sideways instead
              of wrapping. Kept identical to ChatShell's fix so the two
              composers behave the same: textarea + auto-grow, Enter sends,
              Shift+Enter newlines. */}
          <textarea
            ref={draftRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void handleSend();
              }
            }}
            rows={1}
            placeholder={placeholder}
            className="max-h-48 flex-1 resize-none overflow-y-auto rounded-md border px-3 py-2 text-sm"
            disabled={isLoading}
          />
          {isLoading ? (
            <button type="button" onClick={stop} className="rounded-md border px-3 py-2 text-sm">
              Stop
            </button>
          ) : (
            <button
              type="submit"
              className="rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground"
              disabled={!draft.trim()}
            >
              Send
            </button>
          )}
        </form>
      </footer>
    </div>
  );
}

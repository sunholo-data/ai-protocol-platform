"use client";

import { useEffect, useState } from "react";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";

interface PinnedWelcomeProps {
  /** Markdown body — typically the skill's `welcome.intro_message` or
   * legacy `initial_message` field. Empty string disables the component. */
  content: string;
  /** Skill id used to scope the collapse-state key so toggling on one
   * skill doesn't affect another. */
  skillId: string;
  /** Optional skill display name surfaced as part of the header label. */
  skillDisplayName?: string;
  /** Custom header label (overrides skillDisplayName-based default). */
  headerLabel?: string;
}

const KEY_PREFIX = "aitana.welcome.collapsed:";

/**
 * PinnedWelcome — collapsible header that pins the skill's intro / starter
 * prompts to the top of the chat shell. Stays visible across all messages
 * (unlike the one-shot {@link AssistantIntroBubble} that falls off after
 * the first user turn).
 *
 * Use this when the intro is genuine pedagogical scaffolding (e.g. "Open
 * the sim, then ask me a question") that you want students/users to be
 * able to re-reach after they've started talking. For a single ice-
 * breaker that should fall away once the conversation begins, prefer
 * AssistantIntroBubble.
 *
 * Collapse state is per-skill (so one skill's preference doesn't leak
 * into another) and persists in sessionStorage. Ported from CPH UNI's
 * AIPLA fork 2026-06-11; key prefix renamed to `aitana.welcome.collapsed:`
 * so existing AIPLA users don't clobber upstream state via shared origin.
 */
export function PinnedWelcome({
  content,
  skillId,
  skillDisplayName,
  headerLabel,
}: PinnedWelcomeProps) {
  const [collapsed, setCollapsed] = useState(false);

  // Restore collapse state in an effect (not as initial useState value)
  // so SSR doesn't read sessionStorage and React doesn't hydrate mismatch.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.sessionStorage.getItem(KEY_PREFIX + skillId);
    if (stored === "1") setCollapsed(true);
  }, [skillId]);

  if (!content) return null;

  const toggle = () => {
    setCollapsed((v) => {
      const next = !v;
      if (typeof window !== "undefined") {
        window.sessionStorage.setItem(KEY_PREFIX + skillId, next ? "1" : "0");
      }
      return next;
    });
  };

  const label =
    headerLabel ?? `How to get started${skillDisplayName ? ` with ${skillDisplayName}` : ""}`;

  return (
    <div className="border-b bg-muted/30">
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center gap-2 px-4 py-2 text-left text-xs font-medium text-muted-foreground hover:bg-muted"
        aria-expanded={!collapsed}
        aria-controls="pinned-welcome-body"
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={collapsed ? "" : "rotate-90"}
          aria-hidden="true"
        >
          <polyline points="4 2 8 6 4 10" />
        </svg>
        <span>{label}</span>
      </button>
      {!collapsed && (
        <div id="pinned-welcome-body" className="px-4 pb-4 text-sm text-foreground">
          <ChatMarkdown content={content} navigateToBlock={() => {}} />
        </div>
      )}
    </div>
  );
}

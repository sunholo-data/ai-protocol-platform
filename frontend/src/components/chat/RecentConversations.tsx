"use client";

import { ArrowRight, MessageSquare } from "lucide-react";
import type { ChatSessionSummary } from "@/hooks/useSkillSessions";
import { relativeTime } from "@/lib/relativeTime";

interface RecentConversationsProps {
  sessions: ChatSessionSummary[];
  onSelectSession: (sessionId: string) => void;
  /** Open the full history (the sidebar's "Past conversations"). */
  onShowAll: () => void;
  max?: number;
  /** Heading; "Recent conversations" unless the list is narrower (a project's). */
  title?: string;
}

/**
 * "Recent conversations" on the fresh-chat landing, under the composer.
 *
 * Customer feedback 2026-10-20: "When opening a new chat, it would be useful to
 * have the option to return to an older conversation and continue working from
 * a previous prompt." History existed, but only in a sidebar that starts closed,
 * behind an icon-only toggle — the landing is where the user actually is when
 * they want to go back, so the way back belongs here.
 *
 * Renders nothing until there is something to show: a first-time user sees the
 * same landing as before, and a load in progress doesn't shift the composer.
 */
export function RecentConversations({
  sessions,
  onSelectSession,
  onShowAll,
  max = 4,
  title = "Recent conversations",
}: RecentConversationsProps) {
  // A conversation whose messages are gone is a dead end — never offer it as
  // a way to "continue". It is still listed (and labelled) in the sidebar.
  const rows = sessions.filter((s) => !s.transcript_lost).slice(0, max);
  if (rows.length === 0) return null;

  return (
    <section aria-labelledby="recent-conversations-heading" className="space-y-2" data-testid="recent-conversations">
      <div className="flex items-baseline justify-between gap-4">
        <h2 id="recent-conversations-heading" className="text-sm font-medium text-muted-foreground">
          {title}
        </h2>
        <button
          type="button"
          onClick={onShowAll}
          className="rounded text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
        >
          View all
        </button>
      </div>
      <ul className="grid gap-2 sm:grid-cols-2">
        {rows.map((s, i) => {
          const title = s.title || "Untitled chat";
          return (
            <li
              key={s.session_id}
              className="animate-in fade-in-0 slide-in-from-bottom-1 fill-mode-both"
              style={{ animationDelay: `${i * 40}ms`, animationDuration: "300ms" }}
            >
              <button
                type="button"
                onClick={() => onSelectSession(s.session_id)}
                title={`Continue “${title}”`}
                className="group flex w-full items-center gap-3 rounded-lg border border-border bg-background/80 px-3 py-2.5 text-left shadow-sm backdrop-blur transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/50 hover:shadow-md focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              >
                <MessageSquare className="h-4 w-4 shrink-0 text-muted-foreground group-hover:text-primary" aria-hidden />
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="line-clamp-1 text-sm font-medium text-foreground group-hover:text-primary">{title}</span>
                  <span className="line-clamp-1 text-xs text-muted-foreground">
                    {[s.skill_label, relativeTime(s.last_message_at)].filter(Boolean).join(" · ")}
                  </span>
                </span>
                <ArrowRight
                  className="h-4 w-4 shrink-0 -translate-x-1 text-muted-foreground opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100 group-focus-visible:opacity-100"
                  aria-hidden
                />
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

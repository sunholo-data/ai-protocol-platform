"use client";

import type { ChatSessionSummary } from "@/hooks/useSkillSessions";

interface SkillSessionPanelProps {
  sessions: ChatSessionSummary[];
  activeSessionId: string | null;
  isLoading: boolean;
  onSelectSession: (sessionId: string) => void;
  /** Owner-only delete affordance. When provided, a trash icon appears on
   * the user's own session rows; click invokes ``onDelete(session_id)``.
   * Parent owns confirm + DELETE + refetch. Mirrors the per-document
   * panel's pattern from sprint 1.17. */
  onDelete?: (sessionId: string) => void;
  /** Current agent filter (skillId), or null for "All agents". When provided
   * alongside `onFilterChange`, a filter row renders above the list. The list
   * is cross-skill by default — a sitting that moved between agents is split
   * into one session per skill, so scoping to the current agent hid most of
   * the user's own history (2026-08-05). */
  skillFilter?: string | null;
  onFilterChange?: (skillId: string | null) => void;
}

function SessionSkeleton() {
  // Compact: 7px rows mirror the new single-line session item height.
  return (
    <div className="space-y-1 p-1" aria-label="Loading sessions">
      {[1, 2, 3].map((i) => (
        <div key={i} className="h-7 animate-pulse rounded bg-muted" />
      ))}
    </div>
  );
}

function relativeTime(iso: string): string {
  try {
    const diffMs = Date.now() - new Date(iso).getTime();
    const diffMin = Math.floor(diffMs / 60_000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffH = Math.floor(diffMin / 60);
    if (diffH < 24) return `${diffH}h ago`;
    return `${Math.floor(diffH / 24)}d ago`;
  } catch {
    return "";
  }
}

/** Distinct agents present in the list, for the filter dropdown. Built from the
 * rows themselves so it only ever offers agents the user actually has history
 * with, and only ever shows friendly names — never a skillId (CLAUDE.md #9). */
function agentOptions(sessions: ChatSessionSummary[]): Array<{ id: string; label: string }> {
  const seen = new Map<string, string>();
  for (const s of sessions) {
    if (s.skill_label && !seen.has(s.skill_id)) seen.set(s.skill_id, s.skill_label);
  }
  return [...seen.entries()]
    .map(([id, label]) => ({ id, label }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

export function SkillSessionPanel({
  sessions,
  activeSessionId,
  isLoading,
  onSelectSession,
  onDelete,
  skillFilter = null,
  onFilterChange,
}: SkillSessionPanelProps) {
  // The filter must survive an empty result — otherwise filtering to an agent
  // with no conversations strands the user with no way back to "All agents".
  const options = agentOptions(sessions);
  const showFilter = Boolean(onFilterChange) && (options.length > 1 || skillFilter !== null);

  const filterRow = showFilter ? (
    <div className="px-2 pb-1 pt-0.5">
      <select
        aria-label="Filter conversations by agent"
        value={skillFilter ?? ""}
        onChange={(e) => onFilterChange?.(e.target.value || null)}
        className="w-full rounded border bg-transparent px-1 py-0.5 text-[11px] text-muted-foreground"
      >
        <option value="">All agents</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
        {/* A filter pinned to an agent absent from the current page still needs
            its own option, or the select would silently snap to "All agents". */}
        {skillFilter && !options.some((o) => o.id === skillFilter) && (
          <option value={skillFilter}>Selected agent</option>
        )}
      </select>
    </div>
  ) : null;

  if (isLoading) {
    return (
      <>
        {filterRow}
        <SessionSkeleton />
      </>
    );
  }

  if (sessions.length === 0) {
    return (
      <>
        {filterRow}
        <div className="p-3 text-xs text-muted-foreground">
          {skillFilter ? "No conversations with this agent" : "No previous sessions"}
        </div>
      </>
    );
  }

  return (
    <>
      {filterRow}
      <nav aria-label="Session history" className="flex flex-col p-1">
        {sessions.map((s) => {
        const isActive = s.session_id === activeSessionId;
        const title = s.title ?? `Session ${s.session_id.slice(0, 8)}`;
        return (
          <div
            key={s.session_id}
            className={[
              "group flex w-full items-center gap-1 rounded px-1 transition-colors",
              "hover:bg-accent hover:text-accent-foreground",
              isActive ? "bg-accent font-medium text-accent-foreground" : "text-muted-foreground",
            ].join(" ")}
          >
            <button
              type="button"
              onClick={() => onSelectSession(s.session_id)}
              className="flex min-w-0 flex-1 items-baseline justify-between gap-2 px-1.5 py-1 text-left"
              aria-current={isActive ? "true" : undefined}
              title={title}
            >
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="line-clamp-1 text-xs">{title}</span>
                {/* v6.23.0 B5 — a conversation whose stored messages are gone.
                    Opening it already says so; saying it HERE means the user
                    doesn't have to click a dead row to find out. Measured on
                    test 2026-08-10: 14 of the 100 most recent sessions, 6 of
                    them ONE's, all predating the 2026-08-05 sweep fix. */}
                {s.transcript_lost ? (
                  <span
                    data-testid="session-transcript-lost"
                    className="line-clamp-1 text-[10px] text-amber-600 dark:text-amber-500"
                    title="This conversation's messages are no longer available and can't be recovered."
                  >
                    Messages unavailable
                  </span>
                ) : (
                  s.skill_label && (
                    <span
                      data-testid="session-agent-label"
                      className="line-clamp-1 text-[10px] opacity-60"
                    >
                      {s.skill_label}
                    </span>
                  )
                )}
              </span>
              <span className="shrink-0 text-[10px] opacity-60">
                {relativeTime(s.last_message_at)}
              </span>
            </button>
            {onDelete && s.is_owner && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(s.session_id);
                }}
                aria-label={`Delete ${title}`}
                title="Delete"
                className="shrink-0 rounded p-1 text-gray-400 opacity-0 hover:bg-red-100 hover:text-red-600 group-hover:opacity-100"
              >
                <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                  <path d="M3 4h10M5 4v9a1 1 0 0 0 1 1h4a1 1 0 0 0 1-1V4M7 4V3a1 1 0 0 1 1-1h0a1 1 0 0 1 1 1v1" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            )}
            </div>
          );
        })}
      </nav>
    </>
  );
}

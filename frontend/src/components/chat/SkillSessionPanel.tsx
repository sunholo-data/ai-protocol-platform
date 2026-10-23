"use client";

import { useMemo, useState, type ReactNode } from "react";
import type { ChatSessionSummary } from "@/hooks/useSkillSessions";
import { groupByDate, relativeTime } from "@/lib/relativeTime";

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
  /** Current agent filter (skillId), or null for "All skills". When provided
   * alongside `onFilterChange`, a filter row renders above the list. The list
   * is cross-skill by default — a sitting that moved between agents is split
   * into one session per skill, so scoping to the current agent hid most of
   * the user's own history (2026-08-05). */
  skillFilter?: string | null;
  onFilterChange?: (skillId: string | null) => void;
  /** Show whose conversation each row is (`owner_name`) — used by the
   * "Shared with me" list, where every row belongs to someone else. */
  showOwner?: boolean;
  /** Accessible name for the list. Defaults to "Session history". */
  ariaLabel?: string;
  /** Group rows under Today / Yesterday / Previous 7 days / … headings. */
  groupByDate?: boolean;
  /** A load failure. Rendered with Retry (NEVER SILENT #8) — it used to read
   * as "No previous sessions", which tells the user their history is gone. */
  error?: string | null;
  onRetry?: () => void;
  /** Older conversations exist beyond the loaded pages. */
  hasMore?: boolean;
  isLoadingMore?: boolean;
  /** The last "load more" failed — offer a retry under the loaded rows. */
  loadMoreFailed?: boolean;
  onLoadMore?: () => void;
  /** Extra line under a row's title — e.g. a project chat's "used EDP.pdf",
   * which says why it is listed. Null for none. */
  rowNote?: (session: ChatSessionSummary) => string | null;
  /** Extra controls for one of the user's OWN rows, before Delete — e.g. the
   * projects ⋯ menu ("Make this a project", "Move to project…"). */
  rowActions?: (session: ChatSessionSummary) => ReactNode;
  /** Replaces the default empty-list text (the filter has its own). */
  emptyMessage?: string;
}

/** Below this many rows a search box is clutter, not help. */
const SEARCH_THRESHOLD = 6;

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
  showOwner = false,
  ariaLabel = "Session history",
  groupByDate: grouped = false,
  error = null,
  onRetry,
  hasMore = false,
  isLoadingMore = false,
  loadMoreFailed = false,
  onLoadMore,
  rowNote,
  rowActions,
  emptyMessage,
}: SkillSessionPanelProps) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const visible = useMemo(
    () =>
      q
        ? sessions.filter((s) =>
            [s.title || "Untitled chat", s.skill_label ?? "", s.owner_name ?? ""].some((f) =>
              f.toLowerCase().includes(q),
            ),
          )
        : sessions,
    [sessions, q],
  );
  // The filter must survive an empty result — otherwise filtering to an agent
  // with no conversations strands the user with no way back to "All skills".
  const options = agentOptions(sessions);
  const showFilter = Boolean(onFilterChange) && (options.length > 1 || skillFilter !== null);

  const filterRow = showFilter ? (
    <div className="px-2 pb-1 pt-0.5">
      <select
        aria-label="Filter conversations by agent"
        value={skillFilter ?? ""}
        onChange={(e) => onFilterChange?.(e.target.value || null)}
        className="min-h-[44px] w-full rounded-[var(--radius)] border bg-background px-2 text-sm text-foreground md:min-h-[32px] md:text-xs"
      >
        <option value="">All skills</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
        {/* A filter pinned to an agent absent from the current page still needs
            its own option, or the select would silently snap to "All skills". */}
        {skillFilter && !options.some((o) => o.id === skillFilter) && (
          <option value={skillFilter}>Selected agent</option>
        )}
      </select>
    </div>
  ) : null;

  const searchRow =
    sessions.length >= SEARCH_THRESHOLD || q ? (
      <div className="px-2 pb-1 pt-0.5">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search conversations"
          aria-label="Search conversations"
          className="min-h-[44px] w-full rounded-[var(--radius)] border bg-background px-2 text-sm text-foreground placeholder:text-muted-foreground md:min-h-[32px] md:text-xs"
        />
      </div>
    ) : null;

  // Only a FIRST load shows skeletons; a background refresh (window focus,
  // another panel's delete) keeps the list on screen instead of blinking.
  if (isLoading && sessions.length === 0) {
    return (
      <>
        {filterRow}
        <SessionSkeleton />
      </>
    );
  }

  if (error && sessions.length === 0) {
    return (
      <>
        {filterRow}
        <div role="alert" className="flex items-center justify-between gap-2 p-3 text-xs text-destructive">
          <span>Couldn&apos;t load your conversations.</span>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="shrink-0 rounded border px-2 py-0.5 text-foreground hover:bg-accent"
            >
              Retry
            </button>
          )}
        </div>
      </>
    );
  }

  if (sessions.length === 0) {
    return (
      <>
        {filterRow}
        <div className="p-3 text-xs text-muted-foreground">
          {skillFilter
            ? "No conversations with this agent yet"
            : (emptyMessage ?? "No conversations yet — they'll appear here so you can pick up where you left off.")}
        </div>
      </>
    );
  }

  const sections = grouped ? groupByDate(visible, (s) => s.last_message_at) : [{ group: null, items: visible }];

  return (
    <>
      {filterRow}
      {searchRow}
      <nav aria-label={ariaLabel} className="flex flex-col p-1">
        {visible.length === 0 && (
          <div className="p-2 text-xs text-muted-foreground">
            Nothing matches &ldquo;{query.trim()}&rdquo;
            {hasMore ? " yet — show older conversations to search further back." : "."}
          </div>
        )}
        {sections.map(({ group, items }) => (
          <div key={group ?? "all"} role={group ? "group" : undefined} aria-label={group ?? undefined}>
            {group && (
              <div className="px-2 pb-0.5 pt-2 text-[10px] font-medium text-muted-foreground first:pt-0.5">{group}</div>
            )}
            {items.map((s) => {
              const isActive = s.session_id === activeSessionId;
              // Never a raw id (CLAUDE.md #9): "Session 24ea2b56" tells a human
              // nothing. The agent label and time underneath identify the row.
              const title = s.title || "Untitled chat";
              const note = rowNote?.(s) ?? null;
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
                    className="flex min-h-[44px] min-w-0 flex-1 items-center justify-between gap-2 px-2 py-1.5 text-left md:min-h-0 md:items-baseline md:px-1.5 md:py-1"
                    aria-current={isActive ? "true" : undefined}
                    title={title}
                  >
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="line-clamp-1 text-sm md:text-xs">{title}</span>
                      {/* v6.23.0 B5 — a conversation whose stored messages are gone.
                          Opening it already says so; saying it HERE means the user
                          doesn't have to click a dead row to find out. Measured on
                          test 2026-08-10: 14 of the 100 most recent sessions, 6 of
                          them the tenant's, all predating the 2026-08-05 sweep fix. */}
                      {s.transcript_lost ? (
                        <span
                          data-testid="session-transcript-lost"
                          className="line-clamp-1 text-[10px] text-amber-600 dark:text-amber-500"
                          title="This conversation's messages are no longer available and can't be recovered."
                        >
                          Messages unavailable
                        </span>
                      ) : (
                        <>
                          {showOwner && s.owner_name && (
                            <span
                              data-testid="session-owner-name"
                              className="line-clamp-1 text-[10px] opacity-80"
                            >
                              {s.owner_name}
                            </span>
                          )}
                          {s.skill_label && (
                            <span
                              data-testid="session-agent-label"
                              className="line-clamp-1 text-[10px] opacity-60"
                            >
                              {s.skill_label}
                            </span>
                          )}
                          {note && (
                            <span data-testid="session-row-note" className="line-clamp-1 text-[10px] opacity-70">
                              {note}
                            </span>
                          )}
                        </>
                      )}
                    </span>
                    <span className="shrink-0 text-[10px] opacity-60">
                      {relativeTime(s.last_message_at)}
                    </span>
                  </button>
                  {rowActions && s.is_owner && rowActions(s)}
                  {onDelete && s.is_owner && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(s.session_id);
                      }}
                      aria-label={`Delete ${title}`}
                      title="Delete"
                      className="shrink-0 rounded p-1 text-gray-400 opacity-0 hover:bg-red-100 hover:text-red-600 focus-visible:opacity-100 group-hover:opacity-100"
                    >
                      <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                        <path d="M3 4h10M5 4v9a1 1 0 0 0 1 1h4a1 1 0 0 0 1-1V4M7 4V3a1 1 0 0 1 1-1h0a1 1 0 0 1 1 1v1" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        ))}
        {hasMore && onLoadMore && (
          <button
            type="button"
            onClick={onLoadMore}
            disabled={isLoadingMore}
            aria-busy={isLoadingMore}
            className="mt-1 min-h-[44px] rounded px-2 text-left text-xs text-muted-foreground hover:bg-accent hover:text-accent-foreground disabled:opacity-60 md:min-h-[28px]"
          >
            {isLoadingMore ? "Loading older conversations…" : "Show older conversations"}
          </button>
        )}
        {loadMoreFailed && onLoadMore && (
          <div role="alert" className="flex items-center justify-between gap-2 px-2 py-1 text-[11px] text-destructive">
            <span>Couldn&apos;t load older conversations.</span>
            <button type="button" onClick={onLoadMore} className="underline">
              Retry
            </button>
          </div>
        )}
        {error && sessions.length > 0 && onRetry && (
          <div role="alert" className="flex items-center justify-between gap-2 px-2 py-1 text-[11px] text-destructive">
            <span>Couldn&apos;t refresh your conversations.</span>
            <button type="button" onClick={onRetry} className="underline">
              Retry
            </button>
          </div>
        )}
      </nav>
    </>
  );
}

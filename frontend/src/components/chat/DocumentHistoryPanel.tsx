"use client";

import { useState } from "react";
import { type SessionFilter, useDocumentSessions } from "@/hooks/useDocumentSessions";
import type { ChatSessionSummary } from "@/hooks/useDocumentSessions";
import { fetchWithAuth } from "@/lib/apiClient";
import { notifySessionsChanged } from "@/lib/sessionEvents";
import { useConfirm } from "@/components/ui/confirm";
import { notify } from "@/components/ui/toast";

export interface DocumentHistoryPanelProps {
  documentId: string;
  activeSessionId: string | null;
  currentUserUid: string;
  onSelectSession: (sessionId: string, ownerUid: string) => void;
  onNewSession: () => void;
  /** Called when the user deletes the currently-active session — lets
   * the parent clear the URL ?session= so the chat surface resets to
   * a fresh state (same code path as "+ New conversation"). */
  onDeleteActive?: () => void;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

interface SessionRowProps {
  session: ChatSessionSummary;
  isActive: boolean;
  isOwner: boolean;
  onClick: () => void;
  onRename: (newTitle: string) => Promise<void>;
  /** Owner-only delete affordance. When omitted, no trash icon is shown
   * (used for non-owner team rows). */
  onDelete?: () => void;
}

function SessionRow({ session, isActive, isOwner, onClick, onRename, onDelete }: SessionRowProps) {
  const initialTitle = session.title ?? "Untitled conversation";
  const time = relativeTime(session.last_message_at);
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(initialTitle);
  const [saving, setSaving] = useState(false);

  async function commit() {
    const trimmed = draft.trim();
    if (!trimmed || trimmed === initialTitle) {
      setIsEditing(false);
      setDraft(initialTitle);
      return;
    }
    setSaving(true);
    try {
      await onRename(trimmed);
      setIsEditing(false);
    } catch {
      // Revert; the parent's refetch will resync if the request actually
      // succeeded but raised on a transient.
      setDraft(initialTitle);
      setIsEditing(false);
    } finally {
      setSaving(false);
    }
  }

  if (isEditing) {
    return (
      <div
        className={[
          "w-full px-3 py-2 rounded text-sm",
          isActive ? "bg-primary/10 border border-primary/30" : "bg-muted",
        ].join(" ")}
      >
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void commit();
            } else if (e.key === "Escape") {
              setDraft(initialTitle);
              setIsEditing(false);
            }
          }}
          disabled={saving}
          className="w-full rounded-sm bg-transparent font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="Rename conversation"
        />
        <div className="text-xs text-muted-foreground mt-0.5">
          {time} · {session.turn_count} turn{session.turn_count !== 1 ? "s" : ""}
        </div>
      </div>
    );
  }

  return (
    <div
      className={[
        "group flex w-full items-center gap-1 px-3 py-2 rounded text-sm transition-colors",
        isActive
          ? "bg-primary/10 border border-primary/30 text-blue-900"
          : "hover:bg-muted text-foreground",
      ].join(" ")}
    >
      <button onClick={onClick} className="min-w-0 flex-1 text-left">
        <div className="font-medium truncate">{initialTitle}</div>
        <div className="text-xs text-muted-foreground mt-0.5">
          {time} · {session.turn_count} turn{session.turn_count !== 1 ? "s" : ""}
        </div>
      </button>
      {isOwner && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setDraft(initialTitle);
            setIsEditing(true);
          }}
          aria-label={`Rename ${initialTitle}`}
          title="Rename"
          className="shrink-0 rounded p-1 text-muted-foreground opacity-0 hover:bg-muted hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
        >
          <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
            <path d="M11 2l3 3-7 7H4v-3l7-7z" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      )}
      {isOwner && onDelete && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          aria-label={`Delete ${initialTitle}`}
          title="Delete"
          className="shrink-0 rounded p-1 text-muted-foreground opacity-0 hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
        >
          <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
            <path d="M3 4h10M5 4v9a1 1 0 0 0 1 1h4a1 1 0 0 0 1-1V4M7 4V3a1 1 0 0 1 1-1h0a1 1 0 0 1 1 1v1" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      )}
    </div>
  );
}

async function renameSession(sessionId: string, title: string): Promise<void> {
  const res = await fetchWithAuth(
    `/api/proxy/api/sessions/${encodeURIComponent(sessionId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    },
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetchWithAuth(
    `/api/proxy/api/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export default function DocumentHistoryPanel({
  documentId,
  activeSessionId,
  currentUserUid,
  onSelectSession,
  onNewSession,
  onDeleteActive,
}: DocumentHistoryPanelProps) {
  // G28 (template-chat-surface-defaults.md): default-collapsed so a
  // high-traffic doc with 50+ sessions doesn't push the actual
  // DocumentPanel off-screen. Header carries the count so users see the
  // history exists without needing to expand.
  const [isOpen, setIsOpen] = useState(false);
  // Cross-panel sync is the sessions-changed event bus the hook subscribes to;
  // refetch is only the error state's Try again.
  const { sessions, isLoading, error, refetch } = useDocumentSessions(documentId);
  // Rename/delete failures are said out loud, never just reverted.
  const [actionError, setActionError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const confirm = useConfirm();

  const mine = sessions.filter((s) => s.owner_uid === currentUserUid);
  const team = sessions.filter((s) => s.owner_uid !== currentUserUid);
  const totalCount = sessions.length;

  async function handleRename(sessionId: string, title: string): Promise<void> {
    setActionError(null);
    try {
      await renameSession(sessionId, title);
    } catch (err) {
      setActionError("Couldn't rename that conversation. Try again.");
      throw err;
    }
    // Both the skill-level panel and any other doc panel showing this
    // session's title need to update — the bus fans this out.
    notifySessionsChanged();
  }

  async function handleDelete(sessionId: string): Promise<void> {
    // Soft-delete on the backend (archivedAt). Confirm dialog gates the
    // destructive action; backend stays the single source of truth so a
    // network failure here is fully recoverable (the row reappears on the
    // next refetch). See docs/design/v6.1.0/session-delete-ui.md.
    const ok = await confirm({ title: "Delete this conversation?", confirmLabel: "Delete", destructive: true });
    if (!ok) {
      return;
    }
    setActionError(null);
    setDeletingId(sessionId);
    try {
      await deleteSession(sessionId);
      notifySessionsChanged({ deletedSessionId: sessionId });
      if (sessionId === activeSessionId) {
        onDeleteActive?.();
      }
      notify.success("Conversation deleted");
    } catch {
      // Backend rejected. Refetch reconciles any partial state; say why the
      // row is still there.
      setActionError("Couldn't delete that conversation. Try again.");
      notifySessionsChanged();
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="border-b border-border">
      {/* Header */}
      <button
        onClick={() => setIsOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-2 text-sm font-medium text-muted-foreground hover:bg-muted"
        aria-expanded={isOpen}
      >
        <span className="flex items-center gap-2">
          Conversations
          {totalCount > 0 && (
            <span
              className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground"
              aria-label={`${totalCount} conversations`}
            >
              {totalCount}
            </span>
          )}
        </span>
        <span aria-hidden className="text-muted-foreground">{isOpen ? "▲" : "▼"}</span>
      </button>

      {isOpen && (
        // G28: cap height + scroll internally so an expanded list with
        // 50+ sessions never pushes sibling components out of the viewport.
        <div className="max-h-[25vh] overflow-y-auto px-3 pb-3 space-y-3">
          {isLoading && (
            <p className="text-xs text-muted-foreground px-1">Loading…</p>
          )}
          {error && (
            <div role="alert" className="flex items-center gap-2 px-1 text-xs text-destructive">
              <span>{error}</span>
              <button type="button" onClick={() => refetch()} className="rounded px-1.5 py-0.5 underline underline-offset-2 hover:bg-muted">
                Try again
              </button>
            </div>
          )}
          {actionError && (
            <p role="alert" className="px-1 text-xs text-destructive">
              {actionError}
            </p>
          )}

          {/* Mine section — hidden entirely when load failed; the error
              banner above is the only message. */}
          {!error && (
            <div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide px-1 mb-1">
                Mine
              </p>
              {mine.length === 0 && !isLoading && (
                <p className="text-xs text-muted-foreground px-1">No conversations yet</p>
              )}
              <div className="space-y-1">
                {mine.map((s) => (
                  <div
                    key={s.session_id}
                    aria-busy={deletingId === s.session_id}
                    className={deletingId === s.session_id ? "pointer-events-none opacity-50" : undefined}
                  >
                    <SessionRow
                      session={s}
                      isActive={s.session_id === activeSessionId}
                      isOwner={true}
                      onClick={() => onSelectSession(s.session_id, s.owner_uid)}
                      onRename={(t) => handleRename(s.session_id, t)}
                      onDelete={() => void handleDelete(s.session_id)}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Team section — only shown when there are team sessions */}
          {!error && team.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide px-1 mb-1">
                Team
              </p>
              <div className="space-y-1">
                {team.map((s) => (
                  <SessionRow
                    key={s.session_id}
                    session={s}
                    isOwner={false}
                    onRename={async () => {}}
                    isActive={s.session_id === activeSessionId}
                    onClick={() => onSelectSession(s.session_id, s.owner_uid)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* New conversation */}
          <button
            onClick={onNewSession}
            className="w-full text-left px-3 py-2 rounded text-sm text-primary hover:bg-primary/10 transition-colors"
          >
            + New conversation
          </button>
        </div>
      )}
    </div>
  );
}

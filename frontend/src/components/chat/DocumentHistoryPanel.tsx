"use client";

import { useState } from "react";
import { type SessionFilter, useDocumentSessions } from "@/hooks/useDocumentSessions";
import type { ChatSessionSummary } from "@/hooks/useDocumentSessions";
import { fetchWithAuth } from "@/lib/apiClient";
import { notifySessionsChanged } from "@/lib/sessionEvents";

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
          isActive ? "bg-blue-50 border border-blue-200" : "bg-gray-50",
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
          className="w-full bg-transparent font-medium text-gray-900 outline-none"
          aria-label="Rename conversation"
        />
        <div className="text-xs text-gray-400 mt-0.5">
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
          ? "bg-blue-50 border border-blue-200 text-blue-900"
          : "hover:bg-gray-50 text-gray-700",
      ].join(" ")}
    >
      <button onClick={onClick} className="min-w-0 flex-1 text-left">
        <div className="font-medium truncate">{initialTitle}</div>
        <div className="text-xs text-gray-400 mt-0.5">
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
          className="shrink-0 rounded p-1 text-gray-400 opacity-0 hover:bg-gray-200 hover:text-gray-700 group-hover:opacity-100"
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
          className="shrink-0 rounded p-1 text-gray-400 opacity-0 hover:bg-red-100 hover:text-red-600 group-hover:opacity-100"
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
  // refetch is exposed by the hook but unused here — cross-panel sync is
  // handled via the sessions-changed event bus, which the hook subscribes
  // to itself.
  const { sessions, isLoading, error } = useDocumentSessions(documentId);

  const mine = sessions.filter((s) => s.owner_uid === currentUserUid);
  const team = sessions.filter((s) => s.owner_uid !== currentUserUid);
  const totalCount = sessions.length;

  async function handleRename(sessionId: string, title: string): Promise<void> {
    await renameSession(sessionId, title);
    // Both the skill-level panel and any other doc panel showing this
    // session's title need to update — the bus fans this out.
    notifySessionsChanged();
  }

  async function handleDelete(sessionId: string): Promise<void> {
    // Soft-delete on the backend (archivedAt). Confirm dialog gates the
    // destructive action; backend stays the single source of truth so a
    // network failure here is fully recoverable (the row reappears on the
    // next refetch). See docs/design/v6.1.0/session-delete-ui.md.
    if (
      !window.confirm(
        "Delete this conversation? This can't be undone from the UI.",
      )
    ) {
      return;
    }
    try {
      await deleteSession(sessionId);
      notifySessionsChanged({ deletedSessionId: sessionId });
      if (sessionId === activeSessionId) {
        onDeleteActive?.();
      }
    } catch {
      // Backend rejected. Refetch reconciles any partial state — if the
      // row is still there the user sees it return.
      notifySessionsChanged();
    }
  }

  return (
    <div className="border-b border-gray-200">
      {/* Header */}
      <button
        onClick={() => setIsOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50"
        aria-expanded={isOpen}
      >
        <span className="flex items-center gap-2">
          Conversations
          {totalCount > 0 && (
            <span
              className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600"
              aria-label={`${totalCount} conversations`}
            >
              {totalCount}
            </span>
          )}
        </span>
        <span className="text-gray-400">{isOpen ? "▲" : "▼"}</span>
      </button>

      {isOpen && (
        // G28: cap height + scroll internally so an expanded list with
        // 50+ sessions never pushes sibling components out of the viewport.
        <div className="max-h-[25vh] overflow-y-auto px-3 pb-3 space-y-3">
          {isLoading && (
            <p className="text-xs text-gray-400 px-1">Loading…</p>
          )}
          {error && (
            <p className="text-xs text-red-500 px-1">{error}</p>
          )}

          {/* Mine section — hidden entirely when load failed; the error
              banner above is the only message. */}
          {!error && (
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide px-1 mb-1">
                Mine
              </p>
              {mine.length === 0 && !isLoading && (
                <p className="text-xs text-gray-400 px-1">No conversations yet</p>
              )}
              <div className="space-y-1">
                {mine.map((s) => (
                  <SessionRow
                    key={s.session_id}
                    session={s}
                    isActive={s.session_id === activeSessionId}
                    isOwner={true}
                    onClick={() => onSelectSession(s.session_id, s.owner_uid)}
                    onRename={(t) => handleRename(s.session_id, t)}
                    onDelete={() => void handleDelete(s.session_id)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Team section — only shown when there are team sessions */}
          {!error && team.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide px-1 mb-1">
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
            className="w-full text-left px-3 py-2 rounded text-sm text-blue-600 hover:bg-blue-50 transition-colors"
          >
            + New conversation
          </button>
        </div>
      )}
    </div>
  );
}

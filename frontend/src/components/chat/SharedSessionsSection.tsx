"use client";

import { useEffect, useState } from "react";
import { useSharedSessions } from "@/hooks/useSkillSessions";
import { SidebarSection } from "@/components/chat/SidebarSection";
import { SkillSessionPanel } from "@/components/chat/SkillSessionPanel";

interface SharedSessionsSectionProps {
  activeSessionId: string | null;
  /** Called with the thread's own skill id, so a caller can open the thread
   * on the agent it belongs to rather than the one currently on screen. */
  onSelectSession: (sessionId: string, skillId: string) => void;
}

/**
 * "Shared with me" — conversations owned by someone else (typically a machine
 * principal such as an agent) where the caller is a collaborator and may read
 * and reply. Opening a row is identical to opening one of your own.
 *
 * Visibility:
 * - Hidden entirely while the list is empty (most users have none) and during
 *   the first load — there is nothing to wait for, so no spinner.
 * - A load failure renders a visible error with a retry (CLAUDE.md #8 NEVER
 *   SILENT) rather than silently hiding the section.
 */
export function SharedSessionsSection({ activeSessionId, onSelectSession }: SharedSessionsSectionProps) {
  const { sessions, isLoading, error, refetch } = useSharedSessions();
  // A retry clears `error` while it runs; keep the section (with a pending
  // state) on screen until it resolves, so Retry never makes it vanish.
  const [retrying, setRetrying] = useState(false);
  useEffect(() => {
    if (retrying && !isLoading) setRetrying(false);
  }, [retrying, isLoading]);

  const showRetrying = retrying && isLoading;
  if (!error && !showRetrying && sessions.length === 0) return null;

  return (
    <SidebarSection title="Shared with me" persistId="shared-conversations" defaultOpen={true}>
      {showRetrying && sessions.length === 0 ? (
        <div role="status" className="p-3 text-xs text-muted-foreground">
          Retrying…
        </div>
      ) : error ? (
        <div role="alert" className="flex items-center justify-between gap-2 p-3 text-xs text-destructive">
          <span>Couldn&apos;t load conversations shared with you.</span>
          <button
            type="button"
            onClick={() => {
              setRetrying(true);
              refetch();
            }}
            className="shrink-0 rounded border px-2 py-0.5 text-foreground hover:bg-accent"
          >
            Retry
          </button>
        </div>
      ) : (
        <div className="max-h-40 overflow-y-auto">
          <SkillSessionPanel
            sessions={sessions}
            activeSessionId={activeSessionId}
            isLoading={false}
            onSelectSession={(sid) => onSelectSession(sid, sessions.find((s) => s.session_id === sid)?.skill_id ?? "")}
            showOwner
            ariaLabel="Conversations shared with me"
          />
        </div>
      )}
    </SidebarSection>
  );
}

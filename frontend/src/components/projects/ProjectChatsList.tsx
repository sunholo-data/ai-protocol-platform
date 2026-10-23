"use client";

import type { ReactNode } from "react";
import { SkillSessionPanel } from "@/components/chat/SkillSessionPanel";
import type { ChatSessionSummary } from "@/hooks/useSkillSessions";
import type { ProjectSession, useProjectSessions } from "@/hooks/useProjects";

/**
 * The sidebar's history while the chat is in a project: that project's chats
 * (started in it, or that used one of its documents — "used EDP.pdf"), with a
 * line saying so and a way out to every chat. The same list component as the
 * normal history, so a project chat screen reads like any other.
 */
export function ProjectChatsList({
  projectName,
  chats,
  activeSessionId,
  onSelectSession,
  onDelete,
  onShowAll,
  rowActions,
}: {
  projectName: string | null;
  chats: ReturnType<typeof useProjectSessions>;
  activeSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  onDelete?: (sessionId: string) => void;
  /** Leave the project and see every chat. */
  onShowAll: () => void;
  rowActions?: (session: ChatSessionSummary) => ReactNode;
}) {
  return (
    <div data-testid="project-chats">
      <div className="flex items-center gap-1 px-3 pb-1 pt-1.5 text-[11px] text-muted-foreground">
        <span className="min-w-0 truncate">
          In <span className="font-medium text-foreground">{projectName ?? "this project"}</span>
        </span>
        <span aria-hidden>·</span>
        <button type="button" onClick={onShowAll} className="shrink-0 underline underline-offset-2 hover:text-foreground">
          Show all chats
        </button>
      </div>
      <div className="max-h-[40vh] overflow-y-auto">
        <SkillSessionPanel
          sessions={chats.sessions}
          activeSessionId={activeSessionId}
          isLoading={chats.status === "loading" || chats.status === "idle"}
          onSelectSession={onSelectSession}
          onDelete={onDelete}
          groupByDate
          ariaLabel="Project chats"
          error={chats.status === "error" ? "Couldn't load this project's chats" : null}
          onRetry={chats.retry}
          hasMore={chats.hasMore}
          isLoadingMore={chats.isLoadingMore}
          loadMoreFailed={chats.loadMoreFailed}
          onLoadMore={chats.loadMore}
          rowNote={(s) => {
            const linked = (s as ProjectSession).linkedDocument;
            return (s as ProjectSession).membership === "derived" && linked?.filename ? `used ${linked.filename}` : null;
          }}
          emptyMessage="No chats in this project yet. Ask something below to start one."
          rowActions={rowActions}
        />
      </div>
    </div>
  );
}

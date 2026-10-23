"use client";

import Link from "next/link";
import { useEffect } from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, MoreHorizontal } from "lucide-react";
import { MENU_CONTENT_CLASS, MENU_ITEM_CLASS } from "@/components/doc-browser/folderActions";
import type { ChatSessionSummary } from "@/hooks/useSkillSessions";
import { createProjectFromSession, moveSessionToProject, useProjects } from "@/hooks/useProjects";

/** What a chat-row project action is doing, for the notice under the list. */
export type SessionProjectFeedback =
  | { kind: "pending"; text: string }
  | { kind: "done"; text: string; href?: string }
  | { kind: "error"; text: string };

const DONE_MS = 6000;

function chatTitle(session: ChatSessionSummary): string {
  return session.title?.trim() || "Untitled chat";
}

/**
 * A chat row's ⋯ menu (projects design §4 E5, §6.3): "Make this a project",
 * and "Move to project…" — every project, plus "No project". Flat rather than
 * a submenu so it works the same by pointer, keyboard and touch. Results go to
 * `onFeedback` (NEVER SILENT #8): pending, the server's own error text, or done
 * with a link to the project.
 */
export function SessionProjectMenu({
  session,
  onFeedback,
}: {
  session: ChatSessionSummary;
  onFeedback: (feedback: SessionProjectFeedback) => void;
}) {
  const title = chatTitle(session);
  const current = session.project_id ?? null;

  async function move(projectId: string | null, projectName: string) {
    onFeedback({ kind: "pending", text: projectId ? `Moving “${title}” to ${projectName}…` : `Taking “${title}” out of its project…` });
    const r = await moveSessionToProject(session.session_id, projectId);
    if (!r.ok) {
      onFeedback({ kind: "error", text: r.message });
      return;
    }
    onFeedback(
      projectId
        ? { kind: "done", text: `Moved to ${projectName}`, href: `/projects/${encodeURIComponent(projectId)}/chat` }
        : { kind: "done", text: `“${title}” is no longer in a project` },
    );
  }

  async function makeProject() {
    onFeedback({ kind: "pending", text: `Creating a project from “${title}”…` });
    const r = await createProjectFromSession(session.session_id, title);
    if (!r.ok) {
      onFeedback({ kind: "error", text: r.message });
      return;
    }
    onFeedback({ kind: "done", text: `Created ${r.project.name}`, href: `/projects/${encodeURIComponent(r.project.id)}/chat` });
  }

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          onClick={(e) => e.stopPropagation()}
          aria-label={`More actions for ${title}`}
          title="More"
          className="shrink-0 rounded p-1 text-gray-400 opacity-0 hover:bg-accent hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100"
        >
          <MoreHorizontal className="h-3.5 w-3.5" aria-hidden />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={4} className={MENU_CONTENT_CLASS} onClick={(e) => e.stopPropagation()}>
          <DropdownMenu.Item className={MENU_ITEM_CLASS} onSelect={() => void makeProject()}>
            Make this a project
          </DropdownMenu.Item>
          <DropdownMenu.Separator className="my-1 h-px bg-border" />
          <DropdownMenu.Label className="px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            Move to project
          </DropdownMenu.Label>
          <MoveTargets current={current} onMove={(id, name) => void move(id, name)} />
          {current && (
            <DropdownMenu.Item className={MENU_ITEM_CLASS} onSelect={() => void move(null, "")}>
              No project
            </DropdownMenu.Item>
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/** The projects to move to — mounted only while the menu is open, so a list
 * of chats doesn't load projects until someone asks. */
function MoveTargets({ current, onMove }: { current: string | null; onMove: (id: string, name: string) => void }) {
  const { projects, status } = useProjects();
  return (
    <>
      {status === "loading" && projects.length === 0 && (
        <div className="px-2 py-1 text-xs text-muted-foreground">Loading projects…</div>
      )}
      {status === "error" && projects.length === 0 && (
        <div role="alert" className="px-2 py-1 text-xs text-destructive">
          Couldn&apos;t load your projects.
        </div>
      )}
      {status === "ready" && projects.length === 0 && (
        <div className="px-2 py-1 text-xs text-muted-foreground">No projects yet</div>
      )}
      {projects.map((p) => (
        <DropdownMenu.Item
          key={p.id}
          className={MENU_ITEM_CLASS}
          disabled={p.id === current}
          onSelect={() => onMove(p.id, p.name)}
        >
          <span className="flex w-full items-center justify-between gap-2">
            <span className="truncate">{p.name}</span>
            {p.id === current && <Check className="h-3.5 w-3.5 shrink-0" aria-label="current project" />}
          </span>
        </DropdownMenu.Item>
      ))}
    </>
  );
}

/** The line that reports a chat-row project action. "done" clears itself. */
export function SessionProjectNotice({
  feedback,
  onDismiss,
}: {
  feedback: SessionProjectFeedback | null;
  onDismiss: () => void;
}) {
  useEffect(() => {
    if (feedback?.kind !== "done") return;
    const t = setTimeout(onDismiss, DONE_MS);
    return () => clearTimeout(t);
  }, [feedback, onDismiss]);

  if (!feedback) return null;
  const isError = feedback.kind === "error";
  return (
    <div
      role={isError ? "alert" : "status"}
      data-testid="session-project-notice"
      className={`flex items-center gap-2 px-2 py-1 text-xs ${isError ? "text-destructive" : "text-muted-foreground"}`}
    >
      <span className="min-w-0 flex-1">{feedback.text}</span>
      {feedback.kind === "done" && feedback.href && (
        <Link href={feedback.href} className="shrink-0 text-primary underline underline-offset-2">
          Open
        </Link>
      )}
      {feedback.kind !== "pending" && (
        <button type="button" onClick={onDismiss} aria-label="Dismiss" className="shrink-0 rounded px-1 hover:bg-accent">
          ×
        </button>
      )}
    </div>
  );
}

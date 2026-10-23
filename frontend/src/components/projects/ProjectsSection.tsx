"use client";

import Link from "next/link";
import { navigateTo, useOptionalRouter } from "@/lib/useOptionalRouter";
import { useEffect, useRef, useState } from "react";
import { FolderKanban, Plus, Settings } from "lucide-react";
import { DOC_DRAG_TYPE, readDragDoc, type DraggedDoc } from "@/components/doc-browser/folderActions";
import { addDocumentsToProject, createProject, useProjects, type ProjectSummary } from "@/hooks/useProjects";
import { NewProjectInline } from "./NewProjectInline";

/** How many projects the sidebar lists before "See all". */
export const SIDEBAR_PROJECT_LIMIT = 8;

/** How long a success line stays under a project row. Errors stay until dismissed. */
const FEEDBACK_MS = 4000;

type DropFeedback =
  | { kind: "pending"; doc: DraggedDoc }
  | { kind: "added" | "already"; doc: DraggedDoc }
  | { kind: "error"; doc: DraggedDoc; message: string };

function docLabel(doc: DraggedDoc): string {
  return doc.filename ?? "the document";
}

/**
 * The sidebar's **Projects** section body (projects design §4 E1/E2, §6.1, §6.2).
 *
 * Always shown — with nothing yet it explains itself and offers "+ New
 * project", so the feature is discoverable. Each project row opens the normal
 * chat screen scoped to the project (`chatHrefFor`), has a settings link, and
 * is a drop target: dropping a document from "Your files" ADDS it to the
 * project (it stays in its folder — a folder drop MOVES). Every drop says what
 * happened: "Adding…", "Added to X", "Already in X", or the error with Retry.
 *
 * "+ New project" goes to the new project's settings, in set-up mode: a name
 * alone is an empty project, and setting it up is the next thing to do.
 */
export function ProjectsSection({
  chatHrefFor,
  activeProjectId = null,
}: {
  /** The scoped chat a project row opens. */
  chatHrefFor: (project: ProjectSummary) => string;
  /** The project the open chat is in, highlighted. */
  activeProjectId?: string | null;
}) {
  const router = useOptionalRouter();
  const { projects, status, retry } = useProjects();
  const [creating, setCreating] = useState(false);

  async function create(name: string): Promise<string | null> {
    const result = await createProject({ name });
    if (!result.ok) return result.message;
    setCreating(false);
    navigateTo(router, `/projects/${encodeURIComponent(result.project.id)}`);
    return null;
  }

  const visible = projects.slice(0, SIDEBAR_PROJECT_LIMIT);

  return (
    <div className="space-y-0.5 p-1" data-testid="projects-section">
      {status === "loading" && projects.length === 0 && (
        <div className="space-y-1 p-1" role="status" aria-label="Loading your projects">
          {[1, 2].map((i) => (
            <div key={i} className="h-7 animate-pulse rounded bg-muted" />
          ))}
        </div>
      )}

      {status === "error" && (
        <div role="alert" className="flex items-center gap-2 px-2 py-1.5 text-xs text-destructive">
          <span className="flex-1">Couldn&apos;t load your projects.</span>
          <button type="button" onClick={retry} className="shrink-0 rounded border border-destructive/40 px-1.5 py-0.5 hover:bg-destructive/10">
            Retry
          </button>
        </div>
      )}

      {status === "ready" && projects.length === 0 && !creating && (
        <p className="px-2 py-1.5 text-xs leading-snug text-muted-foreground" data-testid="projects-empty">
          Group the documents and chats you&apos;re working on.
        </p>
      )}

      {visible.length > 0 && (
        <ul aria-label="Your projects" className="space-y-0.5">
          {visible.map((p) => (
            <ProjectRow key={p.id} project={p} href={chatHrefFor(p)} active={p.id === activeProjectId} />
          ))}
        </ul>
      )}

      {creating ? (
        <NewProjectInline onCreate={create} onCancel={() => setCreating(false)} />
      ) : (
        <div className="flex items-center justify-between gap-2 px-1">
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="flex items-center gap-1 rounded px-1 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <Plus className="h-3 w-3" aria-hidden />
            New project
          </button>
          {projects.length > SIDEBAR_PROJECT_LIMIT && (
            <Link
              href="/projects"
              className="rounded px-1 py-1 text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            >
              See all ({projects.length})
            </Link>
          )}
        </div>
      )}
    </div>
  );
}

function ProjectRow({ project, href, active }: { project: ProjectSummary; href: string; active: boolean }) {
  const [dragOver, setDragOver] = useState(false);
  const [feedback, setFeedback] = useState<DropFeedback | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function add(doc: DraggedDoc) {
    if (timer.current) clearTimeout(timer.current);
    setFeedback({ kind: "pending", doc });
    const result = await addDocumentsToProject(project.id, [doc.id]);
    if (!result.ok) {
      setFeedback({ kind: "error", doc, message: result.message });
      return;
    }
    if (result.failed.length > 0) {
      const reason = result.failed[0].reason;
      setFeedback({ kind: "error", doc, message: reason ? `Couldn't add ${docLabel(doc)}: ${reason}` : `Couldn't add ${docLabel(doc)} to ${project.name}.` });
      return;
    }
    setFeedback({ kind: result.added.length > 0 ? "added" : "already", doc });
    timer.current = setTimeout(() => setFeedback(null), FEEDBACK_MS);
  }

  function handleDragOver(e: React.DragEvent) {
    if (!Array.from(e.dataTransfer?.types ?? []).includes(DOC_DRAG_TYPE)) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    if (!dragOver) setDragOver(true);
  }

  function handleDragLeave(e: React.DragEvent) {
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setDragOver(false);
  }

  function handleDrop(e: React.DragEvent) {
    setDragOver(false);
    const dragged = readDragDoc(e.dataTransfer);
    if (!dragged) return;
    e.preventDefault();
    void add(dragged);
  }

  return (
    <li
      onDragOver={handleDragOver}
      onDragEnter={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      data-drop-target={dragOver ? "true" : undefined}
      className={`rounded ${dragOver ? "bg-accent ring-1 ring-primary" : ""}`}
    >
      <div className={`group flex items-center rounded ${active ? "bg-accent" : ""}`}>
        <Link
          href={href}
          aria-current={active ? "page" : undefined}
          className="flex min-w-0 flex-1 items-center gap-1.5 rounded px-2 py-1.5 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <FolderKanban className="h-3.5 w-3.5 shrink-0 text-primary/80" aria-hidden />
          <span className="min-w-0 flex-1 truncate font-medium text-foreground">{project.name}</span>
          <span
            className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
            title={`${project.docCount} document${project.docCount === 1 ? "" : "s"}`}
          >
            {project.docCount}
          </span>
        </Link>
        <Link
          href={`/projects/${encodeURIComponent(project.id)}`}
          aria-label={`${project.name} settings`}
          title="Project settings"
          className="shrink-0 rounded p-1 text-muted-foreground opacity-60 hover:bg-accent hover:text-foreground hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring md:opacity-0 md:group-hover:opacity-100"
        >
          <Settings className="h-3.5 w-3.5" aria-hidden />
        </Link>
      </div>
      {feedback?.kind === "pending" && (
        <p role="status" className="px-2 pb-1 text-[10px] leading-tight text-muted-foreground">
          Adding {docLabel(feedback.doc)}…
        </p>
      )}
      {(feedback?.kind === "added" || feedback?.kind === "already") && (
        <p role="status" className="px-2 pb-1 text-[10px] leading-tight text-emerald-700 dark:text-emerald-400">
          {feedback.kind === "added" ? `Added to ${project.name}` : `Already in ${project.name}`}
        </p>
      )}
      {feedback?.kind === "error" && (
        <div role="alert" className="flex items-start gap-2 px-2 pb-1 text-[10px] leading-tight text-destructive">
          <span className="flex-1">{feedback.message}</span>
          <button type="button" onClick={() => void add(feedback.doc)} className="shrink-0 hover:underline">
            Retry
          </button>
          <button type="button" aria-label="Dismiss" onClick={() => setFeedback(null)} className="shrink-0 hover:underline">
            ×
          </button>
        </div>
      )}
    </li>
  );
}

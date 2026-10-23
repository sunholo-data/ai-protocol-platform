"use client";

import Link from "next/link";

export interface ActiveDocumentContext {
  folderName: string;
  docCount: number;
}

/** The project this chat is scoped to (projects §6.3). Only ever passed once
 * the project has loaded — a scope that isn't active is never claimed. */
export interface ActiveProjectContext {
  id: string;
  name: string;
  docCount: number;
  /** The user's own or the project's instructions are set. */
  instructionsOn: boolean;
  /** Its documents are still being prepared for meaning-based search. */
  searchPreparing?: boolean;
}

interface ContextBannerProps {
  context: ActiveDocumentContext | null;
  /** "Your instructions" are set, so the agent gets them in this chat
   * (projects design §6.5). Shown so the user can see they're in effect. */
  instructionsOn?: boolean;
  /** Where "Edit" goes for the instructions. */
  instructionsHref?: string;
  project?: ActiveProjectContext | null;
  /** Leave the project: a plain, unscoped new chat. Omitted → no ✕. */
  onLeaveProject?: () => void;
}

/**
 * The strip above the transcript that says what the agent is working with:
 * a folder's documents, and/or the user's own instructions. Renders nothing
 * when neither applies.
 */
export function ContextBanner({
  context,
  instructionsOn = false,
  instructionsHref = "/settings/instructions",
  project = null,
  onLeaveProject,
}: ContextBannerProps) {
  if (!context && !instructionsOn && !project) return null;

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b bg-muted/50 px-4 py-2 text-xs text-muted-foreground" data-testid="context-banner">
      {context && (
        <span className="inline-flex items-center gap-2">
          <svg
            className="h-3.5 w-3.5 shrink-0 text-teal-600"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            aria-hidden="true"
          >
            <path
              d="M2 4a1 1 0 011-1h3.5l1.5 2H13a1 1 0 011 1v6a1 1 0 01-1 1H3a1 1 0 01-1-1V4z"
              strokeLinejoin="round"
            />
          </svg>
          <span>
            Analyzing <strong className="font-semibold text-foreground">{context.docCount}</strong>{" "}
            {context.docCount === 1 ? "document" : "documents"} from{" "}
            <strong className="font-semibold text-foreground">{context.folderName}</strong>
          </span>
        </span>
      )}
      {project && (
        <span className="inline-flex flex-wrap items-center gap-1" data-testid="project-scope">
          <span>In project</span>
          <strong className="font-semibold text-foreground">{project.name}</strong>
          <span aria-hidden>·</span>
          <span>
            {project.docCount} {project.docCount === 1 ? "document" : "documents"}
          </span>
          {project.instructionsOn && (
            <>
              <span aria-hidden>·</span>
              <span>instructions on</span>
            </>
          )}
          {project.searchPreparing && (
            <>
              <span aria-hidden>·</span>
              <span>preparing search</span>
            </>
          )}
          <span aria-hidden>·</span>
          <Link
            href={`/projects/${encodeURIComponent(project.id)}`}
            className="underline underline-offset-2 hover:text-foreground"
          >
            Project settings
          </Link>
          {onLeaveProject && (
            <button
              type="button"
              onClick={onLeaveProject}
              aria-label={`Leave ${project.name}`}
              title="Leave the project — start a chat that isn't in it"
              className="ml-1 rounded px-1 leading-none hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              ✕
            </button>
          )}
        </span>
      )}
      {/* In a project the scope line already says whether instructions are on. */}
      {instructionsOn && !project && (
        <span className="inline-flex items-center gap-1" data-testid="instructions-on">
          <span>Your instructions on</span>
          <span aria-hidden>·</span>
          <Link href={instructionsHref} className="underline underline-offset-2 hover:text-foreground">
            Edit
          </Link>
        </span>
      )}
    </div>
  );
}

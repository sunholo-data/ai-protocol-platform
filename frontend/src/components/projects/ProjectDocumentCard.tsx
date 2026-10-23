"use client";

import { useState } from "react";
import { FileText, X } from "lucide-react";
import type { ProjectDocument } from "@/hooks/useProjects";
import { removeDocumentFromProject } from "@/hooks/useProjects";
import { relativeTime } from "@/lib/relativeTime";

interface ProjectDocumentCardProps {
  projectId: string;
  doc: ProjectDocument;
  /** Open the document in a chat (the project page has no workbench). */
  onOpen: (doc: ProjectDocument) => void;
}

/**
 * A document in the project page's "Project knowledge" panel: one compact row
 * (the panel is narrow), opening the document on click, with a remove button
 * that says the document itself is kept.
 */
export function ProjectDocumentRow({ projectId, doc, onOpen }: ProjectDocumentCardProps) {
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const when = doc.lastUsedAt ?? doc.updatedAt ?? doc.createdAt ?? null;

  async function remove() {
    setRemoving(true);
    setError(null);
    const r = await removeDocumentFromProject(projectId, doc.id);
    if (!r.ok) {
      setRemoving(false);
      setError(r.message);
    }
  }

  return (
    <div className="group px-2 py-1.5">
      <div className="flex items-center gap-2">
        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <button
          type="button"
          onClick={() => onOpen(doc)}
          aria-label={`Open ${doc.originalFilename}`}
          className="min-w-0 flex-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
        >
          <span className="block truncate text-sm text-foreground group-hover:text-primary">{doc.originalFilename}</span>
          {when && (
            <span className="block text-xs text-muted-foreground">
              {doc.lastSessionId ? "Last used" : "Added"} {relativeTime(when)}
            </span>
          )}
        </button>
        <button
          type="button"
          onClick={() => void remove()}
          disabled={removing}
          aria-label={`Remove ${doc.originalFilename} from project`}
          title="Take it out of this project. The document itself is kept."
          className="shrink-0 rounded p-1 text-muted-foreground opacity-60 hover:bg-muted hover:text-destructive hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 disabled:opacity-40"
        >
          {removing ? <span className="text-xs">Removing…</span> : <X className="h-3.5 w-3.5" aria-hidden />}
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-1 text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

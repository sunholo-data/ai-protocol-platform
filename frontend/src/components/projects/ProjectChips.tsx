"use client";

import { FolderKanban } from "lucide-react";
import { useProjects } from "@/hooks/useProjects";

/**
 * Read-only "this document is in N projects" marker for a document row
 * (projects design §6.1; editing is "Add to project…" or the project home).
 * Friendly names only — the tooltip lists project names, never ids (#9).
 */
export function ProjectChips({ projectIds }: { projectIds?: readonly string[] | null }) {
  if (!projectIds || projectIds.length === 0) return null;
  return <Chips projectIds={projectIds} />;
}

function Chips({ projectIds }: { projectIds: readonly string[] }) {
  const { projects } = useProjects();
  const names = projects.filter((p) => projectIds.includes(p.id)).map((p) => p.name);
  // A project id we can't name yet (list still loading, or just deleted) is
  // not worth a chip that says nothing.
  if (names.length === 0) return null;
  const label = names.length === 1 ? `In project ${names[0]}` : `In projects: ${names.join(", ")}`;
  return (
    <span
      className="inline-flex shrink-0 items-center gap-0.5 rounded bg-primary/10 px-1 py-0.5 text-[10px] font-medium text-primary"
      title={label}
      aria-label={label}
      data-testid="project-chips"
    >
      <FolderKanban className="h-2.5 w-2.5" aria-hidden />
      {names.length === 1 ? <span className="max-w-[6rem] truncate">{names[0]}</span> : names.length}
    </span>
  );
}

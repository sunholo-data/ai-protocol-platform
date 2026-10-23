"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowLeft, FolderKanban, Plus, Settings } from "lucide-react";
import { createProject, useProjects } from "@/hooks/useProjects";
import { useProjectChatHref } from "@/hooks/useProjectChatHref";
import { relativeTime } from "@/lib/relativeTime";
import { navigateTo, useOptionalRouter } from "@/lib/useOptionalRouter";
import { NewProjectInline } from "./NewProjectInline";

/** `/projects` — every project, most recently opened first. A project opens its
 * chats (the chat screen, scoped); its settings are the secondary link. A new
 * project goes to its settings, in set-up mode. */
export function ProjectsList({ uid }: { uid: string }) {
  const router = useOptionalRouter();
  const chatHref = useProjectChatHref(uid);
  const { projects, status, retry } = useProjects();
  const [creating, setCreating] = useState(false);

  async function create(name: string): Promise<string | null> {
    const r = await createProject({ name });
    if (!r.ok) return r.message;
    navigateTo(router, `/projects/${encodeURIComponent(r.project.id)}`);
    return null;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6 md:px-6">
      <Link href="/" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" aria-hidden /> Back to chat
      </Link>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Projects</h1>
          <p className="text-sm text-muted-foreground">
            A project gathers documents from any folder, its own instructions, and the chats about them.
          </p>
        </div>
        {!creating && (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            <Plus className="h-4 w-4" aria-hidden /> New project
          </button>
        )}
      </div>

      {creating && (
        <div className="rounded-lg border p-2">
          <NewProjectInline onCreate={create} onCancel={() => setCreating(false)} />
        </div>
      )}

      {status === "loading" && projects.length === 0 && (
        <ul role="status" aria-label="Loading your projects" className="space-y-2">
          {[0, 1, 2].map((i) => (
            <li key={i} className="h-16 animate-pulse rounded-lg bg-muted" />
          ))}
        </ul>
      )}

      {status === "error" && (
        <div role="alert" className="flex items-center gap-2 rounded-lg border border-destructive/40 p-3 text-sm text-destructive">
          <span className="flex-1">Couldn&apos;t load your projects.</span>
          <button type="button" onClick={retry} className="rounded border border-destructive/40 px-2 py-0.5 text-xs hover:bg-destructive/10">
            Retry
          </button>
        </div>
      )}

      {status === "ready" && projects.length === 0 && !creating && (
        <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
          <p>Group the documents and chats you&apos;re working on.</p>
          <button type="button" onClick={() => setCreating(true)} className="mt-2 text-primary underline underline-offset-2">
            Create your first project
          </button>
        </div>
      )}

      {projects.length > 0 && (
        <ul className="divide-y rounded-lg border" aria-label="Your projects">
          {projects.map((p) => (
            <li key={p.id} className="flex items-center">
              <Link
                href={chatHref(p) ?? `/projects/${encodeURIComponent(p.id)}`}
                className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              >
                <FolderKanban className="h-5 w-5 shrink-0 text-primary/80" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-foreground">{p.name}</span>
                  <span className="block text-xs text-muted-foreground">
                    {p.docCount} document{p.docCount === 1 ? "" : "s"} · {p.chatCount} chat{p.chatCount === 1 ? "" : "s"}
                  </span>
                </span>
                {p.lastOpenedAt && <span className="shrink-0 text-xs text-muted-foreground">{relativeTime(p.lastOpenedAt)}</span>}
              </Link>
              <Link
                href={`/projects/${encodeURIComponent(p.id)}`}
                aria-label={`${p.name} settings`}
                className="mr-2 inline-flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <Settings className="h-3.5 w-3.5" aria-hidden /> Settings
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

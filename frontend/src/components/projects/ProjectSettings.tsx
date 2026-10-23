"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Check, FolderKanban, MessageSquare, Plus } from "lucide-react";
import { UploadDropZone } from "@/components/doc-browser/UploadDropZone";
import { useProjectChatHref } from "@/hooks/useProjectChatHref";
import { useUserSkills } from "@/hooks/useUserSkills";
import {
  addDocumentsToProject,
  deleteProject,
  updateProject,
  useProject,
  useProjectDocuments,
  useProjectSessions,
  type Project,
  type ProjectDocument,
  type ProjectSearchStatus,
} from "@/hooks/useProjects";
import { fetchWithAuth } from "@/lib/apiClient";
import { navigateTo, useOptionalRouter } from "@/lib/useOptionalRouter";
import type { Skill, WelcomeConfig } from "@/types/skill";
import { AddDocumentsDialog, type SharedLibrary } from "./AddDocumentsDialog";
import { InstructionsEditor } from "./InstructionsEditor";
import { MAX_PROJECT_NAME } from "./NewProjectInline";
import { ProjectDocumentRow } from "./ProjectDocumentCard";

/** Server-side cap on a project's instructions (§5). */
export const MAX_PROJECT_INSTRUCTIONS = 8000;

/** How often, and for how long, the page re-reads a project while its
 * documents are being prepared for search. */
const SEARCH_POLL_MS = 10_000;
const SEARCH_POLL_FOR_MS = 3 * 60_000;

/** The shared library a skill offers (its welcome.bucketBrowser), resolved
 * with the bucket filled in per viewer by the single-skill GET. */
function useSkillLibrary(skillId: string | null): SharedLibrary | null {
  const [library, setLibrary] = useState<SharedLibrary | null>(null);
  useEffect(() => {
    setLibrary(null);
    if (!skillId) return;
    let cancelled = false;
    fetchWithAuth(`/api/proxy/api/skills/${encodeURIComponent(skillId)}`)
      .then((res) => (res.ok ? (res.json() as Promise<{ welcome?: WelcomeConfig | null }>) : null))
      .then((body) => {
        const bb = body?.welcome?.bucketBrowser;
        if (!cancelled && bb?.bucket) {
          setLibrary({ bucket: bb.bucket, rootPath: bb.rootPath ?? "", label: bb.label ?? undefined, skillId });
        }
      })
      // No library → the tab says so; nothing else depends on it.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [skillId]);
  return library;
}

/**
 * A project's home (projects design §6.2): its name, instructions and default
 * assistant; its documents; and the chats that belong to it — explicit ones
 * and derived ones ("used EDP.pdf"). App navigation, so ordinary React; the
 * protocols-first rule governs agent output, not this.
 */

/**
 * `/projects/{id}` — **Project settings** (Mark, 2026-10-20: keep the app's UI
 * rules — chat in the middle — or make it clear it's a creation page).
 *
 * Chatting in a project happens on the normal chat screen, scoped to it (the
 * sidebar's project row, "Open project chats"). This page is only what every
 * chat there starts from: name, instructions, which assistant new chats open
 * on, and the documents. A brand-new project (no documents, no chats) reads
 * "Set up your project" and walks through it. App navigation, so ordinary
 * React; the protocols-first rule governs agent output, not this.
 */
export function ProjectSettings({ projectId, uid }: { projectId: string; uid: string }) {
  const { project, status, retry } = useProject(projectId);

  if (status === "loading" && !project) {
    return (
      <div role="status" aria-label="Loading project" className="mx-auto max-w-3xl space-y-4 px-4 py-6 md:px-6">
        <div className="h-8 w-64 animate-pulse rounded bg-muted" />
        <div className="h-24 animate-pulse rounded bg-muted" />
        <div className="h-40 animate-pulse rounded bg-muted/60" />
      </div>
    );
  }

  if (status === "not_found") {
    return (
      <div className="mx-auto max-w-xl space-y-3 p-6 text-center" role="alert">
        <p className="text-base font-medium text-foreground">This project was deleted.</p>
        <p className="text-sm text-muted-foreground">Its documents and chats are still in your library and history.</p>
        <Link href="/projects" className="text-sm text-primary underline underline-offset-4">
          See your projects
        </Link>
      </div>
    );
  }

  if (status === "error" || !project) {
    return (
      <div className="mx-auto max-w-xl space-y-3 p-6 text-center" role="alert">
        <p className="text-base font-medium text-foreground">Couldn&apos;t open this project.</p>
        <div className="flex justify-center gap-3 text-sm">
          <button type="button" onClick={() => void retry()} className="rounded border px-3 py-1 hover:bg-accent">
            Retry
          </button>
          <Link href="/projects" className="rounded border px-3 py-1 hover:bg-accent">
            Back
          </Link>
        </div>
      </div>
    );
  }

  return <ProjectSettingsLoaded project={project} uid={uid} onPoll={retry} />;
}

function ProjectSettingsLoaded({ project, uid, onPoll }: { project: Project; uid: string; onPoll: () => void }) {
  const router = useOptionalRouter();
  const { skills } = useUserSkills(uid);
  const chatHref = useProjectChatHref(uid);
  const library = useSkillLibrary(project.defaultSkillId ?? skills[0]?.skillId ?? null);

  const docs = useProjectDocuments(project.id);
  const chats = useProjectSessions(project.id);
  const existing = useMemo(() => new Set(docs.documents.map((d) => d.id)), [docs.documents]);
  // Set-up mode: nothing in it yet. Decided once loaded, so the title doesn't
  // flicker while the lists arrive.
  const noChatsYet = docs.status === "ready" && chats.status === "ready" && chats.sessions.length === 0;
  const isNew = noChatsYet && docs.documents.length === 0;
  const href = chatHref(project) ?? null;

  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState<{ kind: "status" | "alert"; text: string } | null>(null);

  /** Open a document in a chat in this project (this page has no workbench). */
  function openDocument(doc: ProjectDocument) {
    const target = chatHref(project);
    if (!target) return;
    const [base] = target.split("?");
    const qs = new URLSearchParams({ doc: doc.id, docName: doc.originalFilename, docFormat: doc.sourceFormat || "", project: project.id });
    navigateTo(router, `${base}?${qs.toString()}`);
  }

  async function onUploaded(docId: string, filename: string) {
    setNotice({ kind: "status", text: `Adding ${filename}…` });
    const r = await addDocumentsToProject(project.id, [docId]);
    if (!r.ok) setNotice({ kind: "alert", text: `${filename} was uploaded but couldn't be added: ${r.message}` });
    else if (r.failed.length) setNotice({ kind: "alert", text: `${filename} was uploaded but couldn't be added to the project.` });
    else setNotice({ kind: "status", text: `Added ${filename}` });
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6 md:px-6" data-testid="project-settings">
      <nav className="flex flex-wrap items-center gap-3 text-sm" aria-label="Breadcrumb">
        {href ? (
          <Link href={href} className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-4 w-4" aria-hidden /> Back to {project.name} chats
          </Link>
        ) : (
          <Link href="/projects" className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-4 w-4" aria-hidden /> Back
          </Link>
        )}
        <span className="text-muted-foreground" aria-hidden>
          ·
        </span>
        <Link href="/projects" className="text-muted-foreground hover:text-foreground">
          All projects
        </Link>
      </nav>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-2">
          <h1 className="text-2xl font-semibold text-foreground">{isNew ? "Set up your project" : "Project settings"}</h1>
          <ProjectName project={project} />
          <p className="text-sm text-muted-foreground">
            {project.docCount} document{project.docCount === 1 ? "" : "s"} · {project.chatCount} chat
            {project.chatCount === 1 ? "" : "s"}
          </p>
        </div>
        {href && (
          <Link
            href={href}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            <MessageSquare className="h-4 w-4" aria-hidden /> {isNew ? "Start chatting in this project" : "Open project chats"}
          </Link>
        )}
      </header>

      {/* Until the first chat: each step ticks off as it's done. */}
      {noChatsYet && (
        <GettingStarted
          hasDocuments={docs.documents.length > 0}
          hasInstructions={Boolean(project.instructions.trim())}
          onAdd={() => setAdding(true)}
        />
      )}

      <section aria-labelledby="project-instructions-heading" className="space-y-3 rounded-lg border p-4">
        <h2 id="project-instructions-heading" className="text-base font-semibold text-foreground">
          Instructions
        </h2>
        <InstructionsEditor
          label="Project instructions"
          description="What this project is about and how you want answers. Every chat in this project uses them, on top of your own instructions."
          placeholder="e.g. Focus on curtailment and change-of-law clauses. Quote clause numbers."
          value={project.instructions}
          maxLength={MAX_PROJECT_INSTRUCTIONS}
          onSave={async (instructions) => {
            const r = await updateProject(project.id, { instructions });
            return r.ok ? null : r.message;
          }}
        />
        <DefaultSkillPicker project={project} skills={skills} />
      </section>

      <section aria-labelledby="project-docs-heading" className="space-y-3 rounded-lg border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 id="project-docs-heading" className="text-base font-semibold text-foreground">
              Documents{docs.documents.length > 0 ? ` (${docs.documents.length}${docs.hasMore ? "+" : ""})` : ""}
            </h2>
            <ProjectSearchLine search={project.search ?? null} onPoll={onPoll} />
          </div>
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 text-sm hover:bg-accent"
          >
            <Plus className="h-4 w-4" aria-hidden /> Add documents
          </button>
        </div>
        <p className="text-xs text-muted-foreground">
          Chats in this project can read these. They stay in their folders, and one document can be in several projects.
        </p>
        {notice && (
          <p role={notice.kind} className={`text-sm ${notice.kind === "alert" ? "text-destructive" : "text-muted-foreground"}`}>
            {notice.text}
          </p>
        )}
        <ProjectDocuments docs={docs} projectId={project.id} onOpen={openDocument} onAdd={() => setAdding(true)} />
        <div className="space-y-1">
          <p className="text-xs text-muted-foreground">New files are saved to your library and added to this project.</p>
          <div className="rounded-lg border border-dashed">
            <UploadDropZone onUploadComplete={(id, name) => void onUploaded(id, name)} className="px-3 py-2" />
          </div>
        </div>
      </section>

      <DeleteProject project={project} />

      <AddDocumentsDialog
        open={adding}
        onOpenChange={setAdding}
        projectId={project.id}
        projectName={project.name}
        existingDocIds={existing}
        library={library}
        onAdded={(n) => setNotice({ kind: "status", text: `Added ${n} document${n === 1 ? "" : "s"}` })}
      />
    </div>
  );
}

/**
 * Whether the project's documents are ready for meaning-based search. Passive:
 * it reports, it never asks the user to do anything (indexing is automatic).
 * Absent field or "unavailable" → nothing (the public template has no search).
 * While indexing, re-reads the project every 10s for up to three minutes.
 */
export function ProjectSearchLine({ search, onPoll }: { search: ProjectSearchStatus | null; onPoll: () => void }) {
  const indexing = search?.status === "indexing";
  const [stalled, setStalled] = useState(false);
  const pollRef = useRef(onPoll);
  pollRef.current = onPoll;

  useEffect(() => {
    if (!indexing) {
      setStalled(false);
      return;
    }
    const started = Date.now();
    const timer = setInterval(() => {
      if (Date.now() - started >= SEARCH_POLL_FOR_MS) {
        clearInterval(timer);
        setStalled(true);
        return;
      }
      pollRef.current();
    }, SEARCH_POLL_MS);
    return () => clearInterval(timer);
  }, [indexing]);

  if (!search || search.status === "unavailable") return null;
  if (search.status === "ready") {
    return (
      <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs" data-testid="project-search">
        <span className="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-400">
          <Check className="h-3 w-3" aria-hidden /> Ready for search
        </span>
        {/* A fact, not a task: these files have no text to search. */}
        {search.failed > 0 && (
          <span className="text-muted-foreground">
            {search.failed} document{search.failed === 1 ? "" : "s"} can&apos;t be searched (no readable text)
          </span>
        )}
      </span>
    );
  }
  return (
    <span role="status" className="text-xs text-muted-foreground" data-testid="project-search">
      {stalled
        ? "Still preparing for search — chats can already open these documents by name."
        : `Preparing ${search.indexed} of ${search.total} for search…`}
    </span>
  );
}

function DeleteProject({ project }: { project: Project }) {
  const router = useOptionalRouter();
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirmDelete() {
    setDeleting(true);
    setError(null);
    const r = await deleteProject(project.id);
    if (!r.ok) {
      setDeleting(false);
      setError(r.message);
      return;
    }
    navigateTo(router, "/projects");
  }

  return (
    <section aria-labelledby="project-delete-heading" className="space-y-2 rounded-lg border border-destructive/30 p-4">
      <h2 id="project-delete-heading" className="text-base font-semibold text-foreground">
        Delete project
      </h2>
      {!confirming ? (
        <>
          <p className="text-sm text-muted-foreground">Your documents and chats are kept.</p>
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className="rounded border border-destructive/40 px-3 py-1 text-sm text-destructive hover:bg-destructive/10"
          >
            Delete project…
          </button>
        </>
      ) : (
        <div role="alertdialog" aria-label="Delete project?" className="space-y-2 text-sm">
          <p className="text-foreground">
            Delete &lsquo;{project.name}&rsquo;? Your documents and chats are kept. What the assistant remembers from this project&apos;s chats is deleted.
          </p>
          {error && (
            <p role="alert" className="text-destructive">
              {error}
            </p>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void confirmDelete()}
              disabled={deleting}
              className="rounded border border-destructive/40 px-3 py-1 text-destructive hover:bg-destructive/10 disabled:opacity-50"
            >
              {deleting ? "Deleting…" : "Delete project"}
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirming(false);
                setError(null);
              }}
              disabled={deleting}
              className="rounded border px-3 py-1 text-muted-foreground hover:bg-accent disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function ProjectDocuments({
  docs,
  projectId,
  onOpen,
  onAdd,
}: {
  docs: ReturnType<typeof useProjectDocuments>;
  projectId: string;
  onOpen: (doc: ProjectDocument) => void;
  onAdd: () => void;
}) {
  if (docs.status === "loading" && docs.documents.length === 0) {
    return (
      <ul role="status" aria-label="Loading documents" className="space-y-2">
        {[0, 1, 2].map((i) => (
          <li key={i} className="h-9 animate-pulse rounded-md bg-muted/50" />
        ))}
      </ul>
    );
  }
  if (docs.status === "error" && docs.documents.length === 0) {
    return (
      <div role="alert" className="flex items-center gap-2 rounded-lg border border-destructive/40 p-3 text-sm text-destructive">
        <span className="flex-1">Couldn&apos;t load this project&apos;s documents.</span>
        <button type="button" onClick={docs.retry} className="rounded border border-destructive/40 px-2 py-0.5 text-xs hover:bg-destructive/10">
          Retry
        </button>
      </div>
    );
  }
  if (docs.documents.length === 0) {
    return (
      <div className="rounded-md border border-dashed p-4 text-center text-sm text-muted-foreground" data-testid="project-docs-empty">
        <p>No documents yet.</p>
        <button type="button" onClick={onAdd} className="mt-1 text-xs text-primary underline underline-offset-2">
          Pick from your library
        </button>
      </div>
    );
  }
  return (
    <>
      <ul className="divide-y rounded-md border" aria-label="Project documents">
        {docs.documents.map((d) => (
          <li key={d.id}>
            <ProjectDocumentRow projectId={projectId} doc={d} onOpen={onOpen} />
          </li>
        ))}
      </ul>
      {docs.hasMore && (
        <button
          type="button"
          onClick={docs.loadMore}
          disabled={docs.isLoadingMore}
          className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline disabled:opacity-60"
        >
          {docs.isLoadingMore ? "Loading more…" : "Show more documents"}
        </button>
      )}
      {docs.loadMoreFailed && (
        <p role="alert" className="text-xs text-destructive">
          Couldn&apos;t load more documents.{" "}
          <button type="button" onClick={docs.loadMore} className="underline">
            Retry
          </button>
        </p>
      )}
    </>
  );
}

function GettingStarted({
  hasDocuments,
  hasInstructions,
  onAdd,
}: {
  hasDocuments: boolean;
  hasInstructions: boolean;
  onAdd: () => void;
}) {
  const steps = [
    {
      done: hasDocuments,
      title: "Add the documents you're working with",
      detail: (
        <>
          <button type="button" onClick={onAdd} className="text-primary underline underline-offset-2">
            Pick from your library
          </button>
           or upload new files under Documents. You can also drag a file onto this project in the sidebar.
        </>
      ),
    },
    {
      done: hasInstructions,
      title: "Optional: tell the assistant what this project is about",
      detail: "Instructions apply to every chat in this project, e.g. which clauses matter or how to answer.",
    },
    {
      done: false,
      title: "Start chatting",
      detail: "Chats in this project see its documents and instructions, and remember what you tell them there.",
    },
  ];
  return (
    <section aria-label="Getting started" className="rounded-lg border bg-muted/30 p-4">
      <h2 className="text-sm font-semibold text-foreground">Getting started</h2>
      <ol className="mt-2 space-y-2">
        {steps.map((step, i) => (
          <li key={step.title} className="flex gap-3 text-sm">
            <span
              aria-hidden
              className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-xs ${
                step.done ? "border-emerald-600 bg-emerald-600 text-white" : "text-muted-foreground"
              }`}
            >
              {step.done ? <Check className="h-3 w-3" /> : i + 1}
            </span>
            <span>
              <span className={step.done ? "text-muted-foreground line-through" : "font-medium text-foreground"}>
                {step.title}
              </span>
              {step.done && <span className="sr-only"> (done)</span>}
              {!step.done && <span className="block text-xs text-muted-foreground">{step.detail}</span>}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function ProjectName({ project }: { project: Project }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(project.name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function saveName() {
    const name = draft.trim();
    if (!name) {
      setError("A project name can't be empty.");
      return;
    }
    if (name.length > MAX_PROJECT_NAME) {
      setError(`A project name can be at most ${MAX_PROJECT_NAME} characters.`);
      return;
    }
    if (name === project.name) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setError(null);
    const r = await updateProject(project.id, { name });
    setSaving(false);
    if (r.ok) setEditing(false);
    else setError(r.message);
  }

  return (
    <div className="space-y-1">
      <div className="flex items-start gap-3">
        <FolderKanban className="mt-1 h-5 w-5 shrink-0 text-primary" aria-hidden />
        {editing ? (
          <div className="min-w-0 flex-1 space-y-1">
            <input
              autoFocus
              aria-label="Project name"
              value={draft}
              disabled={saving}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void saveName();
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  setEditing(false);
                  setError(null);
                }
              }}
              onBlur={() => void saveName()}
              className="w-full rounded border bg-background px-2 py-1 text-lg font-semibold text-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
            />
            <p className="text-xs text-muted-foreground" role={saving ? "status" : undefined}>
              {saving ? "Saving…" : "Enter ↵ to save · Esc to cancel"}
            </p>
          </div>
        ) : (
          <p className="min-w-0 flex-1">
            <button
              type="button"
              onClick={() => {
                setDraft(project.name);
                setError(null);
                setEditing(true);
              }}
              title="Rename"
              className="max-w-full truncate rounded text-left text-lg font-semibold text-foreground hover:bg-accent/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
            >
              {project.name}
            </button>
          </p>
        )}
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

function DefaultSkillPicker({ project, skills }: { project: Project; skills: Skill[] }) {
  const [state, setState] = useState<{ kind: "idle" | "saving" | "saved" } | { kind: "error"; message: string }>({ kind: "idle" });
  const [value, setValue] = useState(project.defaultSkillId ?? "");

  useEffect(() => setValue(project.defaultSkillId ?? ""), [project.defaultSkillId]);

  async function change(next: string) {
    const previous = value;
    setValue(next);
    setState({ kind: "saving" });
    const r = await updateProject(project.id, { defaultSkillId: next || null });
    if (r.ok) {
      setState({ kind: "saved" });
    } else {
      setValue(previous);
      setState({ kind: "error", message: r.message });
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <label htmlFor={`default-skill-${project.id}`} className="text-muted-foreground">
        New chats open on
      </label>
      <select
        id={`default-skill-${project.id}`}
        value={value}
        disabled={state.kind === "saving"}
        onChange={(e) => void change(e.target.value)}
        className="rounded-[var(--radius)] border bg-background px-2 py-1 text-sm"
      >
        <option value="">Your default assistant</option>
        {skills.map((s) => (
          <option key={s.skillId} value={s.skillId}>
            {s.displayName || s.name}
          </option>
        ))}
        {/* A default the user can no longer see still needs a label, never an id. */}
        {value && !skills.some((s) => s.skillId === value) && <option value={value}>An assistant you no longer have</option>}
      </select>
      {state.kind === "saving" && (
        <span role="status" className="text-xs text-muted-foreground">
          Saving…
        </span>
      )}
      {state.kind === "saved" && (
        <span role="status" className="text-xs text-emerald-700 dark:text-emerald-400">
          Saved
        </span>
      )}
      {state.kind === "error" && (
        <span role="alert" className="text-xs text-destructive">
          {state.message}
        </span>
      )}
    </div>
  );
}

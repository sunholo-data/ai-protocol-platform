"use client";

import { useEffect, useState } from "react";
import { updateProject, type Project } from "@/hooks/useProjects";
import { fetchWithAuth } from "@/lib/apiClient";

interface KnowledgeBaseOption {
  kbId: string;
  name: string;
  description?: string;
}

/**
 * Knowledge bases this project's chats also search (github-sources.md §2, D9):
 * shared reference material — docs sites, method notes — next to the project's
 * own documents, without copying it in. Only knowledge bases the user can
 * already search are offered; attaching grants nothing. Hidden where the
 * deployment has none (the knowledge-base list doesn't exist or is empty).
 */
export function ProjectKnowledgeBases({ project }: { project: Project }) {
  const [options, setOptions] = useState<KnowledgeBaseOption[] | null>(null);
  const [attached, setAttached] = useState<string[]>(project.attachedKbIds);
  const [pending, setPending] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "status" | "alert"; text: string } | null>(null);
  // A failed list load is NOT "you have no knowledge bases": keep it apart so a
  // network blip never reports attached ones as gone and invites removing them.
  const [loadError, setLoadError] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);

  useEffect(() => setAttached(project.attachedKbIds), [project.attachedKbIds]);
  useEffect(() => {
    let cancelled = false;
    setLoadError(false);
    fetchWithAuth("/api/proxy/api/knowledge-bases")
      .then(async (res) => {
        // 404: this deployment has no knowledge bases — the section stays hidden.
        if (res.status === 404) return [];
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as unknown;
      })
      .then((body) => {
        if (!cancelled) setOptions(Array.isArray(body) ? (body as KnowledgeBaseOption[]).filter((k) => k?.kbId) : []);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [loadAttempt]);

  if (loadError) {
    return (
      <section aria-labelledby="project-kbs-heading" className="space-y-2 rounded-lg border p-4">
        <h2 id="project-kbs-heading" className="text-base font-semibold text-foreground">
          Knowledge bases
        </h2>
        <p role="alert" className="flex items-center gap-2 text-sm text-destructive">
          Couldn&apos;t load knowledge bases.
          <button
            type="button"
            onClick={() => setLoadAttempt((n) => n + 1)}
            className="rounded-md border px-2 py-0.5 text-foreground hover:bg-accent"
          >
            Retry
          </button>
        </p>
      </section>
    );
  }

  if (options === null || (options.length === 0 && attached.length === 0)) return null;

  const names = new Map(options.map((k) => [k.kbId, k.name]));
  // Attached earlier but no longer searchable by you: searches skip it; offer to remove.
  const gone = attached.filter((id) => !names.has(id));

  const toggle = async (kbId: string, on: boolean) => {
    const next = on ? [...attached, kbId] : attached.filter((k) => k !== kbId);
    setPending(kbId);
    setNotice(null);
    const r = await updateProject(project.id, { attachedKbIds: next });
    setPending(null);
    if (!r.ok) {
      setNotice({ kind: "alert", text: r.message });
      return;
    }
    setAttached(next);
    const name = names.get(kbId) ?? "That knowledge base";
    setNotice({
      kind: "status",
      text: on ? `${name} is searched in this project's chats.` : `${name} is no longer searched here.`,
    });
  };

  return (
    <section aria-labelledby="project-kbs-heading" className="space-y-3 rounded-lg border p-4">
      <h2 id="project-kbs-heading" className="text-base font-semibold text-foreground">
        Knowledge bases
      </h2>
      <p className="text-xs text-muted-foreground">
        Chats in this project search these alongside the project&apos;s own documents. Nothing is copied in.
      </p>
      <ul className="space-y-1.5">
        {options.map((kb) => (
          <li key={kb.kbId}>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={attached.includes(kb.kbId)}
                disabled={pending !== null}
                onChange={(e) => void toggle(kb.kbId, e.target.checked)}
              />
              <span>
                <span className="font-medium text-foreground">{kb.name}</span>
                {pending === kb.kbId && <span className="text-muted-foreground"> — saving…</span>}
                {kb.description && <span className="block text-xs text-muted-foreground">{kb.description}</span>}
              </span>
            </label>
          </li>
        ))}
        {gone.map((id) => (
          <li key={id} className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>A knowledge base you can no longer search is still attached.</span>
            <button
              type="button"
              disabled={pending !== null}
              onClick={() => void toggle(id, false)}
              className="whitespace-nowrap rounded-md border px-2 py-0.5 hover:bg-accent disabled:opacity-50"
            >
              Remove it
            </button>
          </li>
        ))}
      </ul>
      {notice && (
        <p role={notice.kind} className={`text-sm ${notice.kind === "alert" ? "text-destructive" : "text-muted-foreground"}`}>
          {notice.text}
        </p>
      )}
    </section>
  );
}

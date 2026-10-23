"use client";

/**
 * Skill Studio: which knowledge bases this skill searches (the
 * `knowledge_search` tool's `knowledge_bases` config).
 *
 * Lists the knowledge bases the author can bind by friendly name, with their
 * size and who can see them, and runs the server's access check live against
 * the skill's own sharing — the exact check the save runs — so a refusal shows
 * here, with its reason and the fix, BEFORE Save (NEVER SILENT).
 */

import { useEffect, useState } from "react";
import { useKnowledgeBases } from "@/hooks/useKnowledgeBases";
import { checkBinding, type BindingVerdict } from "@/lib/knowledgeBases";

const CHECK_DEBOUNCE_MS = 400;

export function KnowledgeBasePicker({
  value,
  onChange,
  includeMyFolders = false,
  onIncludeMyFoldersChange,
  accessControl,
  ownerId,
}: {
  /** Bound knowledge-base ids (or legacy friendly names). */
  value: string[];
  onChange: (next: string[]) => void;
  /** `include_my_folders`: every user's chat also searches their OWN searchable folders. */
  includeMyFolders?: boolean;
  onIncludeMyFoldersChange?: (next: boolean) => void;
  /** The skill's current sharing — what the check measures against. */
  accessControl?: Record<string, unknown>;
  /** The skill's owner (default: the author). */
  ownerId?: string;
}) {
  const { available, list } = useKnowledgeBases();
  const [verdicts, setVerdicts] = useState<Record<string, BindingVerdict>>({});
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  const key = JSON.stringify([value, accessControl ?? null, ownerId ?? ""]);
  useEffect(() => {
    if (value.length === 0) {
      setVerdicts({});
      setCheckError(null);
      return;
    }
    let cancelled = false;
    setChecking(true);
    const timer = setTimeout(() => {
      void checkBinding(value, accessControl, ownerId).then((r) => {
        if (cancelled) return;
        setChecking(false);
        if (!r.ok) {
          setCheckError(r.message);
          return;
        }
        setCheckError(null);
        const byRef: Record<string, BindingVerdict> = {};
        for (const v of r.value.results) {
          byRef[v.ref] = v;
          if (v.kbId) byRef[v.kbId] = v;
        }
        setVerdicts(byRef);
      });
    }, CHECK_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` captures value/accessControl/ownerId by content
  }, [key]);

  if (available === false) return null; // this deployment has no knowledge bases

  const toggle = (kbId: string, on: boolean) =>
    onChange(on ? [...value.filter((v) => v !== kbId), kbId] : value.filter((v) => v !== kbId));

  const listed = new Set(list.map((kb) => kb.kbId));
  // Bound refs that aren't in the author's list: legacy names, or knowledge
  // bases the author can't see. Shown so nothing is dropped silently.
  const extras = value.filter((v) => !listed.has(v) && !list.some((kb) => kb.name === v));
  const refused = Object.values(verdicts).filter((v, i, all) => !v.ok && all.indexOf(v) === i);

  return (
    <fieldset className="space-y-2 rounded-md border p-3" aria-label="Knowledge bases">
      <legend className="px-1 text-sm font-medium">Knowledge bases this skill searches</legend>

      {available === null && <p className="text-xs text-muted-foreground">Loading knowledge bases…</p>}

      {onIncludeMyFoldersChange && (
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={includeMyFolders}
            onChange={(e) => onIncludeMyFoldersChange(e.target.checked)}
          />
          <span className="min-w-0 flex-1">
            <span className="font-medium">Each user&rsquo;s own searchable folders</span>{" "}
            <span className="block text-xs text-muted-foreground">
              Everyone who chats with this skill can search the folders <em>they</em> made searchable in My Documents —
              never anyone else&rsquo;s. Safe for a shared skill; no one has to edit or copy it.
            </span>
          </span>
        </label>
      )}

      {available && list.length === 0 && !includeMyFolders && (
        <p className="text-xs text-muted-foreground">
          You don&rsquo;t have any knowledge bases yet. In My Documents, open a folder&rsquo;s ⋯ menu and choose{" "}
          <strong>Make searchable</strong> — it will appear here.
        </p>
      )}

      {list.map((kb) => {
        const bound = value.includes(kb.kbId) || value.includes(kb.name);
        const v = verdicts[kb.kbId];
        return (
          <label key={kb.kbId} className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={bound}
              onChange={(e) => toggle(kb.kbId, e.target.checked)}
            />
            <span className="min-w-0 flex-1">
              <span className="font-medium">{kb.name}</span>{" "}
              <span className="text-xs text-muted-foreground">
                · {kb.documentCount} document{kb.documentCount === 1 ? "" : "s"} · searchable by {kb.audience ?? "—"}
              </span>
              {bound && v && !v.ok && <span className="block text-xs text-destructive">{v.reason}</span>}
            </span>
          </label>
        );
      })}

      {extras.map((ref) => (
        <label key={ref} className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked onChange={() => toggle(ref, false)} />
          <span className="min-w-0 flex-1">
            <span className="font-medium">{ref}</span>{" "}
            <span className="block text-xs text-destructive">
              {verdicts[ref]?.reason ?? "Not one of your knowledge bases — untick it, or ask its owner for access."}
            </span>
          </span>
        </label>
      ))}

      {value.length === 0 && !includeMyFolders && available && (
        <p className="text-xs text-destructive">Pick at least one — the skill can&rsquo;t be saved with this tool and none.</p>
      )}
      {checking && value.length > 0 && <p className="text-xs text-muted-foreground">Checking who would see these…</p>}
      {checkError && <p role="alert" className="text-xs text-destructive">{checkError}</p>}
      {!checking && refused.length > 0 && (
        <p role="alert" className="text-xs text-destructive">
          Saving will be refused: this skill reaches more people than{" "}
          {refused.length === 1 ? "that knowledge base allows" : "those knowledge bases allow"}. Narrow{" "}
          <em>Who can use this skill</em>, or widen who can search the folder (My Documents → folder ⋯ → Who can
          search…).
        </p>
      )}
      {!checking && value.length > 0 && refused.length === 0 && !checkError && Object.keys(verdicts).length > 0 && (
        <p className="text-xs text-muted-foreground">✓ Everyone who can use this skill may search these.</p>
      )}
    </fieldset>
  );
}

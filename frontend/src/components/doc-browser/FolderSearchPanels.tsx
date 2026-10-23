"use client";

/**
 * Inline panels on a searchable folder (My Documents):
 *
 * - **Add to a skill** — pick a skill (the one you're chatting with first),
 *   see straight away whether it may search this folder, and add it. When the
 *   skill reaches more people than the folder allows, say so plainly and offer
 *   the one-step fix the server proposes (share the folder with exactly that
 *   audience) — never a silent widening. A skill you can't change (shared with
 *   you, or built-in) says so BEFORE any click and offers one button that makes
 *   your own copy and adds the folder to it; a skill that already searches
 *   everyone's own folders says nothing needs adding.
 * - **Who can search** — change the folder's knowledge-base access.
 *
 * Every state renders: loading, checking, refused (with the reason), saving,
 * done, error (NEVER SILENT).
 */

import { useEffect, useMemo, useState } from "react";
import type { KnowledgeBaseInfo } from "@/hooks/useKnowledgeBases";
import { fetchWithAuth } from "@/lib/apiClient";
import {
  KNOWLEDGE_TOOL,
  addKnowledgeBaseToSkill,
  checkBinding,
  forkSkill,
  searchesMyFolders,
  setKnowledgeBaseAccess,
  type BindingVerdict,
} from "@/lib/knowledgeBases";
import { skillHref } from "@/components/navigation/skillHref";
import { AccessControlEditor } from "@/components/studio/AccessControlEditor";

interface SkillRow {
  skillId: string;
  displayName?: string;
  name?: string;
  ownerId?: string;
  accessControl?: Record<string, unknown>;
  slug?: string | null;
  /** "template": the seeder re-applies its SKILL.md on every deploy. */
  managedBy?: string | null;
  /** Per-viewer, from the server's own edit rule. Absent on an older backend. */
  canEdit?: boolean;
  skillMetadata?: {
    tools?: string[];
    toolConfigs?: Record<string, { knowledge_bases?: string[]; include_my_folders?: boolean }>;
    delegation?: { enabled?: boolean; allow?: (string | { skill?: string })[] };
  };
}

/** A specialist this skill hands searches to that already searches the user's
 * own folders — e.g. a front door delegating to a knowledge-search skill. Only
 * delegates in `all` (the skills the viewer can reach) count: delegation is
 * access-filtered at run time too. */
function myFoldersDelegate(s: SkillRow, all: SkillRow[]): SkillRow | undefined {
  const d = s.skillMetadata?.delegation;
  if (!d?.enabled) return undefined;
  const refs = (d.allow ?? []).map((e) => (typeof e === "string" ? e : e.skill)).filter(Boolean);
  return all.find((t) => t.skillId !== s.skillId && (refs.includes(t.skillId) || (!!t.slug && refs.includes(t.slug))) && searchesMyFolders(t.skillMetadata));
}

/** May the viewer change this skill? The server says so (`canEdit`); an older
 * backend that doesn't falls back to "you own it". */
function canChange(s: SkillRow, uid?: string): boolean {
  return s.canEdit ?? (!!uid && s.ownerId === uid);
}

const PANEL = "mx-2 mb-1 space-y-1.5 rounded border px-2 py-1.5 text-[11px] leading-snug";
const BUTTON = "rounded border px-1.5 py-0.5 hover:bg-accent disabled:opacity-50";

function skillLabel(s: SkillRow): string {
  return s.displayName || s.name || s.skillId;
}

function bindsKb(s: SkillRow, kbId: string): boolean {
  const refs = s.skillMetadata?.toolConfigs?.[KNOWLEDGE_TOOL]?.knowledge_bases ?? [];
  return (s.skillMetadata?.tools ?? []).includes(KNOWLEDGE_TOOL) && refs.includes(kbId);
}

function describeAccess(ac: Record<string, unknown> | undefined): string {
  const type = (ac?.type as string) ?? "private";
  if (type === "tagged") return `group ${((ac?.tags as string[]) ?? []).join(", ")}`;
  if (type === "domain") return `everyone at ${ac?.domain as string}`;
  if (type === "specific") return `${((ac?.emails as string[]) ?? []).length} named people`;
  return type === "public" ? "everyone" : "only you";
}

export function AddToSkillPanel({
  kb,
  folderName,
  uid,
  currentSkillId,
  onClose,
}: {
  kb: KnowledgeBaseInfo;
  folderName: string;
  uid?: string;
  currentSkillId?: string;
  onClose: () => void;
}) {
  const [skills, setSkills] = useState<SkillRow[] | null>(null);
  const [allSkills, setAllSkills] = useState<SkillRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [verdict, setVerdict] = useState<BindingVerdict | null>(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState<null | "fork" | "add">(null);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  /** The private copy just made, so the done message can link straight to it. */
  const [forked, setForked] = useState<SkillRow | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetchWithAuth("/api/proxy/api/skills?limit=200");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const all = ((await res.json()) as SkillRow[]) ?? [];
        // The one you're chatting with (even if you can't change it — the panel
        // then explains the fork), plus every skill you can change.
        const mine = all.filter((s) => s.skillId === currentSkillId || canChange(s, uid));
        mine.sort((a, b) => (a.skillId === currentSkillId ? -1 : b.skillId === currentSkillId ? 1 : skillLabel(a).localeCompare(skillLabel(b))));
        if (!cancelled) {
          setAllSkills(all);
          setSkills(mine);
          setSelected(mine[0]?.skillId ?? "");
        }
      } catch (e) {
        if (!cancelled) setLoadError(`Couldn't load your skills (${e instanceof Error ? e.message : "error"}).`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [uid, currentSkillId]);

  const skill = useMemo(() => skills?.find((s) => s.skillId === selected), [skills, selected]);
  const already = skill ? bindsKb(skill, kb.kbId) : false;
  // Every user's chat with this skill already searches their own folders —
  // itself, or through a specialist it hands searches to.
  const viaDelegate = skill && !searchesMyFolders(skill.skillMetadata) ? myFoldersDelegate(skill, allSkills) : undefined;
  const coversMyFolders = !!skill && !already && kb.canManage === true && (searchesMyFolders(skill.skillMetadata) || !!viaDelegate);
  // A template-managed skill's settings are re-applied from its SKILL.md on
  // every deploy — adding the folder there would silently revert — and a skill
  // you can't change refuses the save. Both: offer your own copy instead.
  const templateManaged = skill?.managedBy === "template";
  const mustFork = !!skill && !already && !coversMyFolders && (templateManaged || !canChange(skill, uid));
  const canCheck = !!skill && !already && !coversMyFolders && !mustFork;

  // Picking a different skill starts over. (Only then: a successful add also
  // updates the skill's local copy, and clearing on THAT would wipe the "Done"
  // confirmation the moment it appeared.)
  useEffect(() => {
    setVerdict(null);
    setMessage(null);
    setForked(null);
  }, [selected]);

  // Dry-run the access rule when what it depends on changes: the skill, its
  // sharing and owner — not its tool list.
  const accessKey = JSON.stringify([skill?.skillId, skill?.accessControl ?? null, skill?.ownerId ?? ""]);
  useEffect(() => {
    if (!skill || !canCheck) return;
    let cancelled = false;
    setChecking(true);
    void checkBinding([kb.kbId], skill.accessControl, skill.ownerId).then((r) => {
      if (cancelled) return;
      setChecking(false);
      if (r.ok) setVerdict(r.value.results[0] ?? null);
      else setMessage({ kind: "error", text: r.message });
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- accessKey captures skill's access fields by content
  }, [accessKey, canCheck, kb.kbId]);

  function markBound(skillId: string) {
    setSkills((prev) =>
      (prev ?? []).map((s) =>
        s.skillId === skillId
          ? {
              ...s,
              skillMetadata: {
                ...s.skillMetadata,
                tools: [...new Set([...(s.skillMetadata?.tools ?? []), KNOWLEDGE_TOOL])],
                toolConfigs: {
                  ...s.skillMetadata?.toolConfigs,
                  [KNOWLEDGE_TOOL]: { knowledge_bases: [...(s.skillMetadata?.toolConfigs?.[KNOWLEDGE_TOOL]?.knowledge_bases ?? []), kb.kbId] },
                },
              },
            }
          : s,
      ),
    );
  }

  async function add(widen: boolean) {
    if (!skill) return;
    setSaving("add");
    setMessage(null);
    if (widen && verdict?.widenTo) {
      const w = await setKnowledgeBaseAccess(kb.kbId, verdict.widenTo);
      if (!w.ok) {
        setSaving(null);
        setMessage({ kind: "error", text: w.message });
        return;
      }
    }
    const r = await addKnowledgeBaseToSkill(skill.skillId, kb.kbId);
    setSaving(null);
    if (!r.ok) {
      setMessage({ kind: "error", text: r.message });
      return;
    }
    markBound(skill.skillId);
    setMessage({ kind: "ok", text: `Done — ${skillLabel(skill)} can now search ‘${folderName}’. Start a new chat to use it.` });
  }

  /** Make a private copy of the skill (yours, so you may change it), then add the folder to it. */
  async function forkAndAdd() {
    if (!skill) return;
    setSaving("fork");
    setMessage(null);
    const f = await forkSkill(skill.skillId);
    if (!f.ok) {
      setSaving(null);
      setMessage({ kind: "error", text: f.message });
      return;
    }
    const copy: SkillRow = { ...skill, ...f.value, accessControl: { type: "private" }, managedBy: "firestore", canEdit: true };
    setSaving("add");
    const r = await addKnowledgeBaseToSkill(copy.skillId, kb.kbId);
    setSaving(null);
    // The copy exists either way: list it, so a failed add can be retried on it.
    setSkills((prev) => [...(prev ?? []), copy]);
    setForked(copy);
    if (!r.ok) {
      setMessage({ kind: "error", text: `Your copy ‘${skillLabel(copy)}’ was made, but the folder wasn't added: ${r.message}` });
      return;
    }
    markBound(copy.skillId);
    setMessage({ kind: "ok", text: `Done — your copy ‘${skillLabel(copy)}’ can search ‘${folderName}’.` });
  }

  const busy = saving !== null;

  return (
    <div role="dialog" aria-label="Add this folder to a skill" className={PANEL}>
      <p className="flex items-baseline justify-between gap-2 font-medium text-foreground">
        <span>Let a skill search ‘{folderName}’</span>
        <a
          href="/help/folders#let-a-skill-search-the-folder"
          target="_blank"
          rel="noopener"
          className="shrink-0 font-normal text-muted-foreground underline underline-offset-2 hover:text-foreground"
        >
          How this works
        </a>
      </p>
      {loadError && <p role="alert" className="text-destructive">{loadError}</p>}
      {!skills && !loadError && <p className="text-muted-foreground">Loading your skills…</p>}
      {skills && skills.length === 0 && (
        <p className="text-muted-foreground">You have no skills you can change. Create one in Skill Studio and pick this folder there.</p>
      )}
      {skills && skills.length > 0 && (
        <select
          aria-label="Skill"
          className="w-full rounded border bg-background px-1.5 py-1 text-[11px]"
          value={selected}
          disabled={busy}
          onChange={(e) => setSelected(e.target.value)}
        >
          {skills.map((s) => (
            <option key={s.skillId} value={s.skillId}>
              {skillLabel(s)}
              {s.skillId === currentSkillId ? " (this chat)" : ""}
            </option>
          ))}
        </select>
      )}

      {skill && already && !message && <p className="text-muted-foreground">{skillLabel(skill)} already searches this folder.</p>}

      {coversMyFolders && skill && (
        <p className="text-foreground">
          ✓ Nothing to add: {skillLabel(skill)} already searches your own searchable folders
          {viaDelegate ? ` (through ${skillLabel(viaDelegate)})` : ""}, including ‘{folderName}’. Just ask it.
        </p>
      )}

      {mustFork && skill && !message && (
        <div className="space-y-1 rounded border border-amber-400/60 px-1.5 py-1">
          <p className="text-foreground">
            {templateManaged && canChange(skill, uid) ? (
              <>
                {skillLabel(skill)} is a built-in skill: its settings are reset from its template on every deploy, so
                adding the folder there wouldn&rsquo;t last.
              </>
            ) : (
              <>You can&rsquo;t change {skillLabel(skill)} — it&rsquo;s shared with you, not yours.</>
            )}{" "}
            Make your own copy instead: same instructions and tools, private to you, and able to search ‘{folderName}’.
          </p>
          <button type="button" className={BUTTON} disabled={busy} onClick={() => void forkAndAdd()}>
            {saving === "fork" ? "Copying the skill…" : saving === "add" ? "Adding the folder…" : `Make my copy and add ‘${folderName}’`}
          </button>
          <p className="text-muted-foreground">
            Your copy won&rsquo;t pick up later changes to {skillLabel(skill)}. Or ask its owner to add the folder.
          </p>
        </div>
      )}

      {checking && <p className="text-muted-foreground">Checking who would see it…</p>}

      {canCheck && verdict?.ok && !message && (
        <button type="button" className={BUTTON} disabled={busy} onClick={() => void add(false)}>
          {saving ? "Adding…" : `Add to ${skill ? skillLabel(skill) : "skill"}`}
        </button>
      )}

      {canCheck && verdict && !verdict.ok && !message && (
        <div className="space-y-1">
          <p className="text-foreground">
            {skill ? skillLabel(skill) : "This skill"} is used by <strong>{describeAccess(skill?.accessControl)}</strong>, but ‘
            {folderName}’ is searchable by <strong>{kb.audience ?? "only you"}</strong>. Adding it would let them search
            these documents.
          </p>
          {verdict.widenTo && verdict.canWiden ? (
            <button type="button" className={BUTTON} disabled={busy} onClick={() => void add(true)}>
              {saving ? "Sharing…" : `Share ‘${folderName}’ with ${describeAccess(verdict.widenTo)} and add`}
            </button>
          ) : (
            <p className="text-muted-foreground">{verdict.reason}</p>
          )}
        </div>
      )}

      {message && (
        <p role={message.kind === "error" ? "alert" : "status"} className={message.kind === "error" ? "text-destructive" : "text-foreground"}>
          {message.text}
        </p>
      )}
      {forked && message?.kind === "ok" && (
        <a className={`${BUTTON} inline-block`} href={skillHref({ skillId: forked.skillId, slug: forked.slug ?? null, ownerId: forked.ownerId ?? "" })}>
          Chat with {skillLabel(forked)}
        </a>
      )}
      <button type="button" className={`${BUTTON} text-muted-foreground`} onClick={onClose} disabled={busy}>
        {message?.kind === "ok" || coversMyFolders ? "Close" : "Cancel"}
      </button>
    </div>
  );
}

export function SearchAccessPanel({
  kb,
  folderName,
  onClose,
}: {
  kb: KnowledgeBaseInfo;
  folderName: string;
  onClose: () => void;
}) {
  const [access, setAccess] = useState<Record<string, unknown>>(kb.accessControl ?? { type: "private" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    const r = await setKnowledgeBaseAccess(kb.kbId, access);
    setSaving(false);
    if (r.ok) onClose();
    else setError(r.message);
  }

  return (
    <div role="dialog" aria-label="Who can search this folder" className={PANEL}>
      <AccessControlEditor
        label={`Who can search ‘${folderName}’`}
        types={["private", "tagged", "domain", "specific"]}
        value={access}
        onChange={setAccess}
      />
      <p className="text-muted-foreground">
        Skills can only reach people this allows. Narrowing it takes effect immediately, including for skills already
        using it.
      </p>
      {error && <p role="alert" className="text-destructive">{error}</p>}
      <div className="flex gap-1.5">
        <button type="button" className={BUTTON} disabled={saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" className={`${BUTTON} text-muted-foreground`} disabled={saving} onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}

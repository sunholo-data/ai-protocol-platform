// SKILL-DELEGATION M4 — Skill Studio delegation editor.
//
// Lets a skill author turn on delegation and pick which specialist skills this
// skill may hand off to. The allow-picker is ACCESS-SCOPED: it lists only
// skills the author can access (GET /api/skills already filters by access), and
// the backend independently re-filters per requesting user at agent-build time,
// so `allow` can never grant access a user doesn't already have.

"use client";

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import type { DelegateEntry, DelegationConfig } from "@/types/skill";

/** Skill id off either shape a backend `allow` entry can take: a bare string, or
 * a `{skill, floor}` rule (v6.8.0 8.2). Rendering the raw object as a React child
 * is what crashed the Studio on skills like the ONE front door. */
const entryId = (e: DelegateEntry): string => (typeof e === "string" ? e : (e?.skill ?? ""));

interface SkillOption {
  id: string;
  label: string;
}

interface DelegationEditorProps {
  value?: DelegationConfig;
  /** The skill being edited — excluded from the allow-picker (no self-delegation). */
  currentSkillId?: string;
  onChange: (next: DelegationConfig) => void;
}

const DEFAULT: DelegationConfig = { enabled: false, mode: "auto", allow: [], maxDepth: 1 };

export function DelegationEditor({ value, currentSkillId, onChange }: DelegationEditorProps) {
  const cfg: DelegationConfig = { ...DEFAULT, ...value };
  const [skills, setSkills] = useState<SkillOption[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetchWithAuth("/api/proxy/api/skills");
        const data: unknown = await res.json();
        const list: SkillOption[] = (Array.isArray(data) ? data : [])
          .map((s) => {
            const r = s as { skillId?: string; id?: string; displayName?: string; name?: string };
            const id = r.skillId ?? r.id ?? "";
            return { id, label: r.displayName || r.name || id };
          })
          .filter((s) => s.id && s.id !== currentSkillId);
        if (!cancelled) setSkills(list);
      } catch {
        if (!cancelled) setSkills([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [currentSkillId]);

  // Normalise to skill-id strings for the UI; `cfg.allow` entries may be bare
  // strings OR {skill, floor} objects (and could be missing/null on bad data).
  const allowEntries: DelegateEntry[] = Array.isArray(cfg.allow) ? cfg.allow : [];
  const allowIds = allowEntries.map(entryId).filter(Boolean);

  const update = (patch: Partial<DelegationConfig>) => onChange({ ...cfg, ...patch });
  // Toggle by id while PRESERVING existing entries' shape (a {skill, floor} rule
  // keeps its floor); adds go in as a bare-string id (backend accepts as sugar).
  const toggleAllow = (id: string) =>
    update({
      allow: allowIds.includes(id)
        ? allowEntries.filter((e) => entryId(e) !== id)
        : [...allowEntries, id],
    });

  // Show any allowed ids that aren't in the fetched list (e.g. a skill the
  // author lost access to) so they're visible + removable rather than silently
  // dropped from the UI.
  const known = new Set(skills.map((s) => s.id));
  const orphanAllowed = allowIds.filter((id) => !known.has(id));

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">Delegation</h3>
          <p className="text-xs text-muted-foreground">
            Let this skill hand a question to a specialist skill when it would answer better.
          </p>
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={cfg.enabled}
            onChange={(e) => update({ enabled: e.target.checked })}
            aria-label="Enable delegation"
          />
          Enabled
        </label>
      </div>

      {cfg.enabled && (
        <div className="flex flex-col gap-3 rounded-md border border-border p-3">
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 text-xs font-medium text-muted-foreground">When it hands off</legend>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="radio"
                name="delegation-mode"
                checked={cfg.mode === "auto"}
                onChange={() => update({ mode: "auto" })}
              />
              <span>
                <span className="font-medium">Automatic</span> — hand off directly when the specialist fits.
              </span>
            </label>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="radio"
                name="delegation-mode"
                checked={cfg.mode === "suggest"}
                onChange={() => update({ mode: "suggest" })}
              />
              <span>
                <span className="font-medium">Suggest</span> — propose the handoff and let the user confirm.
              </span>
            </label>
          </fieldset>

          <div className="flex flex-col gap-1">
            <div className="text-xs font-medium text-muted-foreground">
              Can hand off to {allowIds.length > 0 ? `(${allowIds.length})` : ""}
            </div>
            {loading ? (
              <p className="text-xs text-muted-foreground/70">Loading skills…</p>
            ) : skills.length === 0 && orphanAllowed.length === 0 ? (
              <p className="text-xs text-muted-foreground/70">No other skills you can access.</p>
            ) : (
              <ul className="flex max-h-56 flex-col gap-0.5 overflow-auto rounded border border-border p-1.5">
                {skills.map((s) => (
                  <li key={s.id}>
                    <label className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs hover:bg-muted/40">
                      <input
                        type="checkbox"
                        checked={allowIds.includes(s.id)}
                        onChange={() => toggleAllow(s.id)}
                      />
                      <span className="truncate">{s.label}</span>
                    </label>
                  </li>
                ))}
                {orphanAllowed.map((id) => (
                  <li key={id}>
                    <label className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs text-muted-foreground/70 hover:bg-muted/40">
                      <input type="checkbox" checked onChange={() => toggleAllow(id)} />
                      <span className="truncate font-mono">{id}</span>
                      <span className="text-[10px]">(no access)</span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-[10px] text-muted-foreground/60">
              A user only ever sees handoffs to skills they can access — this list is a ceiling, not a grant.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

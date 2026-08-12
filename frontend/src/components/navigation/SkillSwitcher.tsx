"use client";

import { useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import * as Popover from "@radix-ui/react-popover";
import { Check, ChevronsUpDown, Star } from "lucide-react";
import type { Skill } from "@/types/skill";
import { cn } from "@/lib/utils";
import { skillHref } from "./skillHref";
import { SkillStatusBadge } from "@/components/skills/SkillStatusBadge";

/**
 * SkillSwitcher — a single, professional skill picker that replaces the old
 * horizontal tab row (which overflowed and looked cluttered once a deployment
 * had many skills). One trigger shows the active skill; clicking opens a
 * searchable, grouped popover of every skill.
 *
 * v6.11.0 — RICH GROUPING. The flat "production + in-development" split became a
 * categorised list so a tenant with many skills can tell them apart at a glance:
 *
 *   1. Default        — the tenant's front door (`clients/me.default_skill`),
 *                       pinned at the top and highlighted.
 *   2. Category groups — Specialists / Assistants / Tools, driven by each
 *                       skill's `skillMetadata.category` (presentation hint only,
 *                       never an access gate).
 *   3. Your skills     — skills the signed-in user owns (their own tools).
 *   4. In development  — admin/demo/test skills (see {@link isTestSkill}); only
 *                       admins ever receive these from the backend.
 *
 * Every skill lands in exactly one group (precedence: in-development → default →
 * yours → category), so nothing is listed twice. Empty groups are omitted.
 *
 * `system`-tagged skills never appear at all (see {@link isSystemSkill}): they
 * are platform-embedded agents a specific surface mounts directly by slug (the
 * Skill Studio copilot, future help assistants) — never something a user
 * "switches to". Search can't surface them either.
 */

/** Tags that mark a skill as non-production (admin / demo / test surface). */
const TEST_TAGS = new Set([
  "experimental",
  "dev-tool",
  "a2ui-demo",
  "demo",
  "workshop",
  "admin",
]);

/** Tag that marks a skill as a platform-embedded system agent (e.g. the Skill
 * Studio copilot). System skills are mounted by their host surface directly by
 * slug and are excluded from the switcher entirely — presentation only; access
 * is still enforced by `accessControl` on the backend. */
const SYSTEM_TAG = "system";

/** Sentinel ownerId for skills shipped by Aitana Labs (mirrors
 * backend/skills/platform.py + useUserSkills). Platform skills are NEVER
 * treated as the user's "own" skills, even for a platform-admin viewer. */
const PLATFORM_OWNER_UID = "aitana-platform";

/** Category → section label + order. Free-form `category` values fall through to
 * the "Other skills" bucket, so a fork can introduce its own taxonomy without a
 * code change here (they just group under "Other" until a label is added). */
const CATEGORY_SECTIONS: ReadonlyArray<{ key: string; label: string }> = [
  { key: "specialist", label: "Specialists" },
  { key: "assistant", label: "Assistants" },
  { key: "tool", label: "Tools" },
];
const OTHER_CATEGORY_LABEL = "Other skills";

/** True when a skill's tags intersect the admin/test tag set. */
export function isTestSkill(skill: Pick<Skill, "tags">): boolean {
  return (skill.tags ?? []).some((t) => TEST_TAGS.has(t));
}

/** True for platform-embedded system agents — hidden from the picker. */
export function isSystemSkill(skill: Pick<Skill, "tags">): boolean {
  return (skill.tags ?? []).includes(SYSTEM_TAG);
}

function skillName(skill: Skill): string {
  return skill.displayName || skill.name || skill.skillId.slice(0, 8);
}

function categoryOf(skill: Skill): string | null {
  const c = skill.skillMetadata?.category;
  return c ? c.trim().toLowerCase() : null;
}

function monogram(skill: Skill): string {
  const n = skillName(skill).trim();
  return n ? n[0]!.toUpperCase() : "?";
}

function SkillAvatar({ skill }: { skill: Skill }) {
  if (skill.avatar) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={skill.avatar} alt="" className="h-5 w-5 shrink-0 rounded-sm object-cover" />;
  }
  return (
    <span
      aria-hidden
      className="flex h-5 w-5 shrink-0 items-center justify-center rounded-sm bg-muted text-[10px] font-medium text-muted-foreground"
    >
      {monogram(skill)}
    </span>
  );
}

interface SkillSwitcherProps {
  skills: Skill[];
  activeSkillId: string;
  /** The tenant's default skill id — pinned + highlighted at the top. */
  defaultSkillId?: string | null;
  /** The signed-in user's uid — skills they own group under "Your skills". */
  currentUserId?: string | null;
  /** Optional hook to notify a parent when a skill is chosen (before navigation). */
  onNavigate?: (skill: Skill) => void;
}

/** A rendered group: a labelled (or highlighted) run of skill rows. */
interface Group {
  key: string;
  label: string | null;
  skills: Skill[];
  highlight?: boolean;
}

export function SkillSwitcher({
  skills,
  activeSkillId,
  defaultSkillId,
  currentUserId,
  onNavigate,
}: SkillSwitcherProps) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const active = skills.find((s) => s.skillId === activeSkillId);

  const filtered = useMemo(() => {
    // System agents (Skill Studio copilot, help assistants) are never
    // switch-to-able — drop them before search so typing can't reveal them.
    const selectable = skills.filter((s) => !isSystemSkill(s));
    const q = query.trim().toLowerCase();
    if (!q) return selectable;
    return selectable.filter((s) => {
      const hay = `${s.displayName ?? ""} ${s.name ?? ""} ${s.description ?? ""}`.toLowerCase();
      return hay.includes(q);
    });
  }, [skills, query]);

  // Partition every skill into exactly one group. Precedence matters:
  // in-development first (admin skills always corral together), then the pinned
  // default, then the user's own skills, then the category buckets.
  const groups = useMemo<Group[]>(() => {
    const out: Group[] = [];
    const test: Skill[] = [];
    const yours: Skill[] = [];
    const byCategory = new Map<string, Skill[]>();
    let defaultSkill: Skill | null = null;

    for (const s of filtered) {
      if (isTestSkill(s)) {
        test.push(s);
      } else if (defaultSkillId && s.skillId === defaultSkillId) {
        defaultSkill = s;
      } else if (currentUserId && s.ownerId === currentUserId && s.ownerId !== PLATFORM_OWNER_UID) {
        yours.push(s);
      } else {
        const key = categoryOf(s) ?? "__other__";
        (byCategory.get(key) ?? byCategory.set(key, []).get(key)!).push(s);
      }
    }

    if (defaultSkill) {
      out.push({ key: "default", label: "Default", skills: [defaultSkill], highlight: true });
    }
    const knownSections = CATEGORY_SECTIONS.filter(({ key }) => byCategory.get(key)?.length);
    for (const { key, label } of knownSections) {
      out.push({ key, label, skills: byCategory.get(key)! });
    }
    // Any category value we don't have an explicit section for. Only LABEL this
    // bucket when there are real category sections to distinguish it from —
    // otherwise (an uncategorised deployment) the plain list stays header-free
    // and clean, exactly as before categories existed.
    const knownKeys = new Set(CATEGORY_SECTIONS.map((c) => c.key));
    const other = [...byCategory.keys()].filter((k) => !knownKeys.has(k)).flatMap((k) => byCategory.get(k)!);
    if (other.length) {
      out.push({ key: "other", label: knownSections.length ? OTHER_CATEGORY_LABEL : null, skills: other });
    }
    if (yours.length) out.push({ key: "yours", label: "Your skills", skills: yours });
    if (test.length) out.push({ key: "test", label: "In development", skills: test });
    return out;
  }, [filtered, defaultSkillId, currentUserId]);

  function selectSkill(skill: Skill) {
    setOpen(false);
    setQuery("");
    onNavigate?.(skill);
    router.push(skillHref(skill));
  }

  function renderRow(s: Skill, highlight?: boolean) {
    const isActive = s.skillId === activeSkillId;
    return (
      <button
        key={s.skillId}
        type="button"
        onClick={() => selectSkill(s)}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-muted",
          highlight && "bg-accent/60 ring-1 ring-border",
          isActive && !highlight && "bg-muted/60",
        )}
      >
        <SkillAvatar skill={s} />
        <span className="min-w-0 flex-1 truncate">{skillName(s)}</span>
        {highlight ? <Star className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden /> : null}
        <SkillStatusBadge tags={s.tags} variant="dot" />
        {isActive ? <Check className="h-4 w-4 shrink-0 text-foreground" aria-hidden /> : null}
      </button>
    );
  }

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery("");
      }}
    >
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label="Switch skill"
          className="flex h-8 max-w-[16rem] items-center gap-2 rounded-md border px-2 text-sm text-foreground transition-colors hover:bg-muted"
        >
          {active ? (
            <>
              <SkillAvatar skill={active} />
              <span className="min-w-0 truncate">{skillName(active)}</span>
            </>
          ) : (
            <span className="text-muted-foreground">Select a skill</span>
          )}
          <ChevronsUpDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={6}
          onOpenAutoFocus={(e) => {
            // Focus the search input rather than the first row.
            e.preventDefault();
            searchRef.current?.focus();
          }}
          className="z-50 w-72 rounded-md border bg-background p-1 shadow-md outline-none"
        >
          <input
            ref={searchRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search skills…"
            aria-label="Search skills"
            className="mb-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground focus:border-ring"
          />
          <div className="max-h-[70vh] overflow-y-auto">
            {groups.length === 0 ? (
              <div className="px-2 py-3 text-sm text-muted-foreground">No skills found</div>
            ) : (
              groups.map((g, i) => (
                <div key={g.key} className="py-1">
                  {i > 0 ? <div className="mx-2 mb-1 border-t" /> : null}
                  {g.label ? (
                    <div className="px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                      {g.label}
                    </div>
                  ) : null}
                  {g.skills.map((s) => renderRow(s, g.highlight))}
                </div>
              ))
            )}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

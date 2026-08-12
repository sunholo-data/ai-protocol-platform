# Sprint: Skills Bar — Horizontal Skill Navigation (1.11)

**Sprint ID:** SKILLS-BAR
**Design source:** [document-ui.md §1 Skills Navigation Bar](../document-ui.md) — carved out of DOC-UI-IMPL because the rendering pivot consumed all of 1.10's slot.
**Scope:** Frontend-only (no backend changes — `GET /api/skills?ownerId={uid}` already exists).
**Estimated:** 2 days · **Actual:** 1 day
**Status:** ✅ Shipped 2026-04-27

## Implementation Report

**Files added:**
- `frontend/src/hooks/useUserSkills.ts`
- `frontend/src/hooks/__tests__/useUserSkills.test.tsx` (4 tests)
- `frontend/src/components/navigation/skillHref.ts`
- `frontend/src/components/navigation/SkillTab.tsx`
- `frontend/src/components/navigation/SkillsBar.tsx`
- `frontend/src/components/navigation/__tests__/skillHref.test.ts` (4 tests)
- `frontend/src/components/navigation/__tests__/SkillsBar.test.tsx` (5 tests)
- `frontend/src/app/skills/new/page.tsx` (stub — wizard pending)

**Files changed:**
- `frontend/src/types/skill.ts` — added `slug?: string | null` to mirror backend `SkillResponse.slug`
- `frontend/src/app/chat/[skillId]/page.tsx` — replaced minimal `<header>` with `<SkillsBar>`, added `useUserSkills` hook, `+` button routes to `/skills/new`

**Deviations from plan:**
- Kept `useSkillMeta` in the chat page — it's still used to label assistant messages in `ChatMessageList`, so dropping it would have caused a regression in the chat UX. The bar reads display name directly from the `Skill` payload returned by `useUserSkills`, so the tab and the message bubble use independent sources but both hit `/api/skills`.
- Used `animated-aitana-square.svg` for the bar logo (28×28) rather than the wide `animated-aitana.svg` — better fits the 48px-tall bar and matches the existing chat bubble avatar.

**Catch-all route follow-up (2026-04-27, post-pull):** origin/dev's commit 732de1e replaced `[skillId]/page.tsx` with `[...path]/page.tsx` for friendly URLs. Pulled and re-applied the SkillsBar wiring to the surviving route (the SkillsBar and `useUserSkills` are route-agnostic — same 4 imports + 1 hook + JSX swap).

**Outstanding follow-up (separate sprints):**
- Real skill creation wizard (replaces the `/skills/new` stub).
- Doc context preservation across skill switch — explicit out-of-scope here.

**Test results:** 13 new tests, all passing. Full vitest run: 242 passed, 1 pre-existing failure in `DocListItem.test.tsx` (unrelated — caused by commit a45385e adding a delete button without updating the test's `getByRole("button")` query).

---

## Sprint Goal

Replace the minimal `<header><h1>{displayName}</h1></header>` in `frontend/src/app/chat/[skillId]/page.tsx` (line 164) with a horizontal **SkillsBar**: tabs for the user's skills + a `+` button. Active tab = the skill in the current URL. Clicking another tab navigates to that skill's chat. The Aitana logo lives in the left corner.

This is the last visible piece of the [document-workspace.html mockup](/frontend/public/mockups/document-workspace.html) that hasn't shipped (see `SEQUENCE.md` mockup coverage table).

---

## What's Already Done

| Component | Status |
|-----------|--------|
| `GET /api/skills?ownerId={uid}` (filter user's skills) | ✅ `backend/skills/routes.py:124` |
| `Skill` type with `displayName`, `avatar`, `slug?` | ✅ `frontend/src/types/skill.ts` |
| Skill chat route — `/chat/[skillId]` (UUID) and `/chat/[...path]` (friendly) | ✅ both shipped |
| `useSkillMeta(skillId)` — fetches single skill display name | ✅ `frontend/src/hooks/useSkillMeta.ts` (will become redundant once we have the bar — keep for non-chat callers) |
| Brand assets (logo `animated-aitana.svg`) | ✅ from v5 |
| `useAuth()` exposing `user.uid` | ✅ `frontend/src/contexts/AuthContext.tsx` |

## What's Missing

| Gap | Milestone |
|-----|-----------|
| `useUserSkills()` hook fetching `GET /api/skills?ownerId=me` with caching | M0 |
| `SkillTab` + `SkillsBar` components | M1 |
| Wire SkillsBar into ChatShell header (replace lines 164–166) | M2 |
| `+` button → existing skill creation route (stub `/skills/new` if missing) | M3 |

---

## Pre-flight

```bash
cd frontend && npm run quality:check:fast   # confirm clean baseline
cd frontend && npx vitest run                # all tests green
```

---

## Milestones

### M0 — `useUserSkills` hook (~0.25d)

**Scope:** frontend · **Depends on:** nothing.

**File:** `frontend/src/hooks/useUserSkills.ts` (new).

```typescript
import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import type { Skill } from "@/types/skill";

interface UseUserSkillsReturn {
  skills: Skill[];
  isLoading: boolean;
  error: string | null;
}

export function useUserSkills(uid: string | null): UseUserSkillsReturn {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!uid) { setSkills([]); return; }
    setIsLoading(true);
    setError(null);
    const controller = new AbortController();
    fetchWithAuth(`/api/proxy/api/skills?ownerId=${encodeURIComponent(uid)}`, {
      signal: controller.signal,
    })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() as Promise<Skill[]>; })
      .then(s => setSkills(s))
      .catch((e: Error) => { if (e.name !== "AbortError") setError("Could not load skills."); })
      .finally(() => setIsLoading(false));
    return () => controller.abort();
  }, [uid]);

  return { skills, isLoading, error };
}
```

**Acceptance:**
- [ ] Returns owner's skills when `uid` is set; empty list otherwise
- [ ] Aborts in-flight request on unmount or uid change
- [ ] Vitest: 3 cases (success, no uid, abort) — mock `fetchWithAuth`

---

### M1 — `SkillTab` + `SkillsBar` components (~0.5d)

**Scope:** frontend · **Depends on:** M0.

**Files:**
- `frontend/src/components/navigation/SkillTab.tsx` (new)
- `frontend/src/components/navigation/SkillsBar.tsx` (new)

**`SkillTab`:**
```typescript
interface SkillTabProps {
  skill: Skill;
  active: boolean;
  href: string;        // `/chat/@{ownerEmail-slug}/{slug}` if slug, else `/chat/{skillId}`
}
```
- Renders an `<a>` (use Next.js `<Link>`) with display name + optional avatar (24px square).
- Active state: orange underline (use existing brand orange token), bolder weight.
- Hover state: light bg.
- Truncate display name to ~16 chars with ellipsis; tooltip shows full name (`title` attribute is fine for v1).
- No protocol badges in v1 — punt to a v6.2 enhancement.

**`SkillsBar`:**
```typescript
interface SkillsBarProps {
  skills: Skill[];
  activeSkillId: string;
  isLoading: boolean;
  onCreateClick: () => void;
}
```
- Layout: `flex items-center` row with logo left → tabs (overflow-x-auto) → `+` button right.
- Logo: import existing `/public/images/logo/animated-aitana.svg` as a `<Link href="/">` anchor (24–28px tall).
- Loading state: 3 skeleton tabs (use existing `Skeleton` component if present, else simple `bg-muted animate-pulse`).
- Empty state (user has no skills): render only logo + `+` button labeled "Create your first skill".
- URL helper: small `skillHref(skill)` util — prefers `slug` (`/chat/@{uid}/{slug}`) when present, falls back to `/chat/{skillId}`. **Match the catch-all route shape on origin/dev** — read `frontend/src/app/chat/[...path]/page.tsx` first to confirm the exact path format.

**Acceptance:**
- [ ] Renders skills as horizontal tabs, active one underlined
- [ ] Empty state when `skills.length === 0`
- [ ] Loading state when `isLoading === true`
- [ ] Vitest: 4 cases (renders skills, marks active, empty state, loading state)
- [ ] `npm run quality:check:fast` passes

---

### M2 — Wire SkillsBar into ChatShell header (~0.5d)

**Scope:** frontend · **Depends on:** M1.

**Target file:** `frontend/src/app/chat/[skillId]/page.tsx` (and the `[...path]` catch-all if it has its own ChatShell wrapper).

**Replace** (line ~164):
```tsx
<header className="flex items-center border-b px-4 py-2">
  <h1 className="text-sm font-medium">{displayName}</h1>
</header>
```

**With:**
```tsx
<SkillsBar
  skills={userSkills}
  activeSkillId={skillId}
  isLoading={skillsLoading}
  onCreateClick={() => router.push("/skills/new")}
/>
```

- Add `const { skills: userSkills, isLoading: skillsLoading } = useUserSkills(user.uid);` near the existing hook calls (around line 82–97).
- Drop `useSkillMeta` from this page (the bar already has the display name); keep the import elsewhere only if other callers exist. Verify with `grep -rn useSkillMeta frontend/src` first.
- The doc tabs row (`DocTabsBar`) stays directly below SkillsBar — two horizontal bars stacked.

**Acceptance:**
- [ ] Active skill tab highlighted on every chat page
- [ ] Clicking another tab navigates to that skill's chat (loses doc context — that's expected; cross-skill doc preservation is out of scope)
- [ ] No regression in DocTabsBar layout
- [ ] Manual smoke (with `make dev`): create 2 skills, switch between them via the bar, both chats work

---

### M3 — `+` button wires to skill creation (~0.25d)

**Scope:** frontend · **Depends on:** M2.

The `+` button currently has no destination. Two options, in order of preference:

1. **If `/skills/new` route already exists** (check `frontend/src/app/skills/`): `router.push("/skills/new")`. Done.
2. **If not**: stub a minimal `frontend/src/app/skills/new/page.tsx` that says "Skill creation wizard — coming soon" and links back. The full wizard is a separate sprint.

**Acceptance:**
- [ ] `+` button navigates somewhere coherent (existing wizard or stub)
- [ ] Stub page passes typecheck and renders
- [ ] No "404" or broken-link UX

---

### M4 — Smoke + docs (~0.25d)

- [ ] `cd frontend && npm run quality:check` (full)
- [ ] `cd frontend && npx vitest run` (full)
- [ ] Update [document-ui.md](document-ui.md) "Implementation Status" — move SkillsBar from "carved out" to ✅
- [ ] Update [SEQUENCE.md](SEQUENCE.md) — mark 1.11 ✅, mockup coverage row ✅
- [ ] Move this sprint doc to `implemented/skills-bar-sprint.md`

---

## Day-by-Day Plan

| Time | Work |
|------|------|
| Day 1 AM | M0: `useUserSkills` hook + tests |
| Day 1 PM | M1: `SkillTab` + `SkillsBar` components + tests |
| Day 2 AM | M2: wire into ChatShell, drop `useSkillMeta` from chat page, smoke test |
| Day 2 PM | M3: `+` button wiring (stub if needed) + M4: doc updates + final quality gate |

---

## LOC Estimates

| Milestone | Impl | Tests | Total |
|-----------|------|-------|-------|
| M0 — `useUserSkills` | 40 | 50 | 90 |
| M1 — SkillTab + SkillsBar | 110 | 80 | 190 |
| M2 — ChatShell wiring | 20 | 0 (covered by smoke) | 20 |
| M3 — `+` button | 20 | 10 | 30 |
| M4 — Docs | 0 | 0 | 0 |
| **Total** | **190** | **140** | **~330** |

---

## Quality Gates

After each milestone:
```bash
cd frontend && npm run quality:check:fast
```

Final:
```bash
cd frontend && npm run quality:check
cd frontend && npx vitest run
```

Manual smoke (requires `make dev`):
1. Log in, create a second skill via `aitana skill create` (or seeded test data)
2. Visit `/chat/{skill1-id}` — see both skills in bar, skill1 active
3. Click skill2 — URL changes, skill2 now active, chat empty (or loads its history)
4. Click `+` — lands on creation page (real or stub)
5. Logo in top-left navigates to `/`

---

## Out of Scope

These are NOT in this sprint — explicitly so the scope stays at 2 days:

- Skill creation wizard (the `+` button just routes; the wizard itself is a separate sprint)
- Document context preservation across skill switch (mentioned in document-ui.md §"Skill switching"; non-trivial because each skill has its own session — punt)
- Marketplace / public skill discovery from the bar
- Protocol badges (A2UI/MCP icons under skill name)
- Drag-to-reorder tabs
- Recently-used public skills
- Mobile-optimized layout (skills bar on mobile is a separate UX problem — for now, horizontal scroll is fine)
- Replacing `useSkillMeta` everywhere — only drop from the chat page; other callers (if any) keep using it

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `[...path]` catch-all route has its own ChatShell wrapper that also needs updating | Medium | Grep for `<header className=.*border-b` in `frontend/src/app/chat/` and update both if so |
| `GET /api/skills?ownerId={uid}` over-returns or under-returns due to access-control filter (`backend/skills/routes.py:135`) | Low | Owner queries are fast-path in `can_access_skill`; verify with a quick API hit at M0 |
| User has 50+ skills → bar overflows without scroll | Low | `overflow-x-auto` on the tab container handles it; not pretty but functional. Drag/scroll polish is a separate sprint |
| `[...path]` route uses `params.path[]` array shape — `skillHref` needs to match | Medium | Read `frontend/src/app/chat/[...path]/page.tsx` (origin/dev) before writing the helper |
| `/skills/new` route doesn't exist | Medium | M3 stub covers this — 20-line page, zero risk |

---

## References

- Parent design: [document-ui.md](document-ui.md) §1 Skills Navigation Bar
- Skills API: `backend/skills/routes.py` `list_skills`
- Skill type: `frontend/src/types/skill.ts`
- Chat page: `frontend/src/app/chat/[skillId]/page.tsx`
- Friendly URL design: `docs/design/v6.1.0/skill-friendly-urls.md` (origin/dev)
- Mockup: `frontend/public/mockups/document-workspace.html`

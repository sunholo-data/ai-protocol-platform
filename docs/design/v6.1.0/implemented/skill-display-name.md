# Skill Display Name

**Status**: Implemented
**Priority**: P1 (Medium)
**Estimated**: 0.25 days
**Scope**: Frontend (backend endpoint already exists)
**Dependencies**: [chat-message-rendering.md](implemented/chat-message-rendering.md) ✅
**Created**: 2026-04-24
**Last Updated**: 2026-04-24

## Problem Statement

**Current State:**
- The chat page header and `MessageBubble` skill name header both display the raw `skillId` UUID (e.g., `63f6d5b2-a206-4ee7-9571-bfa4e7a9ed60`)
- Skills have a `display_name` field stored in Firestore and returned by `GET /api/skills/{skill_id}` (already implemented)
- The route is authenticated and requires only a valid Firebase session — the same session already present in the chat page

**Impact:**
- The skill UUID is meaningless to users; the chat header and bot bubble header look broken
- Users cannot tell which skill they are talking to

## Goals

**Primary Goal:** Fetch the skill's `display_name` (falling back to `name`) on chat page mount and use it in the header and `MessageBubble` instead of the UUID.

**Success Metrics:**
- Chat header shows skill display name (e.g., "Research Assistant") not UUID
- `MessageBubble` skill name header shows display name
- Fallback: if fetch fails, show a truncated UUID (first 8 chars) — never blank, never broken
- No loading flash: show UUID/truncated name immediately, swap to display name when resolved

**Non-Goals:**
- Skill avatar/icon in the header (separate concern)
- Editing the skill name from the chat page
- Caching skill metadata across sessions (simple in-memory per-mount is fine)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | One background fetch on mount; not in the critical render path |
| 2 | EARNED TRUST | +1 | Users can see which skill they are talking to — reduces confusion about provenance |
| 3 | SKILLS, NOT FEATURES | +1 | Makes the skill identity legible — core to the skills-not-features mental model |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model changes |
| 5 | GRACEFUL DEGRADATION | +1 | Fallback to truncated UUID; page never breaks if fetch fails |
| 6 | PROTOCOL OVER CUSTOM | 0 | Uses existing REST endpoint; no protocol boundary |
| 7 | API FIRST | 0 | Consuming an existing API; no new surface |
| 8 | OBSERVABLE BY DEFAULT | 0 | No new data surface |
| 9 | SECURE BY CONSTRUCTION | 0 | Uses existing authenticated fetch via /api/proxy; no new trust surface |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Display name is fetched from backend, not computed client-side |
| | **Net Score** | **+4** | Threshold: >= +4 ✓ |

## Design

### Overview

Add a `useSkillMeta` hook that fetches `GET /api/proxy/api/skills/{skillId}` on mount and returns `{ displayName: string; loading: boolean }`. Use it in `ChatShell` and pass `displayName` down to `ChatMessageList` → `MessageBubble` and the page header.

### Frontend Changes

**New hook:** `frontend/src/hooks/useSkillMeta.ts`

**Modified:** `frontend/src/app/chat/[skillId]/page.tsx` — use `displayName` from hook in header and pass to `ChatMessageList`

**`useSkillMeta.ts`:**

```typescript
import { useEffect, useState } from "react";
import { apiClient } from "@/lib/apiClient";

interface SkillMeta {
  displayName: string;
  loading: boolean;
}

export function useSkillMeta(skillId: string): SkillMeta {
  const [displayName, setDisplayName] = useState<string>(skillId.slice(0, 8));
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get(`/api/skills/${skillId}`)
      .then((data: { display_name?: string; name?: string }) => {
        if (!cancelled) {
          setDisplayName(data.display_name || data.name || skillId.slice(0, 8));
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
        // displayName stays as truncated UUID fallback
      });
    return () => { cancelled = true; };
  }, [skillId]);

  return { displayName, loading };
}
```

**`ChatShell` integration:**

```tsx
function ChatShell({ skillId, user }: { skillId: string; user: User }) {
  const { displayName } = useSkillMeta(skillId);
  // ...
  return (
    <main className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b px-4 py-2">
        <h1 className="text-sm font-medium">{displayName}</h1>
      </header>
      <ChatMessageList
        // ...
        skillId={displayName}  // MessageBubble uses this as the label
      />
    </main>
  );
}
```

**Fallback chain:** `display_name` → `name` → first 8 chars of UUID. This means the UI never shows a blank label or a full UUID.

### Backend Changes

None. `GET /api/skills/{skill_id}` already exists at `backend/skills/routes.py:151`, returns `SkillResponse` including `display_name`. No backend work required.

### API Changes

None — consuming an existing endpoint.

### CLI Surface

None.

## Implementation Plan

### Phase 1: Hook + wire-in (~0.15 day)
- [ ] Create `useSkillMeta.ts` with fetch + fallback logic
- [ ] Wire `displayName` into `ChatShell` header and `ChatMessageList skillId` prop

### Phase 2: Tests + quality gate (~0.1 day)
- [ ] `useSkillMeta` tests: returns truncated UUID initially; resolves to display_name; falls back to name if display_name absent; stays as truncated UUID on fetch error
- [ ] `npm run quality:check:fast` + `npm run test:run` clean

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] `useSkillMeta`: initial value is first 8 chars of skillId
- [ ] `useSkillMeta`: resolves to `display_name` from API response
- [ ] `useSkillMeta`: falls back to `name` if `display_name` is empty
- [ ] `useSkillMeta`: stays as truncated UUID if fetch throws (graceful degradation)
- [ ] `useSkillMeta`: cancels in-flight fetch on unmount (no state update after unmount)

### Manual Testing
- [ ] Open `/chat/{skillId}` → header briefly shows 8-char UUID, then swaps to display name
- [ ] Open with a non-existent skill ID → header stays as 8-char UUID, no error thrown

## Security Considerations

- `GET /api/skills/{skill_id}` requires auth and returns 404 for inaccessible skills — no information leakage
- The hook reads only `display_name` and `name` from the response; no sensitive data stored

## Migration & Rollout

No migration. Skills that have no `display_name` set fall back to `name` (always set). No existing data needs changing.

## Success Criteria

- [ ] `npm run test:run` passes
- [ ] `npm run quality:check:fast` passes
- [ ] Chat header shows human-readable skill name, not UUID
- [ ] `MessageBubble` skill label shows display name
- [ ] Fetch failure leaves header showing truncated UUID (no blank, no crash)

## Related Documents

- [chat-message-rendering.md](implemented/chat-message-rendering.md) — MessageBubble that uses skillId as a label
- [local-dev-cli.md](local-dev-cli.md) — CLI context
- [Product Axioms](../../product-axioms.md)

---

## Implementation Report

**Completed**: 2026-04-24
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]

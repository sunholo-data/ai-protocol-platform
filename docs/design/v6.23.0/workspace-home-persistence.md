# Workspace Home Persistence — don't destroy the launcher to show a result

**Status**: Implemented 2026-08-07 (pending Dana's confirmation)
**Priority**: P1 (Medium) — the most-repeated UX request of the UAT; cheap relative to its value
**Estimated**: ~1 day
**Scope**: Frontend
**Dependencies**: None. Touches `frontend/src/components/chat/ChatShell.tsx` (workbench tab assembly).
**Created**: 2026-08-06
**Last Updated**: 2026-08-06

## Problem Statement

Dana returned to this point four times during the 2026-08-06 UAT — more than
any other single item:

> "The initial page you have was Workspace and a list of what this assistant can
> do, and you click on 'chart DK1 market prices' and now you have Workspace
> results... When you're having a conversation with this assistant it would be
> nice to also **have the list of skills this assistant can do for other
> iterations, instead of creating a new chat**."

> "In the same chat, if you want to keep the same conversation but use a new
> skill, you cannot do that in the Workspace anymore."

And her own proposed fix, which is the right one:

> "Do you want to have like another tab called Results that shows what is now
> showing the Workspace? ...I just wanted to keep the Workspace options so it's
> easier."

She also made the case for why it matters beyond her own use:

> "It's useful for our colleagues in the company, because switching and
> selecting manually the assistant — maybe they have too many assistants and
> they get confused."

That is the launcher doing the job the skill dropdown cannot: teaching people
what the assistant can do by showing them, in place, without asking them to
know a skill's name in advance.

### Current state (traced in code)

In [`ChatShell.tsx`](../../../frontend/src/components/chat/ChatShell.tsx), the
Workspace tab's content is chosen by:

```tsx
const showHome = !workspaceHasContent && hasArtifacts;
```

with the fallback chain — dominant A2UI workspace surface → `CompareLauncher` →
`SkillExamplesPicker` → empty. So the moment a dominant `workspace` surface
arrives, `workspaceHasContent` flips true, `showHome` goes false, and the
launcher tiles are **replaced** by the result. The Workspace tab is doing two
unrelated jobs — "here is what you can start" and "here is what came back" —
and the second evicts the first.

Result tabs already exist and already work: `artifactTabs` gives each artifact
its own closable tab via `artifact_meta` (the 7.5 workbench-artifacts model). A
*dominant workspace surface* is the one result kind that does not get that
treatment, and it is exactly the one that destroys the home.

This is also why Mark's answer in the meeting ("you do have access up here") did
not land — the skill dropdown starts a **new chat**, losing the conversation.
Dana's objection was never about reachability; it was about reachability
*without abandoning the thread*.

**Impact:**
- **Who:** every user, most acutely non-super-users, who Dana says need the
  launcher as their route into skills.
- **How significant:** major friction, and it undercuts the discovery surface we
  built. It also pushes users to start new chats, which compounds the context
  loss tracked in [conversation-context-fidelity.md](conversation-context-fidelity.md).

## Goals

**Primary Goal:** The Workspace home stays reachable for the entire life of a
conversation, no matter how many results arrive, without starting a new chat.

**Success Metrics:**
- Launcher/examples reachable in ≤1 click at any point in a conversation
  (today: impossible without a new chat once a workspace surface lands).
- Starting a second skill mid-conversation requires zero new chats.
- Auto-focus behaviour is preserved — a new result still takes the stage
  (repo principle #7); the home is retained, not prioritised.

**Non-Goals:**
- Redesigning the launcher's contents. Dana likes it; keep it.
- Changing skill delegation. The agent already routes correctly — she wants a
  faster *manual* path alongside it.
- Removing the skill dropdown. It stays for deliberate new-chat switching.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Restores one-click starts; the alternative is "new chat, re-establish context, retype". |
| 2 | EARNED TRUST | 0 | No change to correctness or claims. |
| 3 | SKILLS, NOT FEATURES | +1 | The launcher IS the skill-discovery surface; keeping it visible is keeping skills primary. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model impact. |
| 5 | GRACEFUL DEGRADATION | +1 | The home becomes a stable fallback that always exists rather than something a result can destroy. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses the existing 7.5 artifact-tab model for the one surface currently exempt from it — removes a special case rather than adding one. |
| 7 | API FIRST | 0 | Frontend only. |
| 8 | OBSERVABLE BY DEFAULT | 0 | No telemetry change. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data access. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Client-side tab assembly; no protocol change. |
| | **Net Score** | **+4** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Design

### Overview

Split the Workspace tab's two jobs. **Home** becomes a permanent tab that always
renders the launcher/examples/index. A dominant workspace surface stops
overwriting it and instead becomes a **Result tab**, the same as every other
artifact — which is the model the codebase already has for everything else.

### Frontend Changes

**Modified: `ChatShell.tsx` (workbench tab assembly)**

- Drop the `showHome = !workspaceHasContent && hasArtifacts` condition. The Home
  tab renders `WorkbenchHome` when there are artifacts to index, and the
  launcher/picker otherwise — but **never** the dominant workspace surface.
- Promote the dominant `workspace` surface into `artifactTabs` so it gets its own
  tab, label and close affordance, exactly like a clauses/prices/sources result.
  This deletes a special case; it does not add one.
- Preserve auto-focus: when the promoted surface first arrives, focus moves to
  its Result tab (principle #7 — auto-focus new workbench elements). The user
  still lands on the result; the difference is that Home is one click away
  instead of gone.

**Tab bar, before and after:**

```
before:  [Workspace*]  [Document]  [Activity]        * launcher, then clobbered by the result
after:   [Home]  [Result: DK1 prices]  [Document]  [Activity]
```

**Naming.** Dana proposed "Results" for the new tab and keeping "Workspace" for
the home. The codebase already labels artifact tabs with eyebrow `Result` and a
friendly title (`DE_LU prices`), so promoting the workspace surface into that
family gives her the shape she asked for for free. Whether the home tab reads
**Home** or stays **Workspace** is a copy decision for Mark — v6.11.0 deliberately
kept the label "Workspace" (OQ4). Recommendation: keep **Workspace** to avoid
re-teaching a name ONE has already learned, and let the eyebrow do the work.

**Empty/edge states (principle #8 — never silent):**
- Zero artifacts, zero launcher (a plain skill): Home shows the examples picker,
  else the existing empty-state copy. Never a blank pane.
- Closing the last Result tab returns focus to Home — already the behaviour in
  `handleCloseArtifact`.

### Backend Changes

None.

## Implementation Plan

### Phase 1: Split the tabs (~0.5 day)
- [ ] Home tab renders launcher/index unconditionally; never the dominant surface (~30 LOC)
- [ ] Promote the dominant `workspace` surface into `artifactTabs` (~40 LOC)
- [ ] Preserve first-arrival auto-focus onto the promoted tab (~20 LOC)

### Phase 2: States and polish (~0.5 day)
- [ ] Verify empty/launcher-less/artifact-less states all render something (~20 LOC)
- [ ] Tests (below) (~120 LOC)

## Migration & Rollout

**Database Migrations:** None.
**Feature Flags:** None — small, reversible, and directly requested by the user who will verify it.
**Rollback Plan:** Revert the commit; no persisted state.
**Environment Variables:** None.

## Testing Strategy

### Frontend Tests (Vitest + RTL)
- [ ] Launcher is still on screen after a workspace surface arrives — **the regression guard**
- [ ] The dominant workspace surface gets its own Result tab, with a close affordance
- [ ] First arrival auto-focuses the Result tab (principle #7 not regressed)
- [ ] Closing the last Result tab returns focus to Home
- [ ] With no artifacts and no launcher, Home renders the picker or empty state, never blank

### Manual Testing
- [ ] Run a prices query, then start a second skill from the launcher **in the same chat** — Dana's exact journey
- [ ] Confirm session/thread continuity across that switch (no new chat)
- [ ] Verify with two results open that both remain independently reachable

## Security Considerations

None. No new data access; a surface already rendered for this user moves between
tabs in the same authenticated client.

## Performance Considerations

Negligible — one additional tab in an existing list. No extra fetches or
subscriptions; the surface is already registered either way.

## Success Criteria

- [ ] Frontend tests passing (`npm run test:run`)
- [ ] Lint and typecheck clean (`npm run quality:check`)
- [ ] Launcher reachable in ≤1 click at any point in a conversation
- [ ] A second skill can be started mid-conversation without a new chat
- [ ] Auto-focus on new results preserved
- [ ] Dana confirms the behaviour before 1 Sept

## Implementation notes (2026-08-07)

Three things the plan did not anticipate.

### 1. Persistence is gated on `canDelegate`, not applied to every skill

Mark's call during implementation, and it is the right one: those tiles
("Compare two PPAs", "Run an obligation analysis") are *delegation prompts* —
the front door answers them by handing off to a specialist. A skill that cannot
delegate would be advertising routing it can't perform, which is a dead end
(CLAUDE.md #8). So the picker persists only for skills that can hand off, and
specialists keep the original first-turn-only onboarding behaviour.

The signal is the existing v6.7.0 delegation policy, surfaced as
`useSkillMeta().canDelegate`: `delegation.enabled` **and** (a non-empty `allow`
list **or** `discoverJobs`), **or** the deprecated-but-live `subSkills` form.
Three forms because all three genuinely delegate; gating on `allow.length` alone
would misread a `discoverJobs` door (v6.8.0 8.3) as a specialist. It fails
CLOSED — an API error yields `canDelegate: false`, so we under-promise.

The rule follows the *reason* rather than the skill name, so `one-ppa-expert`
(which delegates to `one-obligation-analysis`) keeps its tiles too. Today only
`one-assistant` ships `examplePrompts` at all, so the gate is currently a no-op
for every other skill — it is a guard against the failure mode as specialists
grow prompts, not a change in present behaviour.

### 2. B4's root cause was found here, as predicted — and it was a real bug

The triage guessed B4 ("Workspace tab sometimes needs a second click") would
surface during this work. It did, reproduced live on 2026-08-07: with a prices
result open and auto-focused, clicking Workspace mid-run showed Home, and by the
time the run finished focus was back on the Result tab.

The auto-focus effect compared the current artifact ids against *the previous
render's* ids and then replaced the set. `SurfaceRegistry.listArtifacts()` only
returns surfaces whose `state.surface` is non-null, so an artifact drops out for
as long as its surface is being re-registered — one render later it is back, and
because the "seen" set had been overwritten it read as brand NEW and stole focus
again. A user's deliberate click was silently undone; they click again and it
"works".

Fixed by making "new" mean *never seen this session*. The rule is extracted to
[`frontend/src/lib/workbenchFocus.ts`](../../../frontend/src/lib/workbenchFocus.ts)
so the blink sequence is testable directly (same precedent as `compareLauncher.ts`) —
the ChatShell-level test could not reproduce it, because a re-emission through
the event router never nulls the surface. An explicit user close releases the
latch, so a deliberately closed result that the agent re-emits still takes the
stage.

**B4 is not closed by this.** One live reproduction and a plausible mechanism is
not proof that this was the *only* mechanism. Dana should confirm the symptom
is gone.

### 3. The workbench no longer auto-folds for skills with a launcher or examples

`hasContent` includes `showLauncher || showPicker`, and those no longer switch
off after the first turn — so for a front door the pane stays open for the whole
conversation instead of folding once chat starts. That is required (Home is only
"≤1 click away" if the pane is there), and the collapse chevron remains as the
user's escape hatch.

## Open Questions

- **"Home" or "Workspace" for the home tab?** Recommendation above; Mark's call.
  Shipped as **Workspace** with a `HOME` eyebrow, per the recommendation.
- **Ordering on Home.** Currently launcher → picker → results index, so the
  index sits below five document cards. Kept that way because the launcher is
  what Dana asked to reach and every result already has its own tab, but if the
  index turns out to be the thing people scroll for, flip it.
- **Should Home also list recent documents?** Mark floated it in the meeting
  ("these could be the last documents you have been working on"). Good idea,
  separate change — do not let it delay this one.
- **Does the launcher need a compact mode** once several Result tabs exist?
  Only worth it if Home starts feeling cramped in real use.

## Related Documents

- UAT source record (internal notes)
- [workbench-artifacts-model.md](../v6.7.0/implemented/workbench-artifacts-model.md) — the 7.5 tab model this extends
- [`frontend/src/components/protocols/CLAUDE.md`](../../../frontend/src/components/protocols/CLAUDE.md) — A2UI render path

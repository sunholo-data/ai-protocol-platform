# Jobs & Subagents Extensibility

**Status**: Implemented (pattern: job flag + access-scoped discovery + first job skill + CLI + subagent doc). One deferred cleanup — removing the inline `map_ppa_obligations` tool from `one-ppa-expert` (changes live ONE UX; a reviewable follow-up). Fleet/async jobs remain design-ahead.
**Priority**: P2
**Estimated**: ~2.5 days (pattern + first job) + design-ahead
**Scope**: Backend + config
**Dependencies**: 8.2 first-impression-elicited-handoff (the front door that reaches jobs), 8.1 elicitation-in-chat-primitive (L2 forms), 7.1 skill-delegation ✅, 7.6 ppa-obligation-analysis ✅
**Created**: 2026-07-14
**Last Updated**: 2026-07-14

## Problem Statement

Once the front door (8.2) can delegate, we need a repeatable way to add **more
specialist "jobs"** without hand-wiring each one — and a clear story for when a job
should spin up a **subagent** rather than run inline.

**Current State:**
- Expensive operations are unevenly modeled: `one-doc-compare` is its own skill, but
  **obligation analysis is a tool + launcher inside `one-ppa-expert`**
  (`map_ppa_obligations`, `start_obligation_analysis`) — so the front door can't
  delegate to it as a discrete confirmable job.
- The front door's `delegation.allow` is a **hand-maintained list**. Every new specialist
  means editing every door that should reach it — this decays as the fleet grows.
- Subagents exist (ADK `sub_agents` for auto-delegation; `AgentTool` for search/code,
  `backend/tools/search_agent.py`, `code_execution/agent.py`) but there's no documented
  pattern for "assign a scoped subagent to a job."

**Impact:**
- The "more skills for jobs, subagents assigned" direction has no paved road; each addition
  is bespoke and the allow-lists rot.

## Goals

**Primary Goal:** A documented, low-ceremony pattern for adding a delegatable **job skill**
(with a confirmation floor + optional elicited inputs) and for **assigning a subagent** to a
job — plus **access-scoped discovery** so the front door finds jobs without hand-edited lists.

**Success Metrics:**
- Obligation analysis is a first-class **L2 job skill** the front door delegates to, reusing the 8.1 form.
- Adding a new job skill requires **only** its `SKILL.md` (tag + floor) — no edit to the front door.
- A job can run a scoped subagent with session/context continuity (no context re-plumbing).

**Non-Goals:**
- A general workflow/DAG engine — jobs are single delegatable skills, not multi-node pipelines.
- Long-running/async background jobs surviving the request (that's a separate future doc; here jobs complete within a stream).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Jobs are the slow path by definition; front door stays fast (8.2). |
| 2 | EARNED TRUST | +1 | L2 jobs keep the human in the loop with an engine-validated form before expensive/consequential compute. |
| 3 | SKILLS, NOT FEATURES | +1 | A "job" is just a tagged skill with a floor — no new user abstraction; obligation analysis becomes discoverable/selectable. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Each job pins its own tier (`smart`) via `resolve_model_chain`; the door stays `lite`. |
| 5 | GRACEFUL DEGRADATION | +1 | An inaccessible/failed job degrades to the door; per-delegate build failure already degrades gracefully. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Reuses ADK `sub_agents`/`AgentTool` + the delegation + elicitation protocols; no bespoke job runner. |
| 7 | API FIRST | +1 | Discovery + floors are server-side skill config; identical across channels. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Job invocation traces as a delegation + tool run; subagent activity shows in Activity. |
| 9 | SECURE BY CONSTRUCTION | +1 | Discovery is **access-scoped** (a door offers only jobs the user can access) — deny-by-default preserved; the `job:true` tag is a filter, not a grant. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | All job/subagent orchestration is backend; frontend renders markers/forms only. |
| | **Net Score** | **+9** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Design

### Overview

Define a "job skill" as a normal skill tagged `job: true` that declares a confirmation
floor (usually `confirm` or `confirm_with_fields`) and pins its own model tier. The front
door discovers jobs the user can access instead of enumerating them by hand. A job may run
inline or delegate to a scoped subagent via existing ADK idioms.

### Backend Changes

**1. Job skill convention (config, not code):**
- A job skill sets `skill_metadata` tag/flag `job: true`, a confirmation floor
  (`delegation`-side, per 8.2), and `model`/`thinkingModel` as it needs (e.g. `smart`).
- **Obligation-analysis becomes a job skill** (`one-obligation-analysis`): move the
  `map_ppa_obligations` tool + `start_obligation_analysis` launcher into it; its L2
  elicitation form is the 8.1 primitive's `confirm_with_fields`. `one-ppa-expert` keeps
  clause/vocab/search and delegates obligation work down (floor `confirm_with_fields`).

**2. Access-scoped discovery — front-door delegation resolution
(`_resolve_accessible_delegates`, `backend/adk/agent.py:378`):**
- Extend so a door can opt into "offer any accessible skill tagged `job:true`" in addition
  to (or instead of) an explicit `allow`. The access filter stays the hard gate: a user
  who can't access a job never sees it. `allow` remains supported for pinned/curated doors.

**3. Subagent assignment (documented pattern, minimal code):**
- **Inline delegate** (default): the job is an ADK `sub_agent` of the door (auto) or a
  re-issued turn (confirm/confirm_with_fields) — one Runner, shared session.
- **Scoped subagent-as-tool**: when a job needs a helper with a *different* model/toolset
  but must return control to the job (not the user), wrap it as an `AgentTool` (the search/
  code pattern). Document when to choose which; both share session/memory on the thread.

### Subagent Pattern (inline `sub_agent` vs `AgentTool`) — worked example

Two ADK idioms attach a subagent to a job. Pick by a single question:

> **Does control return to the caller when the subagent is done?**

- **No — the subagent becomes the destination → inline `sub_agent`.** The model
  `transfer_to_agent`s and the *user now talks to the subagent*. This is exactly how an
  `auto`-floor delegate (or a discovered auto-floor job) is wired today in
  `create_agent` (`backend/adk/agent.py`): `sub_agents=[…]`, one Runner, one shared
  session/thread. A `confirm`/`confirm_with_fields` job is the same destination reached
  via `request_handoff` → the confirm→switch loop re-issues the turn on the job
  (`surface-action-run`), still one thread. Use this when the job *is* where the work
  should continue — e.g. the front door handing off to **one-obligation-analysis**: the
  analyst keeps talking to the obligation specialist, which owns the rest of the turn.

- **Yes — the subagent is a helper that hands results back → `AgentTool`.** Wrap an
  `LlmAgent` as an `AgentTool` and add it to the job's `tools`. The job *calls* it like a
  function, it does scoped work with its OWN model/toolset, and returns a value the job
  keeps reasoning over — the user never "lands" on it. This is the established
  search/code pattern: `tools/search_agent.py` and `code_execution/agent.py` each build an
  `LlmAgent` exposed to every skill via `AgentTool` (see `adk/agent.py`, which returns two
  separate `AgentTool` instances). Use this when a job needs, say, a market-price lookup on
  a cheaper model mid-analysis but must stay in control of the settlement narrative.

Both share session + memory on the same `thread_id`, so context carries over with no
re-plumbing either way. Rule of thumb: **destinations are `sub_agent`s; helpers are
`AgentTool`s.** A job that would need to consume delegation `max_depth` to reach a helper
is a smell — use an `AgentTool` (which does NOT consume delegation depth) instead (OQ2).

### CLI Surface

- `aiplatform skill list --jobs` — list skills tagged `job:true` the caller can access.
- `aiplatform skill probe <door>` already shows delegation; extend to note discovered jobs.

## Implementation Plan

### Phase 1: Obligation-as-job (~1d)
- [x] New `one-obligation-analysis` job skill (`job:true`, `job_floor:confirm`,
      `map_ppa_obligations` + doc tools, tagged ONE/aitana-admin). **Additive** — the
      front door reaches it via discovery; `one-ppa-expert` KEEPS its inline tool for now
      (removing it changes live ONE UX, so it's a reviewable follow-up, not shipped blind).
- [x] `job_floor: confirm` (not `confirm_with_fields`): `map_ppa_obligations` already emits
      its own engine-validated assumptions form, so a field form at the handoff would double
      up. The door asks a light "run the deep analysis on <doc>?" OK.
- [ ] Deferred cleanup: remove inline `map_ppa_obligations` from `one-ppa-expert`; make it
      delegate down. (Reviewable — changes the specialist's UX.)

### Phase 2: Access-scoped discovery (~1d)
- [x] `discover_jobs` door opt-in (`DelegationConfig.discover_jobs`) + `job`/`job_floor` on
      `SkillMetadata`; `find_jobs(owner_id)` + seam in `create_agent` (access-filtered,
      deduped vs explicit `allow`, resolved-id dedupe in `_resolve_accessible_delegates`).
- [x] `aiplatform skill list --jobs` (access-scoped via `GET /api/skills`).

### Phase 3: Subagent pattern doc + example (~0.5d)
- [x] Documented inline `sub_agent` vs `AgentTool` decision + worked example (above).

## Migration & Rollout

- Moving obligation into a job skill is behind ONE's tagged access; the front door's floor
  governs exposure. Rollback = re-inline the tool on `one-ppa-expert`.
- Discovery is opt-in per door; existing pinned `allow` lists keep working.

## Testing Strategy

### Backend Tests (pytest)
- [ ] Obligation flow works when reached as a delegated L2 job (not inline).
- [ ] `job:true` discovery returns only accessible jobs (deny-by-default holds for a non-tagged user).
- [ ] AgentTool subagent shares session state with its parent job.

### Manual
- [ ] Front door → "analyze obligations for X" → L2 form → submit → verified settlement renders (real browser).

## Security Considerations

- Discovery is an **access filter over the user's own reachable skills** — never a way to
  reach a skill the access evaluator would deny. The `job:true` tag only narrows, never widens.
- Subagents run under the same authenticated user; no cross-user context.

## Success Criteria

- [x] Obligation analysis is a delegatable job; the front door reaches it via discovery
      with no bespoke wire (floor `confirm`, not L2 — the tool self-elicits; see Phase 1).
- [x] A new job skill needs only its `SKILL.md` (`job:true`) to be discoverable by an
      opted-in door (`discover_jobs`) — proven by `one-obligation-analysis`.
- [x] Subagent pattern documented with a working example (inline `sub_agent` vs `AgentTool`).
- [ ] Real-browser E2E: front door → "analyse obligations for X" → confirm → obligation
      artefact renders (deferred to deployed-dev verification, per the sprint plan).

## Open Questions

- OQ1: Discovery default — do curated doors (like `one-assistant`) prefer an explicit `allow`
  for a predictable menu, with `job:true` discovery as an opt-in? (Leaning: yes — explicit for the flagship door, discovery for generic doors.)
- OQ2: Should `max_depth` grow beyond 1 for job→subagent chains, or do jobs always run their subagents as `AgentTool` (which doesn't consume delegation depth)? (Leaning: AgentTool for helpers; keep `max_depth` conservative.)
- OQ3 (design-ahead): async/long-running jobs that outlive the request stream — out of scope here; note as a future doc.

## Related Documents

- [first-impression-elicited-handoff.md](first-impression-elicited-handoff.md) — the door that delegates to jobs (8.2)
- [elicitation-in-chat-primitive.md](elicitation-in-chat-primitive.md) — L2 job forms (8.1)
- [ppa-obligation-analysis.md](../v6.7.0/implemented/ppa-obligation-analysis.md) — the op becoming a job (7.6)
- [skill-delegation.md](../v6.7.0/implemented/skill-delegation.md) — delegation + access filter (7.1)

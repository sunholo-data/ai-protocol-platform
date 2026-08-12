# Workshop helper agent — the meta-demo

**Status**: Proposed
**Priority**: P1 (workshop-experience-critical; July 2026 ramp + Croatia WebSummerCamp first session)
**Estimated**: ~2 days (helper skill + show-and-tell tool + workspace pane wiring + anon-group join flow + slides)
**Scope**: Platform — net-new skill `workshop-helper`, two new tools, one new workspace-pane mode, one slide artefact. **No protocol-level changes.** Stands on the four AIPLA extensions already shipped (2.11–2.14) + multi-surface (2.9) + surface-context loop (2.10).
**Dependencies**:
- Path B shipped (docs-corpus seed + minimal helper skill) — pre-requisite, separate ticket
- Sprint 2.11 anonymous-group-id-auth ✅ (attendee join code)
- Sprint 2.12 budget-enforcement ✅ (per-attendee budget visible during workshop)
- Sprint 2.13 artefact-render-hook ✅ (defence-in-depth on the iframe sandbox)
- Sprint 2.14 tenant-id-span-attribute ✅ (live "watch the wire" trace filtering per-attendee)
- Sprint 2.9 multi-surface-rendering ✅ (show-and-tell workspace pane)
- Sprint 2.10 a2ui-surface-context ✅ (attendees see their submission appear without refresh)
**Surfaced by**: Workshop agenda amplifier #2 — "the helper agent IS the meta-demo." Promise has been in the marketing copy + the agenda since 2026-05-19; this is the doc that turns it into a buildable spec.
**Created**: 2026-05-20

---

## Problem Statement

The workshop agenda promises a helper agent that "uses every protocol, demonstrates all four AIPLA template-extensions, and is preloaded with the workshop's docs corpus" (`agenda.md` amplifier #2). Path B will land the smallest version of this — a skill that can RAG over `docs/workshop/`, `docs/design/v6.X.Y/implemented/`, and `docs/integrations/` and answer attendee questions with real ground truth.

But Path B alone is just a chat skill. The workshop's **make-or-break moment** is block 5 (55 min hands-on), and the agenda promises that "attendees submit their skill via the helper agent" and "show-and-tell submissions update live in the workspace pane." That requires:

1. **Anonymous group-id join flow** — every attendee gets the same join code on a slide, hits `/group/JOIN-CODE`, gets a synthetic identity, and shares the same skill-marketplace view. No GitHub-account dance, no SSO friction. (Sprint 2.11 ships the auth provider; the helper agent is the first consumer of it from a real workshop room.)

2. **Per-attendee budget visible** — instructor wants to see "of 30 attendees, who's hitting their per-session cap?" Without it the room is invisible. Sprint 2.12 ships the enforcer; this is the live demo of the cohort-level budget pattern.

3. **Show-and-tell submission tool** — attendee says to the helper "submit my skill `attendee-alice-photo-summariser`" — helper validates it exists, marks it featured in the cohort's view, posts an A2UI card to the workspace pane.

4. **Live workspace pane updates** — submissions appear in the workspace pane within ~2s for all attendees, not just the submitter. The surface-context loop (sprint 2.10) makes this work without a polling refresh.

5. **The full meta-demo narrative** — at block 6 finale, instructor switches the workspace pane to "Cloud Trace view" and filters by `tenant.group_id = "<workshop-code>"`. The room sees every LLM call from every attendee's session in the past 55 minutes, **filtered by the same cohort identity that gated their budget, their join, and their audit row.** This is the four-AIPLA-extension story made live and visible. Sprint 2.14 makes this query possible.

Without all five, the helper agent is "a chat skill that knows about the workshop." With all five, the helper agent **is the workshop** — the workshop teaches by being the artefact that demonstrates everything it teaches.

### Current state

- **Path B (planned)** — `workshop-helper` skill seeded with docs corpus + `search_workshop_docs` FunctionTool. Lands first.
- **Anon-group auth (sprint 2.11)** — `POST /api/auth/group` ships; no frontend `/group/JOIN-CODE` page yet shipped tied to a specific workshop session.
- **Budget enforcement (sprint 2.12)** — `InMemoryBudgetEnforcer` reference impl ships; no per-session cap pre-configured for the workshop cohort.
- **Tenant span attribution (sprint 2.14)** — every span carries `tenant.group_id`. Cloud Trace filtering works as soon as a workshop runs.
- **Multi-surface workspace (sprints 2.9 + 2.10)** — workspace pane renders A2UI specs and reads back state to the agent. The show-and-tell board is "just another A2UI surface."
- **No show-and-tell tool yet** — needs to be a FunctionTool the helper exposes.
- **No workshop-cohort bootstrap** — instructor currently has no one-command way to provision a workshop session (join code, budget cap, helper skill marked default).

### Impact

- **Workshop experience is one step thinner than promised.** Block 5's "submit via the helper agent" doesn't work; instructor reverts to "raise your hand and show your screen" which doesn't scale past ~6 demos. The 55-minute hands-on loses its showcase moment.
- **Four shipped AIPLA extensions don't get their live demo.** 2.11 + 2.12 + 2.13 + 2.14 all shipped 2026-05-19 in a single day; without the workshop-helper their live-on-stage demo is "trust me, the tests pass." This sprint is what turns four green CI checks into a five-minute live narrative the audience watches happen.
- **The "platform demonstrates itself" axiom stays aspirational.** v6's pitch is that you don't write protocol code; the platform composes the protocols for you. The helper agent is the proof. Without it, attendees take that on faith.

---

## Goals

**Primary Goal:** Ship a workshop-helper skill that (a) answers questions from the docs corpus with citations, (b) accepts show-and-tell submissions and surfaces them in a shared workspace pane, and (c) makes the per-attendee budget + per-cohort trace-filter narratives visible in the room.

**Success Metrics:**
- A first-time attendee joins via `/group/<code>` in <10 seconds (no email, no signup, no GitHub).
- The helper agent answers "how does the surface-context loop work?" with a snippet from `docs/design/v6.2.0/implemented/a2ui-surface-context.md` and a working link. RAG over docs/integrations + docs/workshop + docs/design/v6.X.Y/implemented hits the right document on ≥80% of questions tied to a sprint that shipped.
- An attendee runs `helper, submit my skill alice-photo-summariser` and within 3 seconds the show-and-tell pane shows a card with their skill name + a click-to-try button — on every other attendee's screen.
- At block 5 minute 30, instructor opens Cloud Trace, filters by `tenant.group_id = <workshop-code>`, sees N spans from N distinct synthetic uids, drills into one, and the room sees their own session's spans named. **Latency: trace-to-room ≤ the existing Cloud Trace propagation lag (~30s).**
- At block 6 close, the slide says "your show-and-tell submissions are live at `https://platform-frontend-...run.app/cohort/<code>/showcase` and survive the workshop." Attendees can still see their card a week later.

**Non-Goals:**
- Per-attendee skill ownership / persistence beyond the workshop session. Each cohort is its own sandbox; if attendees want to keep working, they fork the template.
- Cross-cohort skill sharing. Each workshop code is a tenant; cohorts are isolated.
- Auto-grading / scoring submissions. The helper agent surfaces what people built; humans evaluate.
- A polished slide deck for the helper agent itself. The agent IS the demo; minimal slides.
- Workshop attendee analytics / dashboard for the instructor. Out of scope; the Cloud Trace filter is the analytics.
- Multi-instructor coordination (parallel rooms, hand-off). v1 is one cohort per workshop session.

---

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +2 | `/group/<code>` to first message in <10s; show-and-tell submission appears in workspace within 3s; no signup friction at all. |
| 2 | EARNED TRUST | +1 | Joining as anon-group, no PII collection, no email — schools / corporate workshops can attend without legal review. |
| 3 | SKILLS, NOT FEATURES | +1 | The helper agent is a SKILL like any other — same factory, same toolset, no special-case code path. The platform's "skills are the primary unit" axiom proves itself by the demo being one of them. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Helper uses `gemini-2.5-flash` by default; switches to `claude-sonnet` only if explicitly asked. No model-routing innovation. |
| 5 | GRACEFUL DEGRADATION | +1 | If budget enforcer triggers, attendee sees a typed RUN_ERROR banner with retry-after. If their show-and-tell skill isn't valid yet, helper says exactly which check failed. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Show-and-tell uses A2UI for the card + multi-surface for the workspace + AG-UI for streaming — zero custom render code per workshop. Future workshops re-use this. |
| 7 | OBSERVABLE BY DEFAULT | +2 | The Cloud Trace finale is the headline live demo of sprint 2.14. Per-attendee tenant.group_id filtering is the entire point of the four-AIPLA-extension sequence visible at once. |
| 8 | SECURE BY CONSTRUCTION | 0 | Sprint 2.11–2.14 already paid for the security work; this sprint inherits. No new attack surface introduced. |
| 9 | API FIRST | +1 | Show-and-tell submission goes through `POST /api/cohort/<code>/submissions` — same shape as any other platform API. Future "ungraded community marketplaces" reuse the endpoint. |
| 10 | DOCUMENT CENTRIC | +1 | The docs corpus IS the helper's knowledge. The platform's document-loader + retrieve_artifact + load_memory_tool surface is the same one attendees will use when they upload their own docs. |
| | **Total** | **+10** | |

---

## Design

### High-level architecture

```
                                Workshop slide
                                   │
                                   ▼
              Attendee scans QR / types /group/<JOIN-CODE>
                                   │
                          POST /api/auth/group/join  (sprint 2.11)
                                   │
                  HS256 JWT (synthetic uid, group_id=<JOIN-CODE>)
                                   │
                                   ▼
                  Frontend reads bearer from sessionStorage
                                   │
                                   ▼
              Attendee lands on chat page with `workshop-helper` selected by default
                                   │
                  ──────────────────┬──────────────────
                  │                                   │
                  ▼                                   ▼
        Q "how does A2UI work?"           "submit my skill alice-photo-summariser"
                  │                                   │
   search_workshop_docs(query)             submit_for_showandtell(skill_id)
                  │                                   │
        ┌─ RAG hit ─┐                                 │
        │ snippet + │                       validate skill exists for this cohort
        │ link to   │                                 │
        │ design doc│                       POST /api/cohort/<code>/submissions
        └───────────┘                                 │
                  │                                   ▼
                  ▼                       A2UI card emitted to "showcase" surface
              AG-UI text stream                       │
                  │                                   ▼
                  ▼                       Workspace pane on EVERY attendee's
            Cited answer in chat          screen updates within ~3s via the
                                          per-cohort SurfaceRegistry channel
                                                      │
                                                      ▼
                                          Block 6 finale: instructor opens
                                          Cloud Trace, filters by
                                          tenant.group_id = "<JOIN-CODE>"
                                          (sprint 2.14), shows the room
                                          their own spans named live.
```

### Components

#### 1. `workshop-helper` skill (extends Path B's seeded skill)

After Path B lands, the skill exists with `search_workshop_docs`. This sprint adds:

- **`submit_for_showandtell(skill_id: str, blurb: str) -> dict`** — validates the skill is owned-by-or-public-to the current cohort, then POSTs to `/api/cohort/<group_id>/submissions`. Returns success or a typed validation error.
- **`list_showandtell_submissions() -> list[dict]`** — lists everything submitted in this cohort. The agent uses this for the workspace render.
- **Skill prompt extension** — when the attendee says "submit", "show off", "share my skill", "show-and-tell", agent calls `submit_for_showandtell`; when "what has everyone built", agent calls `list_showandtell_submissions` and emits an A2UI grid to the `showcase` surface.
- **`default_surface: "showcase"`** in `tool_configs.a2ui` so submissions render in a persistent pane separate from the chat. This is multi-surface (sprint 2.9) in action.
- **`allow_surface_context_writes: true`** so the helper sees who's clicking on which submission and can answer questions like "did anyone else build something with the photo MCP?".

#### 2. `POST /api/cohort/<group_id>/submissions` endpoint

New FastAPI route, auth-gated by `Depends(get_current_user)` + a new guard requiring `user.auth_mode == "anonymous_group_id"` and `user.group_id == path.group_id`.

Payload:
```json
{ "skill_id": "...", "blurb": "...", "submitter_uid": "<synthetic>" }
```

Storage: Firestore `cohort_submissions/<group_id>/<submission_id>`. TTL 30 days (workshop is over by then).

Returns: the submission doc.

#### 3. `GET /api/cohort/<group_id>/submissions`

Lists submissions for the cohort. Same auth guard. Used by `list_showandtell_submissions` tool.

#### 4. `GET /cohort/<code>/showcase` page (frontend)

Static page that any cohort attendee (or external observer with the code) can visit. Renders the same A2UI grid the workspace pane shows, polled via the cohort submissions endpoint. **Survives the workshop** — TTL 30d on the underlying Firestore docs. Slide at block 6 close points at this URL.

#### 5. Cohort bootstrap CLI command

New `aiplatform workshop new <NAME>` command (or `make workshop-new NAME=croatia-aug-2026`):
1. Generates a join code (8-char human-readable, e.g. `CROATIA-AUG-26` for big workshops or `K7X9` for short ones).
2. Calls `POST /api/auth/group` to provision the anon-group with that exact code.
3. Sets per-session budget via `tool_configs.budget` on the cohort (sprint 2.12) — defaults to $2/attendee/session, configurable.
4. Pre-pins `workshop-helper` as the default skill for that cohort.
5. Prints the slide-ready strings:
   - `Join code: K7X9`
   - `URL: https://workshop.yourcompany.com/group/K7X9`
   - `QR PNG written to ./out/croatia-aug-2026-qr.png`

Instructor runs this ~24h before the workshop, screenshots the strings, drops onto a single slide.

#### 6. Cloud Trace finale prep (slide + script, no code)

Slide template with:
- A pre-built Cloud Trace URL filtered by `tenant.group_id` parameterized by the workshop code (just edit one URL parameter before each session).
- A 60-second instructor script: "this is every LLM call from every one of you in the last hour. Each row is one attendee. Click any row to see the prompt + the model + the cost + the budget remaining."

The platform code already does this work (sprint 2.14); the sprint deliverable is the docs/slide that turns the Cloud Trace UI into a 60-second narrative.

### Per-cohort budget defaults

Following sprint 2.12's enforcer pattern, ship a `WorkshopBudgetEnforcer` (reference impl, not platform default) — keys on `group_id`, ships a sensible workshop default:
- $2/attendee/session (~30 messages of Gemini Flash, plenty for the 55-min build block)
- Soft warn at $1.60 (80%)
- Hard block at $2.00 with `retry_after_seconds: <until end of workshop>` (i.e., the cohort owner can override)
- TTL: until cohort's `expires_at` from sprint 2.11

Lives in `backend/integrations/workshop_enforcer.py` as a worked-example fork-style enforcer (not in `backend/budget/`). Demonstrates how external workshops would write their own.

### What lives where

| Component | Layer | LOC est |
|---|---|---|
| `submit_for_showandtell` + `list_showandtell_submissions` tools | `backend/tools/cohort.py` | ~90 |
| `POST/GET /api/cohort/<group>/submissions` routes | `backend/api/cohort_submissions.py` | ~140 |
| Cohort showcase frontend page | `frontend/src/app/cohort/[code]/showcase/page.tsx` | ~120 |
| `aiplatform workshop new` CLI | `cli/aiplatform/commands/workshop.py` | ~110 |
| `WorkshopBudgetEnforcer` reference | `backend/integrations/workshop_enforcer.py` | ~80 |
| Cloud Trace finale slide template | `docs/workshop/slides/cloud-trace-finale.md` + screenshot | ~30 |
| Helper skill prompt expansion (in fixture + howto) | `backend/db/local_fixture.py` + `docs/integrations/workshop-cohort.md` | ~140 |
| Tests (backend integration + frontend Vitest) | `backend/tests/api_tests/test_cohort.py` + frontend | ~280 |
| **Total** | | **~990** |

### Implementation phases

#### Phase 1 — Backend endpoints + tools (~0.5d, ~310 LOC)
1. `cohort_submissions.py` — POST + GET routes with cohort-membership guards
2. `tools/cohort.py` — both tools with proper error shapes
3. Backend tests (~110 LOC) covering: submission by member ✅, submission by non-member 403, GET returns submissions in submission-time order, skill validation rejects non-existent skill, budget enforcement integrates with the existing 2.12 enforcer

#### Phase 2 — Helper skill expansion + frontend (~0.5d, ~290 LOC)
1. Update `workshop-helper` seed in `local_fixture.py` with the new tools + `showcase` surface + extended prompt
2. Frontend `cohort/[code]/showcase` page (reuses `<A2uiSurface>` from sprint 2.9)
3. Frontend Vitest cases (~100 LOC) covering: page renders for cohort member, page renders read-only for non-member (public showcase URL), submissions appear without polling once on-page (subscribes to submissions endpoint)

#### Phase 3 — Workshop CLI + budget enforcer (~0.5d, ~190 LOC)
1. `aiplatform workshop new` command — generates code, provisions group, sets budget, pins helper as default, prints slide-ready strings, optionally writes QR PNG (use `qrcode` Python lib)
2. `WorkshopBudgetEnforcer` reference implementation
3. CLI tests (~50 LOC) — smoke that the four side-effects happen via stub backend

#### Phase 4 — Docs + slide template + smoke (~0.5d, ~200 LOC)
1. `docs/integrations/workshop-cohort.md` — how to run a workshop end-to-end (cohort bootstrap → slide → workshop day → finale)
2. `docs/workshop/slides/cloud-trace-finale.md` — the 60-second instructor script with the parameterized Cloud Trace URL
3. End-to-end smoke: instructor laptop runs `make dev-local` + `aiplatform workshop new test-cohort`, then a second laptop joins via `/group/<code>`, sends a message, submits a fake skill, sees the card appear in the showcase pane on the instructor laptop. Capture screenshots into `.dev-logs/workshop-helper-smoke/`.
4. Update `agenda.md` block 5 + amplifier #2 to point at this design doc and the howto
5. Move design doc to `implemented/` + register sprint as complete in SEQUENCE.md

### Backwards compatibility

- **No protocol changes.** All wire formats (AG-UI, A2UI, MCP, A2A) unchanged.
- **No breaking changes to existing skills.** The `workshop-helper` skill is opt-in; existing demo skills work the same way.
- **Path B's smaller workshop-helper keeps working** without the show-and-tell tools — they only activate when the cohort has `auth_mode == "anonymous_group_id"`.
- **Existing tenants unaffected.** Cohort submission routes are scoped per-`group_id`; non-anon-group users never see them.

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Per-cohort A2UI surface updates lag >3s for some attendees | Medium | Sprint 2.10's surface-context loop is the carry — verify with 30-laptop smoke at Croatia dry-run (one week before). Polling fallback acceptable for v1. |
| Workshop budget runs out mid-session for one attendee | Medium | Default $2 is generous (~30 Gemini Flash messages); instructor can bump per-cohort with `aiplatform workshop budget set --cohort <code> --amount $5`. Sprint 2.12 already supports the override. |
| Cloud Trace UI loads slowly at finale (live demo risk) | Low | Pre-load the URL in the slide; have a backup screenshot in case of GCP-side latency. Mark as instructor-prep checklist item. |
| Show-and-tell submissions persist longer than 30d → cost creep | Low | TTL on Firestore docs; sprint 2.12 caps total spend per cohort regardless. |
| Synthetic uid in `tenant.uid_hash` makes attendees identifiable when they don't want to be | Low | Helper agent's submitted-by field uses the synthetic uid which IS opaque (no email source); design choice carries from sprint 2.11. |
| 30 attendees joining in 60 seconds hits the rate-limit (10/min/IP from sprint 2.11) | Medium | All attendees share the conference WiFi IP → bottleneck. Either bump cohort-creation rate limit, or document "show the join code 2 minutes before block 1 starts." Update sprint 2.11's rate limiter? Defer to verification. |

### Open questions

- Should the showcase URL be permanent (workshop-after life) or expire with the cohort? Default 30d feels right; ask instructor preference for first-session iteration.
- Should the helper agent see attendee chat history across cohort members (privacy + signal vs noise)? Default no for v1; revisit if attendees ask.
- Cloud Trace URL format: how do we get instructors clean URLs without giving them GCP project access? Either (a) screenshot the finale, (b) shareable trace links (Cloud Trace supports this but project-scoped), (c) embed a tiny custom finale view in the frontend that reads from Cloud Logging API. Default (a) for v1, revisit (c) for v2.

### Testing strategy

| Test | What it proves | Layer |
|---|---|---|
| `test_submit_for_showandtell_by_cohort_member` | Member can submit; doc lands in Firestore | Backend |
| `test_submit_rejects_non_cohort_member` | 403 when uid's group_id ≠ path group | Backend |
| `test_submit_validates_skill_exists` | 400 when `skill_id` references nothing | Backend |
| `test_list_submissions_returns_in_order` | Multi-submitter cohort returns submissions ordered by time | Backend |
| `test_budget_blocks_at_cap` | Per-cohort budget caps the submitter at $2.00 | Backend |
| `test_showcase_page_renders_for_member` | Cohort member sees real A2UI content | Frontend Vitest |
| `test_showcase_page_renders_for_non_member` | Non-member sees read-only public view | Frontend Vitest |
| `test_submissions_appear_without_refresh` | Surface-context loop delivers new submissions to existing tabs | Frontend Vitest |
| `test_workshop_new_cli_provisions_correctly` | CLI smoke against stub backend | CLI tests |
| 30-laptop dry-run at Croatia pre-event | E2E confirmed under realistic load | Operational |
| Cloud Trace finale rehearsal | URL pre-loads, filter works, ≤30s lag | Operational |

---

## Related

- [`docs/workshop/agenda.md`](../../workshop/agenda.md) — the workshop running order this sprint makes concrete
- [`docs/workshop/protocol-gotchas.md`](../../workshop/protocol-gotchas.md) — knowledge base the helper agent retrieves from
- [`docs/design/v6.2.0/implemented/anonymous-group-id-auth.md`](implemented/anonymous-group-id-auth.md) — sprint 2.11 ✅
- [`docs/design/v6.2.0/implemented/budget-enforcement.md`](implemented/budget-enforcement.md) — sprint 2.12 ✅
- [`docs/design/v6.2.0/implemented/artefact-render-hook.md`](implemented/artefact-render-hook.md) — sprint 2.13 ✅
- [`docs/design/v6.2.0/implemented/tenant-id-span-attribute.md`](implemented/tenant-id-span-attribute.md) — sprint 2.14 ✅
- [`docs/design/v6.2.0/implemented/multi-surface-rendering.md`](implemented/multi-surface-rendering.md) — sprint 2.9 ✅
- [`docs/design/v6.2.0/implemented/a2ui-surface-context.md`](implemented/a2ui-surface-context.md) — sprint 2.10 ✅
- [`docs/talks/ai-ui-protocol-stack.md`](../../talks/ai-ui-protocol-stack.md) — the protocol talk this workshop is the lab session of

## Why this is the next sprint after Path B

Path B answers "can the helper agent retrieve and cite from the docs corpus?" in 2-3 hours. This sprint answers "is the workshop itself the live demo of the four AIPLA template-extensions?" in 2 days. Path B without this sprint is a chat skill; this sprint without Path B is a marketing claim. Together, they're the WebSummerCamp Croatia opener and the basis for every future workshop session in the listed schedule (Tue 18 Aug, Tue 17 Nov, Tue 16 Feb).

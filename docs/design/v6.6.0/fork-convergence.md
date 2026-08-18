# ONE App — Downstream Fork Convergence

**Status**: Planned
**Priority**: P1 (Medium)
**Estimated**: ~8–10 days (5 workstreams, independently landable)
**Scope**: Fullstack
**Dependencies**: v6.5.0 5.4 authenticated-landing ✅, v6.4.0 SKILL-ONBOARDING ✅, v6.4.0 SHELL-MODES ✅, per-skill `SkillMetadata.model` (existing), `SkillMetadata.subSkills` (existing)
**Created**: 2026-07-08
**Last Updated**: 2026-07-08

## Problem Statement

Two downstream forks of this platform have matured real product patterns in
front of live users, and we want to fold the best of them back into the
**Acme Energy (ONE)** deployment (this repo) without importing their
complexity:

- **AIPLA** (`cphu-aipla-app`, Danish school AI-tutor) proved: a clean
  chat + optional-workspace layout; a **propose-only authoring copilot** that
  helps a non-technical admin configure a skill/activity (draft-until-saved);
  a **model tier registry**; a **persona-per-settings** identity bundle
  (avatar + interaction style + voice); and a full **read-aloud / TTS** stack.
- **GDE** (`gde-ap-agent`, accounts-payable agent) proved: conditional
  document panels in a persistent Workbench, deterministic docparse pipelines,
  and the authenticated document-preview pattern.

Most of the *plumbing* already exists upstream (see "Current State"). What's
missing are the **user-facing configuration surfaces** and a couple of
capability ports (voice, per-skill persona). The risk is importing AIPLA's
whole teacher-admin section or GDE's AP-specific UI wholesale — ONE needs the
*simplicity*, not the domain scaffolding.

**Current State (what v6 already has — deltas only below):**
- Chat + persistent `Workbench` split-pane, `SkillsBar` top bar with "+ Create"
  placeholder — [ChatShell.tsx](../../../frontend/src/components/chat/ChatShell.tsx),
  [Workbench.tsx](../../../frontend/src/components/chat/Workbench.tsx),
  [SkillsBar.tsx](../../../frontend/src/components/navigation/SkillsBar.tsx).
- A2UI wired for inline + routed surfaces (workspace/sidebar/modal) —
  [a2ui.py](../../../backend/adk/a2ui.py),
  [A2UIRenderer.tsx](../../../frontend/src/components/protocols/A2UIRenderer.tsx),
  [A2UISurfaceMount.tsx](../../../frontend/src/components/protocols/A2UISurfaceMount.tsx).
- Multi-provider models with per-skill `SkillMetadata.model`, a `thinkingModel`
  heuristic router, and `subSkills` delegation — [agent.py](../../../backend/adk/agent.py).
- Authenticated document preview + `ailang_parse` deterministic parsing —
  [documents/routes.py](../../../backend/tools/documents/routes.py),
  [DocumentPanel.tsx](../../../frontend/src/components/document/DocumentPanel.tsx).
- Per-skill static `avatar` + `displayName`; per-deploy branding —
  [branding.ts](../../../frontend/src/lib/branding.ts).

**What's missing (the deltas this doc scopes):**
1. No frontend skill-authoring UI — [skills/new/page.tsx](../../../frontend/src/app/skills/new/page.tsx)
   is a placeholder that points at the CLI. **No authoring copilot.**
2. No per-skill **persona** (voice + interaction style bundle) — only a static avatar.
3. No **audio output** (read-aloud / TTS) anywhere in v6.
4. Model selection is per-skill but not a **named-tier registry**; the
   fast-default → deep-via-delegation pattern is possible but not codified.
5. Workspace/document conditional UX is functional but hasn't been audited for
   the "stays clean for day-to-day users" bar ONE wants.

**Impact:**
- **Who:** ONE's day-to-day PPA analysts (want a clean chat), ONE's small admin
  team (want to create/tune skills without CLI + JSON-by-hand), and us
  (want one model-tier story, not per-skill hardcoded IDs).
- **How significant:** Major friction for the admin path (skill creation is
  CLI-only today); nice-to-have polish for the day-to-day path.

## Goals

**Primary Goal:** Bring the ONE deployment to feature-parity with the best
day-to-day and admin UX from the AIPLA/GDE forks — a clean chat/workspace, a
propose-only Skill Studio copilot, per-skill persona+voice, read-aloud output,
and a codified fast-default → deep-via-delegation model tiering — while keeping
the day-to-day surface visually minimal.

**Success Metrics:**
- Non-technical admin creates a working skill via Skill Studio in <5 min
  (Axiom #3 KPI), zero CLI, zero hand-edited JSON.
- Day-to-day chat first-token latency unchanged (<1s no-tools) — the default
  tier stays a fast/lite model; heavy models only via explicit sub-skill delegation.
- Read-aloud available on every assistant message, behind auth, with a GCS
  content-hash cache hit rate >60% on repeat content.
- Workspace/document panels render only when there is content to show (no empty
  chrome on chat-only turns).

**Non-Goals:**
- **Not** porting AIPLA's full multi-destination teacher-admin (Classes /
  Insights / Research). ONE gets a *lighter* Skill Studio only.
- **Not** GDE's AP-specific surfaces (invoice hero card, vendor KG).
- **Not** Gemini Live / streaming TTS — synthesize-to-blob only (like AIPLA);
  live voice is a later doc.
- **Not** a runtime multi-tenant persona picker — personas are per-skill config,
  consistent with per-deploy branding ([feedback_per_deploy_branding]).
- **Not** talk-to-type / STT in this doc (AIPLA has it; sequence later).

## Axiom Alignment

Score each axiom per [Product Axioms](../../../docs/product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Fast/lite default tier keeps first-token <1s; read-aloud is user-initiated + cached; workspace autohides so chat-only turns stay light. No new blocking on the request path. |
| 2 | EARNED TRUST | +1 | Skill Studio copilot is **propose-only** — nothing mutates saved config until the admin reviews + hits Save (AIPLA's earned-trust model). No auto-execution on unverified AI output. |
| 3 | SKILLS, NOT FEATURES | +2 | Directly advances the core axiom: turns skill creation from CLI-only into a <5-min guided flow; persona bundles keep concept count low (skill carries its own identity). |
| 4 | RIGHT MODEL, RIGHT MOMENT | +2 | Codifies fast-default → deep-via-delegation: day-to-day runs a lite model, sub-skills carry heavier models (Claude) only when the agent delegates. Named tier registry replaces scattered IDs. |
| 5 | GRACEFUL DEGRADATION | +1 | TTS falls back to browser SpeechSynthesis on backend error; copilot proposals are draft-only (a bad proposal is dismissable, never persisted); persona voice falls back to skill/env default. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Skill Studio emits **A2UI** for its dynamic config surfaces and reads/writes the existing `SkillConfig` (Agent Skills spec) — no new config format. Copilot is a normal skill over AG-UI. |
| 7 | API FIRST | +1 | New `/api/voice/*`, persona, and Skill Studio mutations are API routes; the copilot drives the same skill CRUD the CLI uses. Channels get voice config via the same contract. |
| 8 | OBSERVABLE BY DEFAULT | +1 | TTS route emits OTEL spans (provider, chars, cache_hit, cost_estimate); copilot proposals + applies are traced. All inside the GCP edge. |
| 9 | SECURE BY CONSTRUCTION | +1 | Read-aloud of private content stays behind `/api/proxy` + Firebase bearer + per-doc ownership re-check; TTS cache bucket is IAM-scoped to the backend SA; no public audio URLs. Skill Studio writes gated by skill owner/access-control. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Read-aloud adds client-side audio playback + a text-normalization helper (real logic in the browser). Kept minimal (strip markdown for speech only); all synthesis + config resolution is server-side. Net neutral. |
| | **Net Score** | **+11** | Threshold: >= +4 ✅ |

**Conflict Justifications:**
- None scored -1. The only non-positive is #10 (0): read-aloud necessarily
  runs `<audio>` + a markdown→speech text cleaner client-side. This is bounded
  (no business logic, no model decisions) and mirrors AIPLA's shipped pattern.

## Design

### Overview

Five independently-landable workstreams, each a small delta on existing v6
surfaces. Ordered by value-to-ONE and dependency. Workstream A (day-to-day
polish) and D (model tiering) are cheap and unblock the demo feel; B (Skill
Studio copilot) is the headline build; C (persona+voice) and E (audio) port
proven fork code.

| # | Workstream | Source fork | Delta size |
|---|-----------|-------------|-----------|
| A | Clean day-to-day chat/workspace audit | AIPLA layout | Small (frontend) |
| B | Skill Studio + authoring copilot | AIPLA teacher-admin | Large (fullstack) |
| C | Per-skill persona (avatar + style + voice) | AIPLA persona | Medium (fullstack) |
| D | Model tier registry + delegation codification | AIPLA models.yaml | Small (backend) |
| E | Read-aloud / TTS output | AIPLA voice stack | Medium (fullstack) |

---

### Workstream A — Clean day-to-day chat/workspace

**Goal:** ONE's day-to-day users see chat + skills-bar only; the Workbench
appears *only* when a skill emits content (document, A2UI workspace surface, MCP
app). Audit existing `ChatShell`/`Workbench` against AIPLA's config-driven
`workspaceKind` visibility.

**Frontend changes:**
- [Workbench.tsx](../../../frontend/src/components/chat/Workbench.tsx) — confirm
  the pane autohides when no tab has content (AIPLA computes `workspaceKind`
  from element presence; v6's `WorkspaceSurfaceRegion` already autohides —
  verify parity and extend to the doc/MCP tabs).
- [SkillsBar.tsx](../../../frontend/src/components/navigation/SkillsBar.tsx) —
  keep as the only persistent chrome; ensure the "+ Create" entry routes to
  Skill Studio (Workstream B) for authorized users, hidden for others.
- Mobile: adopt AIPLA's tab-switch (chat ⇄ workspace) rather than cramming both
  — a small responsive refinement.

No backend change. This is mostly a verification + trim pass; scoped small.

---

### Workstream B — Skill Studio + authoring copilot (headline)

**Goal:** A focused, single-page **Skill Studio** where an authorized ONE admin
creates/edits a skill, assisted by a **propose-only copilot**. Replaces the
CLI-only path. Deliberately *lighter* than AIPLA's teacher section (no
Classes/Insights/Research nav — just skill create/edit/list).

**The copilot pattern (from AIPLA `_AuthoringCopilot.tsx`):**
1. Admin describes what the skill should do in natural language.
2. Copilot (a dedicated `skill-authoring-assistant` skill) returns **typed
   proposals** as structured JSON: `{ kind, value|spec, label }`.
3. Proposals render as cards (Apply / Edit / Dismiss) in a side panel.
4. **Apply** mutates the *draft* builder state only — nothing is persisted.
5. Admin reviews the assembled `SkillConfig` and hits **Save** → one write to
   the existing skill CRUD. **Earned-trust: draft-until-saved.**

**Proposal kinds (mapped to `SkillConfig`/`SkillMetadata` fields):**
- `set_instructions` → `SkillConfig.instructions`
- `set_description` / `set_display_name` → `SkillConfig.description` / `displayName`
- `set_model_tier` → `SkillMetadata.model` (via named tier, Workstream D)
- `add_sub_skill` → `SkillMetadata.subSkills` (deep-work delegation)
- `set_tools` → `SkillMetadata.tools` / `toolConfigs`
- `set_persona` → skill persona (Workstream C)
- `add_a2ui_surface` → declare a default A2UI surface (`toolConfigs.a2ui.defaultSurface`)
- `set_welcome` → `SkillConfig.welcome` (intro message, example docs)

**Frontend changes:**
- New `frontend/src/app/skills/studio/[skillId]/page.tsx` — the builder form
  (replaces the [skills/new placeholder](../../../frontend/src/app/skills/new/page.tsx)).
- New `frontend/src/components/studio/AuthoringCopilot.tsx` — the propose-only
  panel; persists a copilot `threadId` in localStorage per skill (resume across
  reloads, like AIPLA).
- New `frontend/src/components/studio/applyProposal.ts` — proposal→draft-setter
  dispatch router.
- The dynamic parts of the builder (previews of the skill's A2UI surfaces,
  tool pickers) render via **A2UI**, not bespoke React — Axiom #6.

**Backend changes:**
- New skill: `skill-authoring-assistant` (backend/skills/ config) — instructed
  to emit the typed proposal JSON via a structured tool. Model: the deep tier
  (this is a reasoning-heavy task — Axiom #4 says spend intelligence here).
- Reuse existing skill CRUD ([skill_config.py](../../../backend/skills/skill_config.py))
  for the Save path — **no new persistence.** Save re-checks `access_control`
  (only skill owners / admins may write).

**CLI Surface (5b-bis):**
- `aiplatform skill studio-scaffold <name>` — create a draft SkillConfig from a
  one-line description (headless equivalent of the copilot's first proposal),
  so the studio and CLI share the same code path.

---

### Workstream C — Per-skill persona (avatar + interaction style + voice)

**Goal:** Promote today's static per-skill `avatar` into a first-class
**persona** bundle — avatar + `interactionStyle` + `voice` — surfaced and
editable in Skill Studio. "One persona per skill" (your decision), professional
tone rather than AIPLA's playful teacher personas.

**Data model (extend `SkillConfig`, mirroring AIPLA `Persona` + `SkillVoiceConfig`):**
```python
# backend/db/models — new nested model on SkillConfig
class SkillPersona(BaseModel):
    display_name: str = ""          # overrides displayName in chat
    avatar: str = ""                # URL (already exists, folded in)
    interaction_style: Literal["concise", "rigorous", "warm", "socratic"] = "concise"
    bio: str | None = None          # short professional descriptor
    voice: SkillVoiceConfig | None = None   # ties to Workstream E

class SkillVoiceConfig(BaseModel):
    tts_provider: str | None = None   # "gcp_wavenet" | "gcp_gemini" | "browser"
    tts_voice: str | None = None      # e.g. "en-GB-Wavenet-B"
    language: str | None = None       # BCP-47 short ("en")
    rate: float = 1.0                 # 0.25–4.0
    voice_prompt: str | None = None   # Gemini-TTS style direction
```
- `interaction_style` is appended as a small directive to the skill's system
  instruction at agent build time.
- Back-compat: existing top-level `SkillConfig.avatar` continues to read; the
  loader folds it into `persona.avatar` when `persona` is unset.

**Frontend:** persona editor lives in Skill Studio (Workstream B); avatar +
style rendered on the chat bubble via existing `MessageBubble`. No new
`/api/personas` route needed (persona is per-skill, resolved with the skill) —
simpler than AIPLA's class-inheritance chain.

---

### Workstream D — Model tier registry + delegation codification

**Goal:** Replace scattered per-skill model IDs with a **named tier registry**
(AIPLA `models.yaml`), and codify the fast-default → deep-via-delegation pattern
you specified: day-to-day runs a lite model; the ADK agent **delegates to
sub-skills configured with heavier models** for deep work.

**Backend changes:**
- New `backend/config/models.yaml` — named tiers, provider-agnostic:
  ```yaml
  tiers:
    fast:  { provider: google,    model: gemini-2.5-flash-lite }  # day-to-day default
    smart: { provider: anthropic, model: claude-sonnet-4-6 }      # deep work
    deep:  { provider: anthropic, model: claude-opus-4-8 }        # hardest reasoning
  defaults:
    platform_default: fast     # ONE starts on lite; future deploys can differ
  ```
- New `backend/config/models.py` — `resolve_tier(name) -> (provider, model_id)`;
  `SkillMetadata.model` accepts a **tier name** (`"fast"`/`"smart"`/`"deep"`) OR
  a raw ID (back-compat). [agent.py](../../../backend/adk/agent.py) `resolve_model()`
  resolves the tier first, then routes by provider prefix as today.
- **Delegation is the deep path, not a router swap:** a day-to-day skill runs
  `fast`; it declares `subSkills` (existing) that carry `smart`/`deep` tiers.
  The ADK agent loop delegates to them when the task warrants — heavy models are
  scoped to the delegated turn, not the whole session. This *is* Axiom #4.
- ONE seed config: `one-ppa-expert` default `fast`, with a `smart` sub-skill for
  clause-level contract reasoning.

**CLI Surface (5b-bis):**
- `aiplatform models tiers` — list the registry.
- Extend `aiplatform skill set --model-tier smart` (accepts a tier name).

---

### Workstream E — Read-aloud / TTS output (port from AIPLA)

**Goal:** Per-message read-aloud on assistant turns, behind auth, cached in GCS
by content hash, voice driven by the skill's persona (Workstream C). Direct port
of AIPLA's production stack — synthesize-to-blob, not streaming.

**Backend changes (port `backend/voice/`):**
- `backend/voice/` — `base.py` (protocol), `registry.py`, `providers/gcp_tts.py`
  (Google Cloud TTS), `providers/browser.py` (signal-only), `cache.py`
  (GCS content-hash cache), `cost.py`, `voices.py`.
- `backend/protocols/voice_routes.py`:
  - `GET  /api/voice/config?skill_id=…` — resolves the persona/skill/env voice.
  - `POST /api/voice/tts/synthesize` — text → `audio/mpeg` (cache-first). **Auth
    required (`Depends(get_current_user)`).**
  - `GET  /api/voice/voices` — curated picker for Skill Studio.
- Voice resolution precedence: skill persona voice → skill `voice` → env default
  (`VOICE_TTS_PROVIDER` → `gcp_wavenet`). (Simpler than AIPLA — no class layer.)

**Frontend changes (port):**
- `frontend/src/components/chat/ReadAloudButton.tsx` — play/stop control on
  assistant bubbles; two paths (browser SpeechSynthesis vs GCP blob); barge-in
  cancel; optional `autoSpeakOnMount`; markdown/LaTeX→speech text cleaner.
- `frontend/src/hooks/useVoiceConfig.ts` — fetch + session-cache voice config.
- Wire into [MessageBubble.tsx](../../../frontend/src/components/chat/MessageBubble.tsx).

**Security:** all synthesis behind `/api/proxy` + Firebase bearer; cache bucket
`VOICE_TTS_CACHE_BUCKET` IAM-scoped to the backend SA (private); **no public
audio URLs** — private PPA content read aloud never egresses (Axiom #9,
CLAUDE.md hard rule).

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET | /api/voice/config | Resolve TTS voice for a skill | No (new) |
| POST | /api/voice/tts/synthesize | Text → audio (auth, cached) | No (new) |
| GET | /api/voice/voices | Curated voice list for Studio | No (new) |
| POST | /api/skills (existing CRUD) | Skill Studio Save target — reused | No |
| — | `skill-authoring-assistant` skill | New copilot skill (AG-UI) | No |

### Architecture Diagram

```
Day-to-day user:
[Chat] → SkillsBar (only chrome) → agent runs `fast` tier
   └─ deep task → ADK delegates → sub-skill runs `smart`/`deep` (Claude)
   └─ [Read-aloud ▶] → /api/proxy → /api/voice/tts/synthesize → GCS cache → <audio>

Admin:
[Skill Studio] ⇄ AuthoringCopilot (skill-authoring-assistant, AG-UI)
   proposals → Apply → DRAFT builder state (no persist)
   Save → skill CRUD (access_control re-check) → Firestore SkillConfig
```

## Implementation Plan

### Phase 1: Foundation — model tiers + persona model (~1.5 days)
- [ ] D: `backend/config/models.yaml` + `models.py` `resolve_tier()` (~120 LOC)
- [ ] D: `resolve_model()` accepts tier names (back-compat with raw IDs) (~40 LOC)
- [ ] C: `SkillPersona` + `SkillVoiceConfig` models; loader folds legacy `avatar` (~80 LOC)
- [ ] D: `aiplatform models tiers` + `skill set --model-tier` CLI (~60 LOC)

### Phase 2: Read-aloud port (~2 days)
- [ ] E: port `backend/voice/*` (providers, cache, cost, voices) (~400 LOC)
- [ ] E: `voice_routes.py` (auth-gated synth + config + voices) (~200 LOC)
- [ ] E: port `ReadAloudButton.tsx` + `useVoiceConfig.ts`; wire `MessageBubble` (~500 LOC)
- [ ] E: env vars + IAM-scoped cache bucket (terraform)

### Phase 3: Skill Studio + copilot (~4 days)
- [ ] B: `skill-authoring-assistant` skill + typed-proposal tool (~200 LOC)
- [ ] B: `skills/studio/[skillId]/page.tsx` builder form (~500 LOC)
- [ ] B: `AuthoringCopilot.tsx` + `applyProposal.ts` (~600 LOC)
- [ ] B: persona editor + voice picker inside Studio (~200 LOC)
- [ ] B: `aiplatform skill studio-scaffold` CLI (~60 LOC)

### Phase 4: Day-to-day polish + ONE seed config (~1 day)
- [ ] A: Workbench autohide audit + mobile tab-switch (~150 LOC)
- [ ] A: SkillsBar "+ Create" → Studio for authorized users (~40 LOC)
- [ ] D: seed `one-ppa-expert` fast default + smart sub-skill

## Migration & Rollout

**Database Migrations:**
- `SkillConfig` gains optional `persona` (nested). Existing records read fine;
  `avatar` folds into `persona.avatar` at load. No backfill required.
- No new collections (voice cache is GCS, not Firestore).

**Feature Flags:**
- `NEXT_PUBLIC_ENABLE_SKILL_STUDIO` — gate the admin UI during bring-up.
- `NEXT_PUBLIC_ENABLE_READ_ALOUD` — gate voice per deploy.
- Model tier registry is additive (raw IDs still resolve) — no flag needed.

**Rollback Plan:**
- Each workstream is independently revertible. Studio + voice are net-new UI
  behind flags. Tier registry falls back to raw-ID resolution. Persona is
  optional/back-compat.

**Environment Variables:**
- `VOICE_TTS_PROVIDER` (default `gcp_wavenet`), `VOICE_TTS_CACHE_BUCKET`,
  `VOICE_GEMINI_TTS_MODEL` (if using Gemini-TTS voice prompts).

## Testing Strategy

### Frontend Tests (Vitest + RTL)
- [ ] `AuthoringCopilot` — proposal card render, Apply mutates draft only (no network), Dismiss.
- [ ] `applyProposal` — each kind maps to the right draft setter.
- [ ] `ReadAloudButton` — browser vs GCP path, barge-in cancel, markdown-strip helper.
- [ ] Workbench autohide — no pane on chat-only turn.

### Backend Tests (pytest)
- [ ] `resolve_tier` / `resolve_model` — tier names + raw-ID back-compat + unknown-tier error.
- [ ] `voice_routes` — auth required (401 without bearer), cache hit/miss, cost span emitted.
- [ ] `skill-authoring-assistant` — emits valid typed-proposal JSON (eval case).
- [ ] Skill Studio Save — `access_control` re-check rejects non-owner writes.
- [ ] Persona loader — legacy `avatar`-only record folds into `persona`.

### Manual Testing
- [ ] Admin creates a `one-ppa-expert` variant end-to-end via Studio in <5 min.
- [ ] Read-aloud on a PPA-answer message; confirm audio served via `/api/proxy` (no public URL in network panel).
- [ ] Day-to-day chat with no document shows no Workbench chrome.

## Security Considerations

- **Private content read aloud** must never egress: TTS route is auth-gated,
  cache bucket IAM-scoped to backend SA, no public audio URLs. This is a
  CLAUDE.md hard-rule surface — verify in review.
- **Skill Studio writes** re-check `access_control` server-side; the copilot
  cannot persist (propose-only) — a prompt-injected proposal is inert until a
  human applies + saves.
- **Copilot input** is user text reaching model context — standard skill
  injection defenses apply (it only *proposes* config, never executes tools).

## Performance Considerations

- Default `fast`/lite tier preserves <1s first token; heavy models are scoped to
  delegated sub-skill turns only.
- TTS is user-initiated + GCS-cached by content hash (repeat content = $0, ~cache
  latency). Synthesize-to-blob keeps it off the chat critical path.
- Skill Studio is admin-only, low-traffic — no day-to-day bundle impact if
  code-split (lazy route).

## Success Criteria

- [ ] All frontend tests passing (`npm run test:run`)
- [ ] All backend tests passing (`cd backend && make test-fast`)
- [ ] Lint + typecheck clean (`npm run quality:check:fast`; `make lint`)
- [ ] Non-technical admin creates a skill via Studio in <5 min, no CLI.
- [ ] Read-aloud works on assistant messages, served behind auth, cached.
- [ ] `SkillMetadata.model` accepts tier names; ONE seeds `fast` default + `smart` sub-skill.
- [ ] Day-to-day chat shows only chat + SkillsBar until a skill emits content.
- [ ] New CLI commands work end-to-end (`models tiers`, `skill studio-scaffold`).

## Resolved Decisions (2026-07-08)

- **Persona voice default for ONE:** **Aitana** — a Spanish female professional
  voice. Default `SkillVoiceConfig` = `{ tts_provider: "gcp_wavenet", tts_voice:
  "es-ES-Wavenet-C", language: "es", rate: 1.0 }` (es-ES female; upgrade to a
  Chirp3-HD / Gemini-TTS voice if a warmer professional read is wanted).
- **Skill Studio access:** **both** — skill-owner `access_control` semantics
  *and* a deploy-level `one-admin` group tag may write. Save re-checks
  `owner_email == user OR "one-admin" in user.group_tags`.
- **Auto-read:** **off by default.** `ReadAloudButton` renders but never
  `autoSpeakOnMount`; user opts in per message (professional context).

## Related Documents

- [authenticated-landing.md](../v6.5.0/authenticated-landing.md) — ONE landing + enabled-skills config this builds on.
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI surface conventions for the new commands.
- [feedback_per_deploy_branding] — why personas are per-skill, not a runtime tenant picker.
- Source forks: `cphu-aipla-app` (layout, copilot, persona, voice), `gde-ap-agent` (document/preview patterns already upstream).

# Sprint Plan — ONE App Fork Convergence

**Sprint ID:** ONE-FORK-CONVERGENCE
**Design doc:** [fork-convergence.md](fork-convergence.md)
**Created:** 2026-07-08
**Duration:** ~8 working days (4 milestones)
**Scope:** Fullstack

## Sprint Summary

**Goal:** Fold the proven AIPLA/GDE fork UX back into the ONE deployment — clean
day-to-day chat, a propose-only **Skill Studio** copilot, per-skill **persona +
voice**, **read-aloud/TTS**, and codified **fast-default → deep-via-delegation**
model tiering — without importing fork-specific domain scaffolding.

**Resolved decisions (2026-07-08):**
- Skill Studio access: **owner `access_control` OR `one-admin` group tag**.
- Auto-read: **off by default** (opt-in per message).
- ONE default voice: **Aitana — Spanish female professional** (`es-ES-Wavenet-C`).

**Key discovery during planning:** The model tier registry already exists
(`backend/config/models.yaml` + `config/models.py` with default/smart/fast
tiers), but `agent.py:resolve_model()` ignores it and prefix-routes raw IDs.
Workstream D therefore shrinks to *wiring* the registry, not building it.

## Milestone Breakdown

### M1 — Foundation: model-tier wiring + persona/voice models (~1 day, backend)

**Scope:** backend
**Depends on:** nothing (registry + `SkillMetadata` already exist)

- `config/models.py`: add `resolve_tier(name) -> ModelEntry` and
  `default_model() -> str`; add a `lite` tier concept (ONE's day-to-day default).
  Reconcile with existing default/smart/fast vocabulary — add a `lite` alias or
  a `platform_default` override rather than churning existing tiers.
- `adk/agent.py:resolve_model()`: accept a **tier name** (`"lite"`/`"smart"`/…)
  OR a provider `default`, resolving through the registry before prefix-routing.
  Raw IDs (`gemini-*`, `claude-*`, `gpt-*`) keep working (back-compat).
- `db/models/__init__.py`: add `SkillVoiceConfig` and `SkillPersona` nested
  models; add optional `persona: SkillPersona | None` to `SkillConfig`; loader
  folds legacy top-level `avatar` into `persona.avatar` when `persona` unset.

**Acceptance:**
- `resolve_tier("smart")` returns the smart-tier Claude entry; unknown tier raises.
- `resolve_model("smart")` returns a `Claude` wrapper; `resolve_model("gemini-2.5-flash")` still returns `Gemini`.
- Legacy skill record (avatar only, no persona) loads and exposes `persona.avatar`.
- `make lint` + `make test-fast` green.

### M2 — Read-aloud / TTS port (~2 days, fullstack)

**Scope:** fullstack
**Depends on:** M1 (persona voice model)

- Port `backend/voice/` from AIPLA: `base.py`, `registry.py`,
  `providers/gcp_tts.py`, `providers/browser.py`, `cache.py`, `cost.py`,
  `voices.py`. Strip any AIPLA class/school specifics.
- `backend/protocols/voice_routes.py`: `GET /api/voice/config`,
  `POST /api/voice/tts/synthesize` (auth-gated), `GET /api/voice/voices`.
  Voice precedence: skill persona voice → skill voice → env default. ONE default
  = `es-ES-Wavenet-C` (Aitana).
- Register router in `fast_api_app.py`.
- Frontend: port `ReadAloudButton.tsx` + `useVoiceConfig.ts`; wire into
  `MessageBubble.tsx`. **`autoSpeakOnMount` forced false** (opt-in).
- Flag `NEXT_PUBLIC_ENABLE_READ_ALOUD`; env `VOICE_TTS_PROVIDER`,
  `VOICE_TTS_CACHE_BUCKET`.

**Acceptance:**
- `POST /api/voice/tts/synthesize` returns 401 without bearer, `audio/mpeg` with.
- Cache hit on identical (text, voice, lang, rate) — second call cost 0.
- No public audio URL in the flow (served via `/api/proxy`).
- ReadAloudButton renders on assistant bubbles, plays on click, never auto-plays.
- Vitest for button (browser + GCP paths, barge-in); pytest for route (auth, cache).

### M3 — Skill Studio + authoring copilot (~4 days, fullstack) [HEADLINE]

**Scope:** fullstack
**Depends on:** M1 (persona + tiers)

- Backend skill `skill-authoring-assistant` (backend/skills/ config): emits typed
  `{kind, value|spec, label}` proposals via a structured tool. Model tier `smart`.
- Reuse `skills/skill_config.py` CRUD for Save; add access check
  `owner_email == user.email OR "one-admin" in user.group_tags`.
- Frontend: `app/skills/studio/[skillId]/page.tsx` builder form (replaces the
  `skills/new` placeholder); `components/studio/AuthoringCopilot.tsx`
  (propose-only, per-skill localStorage threadId); `components/studio/applyProposal.ts`
  (proposal→draft-setter dispatch). Persona + voice editor inside the form.
- Proposal kinds: `set_instructions`, `set_description`, `set_display_name`,
  `set_model_tier`, `add_sub_skill`, `set_tools`, `set_persona`,
  `add_a2ui_surface`, `set_welcome`.
- Flag `NEXT_PUBLIC_ENABLE_SKILL_STUDIO`.
- CLI: `aiplatform skill studio-scaffold <name>` (shared draft-scaffold path).

**Acceptance:**
- Copilot Apply mutates draft state only (no network write); Save persists once.
- Non-owner without `one-admin` gets 403 on Save.
- Studio round-trips an existing skill (load → edit → save → reload).
- Vitest: copilot proposal render + apply-is-local; applyProposal mapping.
- pytest: authoring skill emits valid proposal JSON (eval-style); Save access gate.

### M4 — Day-to-day polish + ONE seed config (~1 day, fullstack)

**Scope:** fullstack
**Depends on:** M3 (Studio route), M1 (tiers)

- `Workbench.tsx`: verify/extend autohide so chat-only turns show no pane;
  mobile chat⇄workspace tab-switch.
- `SkillsBar.tsx`: "+ Create" → `/skills/studio/new` for authorized users, hidden otherwise.
- Seed ONE config: `one-ppa-expert` default `lite` tier + a `smart` sub-skill for
  clause-level contract reasoning (via CLI/Firestore).
- CLI: `aiplatform models tiers`; extend `aiplatform skill set --model-tier`.

**Acceptance:**
- Fresh chat with no document → no Workbench chrome.
- `one-ppa-expert` runs lite by default; delegates to smart sub-skill on deep tasks.
- `aiplatform models tiers` lists the registry.
- Full quality gates: `npm run quality:check` + `cd backend && make lint && make test-fast`.

## Timeline

| Day | Milestone | Focus |
|-----|-----------|-------|
| 1 | M1 | Tier wiring + persona/voice models + tests |
| 2–3 | M2 | Voice backend port + routes + frontend button |
| 4–7 | M3 | Skill Studio form + copilot + Save gate + CLI |
| 8 | M4 | Polish, seed config, quality gates, smoke |

## Success Metrics

- Backend test LOC ≥ 30% of impl LOC; every new route has an auth + happy-path test.
- `make lint` + `make test-fast` green after each milestone.
- `npm run quality:check:fast` green after each frontend milestone.
- Non-technical admin creates a skill via Studio in <5 min (manual check, M3).

## Risks

- **ADK sub-agent delegation semantics** for the fast→deep path may need the
  heuristic router (`thinkingModel`) rather than pure `subSkills`. Verify in M1;
  fall back to the existing `_HeuristicRouter` if delegation isn't clean.
- **AIPLA voice port** may carry class/persona-inheritance coupling — strip to
  skill-scoped resolution in M2.
- **A2UI in Studio** (dynamic previews) is nice-to-have; if it risks M3, ship the
  form with plain React first and layer A2UI previews after.

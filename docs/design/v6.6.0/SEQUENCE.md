# v6.6.0 Build Sequence

**Gate:** v6.5.0 substantially landed (5.4 authenticated-landing ✅, 5.5 bucket-browser ✅; 5.1/5.2/5.3/5.6/5.7 planned/gated).

**Status as of 2026-07-08:** One doc planned — `fork-convergence.md` (6.1, sprint key ONE-FORK-CONVERGENCE). Captures downstream-fork learnings (AIPLA `cphu-aipla-app`, GDE `gde-ap-agent`) folded back into the ONE deployment.

**Theme:** Converge the best day-to-day + admin UX from the mature downstream forks into the ONE app, without importing their domain scaffolding. Five independently-landable workstreams: (A) clean chat/workspace audit, (B) Skill Studio + propose-only authoring copilot, (C) per-skill persona (avatar + interaction style + voice), (D) named model-tier registry with fast-default → deep-via-delegation, (E) read-aloud / TTS output port.

---

## Ordering

| Order | Doc | Priority | Est | Depends on | Notes |
|-------|-----|----------|-----|-----------|-------|
| 6.1 | [fork-convergence.md](fork-convergence.md) | **P1** | ~8–10d (5 workstreams, independently landable) | v6.5.0 5.4 authenticated-landing ✅, v6.4.0 SKILL-ONBOARDING ✅ + SHELL-MODES ✅, existing `SkillMetadata.model` + `subSkills` | **Fork convergence for ONE.** Folds AIPLA + GDE fork patterns upstream. (A) Audit `ChatShell`/`Workbench` so day-to-day users see chat + `SkillsBar` only, Workbench autohides until content. (B) **Headline:** Skill Studio — a lighter single-page skill authoring UI with a **propose-only copilot** (`skill-authoring-assistant` skill emits typed `{kind,value,label}` proposals; Apply mutates draft only; Save re-checks `access_control`) — replaces the CLI-only `skills/new` placeholder. Earned-trust draft-until-saved. (C) Promote static per-skill `avatar` into a `SkillPersona` bundle (avatar + `interaction_style` + `voice`), edited in Studio, back-compat with existing `avatar`. (D) `backend/config/models.yaml` named tiers (`fast`=gemini-flash-lite default, `smart`=claude-sonnet, `deep`=claude-opus); `SkillMetadata.model` accepts tier names; deep work via **sub-skill delegation** not router swap. (E) Port AIPLA's `backend/voice/*` + `/api/voice/*` (auth-gated, GCS content-hash cache, no public URLs) + `ReadAloudButton`/`useVoiceConfig` read-aloud. New CLI: `aiplatform models tiers`, `aiplatform skill studio-scaffold`, `aiplatform skill set --model-tier`. Net axiom **+11** — strongest hits on SKILLS-NOT-FEATURES (+2, CLI-only → <5-min guided authoring) and RIGHT-MODEL-RIGHT-MOMENT (+2, codified fast-default/deep-delegation). No axiom scores -1. |

---

## Timeline estimate

| Sprint | Doc | Status |
|--------|-----|--------|
| 6.1 | [fork-convergence.md](fork-convergence.md) | Planned 2026-07-08 |

## What ships in v6.6.0

**From 6.1 (fork-convergence):**
- **Skill Studio** (`frontend/src/app/skills/studio/[skillId]`) — single-page skill authoring UI with a propose-only `AuthoringCopilot`, replacing the CLI-only placeholder. Draft-until-saved; Save reuses existing skill CRUD with `access_control` re-check.
- **`skill-authoring-assistant`** — new copilot skill emitting typed config proposals over AG-UI; runs the deep model tier.
- **`SkillPersona` + `SkillVoiceConfig`** on `SkillConfig` — per-skill avatar + interaction style + voice; legacy `avatar` folds in, no backfill.
- **`backend/config/models.yaml` + `models.py`** — named model tiers (`fast`/`smart`/`deep`); `SkillMetadata.model` accepts tier names (raw IDs still resolve); deep work via sub-skill delegation. ONE seeds `one-ppa-expert` `fast` default + `smart` contract sub-skill.
- **Read-aloud / TTS** — ported `backend/voice/*` + `/api/voice/{config,tts/synthesize,voices}` (auth-gated, GCS content-hash cache, no public audio URLs) + `ReadAloudButton`/`useVoiceConfig` on assistant bubbles. Feature-flagged.
- **Day-to-day polish** — Workbench autohide audit + mobile chat⇄workspace tab-switch; SkillsBar "+ Create" → Studio for authorized users.
- **CLI:** `aiplatform models tiers`, `aiplatform skill studio-scaffold`, `aiplatform skill set --model-tier`.
- **Feature flags:** `NEXT_PUBLIC_ENABLE_SKILL_STUDIO`, `NEXT_PUBLIC_ENABLE_READ_ALOUD`.

## Dependency Graph

```
v6.4.0 SKILL-ONBOARDING ✅ ─┐
v6.4.0 SHELL-MODES ✅ ───────┼─→ 6.1 fork-convergence
v6.5.0 authenticated-landing ✅ ┘   (A audit · B Studio+copilot · C persona · D tiers · E read-aloud)
existing SkillMetadata.model + subSkills ┘
```

# Sprint Plan: ARTIFACT-PROMOTION — build-once artifact promotion (Phase 0 + Phase 1)

## Summary

Give every build an immutable identity and give prod a promotion path that moves
the **tested bytes** instead of rebuilding them — landing entirely *beside* the
existing branch→env flow so nothing that works today changes.

**Duration:** ~3 days
**Scope:** Backend / infra / CLI (no frontend runtime code; the UI is only rebuilt, not modified)
**Dependencies:** CI gate in `cloudbuild.yaml` (shipped, #36); trigger provisioning lives in `sunholo-data/multivac-aitana` (M4 crosses repos)
**Risk Level:** Medium — M1 edits the live dev deploy path; M4 applies Terraform and runs a real promotion
**Design Doc:** [build-once-artifact-promotion.md](build-once-artifact-promotion.md)

**Out of scope:** Phase 2 (runtime frontend config) — spike, separate sprint.
**Explicitly untouched:** the `prod` branch trigger. Per the resolved Open
Question 1, prod becomes promote-only **only after v6 is ratified on test
(`test.yourcompany.com`)**. This sprint validates on `dev → test`.

## Current Status Analysis

### Recent Velocity

- 142 commits / 312 files / ~29k insertions in the last 14 days.
- Closest comparable: FORK-FEEDBACK-CLOSEOUT (v6.19.0) — 6 milestones spanning
  backend security, auth, test doubles and UI, planned at 6 days, executed in ~1.
- Estimated capacity: this sprint is small in LOC (~700 total) and large in
  *verification* — the schedule below is dominated by cloud round-trips
  (~15–20 min per deploy), not by typing.

### Existing Implementation

- [cloudbuild.yaml](../../../cloudbuild.yaml) — builds `ui` / `backend` /
  `toolbox`, tags all three `:${BRANCH_NAME}`, deploys three containers by that
  mutable tag. CI gate + smoke already present.
- [backend/cloudbuild.yaml](../../../backend/cloudbuild.yaml) — same pattern for
  the Model-B standalone service (one image).
- Nothing else in the repo reads `:${BRANCH_NAME}` (verified by grep across
  `*.sh` / `*.yaml` / `*.py`), so the tagging change is contained to those two files.
- [backend/tests/unit/test_cloudbuild_ci_gate.py](../../../backend/tests/unit/test_cloudbuild_ci_gate.py)
  already walks the `waitFor` graph — the pattern M2's structure test extends.
- CLI: 14 command groups in [cli/aiplatform/commands/](../../../cli/aiplatform/commands/),
  each with a `cli/tests/test_cli_*.py`. `deploy.py` follows the established shape.
- No `gcloud builds submit` anywhere — a property M3 must preserve.

## Proposed Milestones

### M1 — Release identity: immutable tags + deploy-by-digest

**Scope:** backend/infra
**Goal:** every image is addressable forever, and every Cloud Run revision says exactly which build produced it.
**Estimated:** ~90 LOC config + ~80 LOC tests
**Duration:** 0.5d

**Tasks:**
- [ ] Root `cloudbuild.yaml`: tag `ui`/`backend`/`toolbox` with an immutable tag alongside `:${BRANCH_NAME}` — `:${SHORT_SHA}`, plus `:${TAG_NAME}` when the build came from a tag (~40 LOC)
- [ ] Root `cloudbuild.yaml`: after push, resolve each image digest and deploy with `--image …@${DIGEST}` instead of the mutable tag (~30 LOC)
- [ ] `backend/cloudbuild.yaml`: same two changes for the standalone service (~20 LOC)
- [ ] Static test: every built image is pushed under at least one immutable tag; no deploy step references a bare `:${BRANCH_NAME}` (~80 LOC)

**Files to Create/Modify:**
- `cloudbuild.yaml` (modify)
- `backend/cloudbuild.yaml` (modify)
- `backend/tests/unit/test_cloudbuild_image_identity.py` (new)

**Acceptance Criteria:**
- [ ] A real dev deploy goes green after the change (this is the gate — not the test)
- [ ] `gcloud run services describe platform-frontend` shows all three containers pinned by `@sha256:…`
- [ ] The `:${SHORT_SHA}` tag exists in Artifact Registry for that build
- [ ] `:${BRANCH_NAME}` is still pushed (nothing that reads it breaks)
- [ ] `make lint && make test-fast` green

**Risks:**
- `TAG_NAME` is **empty on branch builds** — referencing it unconditionally produces an invalid tag and fails the build. Mitigation: compute the tag list in a bash step, never inline in a `docker build -t`.
- Digest resolution adds a step between push and deploy. Mitigation: resolve from the push output / `crane digest`, and fail the build if empty rather than deploying a bare tag as a fallback.

### M2 — The promote pipeline (`cloudbuild.promote.yaml`)

**Scope:** backend/infra
**Goal:** copy the tested backend + toolbox by digest into the target project; rebuild only the UI, from the same tag, with the target's config.
**Estimated:** ~160 LOC config + ~120 LOC tests
**Duration:** 0.5d

**Tasks:**
- [ ] `guard-version` step — refuse to run without an explicit `_VERSION` (~10 LOC)
- [ ] `copy-backend` + `copy-toolbox` — `crane digest` → `crane copy` → re-read destination digest → **fail if it changed in transit** (~50 LOC)
- [ ] `build-frontend` — `get-firebase-config.sh` against the target project + target `_MCP_SANDBOX_URL`/branding substitutions, build + push `ui:${_VERSION}` (~40 LOC)
- [ ] `deploy` — `gcloud run services update`, images only, backend + toolbox by digest (~30 LOC)
- [ ] `smoke` — reuse the existing smoke block (~20 LOC)
- [ ] Structure test: `waitFor` graph puts deploy after both copies and the frontend push; the digest-equality assertion is present; every `gcloud`/`crane` subcommand invoked **actually exists** in the pinned builder image (~120 LOC)

**Files to Create/Modify:**
- `cloudbuild.promote.yaml` (new)
- `backend/tests/unit/test_cloudbuild_promote.py` (new)

**Acceptance Criteria:**
- [ ] Copies **two** images (backend + toolbox) and rebuilds exactly one (ui)
- [ ] Deploy step uses `services update` — no `--set-env-vars`, no `--set-secrets` (the env surface stays owned by env-cut/Terraform)
- [ ] Digest-equality assertion fails the build on mismatch (asserted by test)
- [ ] Subcommand-existence check passes — and would have caught `gcloud artifacts docker images copy`
- [ ] No `gcloud builds submit` introduced anywhere

**Risks:**
- **This is the milestone AIPLA got wrong** — a plausible-looking pipeline that had never run. Mitigation: the subcommand-existence test, and M4's real execution as the acceptance gate. Neither alone is sufficient.
- `crane` image tag drift. Mitigation: pin `gcr.io/go-containerregistry/crane:debug` and assert the entrypoint in the structure test.

### M3 — Operator surface: script + Make target + CLI group

**Scope:** backend/CLI
**Goal:** one typed command promotes a release; the operator's working tree is irrelevant to what ships.
**Estimated:** ~200 LOC implementation + ~140 LOC tests
**Duration:** 0.75d

**Tasks:**
- [ ] `scripts/promote-env.sh --from <env> --to <env> --version <tag> [--dry-run] [--yes]` — single source of promotion logic; validates the edge, verifies the tag exists **on origin**, runs the trigger with `--tag` (~120 LOC)
- [ ] `make promote FROM=test TO=prod VERSION=vX.Y.Z [GO=1]` — dry-run by default (~10 LOC)
- [ ] `cli/aiplatform/commands/deploy.py` — `promote`, `status`, `release`; wire in `cli.py` (~70 LOC)
- [ ] `cli/tests/test_cli_deploy.py` — `CliRunner` + `subprocess.run` mocks: invalid edge (`dev→prod`) rejected, missing tag rejected, exact trigger invocation asserted, `--dry-run` end-to-end (~140 LOC)

**Files to Create/Modify:**
- `scripts/promote-env.sh` (new), `Makefile` (modify)
- `cli/aiplatform/commands/deploy.py` (new), `cli/aiplatform/cli.py` (modify)
- `cli/tests/test_cli_deploy.py` (new)

**Acceptance Criteria:**
- [ ] `aiplatform deploy promote --dry-run` prints a correct copy-not-rebuild plan and exits 0 without touching GCP
- [ ] `aiplatform deploy status --env dev` reports the live revision **and image digest**
- [ ] Promotion runs a **trigger with `--tag`** — grep proves no `builds submit`
- [ ] `dev→prod` and a tag absent from `origin` are both refused with a clear message
- [ ] `make cli-selftest-mock` green; `shellcheck scripts/promote-env.sh` clean

**Risks:**
- Confirm-before-acting must be genuine (default dry-run). Mitigation: `GO=1`/`--yes` required for any mutating call, asserted in tests.

### M4 — Triggers + the first REAL dev→test promotion (the validation gate)

**Scope:** infra (cross-repo) + verification
**Goal:** prove the path works against live GCP, because a pipeline that has never run is not a pipeline.
**Estimated:** ~60 LOC Terraform
**Duration:** 0.5d (mostly cloud round-trips)

> **Scope correction (2026-07-31, from M3 verification).** All three envs share
> **one** Artifact Registry in `your-deploy-project-id` and differ only by tag —
> so the cross-project reader grant this milestone planned is **not needed**, and
> M4 carries no IAM change at all. Details in the design doc's *Trigger shapes*
> correction. This makes M4 smaller and lower-risk than planned.

**Tasks:**
- [ ] In `sunholo-data/multivac-aitana`: `trigger-aitana-test-release` (tag `^v.*$`, `cloudbuild.yaml`) + `trigger-aitana-test-promote` (manual, `cloudbuild.promote.yaml`) (~40 LOC)
- [ ] ~~Cross-project `roles/artifactregistry.reader`~~ — **not required**; the builds already read and write the shared registry. Set both registry substitutions to the same value.
- [ ] Cut a test tag, let the release trigger build it, then run one real `dev → test` promotion
- [ ] Record the outcome + digests in `docs/ops/deployed-urls.md`

**Files to Create/Modify:**
- `multivac-aitana/infrastructure/environments/test/*.tf` (separate repo)
- `docs/ops/deployed-urls.md` (modify)

**Acceptance Criteria:**
- [ ] `aiplatform deploy status --env test` and the source env show the **same backend digest**
- [ ] The promote build's smoke step passed (any non-200 fails it)
- [ ] `test.yourcompany.com` serves normally after the promotion
- [ ] Any latent bug the first run surfaces is fixed **and** given a regression test — expect one; AIPLA's first run found three

**Risks:**
- **Terraform on `dev` auto-applies with no gate.** Mitigation: confirm the plan with the user before pushing the infra change; this milestone pauses for explicit go-ahead.
- A failed promotion could leave test on a half-swapped revision. Mitigation: `services update` is atomic per revision — a failed build leaves the previous revision serving; rollback is a traffic shift.

### M5 — Docs, gotchas, and the gated template refresh

**Scope:** docs
**Goal:** the next fork gets the paved path, and the two traps that cost AIPLA days are written down.
**Estimated:** ~200 lines docs
**Duration:** 0.5d

**Tasks:**
- [ ] `docs/ops/promotion.md` — the three routes, why prod copies, how to verify digest equality, rollback
- [ ] `docs/ops/gotchas.md` — #46 (`--service-account` on **both** trigger-create and `builds submit`; submit also needs `storage.objectViewer` on `<project>_cloudbuild`) and **the frontend is not copyable** (`NEXT_PUBLIC_*` is compile-time inlined)
- [ ] Fix the stale "test — not yet cut" section in `docs/ops/deployed-urls.md` (test is live; `test.yourcompany.com`)
- [ ] Mark #46/#47 resolved in [template/SEQUENCE.md](../template/SEQUENCE.md); note the resolution for the fork's `docs/upstream-feedback.md`
- [ ] **Then** run `aitana-template-publish` — gated on M4 passing

**Acceptance Criteria:**
- [ ] A reader who has never promoted can follow `docs/ops/promotion.md` end-to-end
- [ ] Template refresh happens only after M4 is green
- [ ] `make lint` clean (codespell runs over docs)

**Risks:**
- Publishing before M4 would ship an unproven pipeline to forks — the exact failure this sprint exists to prevent. Mitigation: ordering is an acceptance criterion, not a convention.

## Model Assignment

Rubric: [model-assignment.md](../../../.claude/skills/sprint-planner/resources/model-assignment.md).
Its table still lists the 4.x lineup; mapped here onto the current models
(Opus 5 / Fable 5 / Sonnet 5 / Haiku 4.5) per the environment's model list.

| Stage / milestone | Model | Why |
|-------------------|-------|-----|
| Planning | `claude-opus-5` | Decomposition + interactive iteration; the design doc holds the hard thinking |
| Execute M1 | `claude-opus-5` (xhigh) | Touches the live dev deploy path; short horizon, needs a fast verify loop against real builds |
| Execute M2 | `claude-fable-5` | Highest subtlety on the sprint and fully specified — a wrong-but-plausible promote pipeline passes shallow tests, which is exactly how AIPLA lost six weeks |
| Execute M3 | `claude-opus-5` (xhigh) | Well-specified script + CLI following an established in-repo pattern |
| Execute M4 | `claude-opus-5` | Interactive, judgment-heavy, pauses for user go-ahead before a Terraform apply |
| Execute M5 | `claude-sonnet-5` | Procedural docs work over settled decisions |
| Evaluation (all rounds) | `claude-opus-5` + report-everything prompt | Cross-model diversity vs Fable on M2; instruct it to report every finding with a confidence tag, since it honours severity filters literally |
| Sub-agents (greps, inventories) | `claude-haiku-4-5` | Mechanical fan-out |

## Day-by-Day Breakdown

### Day 1
- M1 in full, including a real dev deploy and a `describe` showing digests
- Start M2 (pipeline steps + structure test)

### Day 2
- Finish M2
- M3 in full (script, Make target, CLI group, tests)
- Pause: present the Terraform plan for M4

### Day 3
- M4: triggers + cross-project reader, tag cut, **one real dev→test promotion**, fix whatever it surfaces
- M5: docs + gotchas, then the gated template refresh

## Success Metrics

- All three containers deployed by digest in dev and test
- Same backend digest reported for the source env and test after promotion
- New tests: ~340 LOC across 3 files, all green in `make test-fast`
- `shellcheck` clean on the new script; `make cli-selftest-mock` green
- Zero changes to the behaviour of `git push origin dev`
- The `prod` trigger is untouched — verifiable by diffing its Terraform

## Quality Gates

- After each milestone: `cd backend && make lint && make test-fast`
- CLI milestones: `make cli-selftest-mock`
- Before push: backend CI parity (`make lint && make test-fast`) — the frontend
  is unmodified in this sprint, so `npm run quality:check` is only needed if M2's
  frontend-rebuild step turns out to require a Dockerfile change
- M4 gate: real promotion green + digest equality proven before M5's publish step

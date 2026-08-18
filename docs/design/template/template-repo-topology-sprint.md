# Sprint Plan: TEMPLATE-INVERT — Template Repo Topology Inversion

## Summary

Make the template the upstream source of truth and this repo a downstream
consumer: drive the parity gap to zero, carve out the private `deploy/` tier,
then flip the publish flow so shipping the public template is one folder glob
instead of a 1154-line subtractive filter.

**Duration:** ~7 days (5 executable + ~1.5 operator-gated)
**Scope:** Repo topology, CI, publish tooling — no runtime behaviour change
**Dependencies:** None for M1–M5. M6–M8 need repo creation + a public push (operator only).
**Risk Level:** Medium — 337 files change content; the mitigation is that the transformation is already proven green by the existing sanitizer.
**Design Doc:** [template-repo-topology.md](template-repo-topology.md)

## Current Status Analysis

### Recent Velocity
- **97 commits / 14 days** (~7/day), 239 files changed, +29,901 / −1,234
- Recent sprints (MAPS, MODEL-RELIABILITY) landed multi-milestone work in 2–4 days each
- Estimated capacity for this sprint: comfortably within recent velocity — but this
  sprint is unusual in that most "LOC" is *deletions and substitutions*, not new code

### Baseline Health (measured 2026-08-17, pre-sprint)
- Backend: **3190 passed**, 3 skipped, 4 deselected (22.7s)
- Frontend: **149 test files / 1262 tests passed**
- Working tree clean at `cc9fa47`

### Existing Implementation We Build On
- `scripts/sanitize-for-template.sh` — 1154 lines. **This is the spec, not just the
  obstacle:** its `TOKEN_REPLACEMENTS` table already performs the exact
  transformation M4 makes permanent, and the sanitized tree's suites pass today
  because it replaces both sides of every assertion.
- `GLOB_DELETIONS` + the `one-*` naming convention — the additive model is already
  proven: `backend/db/local_fixture.py` degrades gracefully when customer templates
  are absent, and customer-touching tests already `pytest.skip` on missing fixtures.
- Three working security gates (secret / customer-identifier / NDA) to port to CI.

### The Measured Gap

337 files exist in both trees with different content. By cause:

| Token class | Files touched | Only cause |
|---|---:|---:|
| Deployment identity | 216 | 128 |
| Customer identity | 128 | 78 |
| Client names | 38 | 14 |
| Local paths | 31 | 11 |
| Repo URL | 18 | 2 |
| Cloud Run URLs | 17 | 0 |
| NDA-preview | 6 | 1 |

By area: `docs/design` 113, `backend/tests` 70, `frontend/src` 41, `backend` src 36,
`cli` 19, `scripts` 13, `docs/ops` 12, `infrastructure` 10, rest singles.

## Proposed Milestones

Ordering is deliberate: **cheap deletions and careful config work first, mechanical
bulk last**, so M4 operates on the smallest possible remainder.

### Milestone 1: Parity harness — the countdown metric
**Scope:** tooling · **Duration:** 0.5d · **Est:** ~180 LOC + ~60 test LOC

**Tasks:**
- [ ] `scripts/template-parity.sh` — sanitize to a scratch dir, diff every tracked
      file present in both trees, **fail on any content difference**; emit the gap
      list plus per-class attribution (~120 LOC)
- [ ] `make template-parity` + wire into `.github/workflows/ci.yml` as a
      **reporting** job (non-blocking until M4 lands, then blocking) (~30 LOC)
- [ ] `scripts/template-triage.py` — classify each gap file by token class and emit
      the disposition worksheet M2 consumes (~60 LOC)
- [ ] Unit test: harness detects a planted content difference and a planted
      deletion, and distinguishes them (~60 LOC)

**Acceptance Criteria:**
- [ ] `make template-parity` prints **337** and exits non-zero
- [ ] Planted-difference test proves the check cannot silently pass
- [ ] Per-class attribution matches the design doc's table

**Risks:** A parity check that reports success on a broken sanitize run is worse
than none — mitigate with the planted-difference test, which is why it's an
acceptance criterion rather than a nicety.

### Milestone 2: Disposition triage + move customer/ops content downstream
**Scope:** docs/content · **Duration:** 1d · **Est:** ~40 files moved, ~0 new LOC

Every gap file gets exactly one of: **scrub in place** / **move downstream** /
**config-drive**. Moving is cheaper than genericizing and is the standing
preference for customer content ("delete rather than ship in disguise").

**Tasks:**
- [ ] Produce the disposition worksheet for all 337 (from M1's triage script)
- [ ] Create `docs/customers/one/` and move customer-specific design/ops docs into it
      (the restructure already deferred once in the publish skill)
- [ ] Move remaining customer assets behind the `one-*` convention:
      obligation artefact, `scripts/gate-obligation-artefact.sh`,
      `infrastructure/mcp-toolbox/tools.yaml`
- [ ] Fix referrers for every moved doc (**this is what the internal-tools fork #11 is about** —
      moving a doc silently invalidates every link to it)
- [ ] Add the moved paths to `GLOB_DELETIONS` so the gap closes by absence

**Acceptance Criteria:**
- [ ] Every one of the 337 has a recorded disposition — no file left unclassified
- [ ] `make template-parity` gap drops measurably (target: ≤ 260)
- [ ] Zero dangling links introduced (link-check over `docs/` + `CLAUDE.md` + `.claude/skills/`)
- [ ] Backend + frontend suites still green

**Risks:** Misclassifying customer content as platform content is the exact
failure this whole sprint exists to prevent. Mitigation: default to *move
downstream* when uncertain — over-moving is recoverable, over-shipping is not.

### Milestone 3: Deployment identity → config-driven or downstream-only
**Scope:** backend + config · **Duration:** 1.5d · **Est:** ~250 LOC + ~120 test LOC

The ~20 files that must hold **real** values for Aitana's deploys become
downstream-only, with upstream shipping `.example`. Everything else gets a neutral
default that is correct for a fork.

**Tasks:**
- [ ] Inventory the real-value files: `cloudbuild.yaml`, `cloudbuild.promote.yaml`,
      `backend/cloudbuild.yaml`, `backend/Makefile`, `backend/.env.example`,
      `firestore.rules`, `firestore.indexes.json`, `get-firebase-config.sh`,
      `docs/ops/deployed-urls.md`
- [ ] Split each into a shipped `.example` + a downstream-only real file, or make it
      substitution-driven where the build already supports it
- [ ] **`AUTH_OPERATOR_DOMAINS`** — currently defaults to `yourcompany.com`; a fork
      enabling `AUTH_REQUIRE_KNOWN_DOMAIN` would admit *our* domain as its
      operators. Functional bug, not cosmetic. Same class: the `firestore.rules`
      admin-email grant hardcoded to a personal address
- [ ] Env-drive `cli/aiplatform/commands/bq.py` `_DATA_PROJECT`
- [ ] Genericize `backend/adk/tools.py` bucket docstring
- [ ] Tests for every new default + absence path

**Acceptance Criteria:**
- [ ] No shipped file names a real Aitana project, service, bucket, domain or email
- [ ] A fork's operator domain defaults to *its own*, proven by test
- [ ] `make template-parity` gap ≤ 150
- [ ] Local `make dev` still works; deployed configs unchanged in value

**Risks:** Highest-risk milestone — a wrong default silently breaks deploys or
weakens auth. Mitigate with tests asserting both the neutral default *and* that this
repo's real values still resolve.

### Milestone 4: Apply the scrub permanently — bulk remainder to zero
**Scope:** fullstack · **Duration:** 1.5d · **Est:** ~150 files, substitutions only

Mechanical, and the spec is literally the existing `TOKEN_REPLACEMENTS` table.

**Tasks:**
- [ ] Apply the surviving token rules to the source tree, both sides of every assertion
- [ ] Scrub `<local-path>` paths, live Cloud Run URLs, NDA-preview tells
- [ ] Repoint repo URLs at the new upstream (`sunholo-data/platform-source` /
      the public repo) rather than `sunholo-data/ai-protocol-platform`
- [ ] Client names in platform design docs → the neutral forms already in the table
- [ ] **Delete every rule from `TOKEN_REPLACEMENTS` as it is applied** — the table
      shrinking to empty *is* the deliverable
- [ ] Full suite green after each batch, not just at the end

**Acceptance Criteria:**
- [ ] `make template-parity` reports **0** — the go/no-go gate for the flip
- [ ] `TOKEN_REPLACEMENTS` is empty; sanitize is deletions-only
- [ ] Backend 3190+ and frontend 1262+ still passing
- [ ] CI parity job flipped from reporting to **blocking**

**Risks:** A half-applied rule leaves an assertion comparing a scrubbed value to an
unscrubbed one (this exact failure happened to the sanitizer twice — the
`acmeenergy\.com` regex-escaped form, and the `deal_tracker` ordering bug).
Mitigate by batching per rule with a full test run per batch.

### Milestone 5: Carve the `deploy/` tier
**Scope:** infra · **Duration:** 1d · **Est:** ~35 files moved + ~80 new LOC

**Tasks:**
- [ ] `git mv` into `deploy/`: cloudbuild pipelines, `bootstrap-*.sh`, `create-*.sh`,
      `promote-env.sh`, `smoke-deployed.sh`, TF modules, deploy-tier ops docs
- [ ] Fix every referrer: root `Makefile`, `.github/workflows/`, docs, `.claude/skills`
- [ ] **New:** `deploy/terraform/create-tf-state-bucket.sh` — closes the internal-tools fork #17,
      the entry blocking every fork's graduation from scripts to IaC. Use
      `versioning_enabled` (not `versioning.enabled` — gcloud prints nothing for an
      unknown format key, so the obvious probe reports "off" on a correct bucket)
- [ ] Fix the internal-tools fork #12 while here: `test_cloudbuild_image_identity.py` asserts against
      two files **by name**; glob every `cloudbuild*.yaml` so the two tag-unsafe
      pipelines are caught
- [ ] Verify the one-glob publish (`grep -v '^deploy/'`) yields a tree whose suites pass

**Acceptance Criteria:**
- [ ] `deploy/` excluded → remaining tree builds and tests green
- [ ] Cloud Build triggers still resolve their config paths (no deploy breakage)
- [ ] `create-tf-state-bucket.sh` creates a versioned, UBLA, public-access-prevented bucket
- [ ] Image-identity test globs all four pipelines

**Risks:** Moving cloudbuild configs can break live triggers whose paths are
registered in Terraform. Mitigate: verify trigger config paths **before** moving,
and treat this as the one milestone needing a real deploy check.

### Milestones 6–8: OPERATOR-GATED (cannot be executed by the agent)

These need a GitHub repo creation and a bulk push to a public destination. Repo
creation requires `MarkEdmondson1234` (the bot is an org member, not an owner), and
the harness hard-blocks bulk pushes to public destinations. **The agent prepares
everything; the operator runs the commands.**

- **M6 — platform-source genesis (0.5d):** create `sunholo-data/platform-source`
  (private), genesis commit from the M5 tree, port the 3 gates to CI + add the
  fork-scope deny-rule, link-check, and `implemented/` honesty check.
- **M7 — downstream graft (0.5d):** `git remote add upstream` + one
  `--allow-unrelated-histories` merge; `scripts/upstream-merge.sh` with staged-deletion
  review; **delete `sanitize-for-template.sh` (1154 lines)**; rewrite the
  `aitana-template-publish` skill as the downstream manual.
- **M8 — publish + re-point forks (0.5d):** `scripts/publish-public.sh` (~50 lines),
  first real publish, verify link-check 55/55, re-point the internal-tools fork + AIPLA.

## Model Assignment

> The rubric at `resources/model-assignment.md` lists a stale lineup
> (`claude-opus-4-8`, `claude-sonnet-4-6`). Current ids used here:
> `claude-opus-5`, `claude-fable-5`, `claude-sonnet-5`, `claude-haiku-4-5`.

| Stage / milestone | Model | Why |
|-------------------|-------|-----|
| Planning | `claude-opus-5`, high | Decomposition + interactive iteration; the design doc holds the hard thinking. |
| Execute M1 (parity harness) | `claude-opus-5`, xhigh | Short horizon, but the check must be un-bypassable — a parity script that passes when the sanitize breaks is worse than none. |
| Execute M2 (triage + moves) | `claude-opus-5`, xhigh | Highest **judgment** load: "is this file customer content or platform content" is the exact call that leaked twice. Not mechanical. |
| Execute M3 (config-drive) | `claude-opus-5`, xhigh | Security-adjacent (operator domains, admin grants) with an incomplete spec — Fable's advantage collapses on vague specs, and this needs iteration with the user. |
| Execute M4 (bulk scrub) | `claude-fable-5` | Textbook Fable fit: long-horizon, mechanical, and **fully specified** — the `TOKEN_REPLACEMENTS` table *is* the spec. Autonomous run, minutes-long turns are fine. |
| Execute M5 (deploy carve) | `claude-opus-5`, xhigh | Moves are mechanical but referrer breakage reaches live Cloud Build triggers; wants interactive verification. |
| Evaluation (all rounds) | `claude-opus-5` + report-everything prompt | Cross-model diversity where it matters most: M4 is written by Fable and evaluated by Opus. Prompt must say *report every issue including low-confidence*, or Opus withholds. |
| Sub-agents | not used this sprint | Per session constraint; inventories run inline. |

## Day-by-Day Breakdown

### Day 1 — M1
- **Focus:** Parity harness
- **Checkpoint:** `make template-parity` prints 337, planted-difference test passes

### Day 2 — M2
- **Focus:** Disposition triage; customer/ops content downstream
- **Checkpoint:** All 337 classified; gap ≤ 260; no dangling links; suites green

### Days 3–4 — M3
- **Focus:** Deployment identity → config
- **Checkpoint:** Gap ≤ 150; operator-domain default proven by test; `make dev` works

### Days 4–5 — M4
- **Focus:** Bulk scrub, batched per rule
- **Checkpoint:** **Gap = 0**; `TOKEN_REPLACEMENTS` empty; CI parity job blocking

### Day 6 — M5
- **Focus:** `deploy/` carve + `create-tf-state-bucket.sh`
- **Checkpoint:** One-glob publish yields a green tree; triggers intact

### Day 7 — M6–M8 handoff
- **Focus:** Prepare operator runbook; agent stops at the push boundary

## Quality Gates

After each milestone:
```bash
make template-parity                          # the countdown
cd frontend && npm run quality:check          # CI parity: lint + typecheck + tests + build
cd backend && make lint && make test-fast     # CI parity: ruff + format + pytest
```

Before M6 (the point of no return):
```bash
bash scripts/template-parity.sh --strict      # must be 0
rm -rf /tmp/tc && bash scripts/sanitize-for-template.sh /tmp/tc
cd /tmp/tc/backend && uv sync && uv run pytest tests/ -m "not slow" --ignore=tests/integration
cd /tmp/tc/frontend && npm install && npm run test:run
```

## Success Metrics

- Parity gap **337 → 0**
- `TOKEN_REPLACEMENTS` **~120 rules → 0**
- Sanitize **1154 lines → ~50** (M7)
- Design-doc link resolution **20/55 → 55/55**
- Backend ≥ 3190 passing, frontend ≥ 1262 passing at every checkpoint
- the internal-tools fork #11, #12, #17 closed; #10 closed structurally

## Open Decisions (do not block M1–M2)

1. **Does the public tier keep a deploy story?** Needed before **M5**, not before.
   Recommendation: a single-env `gcloud run deploy` path that is complete and
   tested; Terraform/multi-env/promotion private.
2. **Commercial fork licensing** — needed before M8 only.

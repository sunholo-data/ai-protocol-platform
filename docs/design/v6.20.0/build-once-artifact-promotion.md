# Build-once artifact promotion — immutable release identity, copy-by-digest to prod

**Status**: Planned
**Priority**: P1 (Phase 0 + 1); P2 (Phase 2)
**Estimated**: Phase 0 ~0.5d · Phase 1 ~1.5d · Phase 2 ~2–3d (spike first)
**Scope**: Infrastructure / Config / CLI — no runtime code paths, no user-facing surface
**Dependencies**: `cloudbuild.yaml` CI-gate (shipped, source item #36); `scripts/bootstrap-gcp-project.sh` (shipped, #7); [docs/ops/env-promotion-audit.md](../../ops/env-promotion-audit.md)
**Created**: 2026-07-31
**Last Updated**: 2026-07-31
**Source items**: #46 #47 (CPH Uni AIPLA fork, `docs/upstream-feedback.md`, added 2026-07-30/31 — the first two entries on that log not already closed by sprint FORK-FEEDBACK-CLOSEOUT). Related: #7 (the trigger half of #46), #18 (the ARG silent-drop that Phase 2 kills), #36 (the CI gate this design inherits).

## Problem Statement

The template ships **branch → environment rebuild**: push to `dev`/`test`/`prod`,
each branch rebuilds both containers from source in that environment's project.
The platform runs the same model. It works, and it has three defects that only
become visible once a fork puts a second and third environment in front of real
users — which the AIPLA fork just did, hitting all three.

**1. Prod does not run the bytes that passed test.** It runs *the same commit,
rebuilt*. Between two builds of one commit, base images, transitive Python/npm
resolution, and build-time config can all drift. "Tested ≠ shipped" is the wrong
default for anything with users on it.

**2. There is no release identity.** Images are tagged `:${BRANCH_NAME}`
([cloudbuild.yaml:188-238](../../../cloudbuild.yaml#L188)) and deployed by that
same mutable tag ([cloudbuild.yaml:310-348](../../../cloudbuild.yaml#L310)).
`:prod` is repushed on every prod deploy, so it names "whatever shipped last",
not a thing. Consequences: "what is prod running?" is answerable only by
inferring from a branch tip; rollback means rebuild; and a concurrent build can
move the tag between our push and the `gcloud run deploy` that resolves it.

**3. The template offers no promotion story at all**, so every fork invents one
(#47). The non-obvious constraint each fork must rediscover is that **the
frontend cannot be copied between environments** — Next.js inlines
`NEXT_PUBLIC_*` into the static bundle at compile time
([frontend/Dockerfile:22-46](../../../frontend/Dockerfile#L22)), so a test-built
UI carries test's Firebase project and API URLs. A fork that copies both
containers ships prod pointed at test, and the failure is **silent**.

**Current State:**

| | Platform (today) | AIPLA (after its dev→test→prod cut) |
|---|---|---|
| dev | push `dev` → rebuild | push `dev` → rebuild (same) |
| test | merge `dev`→`test` branch → rebuild from source | push git tag `v*` → build once, CI-gated |
| prod | merge `test`→`prod` branch → **rebuild from source again** | `make promote` → **copy the tested digest** |
| Image identity | `:${BRANCH_NAME}` — mutable | `:vX.Y.Z` — immutable per release |
| Deploy reference | by tag | backend by `@sha256:…` + digest-equality assertion |
| Rollback | rebuild, or Cloud Run traffic shift | redeploy a prior digest; traffic shift |

**Impact:** for the platform, a latent correctness gap we have so far been lucky
with. For the template, a guaranteed rediscovery cost for every fork that
reaches a second environment, with a silent failure mode at the end of it.

### The process finding, which matters more than the mechanics

AIPLA's promote pipeline was committed, reviewed and documented for **six weeks**
before anyone ran it. Its first real execution failed immediately on
`gcloud artifacts docker images copy` — a command that does not exist.
Independently verified here against SDK 557.0.0: `gcloud artifacts docker images`
offers only `delete`, `describe`, `get-operation`, `list`, `list-vulnerabilities`,
`scan`. Two further latent bugs (both instances of #46) surfaced in the same run.

The lesson generalises past this feature: **a release path that has never
executed is not a release path**, and shipping one into a template multiplies
the error. This design treats "it has run end-to-end, at least once, against a
real environment" as an acceptance criterion, not a follow-up.

## Goals

**Primary Goal:** A release is an immutable artifact with a name; promoting it to
the next environment moves the tested bytes rather than rebuilding them — and the
template makes that the paved path for new forks, without breaking the branch
flow existing forks (and this repo) already run.

**Success Metrics:**
- Every deployed revision references its backend container by **digest**, so
  "what is running here" is answerable exactly, per environment.
- Promotion to prod runs **no source rebuild of the backend or toolbox** — the
  heavy, dependency-laden, logic-bearing artifacts.
- The frontend is rebuilt from the **same immutable tag** with the target
  environment's config — "the tested frontend + prod config", not a divergent build.
- Rollback is a redeploy of a prior digest (no rebuild), in one command.
- A fork that follows the template's documented path cannot silently ship
  test's `NEXT_PUBLIC_*` into prod.
- The pipeline has been **executed successfully at least once** before it is
  published to the template.

**Non-Goals:**
- Changing the dev flow. `push dev → rebuild` stays exactly as it is; fast
  iteration is the point of that environment.
- Forcing the platform off its branch model in this doc. Phase 1 lands the
  machinery and validates it; whether we flip our own prod is a separate,
  explicit decision (see Open Questions).
- Provisioning triggers in the infrastructure repo. This doc specifies their
  shape; `multivac-aitana` owns creating them.
- Any change to release *versioning* policy (what earns a `vX.Y.Z`).

## Axiom Alignment

Scored per [Product Axioms](../../product-axioms.md).

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Deploy infrastructure. Promotion is faster than a rebuild, but that is operator latency, not user latency. |
| 2 | EARNED TRUST | 0 | No factual-claim surface. |
| 3 | SKILLS, NOT FEATURES | 0 | Invisible infrastructure. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | n/a. |
| 5 | GRACEFUL DEGRADATION | +1 | Rollback becomes "redeploy the previous digest" (immutable, still in AR) instead of "rebuild and hope". Removes the rebuilt-differently-in-prod failure class entirely. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Internal infrastructure; touches no protocol boundary. (OCI digest addressing is standard tooling, not an open *communication* protocol — scoring this +1 would be stretching the axiom.) |
| 7 | API FIRST | 0 | Operator surface, not a channel. The CLI group is an affordance over it, not a second API. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Release-level observability: the tested digest *is* the prod digest, recorded in the build and queryable via `aiplatform deploy status`. Weaker than the axiom's trace-centric KPIs — counted, but flagged as the softest of the three. |
| 9 | SECURE BY CONSTRUCTION | +1 | Architectural, not disciplinary: immutable digests remove the "someone repushed `:prod`" mutation risk; running promotion as a trigger removes the operator's laptop from the release path by construction; prod can only receive bytes that passed the CI gate at build time. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Phase 2 *leans on* thin-client (the frontend's only per-env difference is config) but doesn't advance the axiom. |
| | **Net Score** | **+3** | |

**Threshold note (required — +3 is below the +4 band).** The axioms are weighted
for user-facing product work; deploy infrastructure structurally cannot score
above about +4 because six of the ten axioms have no surface here. The same
scoring produced +3 on AIPLA's own version of this doc, and there is precedent in
this version folder — [production-online-evaluation.md](production-online-evaluation.md)
carries an identical below-band note. The three +1s are each concrete and
defensible, and no axiom scores -1, so no hard-fail rule is engaged. **This needs
a human call rather than a redesign**: the alternative to a weak-but-positive
score here is leaving a known correctness gap in the release path.

## Standards Compliance

No schema, wire format, or protocol is invented. The design uses:

- **OCI image digests** (`@sha256:…`) as the release identity — the standard
  content-addressed reference, already understood by Artifact Registry and Cloud Run.
- **`crane`** (`gcr.io/go-containerregistry/crane:debug`) for registry-to-registry
  copy by digest, which moves no layers through local disk. Chosen because the
  `gcloud` equivalent **does not exist** (verified above), not by preference.
- **Cloud Build triggers** with `--tag` as the execution mechanism, so the build
  source is the tagged repo state.
- **Git annotated tags** as the release unit.

No ADK↔custom seam is touched, so `adk-contract-checklist.md` does not apply.

## Design

### What does not change (the non-breaking guarantee)

Every existing path keeps working, unmodified:

- `push dev` → `cloudbuild.yaml` → rebuild + deploy dev. Unchanged.
- The `dev`→`test`→`prod` branch merges and their triggers. Unchanged and still
  functional; Phase 1 adds a path beside them, it does not remove one.
- The `ci-gate-backend` / `ci-gate-frontend` steps and the `waitFor` graph test
  that enforces them (#36). Unchanged — promotion inherits a frozen artifact
  that was gated at build time, and adds no gate of its own.
- The post-deploy smoke block. Reused verbatim by the promote pipeline.

An existing fork that pulls this template refresh and changes nothing continues
to deploy exactly as before.

### Phase 0 — release identity (both models, no behaviour change)

Two edits to `cloudbuild.yaml`, applied to the branch flow as it stands:

1. **Tag every image immutably in addition to the branch tag.** Keep
   `:${BRANCH_NAME}` (nothing that reads it breaks) and add `:${SHORT_SHA}` —
   or `:${TAG_NAME}` when the build was triggered by a tag. An exact build
   becomes addressable forever.
2. **Deploy by digest.** After push, resolve each image's digest and pass
   `--image …@${DIGEST}` to `gcloud run deploy` instead of the mutable tag.
   This closes the tag-moves-under-us race and makes every Cloud Run revision
   self-documenting about which build produced it.

This is worth doing on its own merits even if Phase 1 never ships: it is the
difference between "prod is on the prod branch" and "prod is on
`backend@sha256:b355…`, built by build `52952275` from `39ae0a6`".

### Phase 1 — the promotion path

#### What is copy-promotable, and what is not

Verified against the three containers the platform's Model-A service actually runs
([cloudbuild.yaml:310-348](../../../cloudbuild.yaml#L310)):

| Container | Image | Copy-promotable? | Why |
|---|---|---|---|
| `sidecar` (backend) | `…/backend` | **Yes** | Every environment-specific value is supplied at deploy time via `--set-env-vars` / `--set-secrets`. The image carries no baked environment state. |
| `toolbox` (MCP Toolbox) | `…/toolbox` | **Yes** | Config comes from `/app/tools.yaml` + args at deploy time. Same reasoning. |
| `main` (Next.js UI) | `…/ui` | **No** | `NEXT_PUBLIC_*` is inlined into the static bundle at compile time and every one of those values is environment-specific (Firebase project, backend URL, sandbox URL, auth mode, branding). A copied UI ships the source environment's identity. |

Note this is one container *more* than AIPLA's promote handles — their pipeline
copies one image and rebuilds one. Ours copies two and rebuilds one.

#### The pipeline (`cloudbuild.promote.yaml`, runs in the TARGET project)

1. **`guard-version`** — refuse to run without an explicit `_VERSION`. A promote
   without a frozen tag is a rebuild wearing a promote's clothes.
2. **`copy-backend` / `copy-toolbox`** — resolve the source digest *first* (it is
   the promotion's identity), `crane copy` registry-to-registry, then re-read the
   destination digest and **fail the build if it changed in transit**. A copy that
   silently retagged something else would otherwise deploy unverified bytes to prod.
3. **`build-frontend`** — rebuild the UI from the tagged source with the *target*
   environment's config (`get-firebase-config.sh` against the target project, plus
   the target's `_MCP_SANDBOX_URL` and branding substitutions). Same source, target
   config.
4. **`deploy`** — `gcloud run services update`, swapping **only the images** on the
   three containers, backend and toolbox pinned by digest.
5. **`smoke`** — the existing smoke block; any non-200 fails the promotion.

**`services update`, not `run deploy`, is load-bearing for us.** Our deploy step
re-asserts ~25 `--set-env-vars` and the full secret set on every deploy, several
of them computed inline per branch. Re-asserting that surface from a promote
pipeline would duplicate it and invite drift. `services update --container X
--image Y` changes the images and leaves the environment's configuration — set at
env-cut and owned by Terraform — untouched.

#### Trigger shapes

| Env | Trigger | Fires on | Config | Rebuilds? |
|---|---|---|---|---|
| dev | existing `trigger-aitana-dev-*` | push to `dev` | `cloudbuild.yaml` | yes — unchanged |
| test | `trigger-aitana-test-release` (**new, template default**) | tag push `^v.*$` | `cloudbuild.yaml` | yes — build-once happens here, CI-gated |
| prod | `trigger-aitana-prod-promote` (**new**) | manual, `--tag=vX.Y.Z` | `cloudbuild.promote.yaml` | no backend/toolbox rebuild; UI rebuilt from the tag |

> **Correction (2026-07-31, found during M3 verification).** An earlier draft of
> this section said the promote trigger "needs `roles/artifactregistry.reader`
> for the target's build SA on the source project's registry." **That is wrong
> for this repo.** Checked against the live services:
>
> ```
> dev  -> …/your-deploy-project-id/llm-ops/platform-frontend/ui@sha256:…
> test -> …/your-deploy-project-id/llm-ops/platform-frontend/ui:test
> prod -> …/your-deploy-project-id/llm-ops/platform-frontend/ui:prod
> ```
>
> All three environments pull from **one shared Artifact Registry** in the
> deploy project and are distinguished by **tag**, not by per-env registries.
> AIPLA has per-env registries (`aipla-<env>-2026/cphu/…`), which is why *their*
> promotion needs a genuine cross-project copy; ours does not. So:
>
> - **No new IAM is required.** The builds already run in
>   `your-deploy-project-id` and already read and write that registry.
> - **The copy step is a no-op here** (source and destination resolve to the
>   same repo). It stays in the pipeline because it is what makes the shape
>   correct for a fork with per-env registries, and because the digest-equality
>   assertion is what proves a promotion actually promoted. Both substitutions
>   are set to the same value in this repo.
> - **What actually moves an env forward here is the deploy step** pinning the
>   tested digest onto the target service. That was always the load-bearing
>   part; the copy was scaffolding around it.
>
> This is a good illustration of the sprint's own thesis: the error was in a
> reviewed design doc, survived into a pipeline, and was only caught by looking
> at what the running system actually does.

For a fork whose environments have **separate** registries, the promote trigger
runs in the target project and needs `roles/artifactregistry.reader` for the
target's build SA on the source project's registry — read-only, and declared in
`tf_account_permissions`, **never** a console grant.

For the platform specifically, the existing branch triggers stay armed during
Phase 1 so nothing changes underneath us. The template ships the tag+promote
shape as its documented default, with the branch triggers presented as the
simpler alternative for single-environment forks.

#### Why a trigger and never `gcloud builds submit`

This is AIPLA's newest lesson and is not yet in their feedback log — it is in
their deploy runbook, learned by being bitten.

`gcloud builds submit .` uploads the **operator's local working tree** as the
build source. AIPLA's prod `ui:v0.1.3` was therefore built from a laptop commit
(`07d4751`) rather than from the tag. A `HEAD == tag` guard is a seatbelt on a
design that should not need one. Running the promote as a **trigger with
`--tag`** makes Cloud Build check the repo out at the tag, so the operator's
checkout is irrelevant to what ships.

It also sidesteps **#46** entirely, which is worth stating because we would
otherwise walk into it the moment we wrote a submit-based script:

- `builds submit` without `--service-account` silently falls back to the Compute
  Engine default SA, so one pipeline runs as two identities and any IAM grant
  covers exactly one of them. AIPLA's symptom was `PERMISSION_DENIED` on a
  cross-project Artifact Registry read — which sends you hunting in the
  cross-project grant, the wrong place entirely.
- Adding `--service-account` then reveals a second requirement invisible from the
  trigger path: `builds submit` stages a source tarball in the auto-created
  `<project>_cloudbuild` bucket and the named SA needs `storage.objectViewer` to
  read it back. Trigger builds fetch from the repo connection and never touch it.

We currently have **zero** `gcloud builds submit` calls in [scripts/](../../../scripts/),
[Makefile](../../../Makefile) or [cloudbuild.yaml](../../../cloudbuild.yaml), so
#46 does not bite us today. The mitigation is to keep it that way: the promote
wrapper runs a trigger, and the gotcha is documented so the next script author
does not reintroduce it.

#### CLI Surface

Per the CLI-affordance rule, the operator path is typed, not a `gcloud`
incantation. New Click group `cli/aiplatform/commands/deploy.py`, wired via
`main.add_command(deploy)`:

- **`aiplatform deploy promote --from test --to prod --version vX.Y.Z [--dry-run] [--yes]`**
  — validates the promotion edge (only `dev→test`, `test→prod`), verifies the tag
  exists on `origin` and the source image is present, then runs the
  `trigger-aitana-prod-promote` trigger at that tag. Default is
  confirm-before-acting; `--dry-run` prints the exact commands and exits.
- **`aiplatform deploy status [--env dev|test|prod]`** — the live Cloud Run
  revision **and image digest** per environment, so "are test and prod on the same
  backend digest?" is one command. Useful on day one, independent of whether we
  ever promote.
- **`aiplatform deploy release --version vX.Y.Z`** — thin wrapper over
  `git tag -a … && git push origin …`, which fires the test-release trigger.

Backed by `scripts/promote-env.sh` (single source of promotion logic, with
`--dry-run`) and `make promote FROM=test TO=prod VERSION=vX.Y.Z`, per the
automation principle. Tested with `CliRunner` + `subprocess.run` mocks.

### Phase 2 — runtime frontend config (the real fix, spike first)

Everything above treats "the frontend cannot be copied" as a fact to design
around. It is actually a choice we can reverse, and doing so is the strongest
single improvement available here.

If the frontend reads its public config at **runtime** — a `/api/config` route,
or a `window.__ENV__` blob injected at container start — instead of at compile
time, then:

- The UI image becomes environment-agnostic and promotion is a pure copy of all
  three containers. Full build-once.
- **Source item #18 dies permanently.** That entry (Dockerfile silently drops any
  `--build-arg` for an undeclared `ARG`, yielding `undefined` at runtime and a
  wrong-but-running deploy) was closed by making `get-firebase-config.sh`
  fail loudly on a missing `ARG`. That is a good guard on a design that should
  not need one — the 25-line `ARG` list in
  [frontend/Dockerfile](../../../frontend/Dockerfile#L22) exists only because
  config is compile-time. Delete the cause and the guard becomes unnecessary.
- A config change (a branding tweak, a sandbox URL) stops requiring an image rebuild.

The cost is real and needs scoping before committing: Firebase client init,
static/SSG rendering boundaries, and the first-paint cost of a config fetch all
need answers. Hence **spike first** — hence P2.

### API Changes

None. No backend route, no frontend surface, no Firestore schema.

## Migration & Rollout

Phased so that nothing is at risk at any point:

- **Phase 0** — additive tags + digest deploy. Lands on `dev` first; the digest
  is visible in the revision immediately. No fork action required; forks pick it
  up on the next template refresh and get better rollback for free.
- **Phase 1** — new files (`cloudbuild.promote.yaml`, `scripts/promote-env.sh`,
  the CLI group) plus new triggers in the infrastructure repo. Existing triggers
  stay armed. **Validated on `dev → test` before it is offered for prod, and
  before it is published to the template** — see Testing Strategy.
- **Phase 2** — spike, then a decision. Not scheduled here.
- **The prod flip** — a separate change, gated on v6 being ratified on test
  (`test.yourcompany.com`). See Open Question 1, which is resolved: prod ends up
  promote-only, and nothing about the prod trigger changes before then.

**Rollback of this change itself:** Phase 0 is two lines revertable
independently; Phase 1 is purely additive files plus triggers that can be
disabled without touching the branch path.

**For existing forks:** no action, no breakage. For new forks: the template's
deployment docs present tag→test + promote→prod as the recommended path, with
the branch model documented as the simpler single-environment option.

## Testing Strategy

The unusual requirement here is the one #47 earned the hard way — most of the
value is in proving the pipeline *runs*, not that it parses.

- **Backend/CLI unit tests** (pytest): `CliRunner` + `subprocess.run` mocks
  asserting `deploy promote` refuses invalid edges (`dev→prod`), refuses a
  missing tag, and emits the exact trigger invocation. `--dry-run` asserted
  end-to-end. Runs in `make test-fast`.
- **Pipeline structure test:** extend the existing `waitFor`-graph test that
  guards the CI gate so it also asserts `cloudbuild.promote.yaml`'s deploy step
  waits on both copy steps and the frontend push, and that the digest-equality
  assertion is present. Cheap, and it catches a reordering that would deploy
  before the copy verified.
- **Static check on the promote config:** `shellcheck` the inline scripts and
  assert every `gcloud`/`crane` subcommand invoked actually exists in the pinned
  builder image. This is the check that would have caught
  `gcloud artifacts docker images copy` six weeks earlier.
- **First-run validation (acceptance gate, not optional):** one real
  `dev → test` promotion executed end-to-end, with `aiplatform deploy status`
  showing an **identical backend digest** in both environments afterward.
  Per CI/CD-first: the feature is not done, and is **not published to the
  template**, until this has run against a live environment.
- **Smoke:** the promote pipeline's own smoke step is the deploy-time gate;
  `./scripts/smoke-deployed.sh <env> all` is the operator-side repeat.

## Security Considerations

- **No new trust edge in this repo** — see the correction under *Trigger shapes*.
  All environments share one registry in the deploy project, which the builds
  already read and write, so promotion adds no IAM at all. (A fork with per-env
  registries needs one read-only `roles/artifactregistry.reader` grant, declared
  in `tf_account_permissions` per the no-manual-IAM rule.)
- **Reduced trust surface elsewhere:** promotion by trigger removes operator
  laptops from the release path; immutable digests remove the mutable-tag repush
  vector; prod can only receive artifacts that passed the CI gate at build time.
- No data crosses the GCP project edge. Container images move between Aitana
  projects inside the same folder; no customer content is involved at any step.
- `_SKIP_CI_GATE` remains settable only on a manual `triggers run`, never by a
  push. Promotion runs no gate **by design** (frozen artifact, gated at build);
  that is a deliberate decision, recorded here so it is not read as an omission.

## Success Criteria

- [ ] Phase 0: every Cloud Run revision references backend and toolbox by digest;
      `:${SHORT_SHA}` / `:${TAG_NAME}` tags present in Artifact Registry.
- [ ] `aiplatform deploy status --env <env>` reports the live revision + digest
      for dev/test/prod.
- [ ] `aiplatform deploy promote --dry-run` prints a correct copy-not-rebuild plan.
- [ ] `cloudbuild.promote.yaml` copies backend + toolbox by digest with an
      equality assertion, and rebuilds only the UI.
- [ ] A real `dev → test` promotion has executed successfully, and
      `deploy status` shows the same backend digest in both.
- [ ] Existing branch-push deploys to dev are byte-for-byte unaffected
      (dev deploy green after the change).
- [ ] `docs/ops/` documents the promotion model, including **why the frontend is
      not copyable** and the `builds submit` gotcha (#46).
- [ ] Template refresh published only after the first-run validation above.
- [ ] Entries #46/#47 marked resolved in the fork's `docs/upstream-feedback.md`
      and in [template/SEQUENCE.md](../template/SEQUENCE.md).

## Open Questions

1. ~~**Do we flip the platform's own prod to promote-only, and when?**~~
   **RESOLVED 2026-07-31 (M):** yes — **prod becomes promote-only**, but not
   touched until v6 is ratified on **test** (`test.yourcompany.com`). Sequencing:
   Phase 0 + Phase 1 land and are validated on `dev → test` while test carries
   the ratification traffic; the prod flip (disable the `trigger-aitana-prod-*`
   branch deploys, cut releases by tag, promote by digest) happens **after**
   ratification, as its own change with its own verification. Until then the
   prod branch trigger stays exactly as it is. This is what makes the work
   additive rather than a release-path migration under a live cutover.
2. **Where do the promote triggers live?** Our triggers are in
   `your-deploy-project-id`; AIPLA moved theirs into the environment projects.
   Cross-project AR read is the same shape either way; settle with the
   infrastructure repo owner.
3. **Tag scheme** — reuse the app version (`v6.20.0`) or a deploy-specific
   `rel-YYYYMMDD-N`? Lean: app version, one tag per release candidate.
4. **Phase 2 timing** — before or after the template refresh? Doing it first
   makes promotion a pure copy and deletes #18's cause, but delays #47's fix for
   forks that need a promotion story now. Lean: ship Phase 1, spike Phase 2.
5. **Adjacent finding, needs verification before acting:**
   [cloudbuild.yaml:344](../../../cloudbuild.yaml#L344) sets
   `PUBLIC_BASE_URL=https://your-service-url.example` as a
   **hardcoded, non-substituted** value in the config all three environment
   triggers share — so test and prod stamp dev's URL into
   `backend/protocols/a2a.py`'s agent-card `url`. The G43 proxy rewrite
   (`X-Forwarded-Proto`/`X-Forwarded-Host`) likely masks this for cards fetched
   through the frontend, which would explain why it has not surfaced. Worth
   confirming and moving to a substitution regardless: it is exactly the class of
   drift that "one shared build config, rebuilt per branch" hides, and it is
   invisible under the current model.

## Related Documents

- `docs/upstream-feedback.md` in `sunholo-data/cphu-aipla-app` — entries #46, #47 (the source)
- [docs/ops/env-promotion-audit.md](../../ops/env-promotion-audit.md) — the existing dev→test→prod audit + IAM cascade
- [docs/ops/env-cut-runbook.md](../../ops/env-cut-runbook.md) — one-time environment creation
- [docs/ops/deployment-models.md](../../ops/deployment-models.md) — Model A vs B service topology (G44)
- [docs/design/template/template-cloudbuild-hardening.md](../template/template-cloudbuild-hardening.md) — #7's trigger-side fix, which #46 extends to the submit path
- [docs/design/template/SEQUENCE.md](../template/SEQUENCE.md) — the upstream-feedback item index
- [docs/design/v6.1.0/local-dev-cli.md](../v6.1.0/local-dev-cli.md) — the CLI this adds a `deploy` group to

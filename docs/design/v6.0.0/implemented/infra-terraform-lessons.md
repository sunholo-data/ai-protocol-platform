# INFRA-TERRAFORM Sprint — Lessons & Gotchas

Post-mortem notes from the v6 infrastructure bring-up (sprint INFRA-TERRAFORM, 2026-04-14). These gotchas bit us during dev apply and will bite any similar work in test/prod or in other `multivac-*` repos — capture them here so they inform the next sprint.

## 1. Cloud Build auto-applies on push — there is no "plan-only" gate

**What we assumed:** The sprint plan used "user-gated apply" phrasing, implying a plan-only CI path with a manual approval step.

**What actually happens:** `trigger-terraform-your-deploy-project-id` in project `multivac-deploy` fires on every push to `^(dev|test|prod)$`, runs the full pipeline (`check-client-tfvars → init → workspace select → validate → plan → apply → output`), and ends with a real `terraform apply`. **There is no gate.** The commit review IS the gate.

**Implications:**
- Never push speculatively to `dev` to "see what a plan looks like" — you'll apply it.
- For a plan-only check, run `terraform plan` locally against the shared GCS backend (this works; we have access).
- Logs stream via `gcloud builds log <id> --region=europe-west1 --project=multivac-deploy --stream`. Build log goes to `gs://multivac-deploy-logging-bucket` (GCS_ONLY), not Cloud Logging.

**Stored in:** `memory/reference_terraform_build.md`.

## 2. `google_project_iam_member` shared across modules is a destroy/create race

**The bug:** Four modules (`cloud_run_client["aitana"|"backend-api"|"acme-energy"]` + `cloud_run_multiple["frontend"]`) each declared their own `google_project_iam_member` for the exact same (project, role, member) triple: `roles/artifactregistry.reader` for the Cloud Run Service Agent on `your-deploy-project-id`. Terraform saw 4 resources, GCP saw 1 binding. Every apply, the four copies destroy-recreated (9s each, staggered) — and any Cloud Run revision unlucky enough to pull its image during the ~10s binding-absent window got a 403 DENIED on `artifactregistry.repositories.downloadArtifacts`.

**The fix (infra@c1a58ab):**
1. Declared each unique `(project, role, member)` binding exactly **once** per env in `environments/<env>/iam_cloud_run_agent.tf`, plus a single `time_sleep.cloud_run_agent_iam_propagation`.
2. Stripped `google_project_iam_member.*` and `time_sleep.wait_after_iam` from `modules/run/run.tf` and `modules/run_multiple/run.tf`.
3. Each `module "cloud_run_*"` block now `depends_on = [..., time_sleep.cloud_run_agent_iam_propagation]`.
4. Migrated state with `terraform state rm` on all three workspaces (dev/test/prod) — the binding stays in GCP, just stops being tracked 4 times.

**Rule to migrate up the stack:** `google_project_iam_member` is **non-authoritative** — if two+ resources declare the same binding, the last write at apply-time wins and the others leave permission-gaps. **Declare each binding exactly once** at the highest shared scope (env or project-level module, never per-service).

## 3. Local `terraform plan` lies — `run_client.tfvars` is fetched at build time

**What we saw:** Local `terraform plan` in `environments/dev/` showed 8 destroys including `module.cloud_run_multiple["frontend"]` and `module.cloud_run_client["backend-api"]` — alarming.

**Root cause:** Step `check-client-tfvars` pulls the real `run_client.tfvars` from GCS (`gs://your-deploy-project-id-config-bucket/...`) at build time. The file checked into the repo is a stub that only defines `acme-energy` and `aitana`. Running `terraform plan` locally without the fetched file makes terraform think `backend-api` and `frontend` were removed from config.

**Implication:** Local plans are useful for checking your own changes' shape (creates, in-place updates, state-rm migrations) but cannot be read literally for destroy actions. Trust the build's plan step, not your laptop's.

## 4. `locals.modified_buckets` drops `lifecycle_rule`

We added `platform-logs` to the common buckets map with a 90-day retention lifecycle rule. On `terraform plan` it silently disappeared. The `locals.modified_buckets` transformation in `environments/*/locals.tf` doesn't pass `lifecycle_rule` through to the `gcs` module. We shipped M2 without lifecycle and flagged it in the sprint notes; fix requires editing the locals block or the `gcs` module signature.

## 5. Google provider is pinned `< 6.0.0` — blocks `google_vertex_ai_reasoning_engine`

The Agent Engine resource (`google_vertex_ai_reasoning_engine`) only exists in the google provider v6+. Bumping the pin in a shared infra repo is a cross-project change (all Sunholo deployments use this repo), so we bootstrap Agent Engine via Python SDK (`backend/scripts/bootstrap_agent_engine.py`) instead of Terraform. The resource ID is then seeded into a Terraform-managed Secret Manager entry.

**Migration note:** If/when we do bump google provider, `google_vertex_ai_reasoning_engine` can replace the Python bootstrap.

## 6. BigQuery dataset names: underscores only, no hyphens

Our naming convention uses hyphens (`platform-logs`), but `google_bigquery_dataset.dataset_id` rejects hyphens. We used `platform_telemetry` (underscores) for the dataset and added a regex validation to the variable to prevent accidental hyphen use.

## 7. Agent Engine SDK shape changed between versions

`vertexai.agent_engines.create()` signature differs:
- **Newer SDK:** `create(display_name=..., description=...)` — no `agent_engine` arg.
- **Older SDK:** requires `create(agent_engine=<obj>, display_name=..., description=...)` where `<obj>` implements at least `.query()`.

`bootstrap_agent_engine.py` catches `TypeError` and falls back to a `_NoOpEngine` wrapper for the older shape. Keep the try/except until the SDK version is pinned and known.

## 8. `terraform fmt` writes files by default

Running `terraform fmt` (or `terraform fmt -diff`) canonicalizes files in-place. Use `-check` for read-only. We accidentally reformatted whole files twice during the sprint — the diffs dwarfed the intentional changes. Keep `fmt` commits separate from feature commits when possible, or warn reviewers in the commit body that the diff is mostly canonicalization.

## 9. `gh` account switch required for multivac-aitana pushes

Local git defaults to `sunholo-voight-kampff` but push access to `sunholo-data/multivac-aitana` requires `MarkEdmondson1234`. Switch with `gh auth switch --user MarkEdmondson1234` before push. Auth state persists.

## 10. Secret Manager convention: `dummy_value` placeholder + post-apply seed

The shared `secret_manager` module seeds initial values from the tfvars map, so adding a secret entry like `AGENT_ENGINE_ID` requires a value. Repo convention is `"dummy_value"` as a placeholder; the real value is seeded post-apply with `gcloud secrets versions add <name> --data-file=-`. We used this for AGENT_ENGINE_ID (v1 = `dummy_value`, v2 = real resource ID).

## 11. State migration for shared-infra module refactors

When removing resources from a module that's used in N environments, **every workspace's state must be migrated** (`terraform state rm` for removed-from-config resources) BEFORE the branch that triggers the apply is pushed. Otherwise the apply will see "resource in state but not in config" and plan a destroy — and for IAM bindings, that destroy is a brief but real loss of permission.

Workflow we used:
1. Edit module + add env-level replacement.
2. For each workspace (dev, test, prod): `terraform init && terraform workspace select <env> && terraform state rm <resource>`.
3. Verify plan locally shows CREATE-only for the replacement.
4. Push the branch whose apply should consume the change first (dev).
5. Only after dev validates, push test. Only after test validates, push prod.

This is a brittle choreography — a `removed` block (Terraform 1.7+) with `lifecycle { destroy = false }` could codify step 2 into the configuration and make the migration self-healing. Worth adopting if we do this again.

## 12. Agent Engine ID: numeric suffix vs full resource name

**Symptom:** `VertexAiSessionService.create_session()` returns `404` with a URL containing doubled prefix:
```
/v1beta1/reasoningEngines/projects/.../reasoningEngines/NNN/sessions
```

**Root cause:** ADK's `VertexAiSessionService(agent_engine_id=...)` and `VertexAiMemoryBankService` expect the trailing **numeric ID** (e.g. `6224370509212024832`), not the full resource name. The SDK prepends `reasoningEngines/` unconditionally, so passing the full path doubles the prefix.

**Fix (platform@this commit):**
1. `bootstrap_agent_engine.py` now prints only the numeric ID (`_numeric_id()` helper) — callers piping into `gcloud secrets versions add` get the right value.
2. `backend/adk/session.py` strips any `projects/.../reasoningEngines/` prefix defensively via `_normalize_agent_engine_id()` so either form works.
3. Dev secret re-seeded: `gcloud secrets versions add AGENT_ENGINE_ID --data-file=-` with just `6224370509212024832`.

**Rule:** When an SDK takes `<resource>_id`, pass the trailing ID only. When it takes `<resource>_name` or `<resource>`, pass the full resource path. Don't mix.

## 13. `GOOGLE_API_KEY` in shell env breaks Agent Engine calls

The google-genai SDK prefers `GOOGLE_API_KEY` over ADC. Agent Engine session APIs reject API-key auth with `401 UNAUTHENTICATED / CREDENTIALS_MISSING` ("API keys are not supported by this API"). If a dev has a personal `GOOGLE_API_KEY` in their shell, smoke tests fail with a cryptic 401.

**Workaround:** `env -u GOOGLE_API_KEY uv run python scripts/smoke_test_infra.py ...`. The smoke script now detects and warns on startup.

## 14. Developer impersonation for sa-platform (dev only)

Local `smoke_test_infra.py` needs to read the `AGENT_ENGINE_ID` secret and exercise Agent Engine / Firestore / GCS / BQ. The cleanest path is to impersonate `sa-platform` (which already has all the runtime roles at project level) rather than duplicating every role grant onto developer users. Added `module "impersonate_platform"` in `environments/dev/main.tf` using the existing `modules/iam` pattern, granting `roles/iam.serviceAccountTokenCreator` + `roles/iam.serviceAccountUser` on the SA to `user:${var.gcloud_email}`.

**Not added in test/prod:** impersonation is a local-dev concern. Test/prod Cloud Run already runs as the SA natively.

**Local usage after apply:**
```bash
gcloud auth application-default login \
  --impersonate-service-account=sa-platform@your-project-id.iam.gserviceaccount.com
```

## 15. Shared-IAM race redux: `modules/pubsub` + `modules/gcs` (deferred)

Every aitana Terraform apply shows `19 destroyed / 21 added / 16 changed` even when no aitana-owned resources change. Diagnosed (infra@cae2306 apply, 2026-04-14) as the same class of bug as gotcha #2, now in shared modules we don't own outright:

- **`modules/pubsub`** — each topic instance declares `google_project_iam_member` for 4 project-wide GCP service agents (`editor`, `monitoring`, `pubsub_admin`, `viewer`). With 4 topics, that's 16 duplicate declarations of the same `(project, role, member)` triples. Terraform destroys + recreates them every apply (though the Google-managed binding itself is stable).
- **`modules/gcs`** — the `llmops` bucket has 3 `google_*_iam_member` resources where `member` references a `google_project_service_identity` data source that terraform treats as `(known after apply)`, forcing replacement every run.

**Why we haven't fixed it:** these modules are shared across every `multivac-*` deployment (aitana is one client of several). A narrow aitana-scope fix (add env-level `iam_pubsub_service_agents.tf`, wire a `skip_iam = true` flag into the pubsub module, state-rm orphans across dev/test/prod) would fork the module. A proper upstream fix needs coordination across all multivac client repos and is out of scope for v6 bring-up.

**Real risk:** cosmetic for pubsub — Google-managed service agents, binding gap is brief, no Cloud Run impact (that was gotcha #2's victim). Tackle in `sunholo-data/multivac` repo itself as a tooling-sprint item when capacity allows.

**Signal to watch:** if any future apply to dev/test/prod introduces an IAM binding keyed to a *non-Google-managed* member (user SA, workload identity) and routes it through these modules, the transient-gap window could cause the same class of 403 the `cra_*` fix closed. Flag in review.

## Migration checklist for test/prod rollout

- [x] Dev apply validated (build 3aba14ed SUCCESS, 2026-04-14).
- [ ] Push `test` branch — expect plan: CREATE 3 env-level IAM + time_sleep, no destroys.
- [ ] Verify frontend Cloud Run in `your-project-id-test` stays Ready across the apply.
- [ ] Push `prod` branch — same expectation.
- [ ] Re-run `bootstrap_agent_engine.py` against each env (test, prod) and seed the env's `AGENT_ENGINE_ID` secret.
- [ ] Run `verify_infra.py` against each env.

---

## Implementation Report

**Completed**: 2026-04-21
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

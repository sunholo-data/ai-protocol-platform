# API Security Port — Firebase keys + AI audit logging

**Status**: Implemented (M1–M5 complete; M6 prod apply gated on 24h v6 test smoke — see sprint doc)
**Priority**: P0 (High)
**Estimated**: 1 day coding + 0.5 day rollout (dev → test) + prod applied alongside v6 test→prod cut
**Scope**: Infrastructure (terraform only)
**Dependencies**: None
**Created**: 2026-04-21
**Last Updated**: 2026-04-21

## Problem Statement

The `sunholo/multivac` repo carries five Firebase/AI security controls added after the **2026-03-28 API key abuse incident**. These controls were never ported to `sunholo/multivac-aitana`, which manages `your-project-id-{dev,test,production}`. This means:

- **v5 production is currently unprotected.** The Firebase browser API key on `your-project-id-prod` has no HTTP referrer restriction — a stolen key can be used from anywhere.
- Vertex AI data access on all three aitana projects is **not audit-logged**. A prompt-injection-driven exfil would be invisible after the fact.
- Secret Manager reads are **not audit-logged** either — same blind spot for credential access.
- The Generative Language API is **not quota-blocked** in unused regions, so a compromised key can fan out to every region by default.
- No **usage-spike monitoring alerts** exist, so a runaway-cost abuse event wouldn't page anyone.

**Current State:**
- v5 prod on `your-project-id-prod`: **zero** of the five controls applied.
- v6 dev/test (`your-project-id`, `your-project-id-test`): **zero** of the five controls applied.
- v6 prod cut is imminent (earliest 2026-04-22 after 24h smoke window); applying the controls now means prod is born-secure.

**Impact:**
- v5 production users (live traffic) — an active exposure, not a theoretical one.
- v6 promotion plan — the [env-promotion-audit](../../ops/env-promotion-audit.md) prod-readiness checklist is incomplete without these controls in lockstep across envs.

## Goals

**Primary Goal:** Apply the five post-incident security controls uniformly across `your-project-id-{dev,test,production}` via a single shared terraform module, so no env drifts from another and v5 prod is protected.

**Success Metrics:**
- `gcloud services api-keys list --project=your-project-id-prod` shows the terraform-managed browser key with `allowed_referrers` set.
- `gcloud projects get-iam-policy your-project-id-{dev,test,production} --format="json" | jq '.auditConfigs'` shows matching DATA_READ/DATA_WRITE config for `aiplatform.googleapis.com` and `secretmanager.googleapis.com` on all three projects.
- A synthetic high-volume Vertex AI burst (test env) triggers the `vertex_ai_usage_spike` alert email within 5 minutes.
- `terraform plan` for any of the three envs is a no-op after the initial apply.

**Non-Goals:**
- Firebase App Check enforcement (deferred — requires frontend SDK changes).
- reCAPTCHA Enterprise for Firebase Auth (deferred — requires user-facing rollout).
- Service account key rotation automation (deferred — different scope).
- Porting to non-aitana projects (`multivac-internal-*` already has these via the `multivac` repo).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | No user-facing latency impact; audit logs are async. |
| 2 | EARNED TRUST | 0 | No change to factual-claim surface. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure only; invisible to end users. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Orthogonal to model routing. |
| 5 | GRACEFUL DEGRADATION | +1 | GenAI regional quota blocks + spike alerts prevent a single compromise from fanning out silently — the system degrades visibly rather than burning credits invisibly. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses GCP-native primitives (`google_apikeys_key`, `google_project_iam_audit_config`, `google_monitoring_alert_policy`) instead of any custom rate-limiter or homegrown audit pipeline. |
| 7 | API FIRST | 0 | No API surface change. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Adds DATA_READ/DATA_WRITE audit logs for Vertex AI and Secret Manager — the exact telemetry the axiom calls for, captured inside our GCP project (trust boundary). |
| 9 | SECURE BY CONSTRUCTION | +1 | Enforces security via HCL (not console/discipline). Referrer-restricted keys, regional quota caps, audit logs, and monitoring alerts are all architectural boundaries, not runtime checks. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Pure backend/infra change. |
| | **Net Score** | **+4** | Threshold: >= +4 ✓ |

**Conflict Justifications:** None — no axiom scored -1.

## Design

### Overview

Create a new shared terraform module at `multivac-aitana/infrastructure/modules/api_security/` that bundles all five controls. Invoke it once per environment from `environments/{dev,test,prod}/main.tf` with env-specific values (project_id, allowed_referrers, alert_emails, blocked_genai_regions). This mirrors the folder-cascade IAM pattern the user insisted on after the dev→test drift incident: **one place to change, applies everywhere**.

### Why a module, not per-env duplication

Per-env duplication is exactly what caused [incident #1 in env-promotion-audit.md:273-302](../../ops/env-promotion-audit.md#L273-L302) — dev got `identityplatform.admin` via console while test's terraform didn't know, and the drift was invisible until apply failed. A module with an invocation per env keeps schema and resource topology locked in HCL, reviewable in one place.

### Module Inputs

```hcl
# multivac-aitana/infrastructure/modules/api_security/variables.tf
variable "project_id" { type = string }

variable "allowed_referrers" {
  type        = list(string)
  description = "HTTP referrers allowed to use the Firebase browser API key"
  # dev  default: ["https://platform-frontend-*.run.app/*", "http://localhost:3000/*"]
  # test default: ["https://platform-frontend-test-*.run.app/*", "https://test.yourcompany.com/*"]
  # prod default: ["https://yourcompany.com/*", "https://*.yourcompany.com/*", "https://platform-frontend-prod-*.run.app/*"]
}

variable "alert_emails" {
  type    = list(string)
  default = ["owner@yourcompany.com"]
}

variable "blocked_genai_regions" {
  type        = list(string)
  description = "Regions where Generative Language API quotas are hard-zeroed"
  default     = []  # opt-in per env; start empty, tighten after observing real traffic
}

variable "firebase_api_targets" {
  type = list(string)
  default = [
    "firebase.googleapis.com",
    "firebasehosting.googleapis.com",
    "firebaserules.googleapis.com",
    "firestore.googleapis.com",
    "identitytoolkit.googleapis.com",
    "securetoken.googleapis.com",
    "firebasestorage.googleapis.com",
  ]
}
```

### Module Resources

Ported verbatim from `multivac/infrastructure/environments/prod/main.tf`:

| # | Resource | Ref (multivac) |
|---|---|---|
| 1 | `google_apikeys_key.firebase_browser_key` | main.tf:470-487 |
| 2 | `google_project_iam_audit_config.aiplatform_audit` (ADMIN_READ + DATA_READ + DATA_WRITE) | main.tf:200-206 |
| 3 | `google_project_iam_audit_config.secretmanager_audit` (DATA_READ + DATA_WRITE) | main.tf:208-213 |
| 4 | `google_service_usage_consumer_quota_override.block_genai_region` + `.block_genai_api_region` | main.tf:490-520 |
| 5 | `google_monitoring_notification_channel.email` + `google_monitoring_alert_policy.vertex_ai_usage_spike` + `.genai_api_usage_spike` | main.tf:417-467 |

### Per-Env Invocation

```hcl
# multivac-aitana/infrastructure/environments/prod/main.tf
module "api_security" {
  source     = "../../modules/api_security"
  project_id = var.project_id

  allowed_referrers = [
    "https://yourcompany.com/*",
    "https://*.yourcompany.com/*",
    "https://platform-frontend-*.run.app/*",
  ]

  alert_emails          = ["owner@yourcompany.com"]
  blocked_genai_regions = []  # tighten later once traffic pattern is observed
}
```

Same invocation shape in `dev/` and `test/` with env-appropriate referrer lists.

### Terraform SA Roles Needed

The `sa-cloudbuild@your-deploy-project-id` SA needs these roles (on each target project, via the bootstrap folder cascade — **not per-env `google_project_iam_member`**, per feedback_no_manual_iam_grants and [env-promotion-audit IAM cascade](../../ops/env-promotion-audit.md#L23-L66)):

- `roles/serviceusage.serviceUsageAdmin` — for `consumer_quota_override`.
- `roles/monitoring.alertPolicyEditor` — for alert policies + notification channels.
- `roles/iam.securityAdmin` — for `google_project_iam_audit_config`.
- `roles/apikeys.admin` — for `google_apikeys_key`.

**Rollout prerequisite:** edit `multivac-aitana/infrastructure/bootstrap/terraform.tfvars` → append these roles to `tf_account_permissions`. Push to `dev` branch → bootstrap trigger applies → folder cascade propagates to `your-project-id-{dev,test,production}` within ~60s. Only then does the module apply work.

### Architecture

```
multivac-aitana/
├── infrastructure/
│   ├── bootstrap/
│   │   └── terraform.tfvars         # ← append 4 roles to tf_account_permissions
│   ├── modules/
│   │   └── api_security/            # ← NEW shared module
│   │       ├── main.tf              # 5 resources (see table above)
│   │       ├── variables.tf
│   │       └── README.md            # cross-link to platform design doc
│   └── environments/
│       ├── dev/main.tf              # module "api_security" block
│       ├── test/main.tf             # module "api_security" block
│       └── prod/main.tf             # module "api_security" block
└── docs/
    └── security/
        └── api-key-audit-port.md    # ← NEW companion doc (what + why)
```

## Implementation Plan

### Phase 1: Pre-flight verify (~0.25 day)
- [ ] Run `gcloud services api-keys list --project=your-project-id-prod` to confirm v5 prod actually has no terraform-managed key (expected: no entry or an unrestricted one).
- [ ] Run `gcloud projects get-iam-policy your-project-id-prod --format="json" | jq '.auditConfigs'` to confirm no DATA_READ/DATA_WRITE audit config (expected: null/empty).
- [ ] Record baseline in the companion doc's "Before" section.

### Phase 2: Module + IAM cascade (~0.5 day)
- [ ] Append 4 roles to `bootstrap/terraform.tfvars:tf_account_permissions`. Push to `dev` → wait for bootstrap trigger to apply. Verify via `gcloud projects get-iam-policy` diff on each project.
- [ ] Create `modules/api_security/main.tf` + `variables.tf` + `README.md`.
- [ ] `terraform fmt` + `terraform validate` clean in the module dir.

### Phase 3: Dev rollout (~0.25 day)
- [ ] Wire `module "api_security"` into `environments/dev/main.tf` with dev referrers/emails.
- [ ] `terraform plan` in dev workspace — review.
- [ ] Apply via the terraform trigger (push to `dev` on `sunholo-data/multivac-aitana`).
- [ ] Verify: browser key listed, audit configs present, alert policies present. Document in companion doc.
- [ ] Confirm v6 dev frontend still loads (referrer list is permissive enough).

### Phase 4: Test rollout (~0.25 day)
- [ ] Merge `dev → test` on `multivac-aitana`. Auto-apply via terraform trigger.
- [ ] Same verification as dev.
- [ ] **Trigger the alert**: run a synthetic high-volume Vertex AI burst against test and confirm email lands within 5 min. If not, fix before proceeding.
- [ ] Check v6 test frontend still loads.

### Phase 5: v5 prod + v6 prod (alongside test→prod cut, ~0.25 day)
- [ ] This runs during the v6 test→prod merge window (earliest 2026-04-22) — user confirmed coverage via dev + test is sufficient to take the combined risk.
- [ ] Merge `test → prod` on `multivac-aitana` (applies to `your-project-id-prod`, i.e. v5 prod's project).
- [ ] Verify: controls present. v5 frontend still loads (this is the critical check — prod referrer list must include `yourcompany.com`).
- [ ] If v5 frontend breaks on referrer mismatch: revert the `google_apikeys_key` resource only (keep audit configs + alerts — those don't affect traffic), fix referrer list, reapply within same window.
- [ ] Proceed with v6 test→prod merge on `sunholo-data/ai-protocol-platform`.

## Migration & Rollout

**Database Migrations:** None.

**Feature Flags:** None — this is infra that's either applied or not.

**Rollback Plan:**
- The audit configs, quota overrides, and monitoring alerts are all **non-traffic-affecting**. Rollback = `terraform destroy` on those resources (or comment out module invocation); no user-visible impact.
- The `google_apikeys_key` resource **is** traffic-affecting: a misconfigured `allowed_referrers` will 403 frontend requests. Mitigation: deliberately-permissive referrer list on first apply (include both `.run.app` and the custom domain), tighten in a follow-up PR once observed working.
- Per-env rollback is independent — dev can keep the module while prod reverts.

**Environment Variables:** None (all config is terraform vars).

## Testing Strategy

### Infra Validation (per env)
- [ ] `terraform plan` no-op after apply.
- [ ] `gcloud services api-keys list --project=<proj>` shows the terraform-managed key.
- [ ] `gcloud projects get-iam-policy <proj> --format="json" | jq '.auditConfigs'` shows 2 service entries.
- [ ] `gcloud alpha monitoring policies list --project=<proj>` shows both alert policies.
- [ ] `gcloud logging read 'protoPayload.serviceName="aiplatform.googleapis.com" AND protoPayload.methodName=~"Predict"' --project=<proj> --limit=5` returns at least one audit entry after making a Vertex AI call.

### Live Alert Test (test env only, once)
- [ ] Fire a synthetic high-volume burst → confirm `vertex_ai_usage_spike` alert email within 5 minutes. If threshold doesn't trip, tune and retest **on test, never on prod**.

### Frontend Smoke (all envs)
- [ ] After apply, hit each env's frontend URL via `scripts/smoke-deployed.sh [env] frontend`. A broken referrer config will surface as `/api/proxy/api/skills/marketplace` returning 403 from Firebase.

### Manual Testing
- [ ] v5 prod frontend still loads `yourcompany.com` after the prod apply.
- [ ] Audit log entries visible in Cloud Logging for a known Vertex AI call.
- [ ] A deliberately-wrong referrer (e.g. `curl` without `Referer` header) is rejected by Firebase Auth.

## Security Considerations

This change **is** the security consideration. Specific points:

- **Trust boundary unchanged:** audit logs flow to Cloud Logging (inside GCP project). No egress to third-party SaaS. Aligns with Axiom #9's privacy boundary.
- **Key scope:** the terraform-managed browser key is a Firebase-only key (7 specific API targets). Do not add `aiplatform.googleapis.com` or `generativelanguage.googleapis.com` to `api_targets` — those must go through backend service accounts, never through a browser-visible key.
- **Blast radius of misapply:** worst case = frontend 403s for the time it takes to push a fix (minutes). We mitigate via dev/test rehearsal; the referrer list that works on test will work on prod since the URL patterns are analogous.
- **What this does NOT close:** App Check (request-origin attestation) and reCAPTCHA Enterprise (bot deflection). Those are follow-ups — referrer restrictions are the floor, not the ceiling.

## Performance Considerations

- Audit logs add negligible latency (async write, sampled at 100%).
- Monitoring alert policies evaluate every 5 min — no hot-path impact.
- Regional quota blocks are enforced at the GCP edge, no app-level check.

## Success Criteria

- [ ] Shared module applied cleanly to dev, test, and production (`terraform plan` no-op).
- [ ] All five controls verifiable via gcloud on each env (commands listed in Testing Strategy).
- [ ] Live alert fires in test env under synthetic load.
- [ ] v5 prod and v6 prod frontends both load after prod apply (custom domains + Cloud Run URLs).
- [ ] Companion doc in `multivac-aitana/docs/security/api-key-audit-port.md` committed with before/after state.
- [ ] [env-promotion-audit.md](../../ops/env-promotion-audit.md) prod-readiness checklist updated to reference this doc.
- [ ] Memory updated: note that aitana projects now match multivac projects on post-incident controls.

## Open Questions

- **Initial `allowed_referrers` for prod** — exact custom domain list for v5 (`yourcompany.com` vs. `app.yourcompany.com` vs. anything else currently live). Verify via `gcloud run services describe backend-api --project=your-project-id-prod` before the prod apply.
- **Alert threshold** — `1000 req/5min` is the multivac default. Aitana traffic pattern may differ; may need to re-baseline after a week of production telemetry.
- **`blocked_genai_regions` seed list** — left empty initially. Candidate regions to block after a month of observation: anything outside `us-central1`, `europe-west1`, `europe-west4` if those are the only regions we actually use.

## Related Documents

- [env-promotion-audit.md](../../ops/env-promotion-audit.md) — sitrep + IAM cascade pattern + prod-readiness checklist this feeds into.
- [auth-and-permissions.md](implemented/auth-and-permissions.md) — the Firebase-auth design; the API key this doc restricts is the same one that JS SDK loads.
- [cloud-infrastructure.md](implemented/cloud-infrastructure.md) — the broader v6 infra design; this is a security patch on top.
- `multivac/infrastructure/environments/prod/main.tf` — reference implementation (lines 199-213, 415-467, 469-520).
- `multivac-aitana/docs/security/api-key-audit-port.md` (companion, to be created) — what was done and why, from the infra-repo perspective.
- Related incident: **2026-03-28 API key abuse** (the event that prompted these controls on multivac originally).

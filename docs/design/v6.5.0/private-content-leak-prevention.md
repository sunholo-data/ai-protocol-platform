# Private Content Leak Prevention — Defense in Depth Beyond CLAUDE.md

**Status**: Planned
**Priority**: P1 (Medium — important architectural guard, not demo-blocking)
**Estimated**: 1-2 days for the full combination; incrementally landable (IAM scoping alone is ~0.25d)
**Scope**: Infrastructure (terraform + IAM) + Tooling (pre-commit + CI workflow) + Conventions (CLAUDE.md / lint patterns)
**Dependencies**: Existing public bucket `gs://aitana-public-bucket/`, existing tagged-access buckets (e.g. `gs://multivac-acme-energy-bucket/`), existing CLAUDE.md "Security Hard Rules" section landed in commit 88f6a86
**Sprint Key**: PRIVCONTENT-GUARD
**Created**: 2026-06-11
**Last Updated**: 2026-06-11

## Problem Statement

On 2026-06-11, while polishing the v6.4.0 4.5 SKILL-ONBOARDING demo for the Fri 2026-06-12 ONE customer demo, the development workflow took the following path:

1. Generate page-1 PNG thumbnails of the 5 ONE example PPAs using `pdftoppm` locally
2. Upload the thumbnails to `gs://aitana-public-bucket/ppa-thumbnails/` (the existing public bucket used for fork-demo assets)
3. Wire the public `https://storage.googleapis.com/aitana-public-bucket/ppa-thumbnails/...` URLs into `SKILL.md` `welcome.example_documents.thumbnail` fields

**The source PPAs at `gs://multivac-acme-energy-bucket/PPAs/longform/` are tagged-access** (ONE + aitana-admin) — confidential customer contracts subject to NDA. Page 1 of a PPA contract still exposes party names, jurisdiction, contract type, signing dates, and counterparty identities. Anyone on the public internet could fetch those page-1 thumbnails from `storage.googleapis.com` without auth.

Mark caught the leak within minutes ("err these ppas should not be public?"). Mitigation shipped in commit `88f6a86`:

- All 5 leaked URLs overwritten with 67-byte blank 1×1 transparent PNGs + `Cache-Control: no-cache` headers. **Origin delete alone wasn't enough** — Google's edge cache stale-served the original 200 responses for 3 of 5 URLs after the delete completed. Overwrite-with-blank + no-cache invalidated the edge cache faster than waiting for TTL expiry.
- `SKILL.md` `thumbnail` fields removed; picker falls back to the generic doc-icon container.
- `CLAUDE.md` "Security Hard Rules" section added at the top of the file — architectural rule that derivative artefacts (thumbnails, snippets, summaries, screenshots) inherit source-document access policy; `storage.googleapis.com/...` is the public internet, not behind Firebase auth.

**Current State:**

- CLAUDE.md is the **only** line of defense. It's a textual norm.
- The dev SA Mark uses for ad-hoc ops has `storage.objects.create` on every bucket in the dev project, including `aitana-public-bucket`.
- There is no pre-commit / CI guard that scans staged diffs or PRs for `storage.googleapis.com/aitana-public-bucket/...` URLs paired with references to private buckets.
- There is no enumeration of "approved paths under public bucket" — any prefix can be written to.
- Bucket-level IAM Conditions are not used; the public bucket is uniformly writable by anyone with `storage.objects.create` on the project.
- The edge-cache stale-serve gotcha is not documented anywhere outside this design doc; future operators recovering from a similar leak will rediscover it the hard way.

**Impact:**

- **Who is affected**: every operator (human or AI agent) with write access to `aitana-public-bucket` and any reason to interact with restricted content. This is a small group today but grows with team size and fork count.
- **How significant**: a single leak of customer content can break trust irrecoverably, breach an NDA, and trigger GDPR / contract incident response. The cost of one mistake is measured in customer relationships and lawyer-hours; the cost of one preventive guard is measured in setup-day hours.
- **Realistic threat**: not malicious — this is the **good-faith mistake** pattern. The most likely repeat offender is an agent or contributor moving fast on a demo who rationalizes "but it's just a thumbnail, just for the demo, just this once." The hard rule needs to be enforced by architecture, not discipline.

## Goals

**Primary Goal:** Make it architecturally impossible for an operator acting in good faith to publish a derivative artefact (thumbnail, snippet, screenshot, preview image) of restricted content to a public surface (`gs://aitana-public-bucket/`, public CDN, etc.) — even if they forgot to read CLAUDE.md or rationalized their way past it.

**Success Metrics:**

- An operator running `blob.upload_from_filename("/tmp/ppa-thumb.png")` against `gs://aitana-public-bucket/<any-new-prefix>/` with the default dev SA gets a `403 storage.objects.create denied` immediately — without any human review step.
- A git commit that adds `https://storage.googleapis.com/aitana-public-bucket/<unapproved-prefix>/...` to any tracked file fails the pre-commit hook with a clear error message pointing at this doc.
- A PR that bypassed pre-commit (via `--no-verify` or non-local push) fails the corresponding CI check with the same error.
- Every legitimate public-asset path (branding logos, fork-template assets, etc.) lives under a documented manifest in this repo, so reviewers can scan the manifest diff instead of every URL in every file.
- Zero "leak then mitigate" incidents after this lands. The next near-miss is caught at the dev-SA IAM layer, not at the customer-noticing-it layer.

**Non-Goals:**

- **Live monitoring / DLP scanning of bucket content**. That addresses a different threat (data-at-rest classification); out of scope.
- **Network-level egress controls (VPC Service Controls)**. Bigger infra change; relevant for future hardening but blocks too much legitimate ops to ship for this incident class alone.
- **Audit-after-the-fact tooling**. Useful but reactive; doesn't prevent the next leak from going public for the time-to-detect window.
- **Cross-project / cross-tenant content sharing controls**. Forks and tenants are separate trust boundaries handled by the per-deploy branding model (see the deployment's demo-readiness doc (deployment-private)); not in scope here.
- **Replacing CLAUDE.md guidance**. The rule stays — this doc adds enforcement layers underneath it, not a replacement.

## Axiom Alignment

Score each axiom per [Product Axioms](../../../docs/product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Defense layer; no runtime perf impact on user-facing latency. Pre-commit may add ~1-2s to a commit but commits aren't a latency budget. |
| 2 | EARNED TRUST | +2 | Customer-trust failure is the most acute form of "lost trust" this product can suffer. An architectural guard against confidential-data leakage is the strongest possible alignment with this axiom. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure / tooling concern; no user-facing surface. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Not model-routing related. |
| 5 | GRACEFUL DEGRADATION | +1 | Adds defense-in-depth layers (IAM + pre-commit + CI) without removing CLAUDE.md. Any single layer failing still leaves redundant guards. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Uses standard IAM Conditions and standard pre-commit / GitHub Actions tooling; no custom protocol invented. |
| 7 | API FIRST | 0 | Internal tooling; no API surface. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Pre-commit failures, CI failures, and IAM denials all emit clear audit trails (git hook output, GHA logs, Cloud Audit Logs). Every blocked attempt is traceable. |
| 9 | SECURE BY CONSTRUCTION | +2 | **This is the textbook application of "if it can be misconfigured, it will be."** Moves the rule from developer discipline (CLAUDE.md text) to architecture (IAM-enforced write access + lint-enforced reference checks). Highest-possible alignment with this axiom. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Server-side guards; no frontend impact. |
| | **Net Score** | **+6** | Threshold: >= +4. ✅ Proceed. |

**Conflict Justifications:**

- None. No axiom scored -1.

## Standards Compliance Check

This doc uses existing standards exclusively — no new protocols or formats invented.

- **IAM Conditions** — Google Cloud's standard mechanism for fine-grained per-resource ACLs. Documented at [cloud.google.com/iam/docs/conditions-overview](https://cloud.google.com/iam/docs/conditions-overview).
- **Pre-commit framework** — industry-standard `pre-commit` Python tool ([pre-commit.com](https://pre-commit.com)) OR vanilla `.git/hooks/pre-commit` if pre-commit isn't already in the repo.
- **GitHub Actions** — existing CI surface already running `.github/workflows/ci.yml`.
- **CODEOWNERS** — GitHub's standard required-reviewer mechanism, used to gate changes to the public-paths manifest.

The "approved public paths" data lives in a YAML file (`infrastructure/public-paths.yaml` or similar) — YAML is the established standard for declarative config in this repo (Terraform tfvars, SKILL.md frontmatter, etc.). Not invented here.

## Design

### Overview

Stack three independent defense layers, each strong enough to prevent the incident class on its own:

1. **IAM scoping** (foundational) — restrict `storage.objects.create` on `aitana-public-bucket` to a dedicated `public-assets-writer` role that the default dev SA does NOT carry. Operators wanting to legitimately publish a logo / template asset explicitly activate the role per-session.
2. **Pre-commit hook** (developer-time) — scans the staged diff for `storage.googleapis.com/aitana-public-bucket/<prefix>/...` URLs and verifies `<prefix>` is in the approved-paths manifest. Blocks the commit with a clear error otherwise.
3. **CI lint** (push-time) — runs the same check on every PR. Catches anyone who bypassed the pre-commit hook with `--no-verify`.

Plus two supporting elements:

- **Approved-paths manifest** (`infrastructure/public-paths.yaml`) — explicit allowlist of which prefixes under `aitana-public-bucket` are intentionally public, with a one-line rationale per entry. Changes to this file require CODEOWNERS approval from a second reviewer.
- **CLAUDE.md** (already shipped) — remains the operator-facing explainer. Updated to point at this design doc.

### Defense Layer 1: IAM Scoping (foundational)

**Current state:** the dev SA used for ad-hoc Python ops (`gcloud auth application-default login` from a developer laptop) inherits `storage.objects.create` on every bucket in the dev project via a project-level role. That's how my `blob.upload_from_filename()` call succeeded without any guardrail.

**Proposed state:** create a dedicated `roles/aitanaPublicAssetsWriter` custom IAM role granting `storage.objects.create` + `storage.objects.delete` + `storage.objects.update` **scoped to `gs://aitana-public-bucket/` only**. Remove project-level `storage.objectAdmin` from the dev SA. Add the new role to a `public-assets-writer@yourcompany.com` Google Group; operators are NOT added by default. Add via an explicit on-call rotation or a short-TTL elevation request.

**Terraform sketch** (in `infrastructure/environments/common/`):

```hcl
resource "google_project_iam_custom_role" "public_assets_writer" {
  project     = var.aitana_project_id
  role_id     = "aitanaPublicAssetsWriter"
  title       = "Aitana Public Assets Writer"
  description = "Write access to gs://aitana-public-bucket only. Use for branding/logo/template assets — never customer-derivative content."
  permissions = [
    "storage.objects.create",
    "storage.objects.delete",
    "storage.objects.update",
    "storage.objects.list",
  ]
  stage = "GA"
}

resource "google_storage_bucket_iam_binding" "public_assets_writer_binding" {
  bucket  = google_storage_bucket.aitana_public.name
  role    = google_project_iam_custom_role.public_assets_writer.id
  members = ["group:public-assets-writer@yourcompany.com"]
}
```

**Operator workflow** (the legitimate-ops case):

```bash
# Before uploading a new brand logo:
gcloud iam groups members add public-assets-writer@yourcompany.com --member=user:alice@yourcompany.com
# ... do the upload ...
gcloud iam groups members remove public-assets-writer@yourcompany.com --member=user:alice@yourcompany.com
```

Or use a short-TTL elevation tool (out of scope here; the explicit group-add/remove is fine for the demo-scale ops volume).

**Why this is the strongest layer:** it operates at the GCS write API itself. There is no possible code path — Python, gsutil, console, or otherwise — that can write to the public bucket without first going through this gate. CLAUDE.md and lint hooks rely on humans/agents reading and respecting them; IAM operates whether anyone reads it or not.

### Defense Layer 2: Pre-commit Hook (developer-time)

A git hook scans the staged diff for new or modified lines containing `storage.googleapis.com/aitana-public-bucket/` and verifies each referenced path is in the approved-paths manifest. Blocks the commit otherwise.

**Implementation** (in `.githooks/pre-commit` or via the [pre-commit framework](https://pre-commit.com)):

```bash
#!/usr/bin/env bash
# .githooks/pre-commit-public-paths-check
# Refuse commits that introduce storage.googleapis.com/aitana-public-bucket/
# URLs to prefixes not declared in infrastructure/public-paths.yaml.

set -euo pipefail

MANIFEST="infrastructure/public-paths.yaml"
PUBLIC_PREFIX_PATTERN='storage\.googleapis\.com/aitana-public-bucket/([^/[:space:]"]+)'

# Get the set of approved prefixes from the manifest
APPROVED=$(yq -r '.approved_prefixes[]' "$MANIFEST" 2>/dev/null | sort -u)

# Scan staged diff for offending URLs
BAD=$(git diff --cached --unified=0 | grep -E "^\+.*$PUBLIC_PREFIX_PATTERN" || true)
[ -z "$BAD" ] && exit 0

# Extract the prefix segment from each match and check membership
violations=()
while IFS= read -r line; do
  prefix=$(echo "$line" | grep -oE "$PUBLIC_PREFIX_PATTERN" | head -1 | sed -E "s|.*aitana-public-bucket/([^/[:space:]\"']+).*|\1|")
  if ! echo "$APPROVED" | grep -qx "$prefix"; then
    violations+=("$prefix")
  fi
done <<< "$BAD"

if [ ${#violations[@]} -gt 0 ]; then
  echo ""
  echo "REFUSING COMMIT — public-bucket URL with an UNAPPROVED prefix detected:"
  for v in "${violations[@]}"; do
    echo "  storage.googleapis.com/aitana-public-bucket/$v/..."
  done
  echo ""
  echo "Approved prefixes are listed in $MANIFEST."
  echo ""
  echo "If this path SHOULD be public, add it to the manifest (requires CODEOWNERS"
  echo "approval) and try again. If not — DO NOT publish customer-derivative content."
  echo "See docs/design/v6.5.0/private-content-leak-prevention.md and CLAUDE.md."
  echo ""
  exit 1
fi
```

**Approved-paths manifest** (`infrastructure/public-paths.yaml`):

```yaml
# Approved prefixes under gs://aitana-public-bucket/.
# ANY new entry MUST be reviewed by a CODEOWNER for this file (see
# .github/CODEOWNERS). Adding an entry asserts: "the content under this
# prefix is intentionally public; it does NOT contain customer-derivative
# data or any content from a tagged-access bucket."
approved_prefixes:
  - branding         # Sunholo + fork brand logos referenced from branding.ts
  - template-assets  # Shared assets for the public ai-protocol-platform template
  - demo             # Public demo videos / hero images (created from public sources only)
```

`.github/CODEOWNERS` gains:

```
infrastructure/public-paths.yaml  @aitana-labs/security-approvers
```

### Defense Layer 3: CI Lint (push-time)

A new GitHub Actions job runs the same check as the pre-commit hook against every PR's diff. Catches the bypass-pre-commit case (`--no-verify`, non-local push, etc.).

**Implementation** — add to `.github/workflows/ci.yml`:

```yaml
public-paths-check:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
      with:
        fetch-depth: 0
    - name: Check for unapproved public-bucket URLs
      run: |
        # Diff against the merge-base with main
        BASE=$(git merge-base HEAD origin/main)
        ./scripts/check-public-paths.sh "$BASE" "HEAD"
```

`scripts/check-public-paths.sh` is the same logic as the pre-commit hook, parameterized to scan a commit-range diff instead of `--cached`.

### Defense Layer 4 (not implemented; recorded for context)

**Bucket-level IAM Conditions** could enforce "only writes to `branding/` or `template-assets/` succeed even if you have the role" via:

```hcl
condition {
  title       = "Restrict public-assets writes to approved prefixes"
  expression  = "resource.name.startsWith('projects/_/buckets/aitana-public-bucket/branding/') || resource.name.startsWith('projects/_/buckets/aitana-public-bucket/template-assets/') || resource.name.startsWith('projects/_/buckets/aitana-public-bucket/demo/')"
}
```

**Tradeoff:** adds maintenance friction — every new approved prefix requires a terraform change + apply. For v1 the IAM-scoping role + manifest is sufficient; if leaks recur this layer is the next escalation.

### CLI Surface

No new CLI commands. The pre-commit hook is invoked by git automatically; the CI lint runs in GitHub Actions; IAM changes are operator-side `gcloud` calls (already standard).

Optionally a small helper `aiplatform infra approved-paths list` could print the manifest contents — but reading the YAML file directly is fine. Not worth the CLI surface.

### Architecture Diagram

```
                   ┌─────────────────────────────────────────┐
                   │  Operator wants to write to public bucket│
                   └─────────────────────────────────────────┘
                                       │
                                       ▼
            ┌──────────────────────────────────────────────────────┐
            │ Layer 1 — IAM:  is the SA in public-assets-writer@?  │
            └──────────────────────────────────────────────────────┘
                       │                              │
                  Yes  │                              │  No → 403 (architectural deny)
                       ▼                              │
            ┌────────────────────────────────┐        │
            │ GCS object writes succeed       │        │
            └────────────────────────────────┘        │
                       │                              │
                       ▼                              │
            ┌──────────────────────────────────────────────────────┐
            │ Operator references URL in code / SKILL.md / YAML    │
            └──────────────────────────────────────────────────────┘
                                       │
                                       ▼
            ┌──────────────────────────────────────────────────────┐
            │ Layer 2 — Pre-commit hook: prefix in manifest?       │
            └──────────────────────────────────────────────────────┘
                       │                              │
                  Yes  │                              │  No → commit refused
                       ▼                              │
            ┌────────────────────────────────┐        │
            │ Commit lands locally            │        │
            └────────────────────────────────┘        │
                       │                              │
                       ▼                              │
            ┌──────────────────────────────────────────────────────┐
            │ Layer 3 — CI lint on PR: prefix in manifest?         │
            └──────────────────────────────────────────────────────┘
                       │                              │
                  Yes  │                              │  No → CI red, PR blocked
                       ▼                              │
            ┌────────────────────────────────┐        │
            │ PR merges                       │        │
            └────────────────────────────────┘        │
                                                      │
            ┌────────────────────────────────┐        │
            │ Layer 0 — CLAUDE.md hard rule  │◀───────┘
            │ (already shipped; norm-layer)  │
            └────────────────────────────────┘
```

## API Changes

None. This doc proposes infrastructure + tooling changes only.

## Migration

**Migration steps (incremental):**

1. **Land the manifest first** (`infrastructure/public-paths.yaml`) with the current `branding/`, `template-assets/`, `demo/` (or whatever's actually used today) — verify by listing existing prefixes in `gs://aitana-public-bucket/` and adding each.
2. **Add the pre-commit hook + CI lint** scoped to the manifest. This catches FORWARD violations immediately; backfill audit is a non-goal.
3. **Add the IAM custom role + group** (terraform). Initially grant the role to a wide set so nothing breaks. Slowly narrow the membership as legitimate uses are documented.
4. **Remove project-level `storage.objectAdmin` from the dev SA** (the riskiest change — gate on full team awareness and a documented "how to elevate for a public-bucket op" runbook).

**Feature flags:** none. The pre-commit hook is opt-in by default (the hook only fires if installed); recommend documenting the install step in CLAUDE.md. CI lint is opt-out by default once added (existing PRs gate on green CI).

**Rollback plan:**

- **Pre-commit + CI:** delete the hook / workflow file. The manifest stays as documentation.
- **IAM role removal:** re-grant the project-level `storage.objectAdmin` role to the dev SA. Reverts in ~30s via terraform apply.
- **No data migration risk.** None of these changes modify bucket contents; they only modify who can write to what.

## Testing Strategy

**Pre-commit hook unit tests** (`scripts/test-public-paths.sh`):

- Test 1: commit adds `storage.googleapis.com/aitana-public-bucket/branding/logo.png` → ALLOWED (prefix in manifest).
- Test 2: commit adds `storage.googleapis.com/aitana-public-bucket/customer-pii/data.json` → REFUSED.
- Test 3: commit modifies an existing file without touching public-bucket URLs → ALLOWED (no-op for unrelated diffs).
- Test 4: commit adds a URL in a deleted line (`-` prefix in diff) → ALLOWED (we only scan additions).
- Test 5: commit adds a URL inside a backtick-block in a markdown file → REFUSED (treat the doc reference as a real reference; if the prefix really is approved-public, add to manifest).

**CI lint integration test** — a fixture branch added once with an intentionally-unapproved URL; the corresponding CI run is asserted to fail. Tagged as `[lint-fixture]` and merged-once-then-reverted in this design doc's implementation PR.

**IAM scoping verification** — manual smoke from a dev laptop:

```bash
# As an operator NOT in public-assets-writer@:
gcloud auth application-default login
python3 -c "from google.cloud import storage; storage.Client().bucket('aitana-public-bucket').blob('test-deny.txt').upload_from_string('test')"
# Expect: 403 storage.objects.create denied
```

**E2E demo** — a documented before/after walkthrough in this PR's description showing the same code that previously published a leak now gets refused at all three layers.

## Success Criteria

- [ ] `infrastructure/public-paths.yaml` exists, lists every currently-used prefix under `aitana-public-bucket` with a one-line rationale per entry.
- [ ] `.git/hooks/pre-commit` (or `.pre-commit-config.yaml`) is installed in the repo and rejects an attempted commit referencing an unapproved prefix; rejection message points at this design doc and CLAUDE.md.
- [ ] GitHub Actions PR workflow has a `public-paths-check` job that fails on the same condition.
- [ ] `roles/aitanaPublicAssetsWriter` custom role exists in dev project; `public-assets-writer@yourcompany.com` group exists.
- [ ] Dev SA `storage.objectAdmin` (project-level) is removed; legitimate ops documented in CLAUDE.md or a runbook.
- [ ] `.github/CODEOWNERS` requires a security-approver reviewer on changes to `infrastructure/public-paths.yaml`.
- [ ] CLAUDE.md "Security Hard Rules" section is updated to link this design doc + the pre-commit install instructions.
- [ ] Zero "leak then mitigate" incidents in the 90 days after landing. (Tracked via incident log; current count = 1, the one this doc responds to.)

## Security Considerations

- **Trust boundary:** the public bucket itself sits OUTSIDE the GCP project trust boundary by definition (readable by `allUsers`). This is intentional — it hosts brand logos, template assets, public demo videos. The guards here ensure nothing customer-derivative crosses into it.
- **Edge-cache stale-serve gotcha (DOCUMENT THIS):** Google's edge CDN serves cached 200 responses for public bucket objects for up to ~1 hour after origin delete. **Overwrite-with-blank + `Cache-Control: no-cache` invalidates faster than waiting for TTL expiry.** Codify this in an incident-response runbook so future operators don't waste time on `gsutil rm` thinking the leak is closed.
- **Privilege escalation surface:** the new `public-assets-writer@` group concentrates the "publish to public bucket" privilege in one membership list. CODEOWNERS approval on `public-paths.yaml` changes prevents bulk-add of new prefixes; group membership audit (who can add?) needs to be locked down separately. Follow-up.
- **CI bypass:** GitHub Actions can be bypassed by repo admins with push directly to `main`. This is a known general risk; mitigated by the existing branch protection rules on `main` and PR requirements. The CI lint here adds another required check.
- **No data egress beyond GCP project edge** for the guard itself — pre-commit runs locally, CI runs in GitHub-hosted runners, IAM checks run in Google Cloud. No third-party SaaS touches this path.
- **Disclosure:** the incident that prompted this doc (2026-06-11 public-thumbnail leak) was caught + mitigated within minutes; the affected URLs returned blank PNGs before any external party is known to have fetched them. No public disclosure required.

## Related Documents

- [CLAUDE.md](../../../CLAUDE.md) — "Security Hard Rules" section (landed in commit 88f6a86); this design doc is referenced from there.
- [product-axioms.md](../../../docs/product-axioms.md) §9 SECURE BY CONSTRUCTION — the axiom this doc is an architectural application of.
- [parsed-blocks-pipeline.md](parsed-blocks-pipeline.md) — sibling v6.5.0 doc; same trust-boundary logic informs both designs.
- Google Cloud IAM Conditions: [cloud.google.com/iam/docs/conditions-overview](https://cloud.google.com/iam/docs/conditions-overview).
- Pre-commit framework: [pre-commit.com](https://pre-commit.com).

## Open Questions

1. **How do legitimate ops coexist with IAM scoping?** Pre-approved role-activation pattern (gcloud groups add/remove per session) is the v1 answer. Short-TTL elevation (e.g. a Cloud Function that grants the role for 10 minutes then revokes) is a v2 if the manual flow becomes friction. Open.
2. **What goes in the pre-commit URL pattern set?** v1: just `storage.googleapis.com/aitana-public-bucket/*` paths in any tracked file. Consider expanding to cover `gs://aitana-public-bucket/...` (raw GCS URI) in code too — same content, different scheme. Open.
3. **Should the CI lint cover SKILL.md YAML specifically, or any text file?** SKILL.md is the highest-risk vector (skill configs reference asset URLs liberally), but `*.tsx`, `*.py`, `*.md`, `*.yaml` are all potential vectors. v1: scan ALL tracked text files via the diff. Cheap; no reason to special-case. Open: should generated / vendored files (e.g. `node_modules/`) be excluded? Yes, follow `.gitignore` boundaries.
4. **Backfill — should this also retroactively audit existing public bucket content?** Out of scope for v1 (non-goal). Worth a one-off audit script if scope expands; could be a v2 add. Open.
5. **Should the manifest also enumerate APPROVED CONTENT-TYPES per prefix?** E.g. `branding/` allows `image/png`, `image/svg+xml` but not `application/pdf`. Lowers attack surface (a "branding logo" can't be an entire PDF document); raises maintenance cost. v1: skip; revisit if abuse pattern emerges. Open.
6. **Pre-existing infra:** terraform might already define `aitana-public-bucket` with project-level uniform-bucket-level-access. Need to confirm the bucket configuration and whether per-object ACLs are even an option vs. bucket-level IAM only. Verification step in M1 below.

## Implementation Plan (preview — not a sprint plan)

Lay out the milestones at design-doc level so sprint-planner has structure when this picks up:

| Milestone | Description | Est | Files Touched |
|-----------|-------------|-----|---------------|
| M1 | Audit + write `infrastructure/public-paths.yaml` (list current legitimate prefixes); verify bucket configuration | 0.25d | `infrastructure/public-paths.yaml`, `.github/CODEOWNERS` |
| M2 | Pre-commit hook script + install instructions in CLAUDE.md | 0.25d | `.githooks/pre-commit-public-paths`, `scripts/check-public-paths.sh`, `CLAUDE.md` |
| M3 | CI lint job in `.github/workflows/ci.yml` + intentional-failure fixture for regression coverage | 0.25d | `.github/workflows/ci.yml`, `scripts/check-public-paths.sh` |
| M4 | Terraform custom role + group + bucket binding; document operator workflow | 0.5d | `infrastructure/environments/common/iam.tf`, runbook in `docs/ops/` |
| M5 | Remove project-level `storage.objectAdmin` from dev SA after team awareness window | 0.25d | `infrastructure/environments/dev/sa.tf` |
| M6 | Verification — attempt the same upload that caused the original leak; assert all three layers fire | 0.1d | manual + a one-liner test fixture |

Total ~1.6 days. Incrementally landable (each milestone independently shippable). M5 is gated on M4 + team comms; ideally land all preceding milestones first to ensure no legitimate ops break.

## Implementation Report

_(To be filled in after the sprint completes — same format as 4.5 / 4.5.1 / 5.1 implementation reports when this doc is moved to `implemented/`.)_

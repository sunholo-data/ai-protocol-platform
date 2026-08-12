# Tenant-Scoped Data Access — bucket authorization + domain allowlist

**Status**: Proposed
**Priority**: P0 (security — confidential customer content)
**Estimated**: ~1.5–2 days
**Scope**: Backend
**Dependencies**: existing `AccessContext` / `resolve_documents_bucket` / `TENANT_FALLBACK_FAIL_CLOSED`
**Created**: 2026-07-23
**Last Updated**: 2026-07-23

## Problem Statement

Two gaps let an authenticated user read another tenant's confidential documents.
Surfaced 2026-07-23 while wiring the Contracts library to the per-env llmops bucket.

**Gap A — the bucket file endpoints have no per-tenant authorization.**
`GET /api/buckets/{name}/list|thumbnail|preview` (`backend/buckets/routes.py`)
authorize on *only* "authenticated + the SA can read the bucket" — the docstrings
say so ("auth + the SA's bucket whitelist"). The handler reads **whatever bucket
name the request specifies** with the runtime SA's credentials. The SA can read
the env llmops bucket (`your-project-id-<env>-<env>-llmops-bucket`), which holds
ONE's confidential corpus (PPAs, financials). So **any authenticated user can
list/preview ONE's documents** by naming that bucket — regardless of tenant or
`group_tags`. This is a direct violation of the CLAUDE.md security hard rule
(confidential customer content must be gated by the same access as the source).
Pre-existing since v6.4.0; newly the live path because the ONE library now points
at that bucket.

**Gap B — authentication is not domain-restricted.** `get_current_user`
(`backend/auth/firebase_auth.py`) accepts any valid Firebase token; there is no
email-domain allowlist. "Who can read" therefore reduces to "who can obtain a
token for the Firebase project," and the API is directly reachable with a Bearer
(not only through the frontend).

**Impact:** confidential customer content (contracts, financials) is readable by
any authenticated principal. For a single-tenant ONE deployment the blast radius
is "anyone who can sign up," but the requirement is explicit: **only
`acme-energy.example` (the customer) and `yourcompany.com` (operators) should have
access.**

**Current state (verified 2026-07-23):**
- `thumbnail_bucket_object` / `preview_bucket_object` / `list_bucket_objects`: no
  `request.state.access` check; read by object name via `google.cloud.storage.Client()`.
- `_apply_derived_group_tags` gives `@acme-energy.example` the `ONE` tag; skills ARE
  tag-gated (`can_access_skill`). Only the **bucket file endpoints** bypass tags.
- `TENANT_FALLBACK_FAIL_CLOSED` exists (off by default) — denies unmapped tenants
  a `documents_bucket`, but the bucket endpoints don't call `resolve_documents_bucket`.

## Goals

**Primary Goal:** No principal can read a bucket's bytes through the API unless
they are authorized for that bucket, and — on the ONE deployment — only
allowlisted email domains can authenticate. Enforced **by construction**, not by
"the SA happens not to have the grant."

**Success Metrics:**
- A non-ONE authenticated user gets **403** from `/api/buckets/<one-llmops>/…` for
  every verb (list/thumbnail/preview) — covered by a cross-tenant test.
- A user whose domain is not allowlisted gets **401/403 at the auth gate** on the
  ONE deployment (flag on) — covered by a test.
- ONE users + operators are unaffected (library previews still 200).
- Zero change for envs that leave the flags off (backward-compatible default).

**Non-Goals:**
- Re-architecting the SA's GCS grants (defense-in-depth stays; app-layer authz is
  the fix). - Per-object ACLs inside a bucket (bucket-level scoping is the unit here).
- Signed-URL / public serving (explicitly forbidden by the security rule).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | One Firestore-cached tenant lookup per bucket request; negligible. |
| 2 | EARNED TRUST | +1 | Prevents cross-tenant confidential-data exposure — the strongest form of "don't present another tenant's data." |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure; invisible to skill authors. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Orthogonal. |
| 5 | GRACEFUL DEGRADATION | +1 | Deny-by-default with a clear 403/401; a Firestore hiccup fails closed (deny), never open. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Uses the existing `AccessContext` / tenant model, no new protocol. |
| 7 | API FIRST | +1 | The gate is at the API surface, so every channel inherits it (not frontend-only). |
| 8 | OBSERVABLE BY DEFAULT | +1 | Every deny is logged with uid + bucket + reason (auditable); no content leaves GCP. |
| 9 | SECURE BY CONSTRUCTION | +1 | The whole point: authorization enforced by architecture, deny-by-default, "if it can be misconfigured it will be" → the SA-grant-only model is exactly that. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Backend-only. |
| | **Net Score** | **+6** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None (no axiom scores -1). Hard-fail rules pass:
SECURE BY CONSTRUCTION is +1 (not -1) despite introducing a new data-access gate —
the gate *tightens* access.

## Design

### A. Bucket-endpoint authorization (the primary fix)

A single guard, applied to `list_bucket_objects`, `preview_bucket_object`,
`thumbnail_bucket_object` in `backend/buckets/routes.py`, before any GCS read:

```python
def _authorize_bucket_read(user: User, access: AccessContext, name: str) -> None:
    """403 unless the caller is authorized to read GCS bucket `name`."""
    if access.is_platform_admin:                       # operators: full read (diagnostics)
        return
    try:
        if name == resolve_documents_bucket(user):     # the caller's tenant bucket
            return
    except UnmappedTenantError:
        pass                                            # fail-closed unmapped → fall through to deny
    cfg = bucket_config.find_by_gcs_name(name)          # a registered bucket-config…
    if cfg is not None and access.can_access(cfg):      # …the caller can_access (v6.3.0 ACLs)
        return
    logger.warning("bucket-authz DENY uid=%s bucket=%s", user.uid, name)
    raise HTTPException(status_code=403, detail={       # structured shape, reused from #31's
        "code": "BUCKET_NOT_AUTHORIZED",                # TENANT_NOT_PROVISIONED (NEVER-SILENT #8)
        "message": "You don't have access to these documents.",
    })
```

The `{code, message}` 403 **reuses the shape shipped for the upload side** (issue
#31, `TENANT_NOT_PROVISIONED` in `tools/documents/upload.py`) so the frontend
branches on `code` and shows a specific "why", not a generic "Forbidden" — do NOT
invent a second error contract. `access.is_platform_admin` is the existing
property (not the free function); `UnmappedTenantError` is caught so a fail-closed
unmapped tenant denies rather than 500s.

- **Tenant bucket:** a `@acme-energy.example` user's `documents_bucket` = the env
  llmops bucket → allowed. A user from another (or no) tenant resolves to a
  *different* bucket → denied for the llmops bucket. This is the case that fixes
  the leak.
- **Registered bucket-configs:** preserves the existing v6.3.0 bucket-browser
  behaviour for buckets that ARE registered configs (their `accessControl` still
  governs). The file endpoints key off the **GCS bucket name**, but
  `bucket_config` only looks up by config-id today, so add a thin
  `find_by_gcs_name(name)` (a `("gcsBucket","==",name)` Firestore query, mirroring
  `list_buckets`). It returns null for raw buckets like the llmops bucket (never a
  registered config), which then fall to the tenant-bucket check above — the case
  that fixes the leak.
- **Platform admins** keep full read (operations/diagnostics) — an explicit,
  logged bypass, not an accident.
- **Deny-by-default + fail-closed:** any exception in resolution → deny (never a
  silent allow). `resolve_documents_bucket` under `TENANT_FALLBACK_FAIL_CLOSED`
  raises for unmapped tenants → caught → 403.

Note this makes the GCS-403 (SA can't read) redundant as the *primary* gate — good;
defense-in-depth stays, but the app now decides, per the axiom.

### B. Domain allowlist at the auth gate

Extend the **unified** `get_current_user` dispatcher (`auth/__init__.py`, right
after `user = await _resolve_user(request)` — the single insertion point all 13
endpoints inherit, same place sprint 2.14 bound the tenant contextvar) with a
domain check, **behind a flag** so envs opt in:

```python
if _require_known_domain() and not _domain_allowed(user):
    logger.info("auth: rejected domain uid=%s domain=%s", user.uid, user.domain)
    raise HTTPException(status_code=403, detail={
        "code": "DOMAIN_NOT_PERMITTED",                 # same structured shape (NEVER-SILENT #8)
        "message": "This account's domain isn't permitted on this deployment.",
    })
```

- **Allowlist source = the `clients/` collection ∪ operator domains.** A domain is
  allowed if `clients/{domain}` exists (a mapped tenant — reuses the existing
  allowlist, so adding a customer is a Firestore write, no redeploy) **or** it is
  in `AUTH_OPERATOR_DOMAINS` (config; default `yourcompany.com,yourcompany.test`).
  This keeps the ONE deployment to exactly `acme-energy.example` + operators without
  a hardcoded customer domain in code.
- **Flag:** `AUTH_REQUIRE_KNOWN_DOMAIN` (default **off** → backward-compatible;
  every existing env keeps working until it opts in). Set on the ONE deployment.
- **Exemptions (must not break):**
  - `LOCAL_MODE` — the stub sets a `local`-domain identity (`auth_mode` reflects
    the stub); exempt it so dev/forks keep working. It only fires when `LOCAL_MODE=1`,
    never on a deployed env, so exempting it costs nothing on ONE.
  - **group-id / anonymous auth** (`group_id_auth.py`, `auth_mode ==
    "anonymous_group_id"`, `domain=""`) — workshop identities have no email
    domain. The gate exempts `auth_mode == "anonymous_group_id"` (an anonymous
    workshop principal is not a confidential-tenant reader; it gets only its
    group's tags and never resolves to a customer bucket, so Gap-A still contains
    it).
  - The `whoami-test@yourcompany.test` smoke user → covered by the
    `yourcompany.test` operator domain.

### Why not just fix the SA grants?

Removing the SA's read on the llmops bucket would break the *legitimate* uses
(document loader, thumbnails for authorized users, aitana3 source reads). The SA
*must* read it; the app must decide *who* it reads on behalf of. Authorization
belongs at the app layer (Axiom #9).

### CLI Surface

- `aitana whoami --env <env>` already exists; add `--check-bucket <name>` to print
  the authz decision (allowed/denied + reason) for the minted identity — a one-command
  check that the gate does what the design says (~0.15d).

## Implementation Plan

### Phase 1: Bucket authorization (~0.75d) — the leak
- [x] `_authorize_bucket_read` helper + `bucket_config.find_by_gcs_name` lookup
- [x] Wire into list/preview/thumbnail routes (before GCS read)
- [x] Cross-tenant test: non-ONE user → 403 on the ONE llmops bucket for all 3 verbs;
      ONE user + admin → 200; unmapped/fail-closed → 403 (proven fail-on-revert)

### Phase 2: Domain allowlist (~0.5d)
- [x] `_domain_allowed` (clients/ ∪ `AUTH_OPERATOR_DOMAINS`) + `AUTH_REQUIRE_KNOWN_DOMAIN` gate in `get_current_user`
- [x] Exempt LOCAL_MODE + group-id auth; tests for allowed / denied / exempt
- [ ] `aitana whoami --check-bucket` (deferred — verification convenience, not on the security boundary)

### Phase 3: Enablement (config, per-env) (~0.25d)
- [ ] Set `AUTH_REQUIRE_KNOWN_DOMAIN=1` + `AUTH_OPERATOR_DOMAINS` on dev/test via
      `run_client.tfvars` substitutions. **Prod deferred** (prod is frozen pending
      the v5/v6 Firestore de-risk).
- [ ] **Coordinate `TENANT_FALLBACK_FAIL_CLOSED` with issue #31** — this is a
      SEPARATE lever from the two flags above (see Migration & Rollout). Flip it
      **once, jointly** with the upload side, and **only after** the
      `yourcompany.com` upload-home decision below is resolved, per the migration
      window baked into `_fail_closed`'s docstring ("flip only AFTER mapping
      current unmapped uploaders to their own tenant bucket").
- [ ] **Pre-flip decision — `yourcompany.com` upload home** (blocks the fail-closed
      flip, not Gap-A/B): only `acme-energy.example` has a `documents_bucket` today;
      `yourcompany.com` operators rely on the shared fallback. The operator bypass
      covers *reads*, not *uploads*, so once fail-closed is on, `yourcompany.com`
      uploads 403 with `TENANT_NOT_PROVISIONED`. Either (a) map `yourcompany.com` to
      its own `documents_bucket` (covers uploads + reads — recommended, matches the
      docstring's migration window), or (b) accept operators are upload-read-only
      and document the 403 as intended.

## Migration & Rollout

**Three levers, deliberately distinct — the note's "A/B enablement implies
fail-closed" is not literally true, and the difference matters:**

| Lever | What it does | Default | Who owns |
|-------|--------------|---------|----------|
| Gap-A guard | Authorizes bucket-byte reads per-tenant | **on** (no flag) | this doc |
| `AUTH_REQUIRE_KNOWN_DOMAIN` | Restricts *who can authenticate* by domain | off (opt-in) | this doc |
| `TENANT_FALLBACK_FAIL_CLOSED` | Denies unmapped tenants the shared fallback bucket (read **and** write) | off | **shared with #31** |

**The real coupling (traced in code, not assumed):** Gap-A calls
`resolve_documents_bucket(user)`. With fail-closed **off**, an unmapped user
resolves to the shared `aitana-documents-bucket`, so Gap-A lets them read *that*
bucket (their resolved bucket) while denying the ONE llmops bucket. **So Gap-A
alone fully closes the llmops leak — the primary incident — with or without
fail-closed.** What fail-closed *additionally* closes is the shared-bucket read
exposure (where #31's mis-uploaded ONE contracts landed): with it **on**,
`resolve_documents_bucket` raises for unmapped users → Gap-A catches → they read
*nothing*. That is why fail-closed belongs in the same rollout — but as its own
coordinated flip, not a side effect of A/B.

**Ship order:**
1. Gap-A on-by-default (deny-by-default for confidential data) — closes the leak.
2. Gap-B behind `AUTH_REQUIRE_KNOWN_DOMAIN` (opt-in; a domain lockout is
   deployment-specific), enabled on dev/test.
3. `TENANT_FALLBACK_FAIL_CLOSED=1` — **coordinated once with #31**, and only after
   the `yourcompany.com` upload-home decision (Phase 3). This is the shared lever;
   flipping it from one side alone would surprise the other.

**Rollout env order:** dev → test, verify cross-tenant 403 + ONE 200 in a real
browser + scripted. **Prod is frozen** — do not deploy there until the separate
v5/v6 Firestore-sharing de-risk lands; the fix promotes to prod at that time.

**Rollback:** Gap-A is a pure authorization add; if it wrongly denies, the admin
bypass + the `find_by_gcs_name` allow-path are the escape valves, and it's
revertable in one commit. Gap-B and fail-closed are flag-gated (unset the flag).

**Env vars:** `AUTH_REQUIRE_KNOWN_DOMAIN` (bool), `AUTH_OPERATOR_DOMAINS` (csv,
default `yourcompany.com,yourcompany.test`), `TENANT_FALLBACK_FAIL_CLOSED` (bool,
shared with #31).

## Testing Strategy

### Backend (pytest)
- [ ] `test_bucket_authz`: matrix over {ONE user, other-tenant user, unmapped user,
      platform admin} × {list, preview, thumbnail} × {ONE llmops bucket, own bucket}
      → asserts 200/403 per the design. The **cross-tenant deny** is the load-bearing case.
- [ ] `test_domain_allowlist`: allowed (clients/ or operator) → pass; unmapped +
      flag-on → 403; flag-off → pass; group-id/LOCAL_MODE → exempt.
- [ ] Fail-closed: Firestore raises during resolution → deny (never allow).

### Live (non-negotiable)
- [x] **dev (2026-07-23):** revision Ready + sidecar `AUTH_REQUIRE_KNOWN_DOMAIN=1`;
      operator `/api/auth/whoami` → 200 (domain gate admits); admin bucket `/list`
      → 200 (Gap-A allow). Deny paths carried by hermetic fail-on-revert tests
      (can't forge a non-operator Firebase token live).
- [ ] On test: same smoke after promotion.

### UX: access-restricted screen (NEVER-SILENT #8) — SHIPPED on dev (2026-07-23)
A domain-rejected user (e.g. `@gmail.com`) completes Firebase sign-in (Firebase
doesn't know the allowlist), then every proxied call 403s `DOMAIN_NOT_PERMITTED`.
`fetchWithAuth` (the one authed-fetch, ~76 callers) now detects that code on any
403 and dispatches a `window` event; `AccessRestrictedGate` (in `AppProviders`,
inside `AuthProvider`) swaps the whole UI for a clear "access restricted to your
organization — signed in as `<email>`" screen + sign-out. Clears on sign-out.
Inert for LOCAL_MODE / anonymous-group auth. Tests: dispatch-on-code-only +
body-clone preserved (`apiClient.test.ts`); gate render / sign-out / clear
(`AccessRestrictedGate.test.tsx`). **Verified on dev:** revision `00413-zdp`
Ready; operator whoami/marketplace/bucket-list all 200 (no regression); the event
name + screen copy confirmed present in the deployed JS bundle. Residual: a real
`@gmail.com` browser click-through isn't automated here (no non-operator Firebase
identity / browser-driving tool) — best done by a human sign-in on dev.

## Security Considerations

- This IS a security change; it only tightens. No data leaves GCP; denies are
  logged (uid + bucket + reason) for audit (Axiom #8).
- **Threat closed:** authenticated-but-unauthorized read of confidential customer
  buckets, and (flag-on) authentication by non-allowlisted domains.
- **Residual:** intra-bucket object scoping is bucket-level, not per-object — ONE's
  bucket is single-tenant so acceptable; note it if a bucket ever mixes tenants.

## Success Criteria

- [ ] Non-ONE authenticated user → 403 on the ONE llmops bucket (all 3 verbs), test + live
- [ ] ONE user + operator → 200 (library previews render)
- [ ] `AUTH_REQUIRE_KNOWN_DOMAIN=1`: non-allowlisted domain → 403 at the gate; group-id/LOCAL exempt
- [ ] Backward-compatible: flags off → no behaviour change
- [ ] `make lint` + `make test-fast` green; deployed to dev + test (prod deferred)

## Decisions (signed off 2026-07-23)

- **Gap-A default** — RESOLVED: **ship on-by-default**. Deny-by-default is the
  correct posture for confidential data; the admin bypass + registered-config
  allow-path are the escape valves.
- **Operator domains** — RESOLVED: `AUTH_OPERATOR_DOMAINS=yourcompany.com,yourcompany.test`.
  `sunholo.com` is **excluded** (gcloud-infra identity, not an app user). Adding a
  customer is a `clients/{domain}` Firestore write, not a config change.
- **Fix B scope** — RESOLVED: **ship A and B together**, both enabled on dev/test.
- **`find_by_gcs_name`** — RESOLVED: no such lookup exists (`bucket_config` keys by
  config-id only); add a thin `("gcsBucket","==",name)` query. Raw buckets like
  llmops have no config and rely on the tenant-bucket check.
- **Error shape (from #31 review)** — RESOLVED: Gap-A/B 403s reuse the shipped
  `{code, message}` structured shape (`BUCKET_NOT_AUTHORIZED` /
  `DOMAIN_NOT_PERMITTED`), not bare strings — mirrors `TENANT_NOT_PROVISIONED` so
  the frontend renders a specific "why" (NEVER-SILENT #8).
- **Audit ownerUid→email (from #31 review)** — RESOLVED: admin analytics already
  resolves `ownerUid → email/name` (commit `ae9cf75`); reuse it for the audit-log
  goal (Axiom #8), don't rebuild.
- **`TENANT_FALLBACK_FAIL_CLOSED` + `yourcompany.com` upload home (from #31 review)**
  — RESOLVED (2026-07-23, corrected): Gap-A/B do NOT require fail-closed; the
  llmops leak is closed without it. **`yourcompany.com` does NOT need its own
  bucket** — `clients/yourcompany.com` grants `derived_group_tags:[aitana-admin]`,
  so every operator is a platform admin and reads any bucket via the Gap-A admin
  bypass (a `documents_bucket` buys nothing for reads). Operators don't upload
  (customer corpus is uploaded by `@acme-energy.example` users), so under the
  eventual fail-closed flip an operator upload-403 is **intended, not a
  regression** (option b). Net: the flip has **no bucket precondition** — no
  `yourcompany.com` bucket to provision. (Supersedes the earlier "map yourcompany.com
  → bucket" note, which over-assumed operators upload.)
  **FLIPPED on dev/test 2026-07-23** via a cloudbuild per-branch case
  (`dev|test) TENANT_FALLBACK_FAIL_CLOSED=1`; prod `0`, frozen) — both sides were
  ready (#31 write-side UX shipped, read-side reuses the same `_fail_closed`), and
  Gap-B already blocks unmapped domains so the only effect is the intended
  operator upload-403. Closes the shared-bucket fallback path; completes the
  isolation story on dev/test.

## Coordination note — issue #31 (upload/write-side sibling) · please review before executing

> Added 2026-07-23 by the concurrent admin-analytics / tenant-fallback session
> (issue [#31](https://github.com/sunholo-data/ai-protocol-platform/issues/31)). One gap in the
> signed-off Decisions above, and one thing already shipped you should NOT
> re-implement.

This design is the **read-side** fix. Issue #31 is the **write-side** sibling of
the *same* incident: an **unmapped** personal-Gmail account (a ONE tester on the
wrong identity) uploaded confidential ONE contracts *into* the shared
`aitana-documents-bucket`, because `resolve_documents_bucket` silently falls back
when `TENANT_FALLBACK_FAIL_CLOSED` is off. Same model, same incident, two surfaces
— treat as one design.

**Already shipped (dev + test, commit `ae9cf75` / merged `5f2c492`) — do NOT
re-implement:**
- The fail-closed upload denial is now **user-legible**: `UnmappedTenantError` →
  structured `403 {code: "TENANT_NOT_PROVISIONED", message}` in
  `backend/tools/documents/upload.py`, surfaced by `UploadDropZone` (NEVER-SILENT
  #8). **Reuse this shape for the Gap-A bucket-read 403** rather than a bare
  `"Not authorized for this bucket"` — a denied user should be told *why*.
- Admin analytics resolves `ownerUid → email/name` — directly serves this design's
  audit goal (Axiom #8): logs can now say *who*, not just a uid.

**Gap in the signed-off Decisions — `yourcompany.com` has no upload home under
fail-closed.** The Decisions settle `yourcompany.com` as an **operator domain**
(read bypass, no `documents_bucket`). But verified 2026-07-23: **only
`acme-energy.example` has its own bucket; `yourcompany.com` currently relies on the
shared fallback.** The operator bypass covers *reads* — it gives `yourcompany.com`
users nowhere to *upload*. So the moment `TENANT_FALLBACK_FAIL_CLOSED=1` is set
(which A/B enablement implies), **internal `yourcompany.com` uploads will 403**
with the new `TENANT_NOT_PROVISIONED` message. Decide before the flip:
- **(a)** map `yourcompany.com → its own `documents_bucket`** (real tenant — covers
  uploads *and* reads), or
- **(b)** accept operators are read-only (no document uploads) — state it so the
  upload 403 is intended, not a regression.

**Single-flip coupling:** `TENANT_FALLBACK_FAIL_CLOSED` is a shared lever with #31
— enable it **once, coordinated**, not independently from either side.

**Same incident, separate cleanup:** the ONE contracts already in the shared bucket
under the personal Gmail account still need remediation (remove / move to ONE's
bucket) — tracked on #31, not part of this code change.

> **→ Reviewed & folded in (read-side session, 2026-07-23):** all three claims
> verified against code/git (the `TENANT_NOT_PROVISIONED` shape, commits
> `ae9cf75`/`5f2c492`, the upload fail-closed path). Actioned: (1) Gap-A/B 403s now
> reuse the structured `{code, message}` shape; (2) audit reuses the shipped
> `ownerUid→email` resolution; (3) `TENANT_FALLBACK_FAIL_CLOSED` is now an explicit,
> #31-coordinated third lever with the `yourcompany.com` upload-home decision as its
> precondition (Phase 3 + Decisions). **One correction:** A/B enablement does **not**
> literally *set* fail-closed — Gap-A closes the llmops leak without it (traced in
> Migration & Rollout); fail-closed is the coordinated companion flip that
> additionally closes the shared-bucket exposure. Net: no code-shape change to
> Gap-A/B, one new coordinated flip, one open decision for the user.

## Related Documents

- `CLAUDE.md` — Security Hard Rules (confidential customer content; derivative artefacts)
- Issue [#31](https://github.com/sunholo-data/ai-protocol-platform/issues/31) — upload/write-side sibling (see Coordination note)
- `docs/design/v6.3.0/` — client-tenant management + bucket accessControl
- `backend/auth/access_context.py`, `backend/auth/firebase_auth.py`, `backend/db/clients.py`
- `docs/ops/env-cut-runbook.md` Gap 9 — ONE data lives in the platform llmops bucket

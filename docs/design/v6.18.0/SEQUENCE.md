# v6.18.0 — Build Sequence

Security hardening, not a feature: close the two gaps that let an authenticated
user read another tenant's confidential documents. Surfaced 2026-07-23 while
repointing the Contracts library at the per-env llmops bucket — the bucket file
endpoints authorize on "authenticated + the SA can read the bucket", so any
authenticated user can name the ONE llmops bucket and list/preview confidential
PPAs. Enforce authorization at the app layer (Axiom #9 SECURE BY CONSTRUCTION),
and add an opt-in email-domain allowlist so the ONE deployment admits only
`acme-energy.example` + operators.

## Ordering

| # | Doc | Priority | Est. | Depends on | Notes |
|---|-----|----------|------|------------|-------|
| 1 | [tenant-scoped-data-access](tenant-scoped-data-access.md) | P0 | ~1.5–2d | v6.3.0 client-tenant + bucket accessControl ✅; **coordinates with issue [#31](https://github.com/sunholo-data/ai-protocol-platform/issues/31)** (upload/write-side sibling — same incident) | Phase 1 (bucket authz) is the leak and ships first, on-by-default. Phase 2 (domain allowlist) is flag-gated. Phase 3 enables on dev/test; the **#31-coordinated `TENANT_FALLBACK_FAIL_CLOSED` flip** is deferred and has **no bucket precondition** (operators are admins → read via bypass, don't upload). **Prod deferred** (frozen pending v5/v6 Firestore de-risk). |

## Timeline estimate

| Phase | Work | Est. | Status |
|-------|------|------|--------|
| 1 | `_authorize_bucket_read` guard on list/preview/thumbnail; cross-tenant 403 tests | ~0.75d | Proposed |
| 2 | `_domain_allowed` + `AUTH_REQUIRE_KNOWN_DOMAIN` gate in `get_current_user`; exempt LOCAL_MODE/group-id; `aitana whoami --check-bucket` | ~0.5d | Proposed |
| 3 | Enable flags on dev/test via `run_client.tfvars`; live cross-tenant 403 + ONE 200 verification | ~0.25d | Proposed (prod deferred — frozen) |

## What ships in v6.18.0

- **Phase 1 (the leak):** a single `_authorize_bucket_read(user, access, name)`
  guard on the three bucket file endpoints, before any GCS read — platform admin
  OR the caller's own tenant bucket OR a registered bucket-config the caller
  `can_access`, else **403**. Deny-by-default, fail-closed. Ships **on** (a
  confidential-data leak shouldn't wait for opt-in). Guarded by a cross-tenant
  test: a non-ONE user → 403 on the ONE llmops bucket for list/preview/thumbnail.
- **Phase 2:** an opt-in `AUTH_REQUIRE_KNOWN_DOMAIN` gate in `get_current_user` —
  a domain is admitted if `clients/{domain}` exists (a mapped tenant) or it is in
  `AUTH_OPERATOR_DOMAINS` (default `yourcompany.com,yourcompany.test`). LOCAL_MODE
  and anonymous group-id auth are exempt. Adding a customer stays a Firestore
  write, no redeploy.
- **Phase 3:** flags set on dev/test; verified with a real cross-tenant token
  (403) and a real ONE/operator token (200, library previews render). **Prod is
  frozen** — this promotes to prod when the v5/v6 shared-Firestore de-risk lands.

## Dependency graph

```
v6.3.0 client-tenant + bucket accessControl ─► tenant-scoped-data-access
                                                 Phase 1 (bucket authz, on-by-default)
                                                 Phase 2 (domain allowlist, flag-gated)
                                                 Phase 3 (enable dev/test; prod deferred)
```

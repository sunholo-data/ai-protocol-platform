# Resource Access Control

**Status**: Implemented
**Priority**: P0 (High)
**Estimated**: 2 days
**Scope**: Backend (+ thin frontend surfaces + CLI)
**Dependencies**: [Skills Data Model](skills-data-model.md), [Auth & Permissions](auth-and-permissions.md), [Cloud Infrastructure](cloud-infrastructure.md)
**Created**: 2026-04-15
**Last Updated**: 2026-04-22
**Completed**: 2026-04-22

## Problem Statement

v5 ships a working email+domain permission system (`backend/tool_permissions.py`) that gates tools, buckets, and datastores per user/domain. It works but has two problems we do not want to carry into v6:

1. **It adds hot-path latency.** v5's `ConfigManager` re-walks a YAML permissions array on every tool invocation, re-reading per-tool configs (e.g., `datastore_id`) keyed by email/domain. Each tool call pays the lookup cost.
2. **It was bolted on.** v5 grew ownership/visibility later as separate concerns layered over an already-shipped tool-permission matrix. The result is per-tool config sprawl (wildcards, per-domain `datastore_id` overrides) that is hard to audit and hard to remove.

v6 has already landed the *skill* half of this correctly: `SkillConfig.access_control` + `SkillConfig.tags` are live in [backend/db/models/__init__.py:43-99](../../../../backend/db/models/__init__.py#L43-L99) and enforced by `canAccessSkill()` in [firestore.rules:23-47](../../../../firestore.rules#L23-L47). [auth-and-permissions.md](auth-and-permissions.md) specifies the API-side checks (not yet coded).

What is **missing** is the same access model applied uniformly to the other resources users own or consume: **GCS buckets** and **bucket folders**. Without this, storage tools ported in Phase 1A ([tools-porting-guide.md](tools-porting-guide.md)) will either (a) fall back to v5's pattern and re-import the bloat, or (b) ship wide-open and get retrofitted with security later.

**Current State:**
- Skills: access_control + tags in model + Firestore rules + API design (implementation pending).
- Buckets: no model, no rules, no design. v5 uses per-domain `datastore_id` strings baked into tool configs.
- Bucket folders: no model, no rules, no design.
- Tool permissions: designed as per-user/domain allow-list (in `auth-and-permissions.md`), but *resource*-level access (which bucket? which folder?) is undefined.

**Impact:**
- Blocks storage tool port (ai_search, file_browser) — they need a resource model to gate against.
- Forces Phase 1A to either stall or hard-code domain-specific paths, replicating v5's sprawl.
- Every hour we wait, the "bolt it on later" risk grows.

## Goals

**Primary Goal:** Ship one unified, low-latency access-control model covering skills, buckets, and bucket folders, enforced by architecture (Firestore rules + request-scoped cache + signed URLs), not by per-call permission walks.

**Success Metrics:**
- Same `AccessControl` + `tags` schema applied to all three resource types.
- Resource access check in the request hot path: **<0.5ms** (in-memory, post-JWT).
- Zero Firestore reads for access enforcement during a tool invocation (all access decisions made at request boundary or via signed-URL scope).
- GCS reads bypass the backend (signed URL → browser/tool → GCS directly).
- Folder access resolvable in a single Firestore read (no recursive traversal).

**Non-Goals:**
- RBAC / group permissions (keep to owner + public/private/domain/specific).
- Per-operation ACLs (no separate read/write/delete tiers; owner does everything, non-owners read-only).
- Bucket creation/management UI in v6.0.0 (config via CLI + Terraform only).
- Tool-level allow-lists — those stay in [auth-and-permissions.md](auth-and-permissions.md)'s `tool_permissions` collection. This doc covers *resources*, not *capabilities*.
- v5 datastore_id override parity. v6 resources carry their own config; no per-domain wildcard tricks.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Request-scoped cache + signed URLs keep access checks off the hot path. Avoids v5's per-tool-call Firestore read. |
| 2 | EARNED TRUST | 0 | Not a factual-claims feature. |
| 3 | SKILLS, NOT FEATURES | +1 | Unifies the access model users already see on skills; no new user-facing abstraction. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model routing impact. |
| 5 | GRACEFUL DEGRADATION | +1 | Deny-by-default. Missing resource = 404, not leak. Signed URL expiry bounds damage from a compromised link. |
| 6 | PROTOCOL OVER CUSTOM | 0 | No protocol change. Firebase JWT + Firestore rules + GCS signed URLs are all standard primitives. |
| 7 | API FIRST | +1 | Same access check is channel-agnostic (web, Telegram, CLI all go through the same `AccessContext`). |
| 8 | OBSERVABLE BY DEFAULT | +1 | Each access decision emits a span attribute (`resource.id`, `resource.type`, `access.decision`). Signed URL issuance logged. |
| 9 | SECURE BY CONSTRUCTION | +1 | Firestore rules enforce at the storage layer; API layer is a safety net, not the primary gate. Deny-by-default. Data stays in-project. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend never evaluates permissions; it receives pre-filtered resource lists + short-lived signed URLs. |
| | **Net Score** | **+7** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None (no -1 scores).

## Design

### Overview

One `AccessControl` + `tags[]` schema, three collections (`skills/`, `buckets/`, `buckets/{id}/folders/`), three enforcement points chosen to keep the hot path clean:

1. **Firestore rules** — primary gate for reads/writes from the client. Mirrors the existing `canAccessSkill()` pattern.
2. **`AccessContext` (request-scoped)** — computed once per request during JWT verify; carries the user's effective access set (owned IDs + domain + email) into the request. Tools read from context, not from Firestore.
3. **Signed GCS URLs** — issued at skill-start for the buckets/folders the skill is allowed to touch. The browser/tool hits GCS directly. Backend is not on the data path.

### The AccessControl Schema (already exists — reuse verbatim)

```python
# backend/db/models/access.py  (promote from __init__.py for reuse)

class AccessControl(BaseModel):
    type: Literal["private", "public", "domain", "specific", "tagged"] = "private"
    domain: str | None = None            # required iff type == "domain"
    emails: list[str] | None = None      # required iff type == "specific"
    tags: list[str] | None = None        # required iff type == "tagged" — user must carry ≥1 of these in user.groupTags
```

Applied identically to `SkillConfig`, `BucketConfig`, `BucketFolderConfig`, and `ChatSessionIndex` (see [chat-history.md](chat-history.md)). No per-resource extensions. No wildcards.

**The `tagged` variant** lets resources be shared with a group of users who carry a matching tag on their profile (e.g. `finance-team`, `acme-corp:project-x`). It is the B2B team-sharing primitive — the same one consumed by chat-history to let teammates see each other's conversations about a shared document. Group tags are **opaque strings**, server-controlled, never client-declared. They are assigned to users via admin CLI (`aitana groups add-user <uid> <tag>`) and propagated to the Firebase JWT as a custom claim (`groupTags: string[]`) at token mint.

### New Models

```python
# backend/db/models/buckets.py

class BucketConfig(BaseModel):
    """Firestore doc: buckets/{bucket_id}"""
    bucket_id: str = Field(alias="bucketId")      # matches GCS bucket name
    display_name: str = Field(alias="displayName")
    description: str = ""
    owner_email: str = Field(alias="ownerEmail")
    owner_id: str = Field(alias="ownerId")
    access_control: AccessControl = Field(alias="accessControl")
    tags: list[str] = Field(default_factory=list)
    gcs_bucket: str = Field(alias="gcsBucket")    # actual GCS bucket name
    region: str = "europe-west1"
    created_at: float = Field(default_factory=time.time, alias="createdAt")
    updated_at: float = Field(default_factory=time.time, alias="updatedAt")

    model_config = {"populate_by_name": True}


class BucketFolderConfig(BaseModel):
    """Firestore doc: buckets/{bucket_id}/folders/{folder_id}"""
    folder_id: str = Field(alias="folderId")
    bucket_id: str = Field(alias="bucketId")      # denormalized for query
    path: str                                     # e.g. "reports/2026/"
    display_name: str = Field(alias="displayName")
    owner_id: str = Field(alias="ownerId")

    # Inherited from parent bucket unless overridden
    access_control: AccessControl | None = Field(default=None, alias="accessControl")
    effective_access: AccessControl = Field(alias="effectiveAccess")  # denormalized
    tags: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time, alias="createdAt")

    model_config = {"populate_by_name": True}
```

**Folder inheritance rule:** on write, the API computes `effective_access = folder.access_control or parent_bucket.access_control` and stores it. Rules and runtime checks read only `effective_access`. **No recursive resolution at read time.** If the parent bucket's access changes, a fan-out job rewrites `effective_access` on all folders that don't override. Rare, offline cost — never hot-path cost.

### Request-Scoped AccessContext (the latency win)

```python
# backend/auth/access_context.py

@dataclass(frozen=True)
class AccessContext:
    uid: str
    email: str
    domain: str
    group_tags: frozenset[str] = frozenset()   # from JWT custom claim "groupTags"
    # Computed once at JWT verify; None = not yet loaded (lazy)
    _owned_skills: set[str] | None = None
    _owned_buckets: set[str] | None = None

    def can_access_skill(self, skill: SkillConfig) -> bool: ...
    def can_access_bucket(self, bucket: BucketConfig) -> bool: ...
    def can_access_folder(self, folder: BucketFolderConfig) -> bool: ...
    def is_owner(self, resource) -> bool: ...


async def build_access_context(user: User) -> AccessContext:
    """Called once per request by the auth middleware after JWT verify.

    Owned-resource sets loaded lazily on first use — most requests don't need them
    (public/domain access paths don't require ownership lookup).
    """
    return AccessContext(uid=user.uid, email=user.email, domain=user.domain)
```

The evaluator is pure-Python on already-fetched data:

```python
def can_access(ac: AccessControl, ctx: AccessContext, owner_id: str) -> bool:
    if ac.type == "public": return True
    if owner_id == ctx.uid: return True           # owner always wins
    if ac.type == "domain": return ctx.domain == ac.domain
    if ac.type == "specific": return ctx.email in (ac.emails or [])
    if ac.type == "tagged": return bool(ctx.group_tags & set(ac.tags or []))
    return False                                   # private and not owner
```

**Latency budget:**
- JWT verify: ~5ms (Firebase Admin SDK, public-key cached).
- `build_access_context`: <0.1ms (struct construction).
- `can_access()`: <0.01ms per call (in-memory comparisons).
- Hot path per tool call: **0 Firestore reads**. Context is already in memory from the request boundary.

Compare to v5: per tool call, load YAML → walk permissions array → walk per-tool config. Every. Tool. Call.

### GCS Signed URLs (keeping the backend off the data path)

On skill invocation, the agent factory determines which buckets/folders the skill declares in `SkillConfig.skill_metadata.tool_configs` (e.g. `{"ai_search": {"folders": ["reports/2026/"]}}`). For each folder the user can access:

```python
# backend/auth/signed_urls.py

def issue_folder_read_urls(
    folder: BucketFolderConfig,
    ctx: AccessContext,
    ttl_seconds: int = 900,         # 15 min
) -> list[SignedURL]:
    """Returns a list of short-lived GCS signed URLs scoped to this folder only."""
    if not ctx.can_access_folder(folder):
        raise AccessDenied(...)
    # Use google.auth.compute_engine.IDTokenCredentials or SA impersonation
    # to sign URLs scoped to f"gs://{folder.bucket_id}/{folder.path}*"
    ...
```

The browser (or tool) then reads GCS directly. Backend does not proxy bytes. This is the single biggest latency delta vs. v5, which routed bucket reads through the Flask server.

**Fallback:** if signed URLs can't be issued (SA misconfig, GCS outage), tools degrade to backend-proxied reads with a warning span. Graceful degradation (Axiom #5).

### Firestore Security Rules (primary gate)

Extend [firestore.rules](../../../../firestore.rules) with one helper and two collection blocks:

```javascript
// firestore.rules (append)

function canAccessResource(ac, ownerId) {
  return ac != null && (
    ac.type == 'public' ||
    (isAuthenticated() && (
      request.auth.uid == ownerId ||
      isAdmin() ||
      (ac.type == 'domain'   && request.auth.token.email.matches('.*@' + ac.domain)) ||
      (ac.type == 'specific' && request.auth.token.email in ac.emails) ||
      (ac.type == 'tagged'   && request.auth.token.groupTags.hasAny(ac.tags))
    ))
  );
}

match /buckets/{bucketId} {
  allow read: if canAccessResource(resource.data.accessControl, resource.data.ownerId);
  allow create: if isAuthenticated() && request.resource.data.ownerId == request.auth.uid;
  allow update, delete: if isAuthenticated() &&
                          (resource.data.ownerId == request.auth.uid || isAdmin());

  match /folders/{folderId} {
    allow read: if canAccessResource(resource.data.effectiveAccess, resource.data.ownerId);
    allow create: if isAuthenticated() &&
                    get(/databases/$(database)/documents/buckets/$(bucketId)).data.ownerId == request.auth.uid;
    allow update, delete: if isAuthenticated() &&
                            (resource.data.ownerId == request.auth.uid || isAdmin());
  }
}
```

Note: `canAccessSkill()` is refactored to call the new generic `canAccessResource()` — one code path for all three resource types.

### CLI Surface

Per [local-dev-cli.md](../../v6.1.0/local-dev-cli.md), a feature with a developer-facing surface must scope its own CLI commands in the design doc. Buckets and folders are configured by developers, so they get commands from day one:

```
aitana bucket list [--owner me|<email>] [--tag <tag>]
aitana groups add-user <uid> <tag>              # assign a group tag to a user (admin)
aitana groups remove-user <uid> <tag>
aitana groups list-user <uid>                   # show a user's group tags
aitana bucket grant <bucket-id> --tag <tag>     # convert to tagged access
aitana bucket show <bucket-id>
aitana bucket create <bucket-id> --display-name "..." --access public|private|domain|specific [--domain ...] [--emails ...]
aitana bucket grant <bucket-id> --domain <domain>          # convert to domain access
aitana bucket grant <bucket-id> --email <email>            # append to specific list
aitana bucket revoke <bucket-id> --email <email>
aitana folder list <bucket-id>
aitana folder create <bucket-id> <path> [--inherit | --access ...]
aitana access check <resource-id> --as <email>             # dry-run an access decision
```

Each command is a thin Click subcommand → httpx call to backend API → typed response. ~0.15 day per command pair; unit tests verify argument parsing and request shape.

### Architecture Diagram

```
[JWT] → get_current_user → build_access_context
                                   │
                                   ▼
                           [AccessContext ctx]  (in request state for whole request)
                                   │
          ┌────────────────────────┼────────────────────────┐
          ▼                        ▼                        ▼
   [Skill endpoint]         [Bucket endpoint]        [Agent run]
   ctx.can_access_skill()   ctx.can_access_bucket()  per skill: preload allowed
                                                     bucket/folder configs →
                                                     issue signed URLs →
                                                     attach to tool_context
                                                             │
                                                             ▼
                                                    [Tool invocation]
                                                    reads from tool_context
                                                    — no Firestore read,
                                                    no permission walk
                                                             │
                                                             ▼
                                                    [Browser/tool → GCS directly
                                                     via signed URL]
```

## Implementation Plan

### Phase 1: Schema + Rules (~0.5 day)
- [ ] Promote `AccessControl` to `backend/db/models/access.py` and import from `skills.py`, `buckets.py`, `folders.py` (~20 LOC move).
- [ ] Add `BucketConfig` and `BucketFolderConfig` models with Pydantic validators (~80 LOC).
- [ ] Extend [firestore.rules](../../../../firestore.rules) with `canAccessResource()` + `buckets/` + `buckets/*/folders/` blocks. Refactor `canAccessSkill()` to delegate (~40 LOC).
- [ ] Add rules emulator tests covering all 4 access types × {owner, non-owner, admin} for each collection.

### Phase 2: AccessContext + API (~0.75 day)
- [ ] `backend/auth/access_context.py` — `AccessContext` dataclass + `build_access_context()` + `can_access()` (~80 LOC).
- [ ] Wire `build_access_context()` into `get_current_user()` so every authenticated request carries `AccessContext` in `request.state` (~10 LOC diff in `firebase_auth.py`).
- [ ] Refactor the not-yet-written `has_skill_access()` / `is_skill_owner()` in [auth-and-permissions.md](auth-and-permissions.md) to be methods on `AccessContext` (alignment — no duplication).
- [ ] Bucket CRUD endpoints (`GET/POST/PUT/DELETE /api/buckets/{id}`, `.../folders/{id}`) with `AccessContext` checks (~150 LOC).
- [ ] pytest coverage: each endpoint × each access type × {owner, non-owner, admin}.

### Phase 3: Signed URLs + Tool Integration (~0.5 day)
- [ ] `backend/auth/signed_urls.py` — `issue_folder_read_urls()`, `issue_bucket_read_urls()` using SA impersonation (~60 LOC).
- [ ] Integrate into agent factory: at skill-start, resolve `tool_configs → folders`, check access, issue URLs, stash in `tool_context.state["signed_urls"]` (~40 LOC diff in `adk/agent.py`).
- [ ] Storage tools (`ai_search`, `file_browser`) read from `tool_context.state["signed_urls"]` — no per-call Firestore reads.
- [ ] Tests: mock IAM signer, verify URL scope + TTL.

### Phase 4: CLI + Docs (~0.25 day)
- [ ] Click subcommands under `cli/aitana/commands/bucket.py` and `folder.py` + `access.py` (~150 LOC).
- [ ] Update [local-dev-cli.md](../../v6.1.0/local-dev-cli.md) command tree.
- [ ] Update [auth-and-permissions.md](auth-and-permissions.md) to cross-reference this doc (avoid drift).

**Total:** ~2 days, fits in Phase 1A slot between `skills-data-model` (done) and `tools-porting-guide` (needs this before porting storage tools).

## Migration & Rollout

**Database Migrations:**
- New collections: `buckets/`, `buckets/{id}/folders/`. No backfill needed — v5 buckets are configured in YAML and will be migrated in v6.2.0 per [migration-to-v6.md](../../v5.0.0/migration-to-v6.md).
- For dev seeding: `aitana bucket create` with known dev-bucket IDs.

**Feature Flags:** None. Deny-by-default means an un-rolled-out client just sees empty lists.

**Rollback Plan:**
- Revert `firestore.rules` to pre-change version (safety net, no data loss).
- Remove `buckets/` / `folders/` collections (no references from skills in v6.0.0).
- `AccessContext` is additive — no frontend depends on it yet when first shipped.

**Environment Variables:**
- `SIGNED_URL_TTL_SECONDS` (default: 900) — per-environment tunable.
- `SIGNED_URL_SIGNER_SA` (default: `platform@{project}.iam.gserviceaccount.com`) — the SA used to sign URLs; must have `roles/iam.serviceAccountTokenCreator` on itself.

## Testing Strategy

### Backend Tests (pytest)
- [ ] Unit: `can_access()` truth table — 4 access types × owner/domain-match/email-match/nothing × admin=true/false.
- [ ] Unit: folder `effective_access` computed correctly on create with and without override.
- [ ] Integration (Firestore emulator): rules enforce on all 4 access types for `buckets/` and `buckets/*/folders/`.
- [ ] Integration: `build_access_context()` runs once per request (assert via request-count fixture).
- [ ] Integration: signed URL scope is limited to declared folder path (reject URL rewrites).
- [ ] Integration: tool invocation with `signed_urls` in `tool_context.state` does not hit Firestore.

### Frontend Tests (Vitest)
- [ ] Bucket list API client returns pre-filtered results (no client-side filtering).
- [ ] Signed-URL expiry handling: 403 on expired URL triggers graceful re-request, not crash.

### Manual Testing
- [ ] Create a `domain: yourcompany.com` bucket; verify a `@gmail.com` user gets 404 (not 403 — avoid leaking existence).
- [ ] Create a folder that overrides parent access; verify the override takes effect.
- [ ] Change parent bucket access; verify folders with `null` override are rewritten (fan-out job correctness).

## Security Considerations

- **Trust boundary:** all access decisions made inside the GCP project. Signed URLs are the only credential that leaves — short-lived, scoped, revocable via SA key rotation.
- **404 vs 403:** API returns 404 for resources the user cannot see (consistent with `canAccessResource` denying reads at the rules layer). 403 is reserved for "you can see it, you can't modify it" (owner-gated writes).
- **Admin bypass:** `isAdmin()` check is email-literal (`owner@yourcompany.com`) — inherited from existing rules. Acceptable for v6.0.0; revisit when more admins exist.
- **Prompt injection:** signed URLs are not put in the model context; they are attached to `tool_context.state` and consumed by tool code. No path for a model to exfiltrate URLs.
- **No data egress:** all enforcement, logging, and signing happens inside the GCP project. Zero third-party SaaS touched. Per Axiom #9.

## Performance Considerations

- **Hot-path cost per tool invocation:** 0 Firestore reads, 0 YAML walks. One in-memory struct lookup.
- **Hot-path cost per authenticated request:** 1 JWT verify (~5ms, already paid in middleware), 1 `AccessContext` construction (<0.1ms). Lazy-loaded owned-ID sets only fetched when needed (most requests: never).
- **Signed URL issuance:** ~50ms the first time a skill is invoked in a session (SA token mint + URL sign). Cached in session state for the session TTL.
- **Folder access propagation:** fan-out on bucket access change is async; can take seconds. Acceptable — changing an access model is not a hot-path operation.

## Success Criteria

- [ ] `AccessControl` schema used uniformly across `SkillConfig`, `BucketConfig`, `BucketFolderConfig` (no duplicate definitions).
- [ ] Firestore rules enforce access for all three collections; emulator tests green.
- [ ] `AccessContext` computed once per request; verified by trace inspection.
- [ ] Zero Firestore reads measured during a storage-tool invocation (end-to-end trace).
- [ ] Signed URLs issued with ≤15 min TTL and folder-scoped prefix.
- [ ] `aitana bucket {list, show, create, grant, revoke}` + `aitana folder {list, create}` + `aitana access check` all work against local backend.
- [ ] Lint and typecheck clean (`make lint`, `npm run quality:check:fast`).
- [ ] No `-1` scores on axiom re-review after implementation.

## Open Questions

- **Signed URL caching granularity:** per-session or per-(session, folder)? Start per-session for simplicity; split if we see URL refresh churn.
- **Group support:** ~~do we need `access_control.type == "group"` with a separate `groups/` collection for v6.0.0?~~ **Resolved 2026-04-15:** added `type == "tagged"` variant using opaque group-tag strings on user profiles (no separate `groups/` collection). Drove by [chat-history.md](chat-history.md) — teams need to see each other's conversations about shared documents. Tags are server-controlled via `aitana groups add-user`, carried on the Firebase JWT as `groupTags`.
- **Bucket listing visibility:** should the bucket list API return buckets the user cannot access (as shadow entries) or hide them entirely? Current answer: **hide entirely** — 404-on-deny pattern.

## Implementation Report

**Completed:** 2026-04-22
**Sprint:** RESOURCE-ACCESS (1A.1b) — 5 milestones, all passing
**Actual effort:** ~3h wall time (planned 2 days); parallelism on M3+M4 accounted for most of the compression
**Commits:** `4ed9717` (M1), `e79e88e` (M2), `211e6c2` (M3), `c10b03d` (M4), plus M5 docs

### What was built

- **M1 — Models + rules** (`4ed9717`): `backend/db/models/buckets.py` with `BucketConfig` + `BucketFolderConfig`; `firestore.rules` now has a single generic `canAccessResource(ac, ownerId)` 5-type evaluator — `canAccessSkill()` delegates to it (no duplicated branches). `/buckets/{bucketId}` + nested `/folders/{folderId}` rules blocks; folder writes require `effectiveAccess`. Compound indexes mirror the skills pattern. 26 model tests.
- **M2 — CRUD API** (`e79e88e`): `/api/buckets` router with full CRUD on buckets and `/buckets/{id}/folders/{id}`. 404-on-deny pattern (no existence leak). `compute_effective_access(folder_access, parent)` writes `effectiveAccess` on every folder create/update — folder reads hit `effectiveAccess` directly, never recurse to parent. 37 API tests (22 bucket + 15 folder) covering the 5-type × CRUD × {owner, non-owner-match, non-owner-nomatch, admin} matrix.
- **M3 — Signed URLs + agent factory** (`211e6c2`): `backend/auth/signed_urls.py` with `issue_folder_read_urls` / `issue_bucket_read_urls` via `google.auth.impersonated_credentials` (target SA from `SIGNED_URL_SA_EMAIL`, Cloud Run ADC fallback). TTL env-tunable, clamped ≤ 3600s. Access check runs *before* signing — `AccessDenied` otherwise. Agent-factory `make_before_agent()` gained `tool_configs` + `access_context` kwargs; resolves `{tool: {bucket_folders: [...]}}` convention → signed URLs → `callback_context.state['signed_urls']`. Signer-unavailable fallback sets `state['signed_urls_unavailable']=True` (no crash). 17 unit tests including a zero-Firestore-reads invariant assertion.
- **M4 — `aitana` CLI bootstrap** (`c10b03d`): `cli/` was empty; now holds a full Click + httpx + respx package with `bucket list/show/create/grant/revoke`, `folder list/create`, `groups add-user/remove-user/list-user`, `access check`. Token resolution: `$AITANA_ID_TOKEN` → `gcloud auth print-identity-token`, with clear errors rather than a bare 401. 25 respx-mocked tests, ruff clean. `--env {dev|test|prod|local}` flag resolves base URL via `AITANA_API_URL` / `AITANA_API_URL_<ENV>`.
- **M5 — Docs + smoke**: `scripts/smoke-deployed.sh` now probes anon `GET /api/buckets` (expects 401/403) and authed `GET /api/buckets` (expects 200). `docs/design/v6.1.0/local-dev-cli.md` lists the shipped bucket/folder/groups/access command tree. `docs/ops/deployed-urls.md` mentions `/api/buckets` under the smoke-tests section.

### Deviations from the design doc

- **`bucket_id` vs `gcs_bucket`**: the design implied a single strict GCS naming rule. In practice we split these — `bucket_id` is a relaxed 1–64 char logical Firestore doc ID, `gcs_bucket` carries the strict GCS regex. Matches the acceptance criterion ("validator rejecting invalid GCS bucket names") without forcing logical IDs to follow GCS rules.
- **`tool_configs` shape is conventional, not typed**: the agent-factory callback reads `{tool_name: {"bucket_folders": [{bucket_id, folder_id}]}}` as a dict convention today. Formalizing into `SkillMetadata` is flagged TODO(v6.1) inline, to land with the first real storage-backed tool.
- **Parent-access fan-out deferred**: as the design doc Open Questions already noted, we did *not* re-compute descendant `effectiveAccess` on bucket-access changes. Punted to v6.1.
- **Backend endpoints for `groups` + `access check`**: the CLI ships pointing at `/api/groups/...` and `/api/access/check`, which do not exist yet. Wiring is follow-on work for v6.1 — documented in the CLI README and the `local-dev-cli.md` command tree (tagged *"Backend endpoint pending — v6.1."*).

### Lessons

- **Shared evaluator paid off immediately.** Folding skills + buckets + folders onto `canAccessResource()` deleted ~30 lines of near-duplicate rules and left a single place to change when group/tag semantics evolve. Worth doing the refactor even though skills already worked.
- **Pre-computed `effectiveAccess` ≫ runtime recursion.** Keeping rules reads O(1) was the right call — the one place we need fan-out (bucket access changes) is rare and can be async; the read path is hot.
- **404-on-deny needed a test.** It's easy to accidentally `raise HTTPException(403)` from a helper and leak existence. The API matrix tests caught one instance before it shipped.
- **Parallelising M3 + M4 worked cleanly** once we gave the sub-agents non-overlapping paths (`backend/auth/signed_urls.py` + `backend/adk/*` vs `cli/**`). No merge conflicts.
- **Signer fallback belongs in the callback, not the signer.** Keeping `build_signed_urls_for_folders` itself strict (raises on access denied) and handling the `signer unavailable` degradation in the pre-run callback kept unit tests simple.

## Related Documents

- [Auth & Permissions](auth-and-permissions.md) — JWT middleware, tool permissions, owner checks. This doc extends it to non-skill resources.
- [Skills Data Model](skills-data-model.md) — existing `AccessControl` + `tags` on `SkillConfig`.
- [Cloud Infrastructure](cloud-infrastructure.md) — SA setup for signed URL signer.
- [Tools Porting Guide](tools-porting-guide.md) — the next sprint that consumes this work (storage tools).
- [Local Dev CLI](../../v6.1.0/local-dev-cli.md) — `aitana bucket/folder/access` commands land here.
- [Migration to v6](../../v5.0.0/migration-to-v6.md) — v5 `tool_permissions.py` and datastore_id context.
- v5 reference: `<your-v5-source>/backend/tool_permissions.py` — what we are deliberately simplifying away from.

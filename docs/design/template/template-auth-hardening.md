# Template Auth Hardening

**Status**: ✅ All 4 items shipped in platform (audit-confirmed 2026-06-05) — **template sync pending**.  
**Priority**: P1  
**Estimated**: 1d planned; all items done  
**Scope**: Backend + Frontend + Docs  
**Dependencies**: None (can run in parallel with other template PRs)  
**Created**: 2026-05-21  
**Last Updated**: 2026-06-05  
**Source items**: #9 #19 #20 #21 (CPH Uni AIPLA upstream feedback)

## Implementation Status (audit 2026-06-05)

| Item | Status | Files |
|------|--------|-------|
| **#9** Firebase Resource Location set by first Firestore write | ✅ Documented | [docs/ops/gotchas.md](../../ops/gotchas.md) — "Firebase resource-location must be set before first Firestore write" |
| **#19** `auth/permissions.py` crash on empty `user_email` | ✅ Fixed in platform | [backend/auth/permissions.py:103](../../../backend/auth/permissions.py#L103) — `if user_email else None` guard plus same for `user_domain` at L111 |
| **#20** `tool_permissions/*` wildcard not seeded in prod | ✅ Fixed in platform | [backend/admin/platform_seed.py::_ensure_tool_permissions_wildcard](../../../backend/admin/platform_seed.py) — called at top of `seed()`; `SeedSummary.tool_permissions_wildcard_seeded` surfaces the result. Mirrors `local_fixture.py`. |
| **#21** Frontend `onSnapshot` permission-denied for anonymous-group users | ✅ Already shipped | [`useDocBrowser.ts:57,93`](../../../frontend/src/hooks/useDocBrowser.ts#L57) + [`useDocument.ts:80`](../../../frontend/src/hooks/useDocument.ts#L80) — all 3 `onSnapshot` listeners early-return when `isAnonymousGroupAuthMode()` is true; no permission-denied console spam for anonymous-group sessions. |

## Problem Statement

The template has four auth-related gaps that manifest only in non-Aitana identity
configurations — anonymous-group auth, fresh deployments, or forks that don't use Firebase
Auth for every user:

1. **Firebase Resource Location is set by the first Firestore create** (item #9). If the
   operator doesn't know this, a stray `us-central1` Firestore create on a EU-residency
   project silently violates the data-residency requirement. No documentation exists.

2. **`auth/permissions.py` crashes on empty `user_email`** (item #19). `fs.get_document`
   is called unconditionally with `user_email` as the document key. When
   `user_email == ""` (anonymous-group users, system callers), Firestore returns
   `400 InvalidArgument: Document name "tool_permissions/" has invalid trailing "/"`.
   The error surfaces as `Agent run failed` with no client-side diagnostic.

3. **`tool_permissions/*` wildcard seeded in LOCAL_MODE but not in prod** (item #20).
   `local_fixture.py` writes a wildcard `*` doc so workshop users can chat locally. The
   deployed path through `platform_seed.seed()` doesn't. A deployed env has skills but no
   permission rules → every tool call is denied.

4. **Frontend `onSnapshot` listeners fail for anonymous-group users** (item #21). Three
   listeners (`useDocBrowser` ×2, `useDocument` ×1) are gated on `uid` being truthy. For
   anonymous-group users, `uid` is set (synthetic JWT subject `anon-<id>-<random>`), but
   no Firebase Auth user exists → Firestore rules deny → repeating
   `permission-denied` console errors.

**Impact:**

- Item #19 causes complete chat failure for anonymous-group users. First message returns
  `stream_run_failed`; every retry hits the same 400.
- Item #20 means a freshly-deployed fork has skills but can't invoke them (all tool calls
  denied), and the cause is invisible — no 403 message, just no tool output.
- Item #21 produces console spam from the first page load, damaging credibility in a demo.
- Item #9 is a data-residency risk, especially relevant for EU/GDPR forks.

## Goals

**Primary Goal:** Any auth mode supported by the template (Firebase, anonymous-group,
LOCAL_MODE) must pass end-to-end without silent failures or permission crashes.

**Success Metrics:**
- Anonymous-group user chat completes a full turn without triggering a 400/403.
- `platform_seed.seed()` writes the wildcard permission rule; a freshly-deployed fork can
  invoke tools immediately after first deploy.
- The three `onSnapshot` listeners don't fire for anonymous-group sessions.
- `docs/ops/gotchas.md` documents the Firebase resource location gotcha with an assertion
  check for bootstrap scripts.

**Non-Goals:**
- Full Firebase Auth integration for anonymous-group users (`signInAnonymously()` is a
  valid long-term fix for #21 but is scoped to a follow-up; the immediate fix is to gate
  the listeners).
- Changing the anonymous-group auth mode design (that lives in `anonymous-group-id-auth.md`).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | |
| 2 | EARNED TRUST | +1 | Silent failures replaced by explicit errors |
| 3 | SKILLS, NOT FEATURES | 0 | |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | |
| 5 | GRACEFUL DEGRADATION | +1 | Listeners skip gracefully for anonymous users |
| 6 | PROTOCOL OVER CUSTOM | 0 | |
| 7 | API FIRST | 0 | |
| 8 | OBSERVABLE BY DEFAULT | +1 | Diagnostic errors replace silent 400s |
| 9 | SECURE BY CONSTRUCTION | +1 | Empty-email guard prevents Firestore path injection |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | |
| | **Net Score** | **+4** | Meets threshold |

## Design

### Item #9 — Firebase Resource Location documentation

**File:** `docs/ops/gotchas.md` (add entry)

```markdown
## Firebase: Resource Location ID is set by the first Firestore create, not by `firebase add`

After `firebase projects:addfirebase`, the project's "Resource Location ID" shows as
`[Not specified]`. It is silently populated by whatever region is used in the next
`gcloud firestore databases create` call.

**Risk:** Running `gcloud firestore databases create --location=us-central1` on a project
that expects EU residency violates data-residency requirements permanently (location
cannot be changed after creation).

**Fix:** Before any Firestore operation, assert the intended region:

```bash
INTENDED_REGION="europe-west1"
CURRENT=$(gcloud firestore databases describe --database="(default)" \
  --project="$PROJECT_ID" --format="value(locationId)" 2>/dev/null || echo "")
if [ -n "$CURRENT" ] && [ "$CURRENT" != "$INTENDED_REGION" ]; then
  echo "ERROR: Firestore is already in $CURRENT, expected $INTENDED_REGION"
  exit 1
fi
```

Add this check to `scripts/bootstrap-gcp-project.sh` (see cloudbuild-hardening design doc).
```

### Item #19 — Guard empty `user_email` in `permissions.py`

**File:** `backend/auth/permissions.py`

```python
# Before (crashes on empty string)
async def can_use_tool(user_email: str, tool_name: str, ...) -> bool:
    ...
    user_doc = await fs.get_document(COLLECTION, user_email)   # 400 if user_email == ""
    ...

# After — guard before each Firestore key lookup
async def can_use_tool(user_email: str, tool_name: str, ...) -> bool:
    ...
    user_doc = await fs.get_document(COLLECTION, user_email) if user_email else None
    user_domain = _extract_domain(user_email) if user_email else ""
    domain_doc = await fs.get_document(COLLECTION, user_domain) if user_domain else None
    wildcard_doc = await fs.get_document(COLLECTION, "*")
    ...
```

Empty strings are a legitimate value for any auth mode that doesn't carry identity
(anonymous-group, signed-out, system callers). The wildcard fallback is the correct
permission resolution path for these callers.

**Optional follow-up (not in this PR scope):** Add explicit `group/<group_id>` lookup
so anonymous-group sessions can have per-group permission overrides. Wildcard fallback
covers v0.1 needs.

### Item #20 — Seed wildcard permission rule in `platform_seed.py`

**File:** `backend/admin/platform_seed.py`

```python
async def seed(db: FirestoreClient, ...) -> SeedSummary:
    ...
    wildcard_seeded = await _ensure_tool_permissions_wildcard(db)
    return SeedSummary(..., tool_permissions_wildcard_seeded=wildcard_seeded)

async def _ensure_tool_permissions_wildcard(db: FirestoreClient) -> bool:
    """Idempotent: write a wildcard allow-all rule if none exists."""
    doc = await db.get_document("tool_permissions", "*")
    if doc is not None:
        return False   # already exists
    await db.set_document("tool_permissions", "*", {
        "allowed_tools": ["*"],
        "created_by": "platform_seed",
        "created_at": datetime.utcnow().isoformat(),
    })
    return True
```

Add `tool_permissions_wildcard_seeded: bool` to `SeedSummary`. This mirrors what
`local_fixture.seed_local_fixture()` already does so dev and prod stay consistent.

**Why idempotent matters:** `platform_seed.seed()` runs on every deploy (Cloud Build seed
step). If the wildcard doc already exists from a previous deploy, the function skips it —
no double-writes.

### Item #21 — Gate Firestore listeners for anonymous-group sessions

**Files:** `frontend/src/hooks/useDocBrowser.ts`, `frontend/src/hooks/useDocument.ts`

The Firebase JS SDK uses `firebase.auth().currentUser` to auth Firestore listeners.
Anonymous-group users have no Firebase Auth user → Firestore rules deny → console spam.

**Immediate fix (this PR):** Gate the three listeners on `isFirebaseAuthSession()`:

```ts
// useDocBrowser.ts — add before existing uid guard
const { authMode } = useAuth();

useEffect(() => {
  if (!db || !uid) return;
  if (authMode !== "firebase") return;   // anonymous-group: skip, no Firebase Auth user
  const unsubscribe = onSnapshot(foldersQuery, ...);
  return unsubscribe;
}, [db, uid, authMode, ...]);
```

Same pattern for the `parsed_documents` listener and `useDocument.ts`.

For callers that previously received live document updates: fall back to
`"Document preview unavailable in this session."` or a manual fetch-on-demand pattern.

**Future-facing note (not in this PR):** The on-spec fix is to call
`firebase.auth().signInAnonymously()` as part of the group-join flow. This gives the SDK
a real Firebase Auth identity so snapshot listeners work without changing Firestore rules.
The `AnonymousGroupAuthProvider` is the right place to add this. Defer to a follow-up
once the template's anonymous-group-id-auth module ships (see v6.2.0 2.11 sync).

### CLI Surface

No new commands. `aiplatform admin seed` already calls `platform_seed.seed()`; the
wildcard seeding happens automatically.

## Implementation Plan

| Step | File(s) | Effort |
|------|---------|--------|
| 1 | Add Firebase resource location gotcha to `docs/ops/gotchas.md` + bootstrap assertion (#9) | 1h |
| 2 | Guard empty `user_email` / `user_domain` in `permissions.py` (#19) | 1h |
| 3 | Add `_ensure_tool_permissions_wildcard` to `platform_seed.py` (#20) | 1h |
| 4 | Gate three `onSnapshot` listeners on `authMode === "firebase"` (#21) | 1.5h |
| 5 | Tests: permissions crash, wildcard seed idempotency, listener gating | 2h |
| 6 | Update `docs/ops/` references | 0.5h |

**Total: ~7h ≈ 1d**

## Testing Strategy

- **`test_permissions.py`** — assert `can_use_tool("", "web_search", ...)` does not raise;
  returns the wildcard permission.
- **`test_platform_seed.py`** — call `seed()` twice; assert `tool_permissions_wildcard_seeded`
  is `True` on first call, `False` on second (idempotent).
- **`test_doc_browser.ts`** — render `useDocBrowser` in `anonymous_group_id` auth mode;
  assert no `onSnapshot` subscription created (no Firestore calls).
- Manual smoke: deploy to a clean env; first chat turn from an anonymous-group user completes
  without a 400 or 403.

## Success Criteria

- [ ] Anonymous-group user chat completes a full turn; no `400 InvalidArgument` in logs.
- [ ] `platform_seed.seed()` on fresh Firestore → `tool_permissions` wildcard doc exists.
- [ ] Second `platform_seed.seed()` → no error; `tool_permissions_wildcard_seeded: false`.
- [ ] `useDocBrowser` / `useDocument` produce zero `permission-denied` errors in an anonymous-group session.
- [ ] `docs/ops/gotchas.md` has a Firebase resource location entry.

## Related Documents

- [anonymous-group-id-auth.md](../../v6.2.0/implemented/anonymous-group-id-auth.md) — v6.2.0 2.11 (template sync pending)
- [template-cloudbuild-hardening.md](template-cloudbuild-hardening.md) — `scripts/bootstrap-gcp-project.sh` where the Firebase region assertion goes
- [SEQUENCE.md](SEQUENCE.md)

# Sprint Plan: CLIENT-MGMT — Client/Tenant Management

## Summary

Add admin CRUD API (`/api/admin/clients`) and `aiplatform client` CLI for managing
`clients/{domain}` Firestore records, replacing the current Firestore-console-as-interface
with a versioned API + thin CLI wrapper.

**Duration:** 0.5 day (≈5–6h)
**Scope:** Backend + CLI (no frontend changes)
**Dependencies:** `rag-document-corpus` (3.1 ✅) — no code deps; bucket resolution already works
**Risk Level:** Low — clear patterns exist in `admin/routes.py` and `bucket.py`
**Design Doc:** [client-tenant-management.md](client-tenant-management.md)

---

## Current Status Analysis

### Recent Velocity

- `f684c51` RAG corpus — large fullstack feature, ~500 LOC backend + tests, 1 day
- `backend/admin/routes.py` + `test_admin_routes.py` — existing pattern for admin routes with mocked auth
- `cli/aiplatform/commands/bucket.py` — 144 LOC, exact CLI pattern to follow
- `cli/tests/test_cli_bucket.py` — respx mock pattern for all CLI tests

**Estimated capacity:** 250–300 LOC/half-day for pure backend+CLI work with established patterns.

### Existing Implementation

- `backend/db/clients.py` — `ClientConfig` model + `get_client_sync()` read path ✅ (schema unchanged)
- `backend/admin/auth.py` — SA-based admin guard (seed routes); new routes use a different Firebase JWT guard
- `backend/admin/routes.py` — example of router structure and test patterns
- `cli/aiplatform/commands/bucket.py` — full CLI group pattern (list/show/create/grant/revoke)
- `cli/tests/test_cli_bucket.py` — `respx.mock` + `CliRunner` test pattern
- `backend/tests/api_tests/test_admin_routes.py` — `FastAPI TestClient` + `monkeypatch` pattern

---

## Milestones

### M1: Backend — Admin CRUD routes + tests

**Scope:** backend
**Goal:** Four admin routes behind `_require_admin` dep (Firebase JWT + `aitana-admin` group tag); 8 API tests passing.
**Estimated:** ~100 LOC impl + ~130 LOC tests = **230 LOC total**
**Duration:** 3h

**Tasks:**
- [ ] Write failing tests first in `backend/tests/api_tests/test_admin_clients.py` (~130 LOC)
- [ ] Create `backend/admin/clients.py` — `_require_admin` dep + 4 routes (~100 LOC)
- [ ] Wire router into `fast_api_app.py` (~5 LOC delta)
- [ ] Make all tests pass; `make lint && make test-fast` green

**Files to Create/Modify:**
- `backend/admin/clients.py` (new, ~100 LOC)
- `backend/tests/api_tests/test_admin_clients.py` (new, ~130 LOC)
- `backend/fast_api_app.py` (modify, ~5 LOC delta — one import + one `include_router` call)

**Acceptance Criteria:**
- [ ] `GET /api/admin/clients` → 200 + list for admin user, 403 for non-admin
- [ ] `PUT /api/admin/clients/example.com` → upserts record, returns full ClientConfig
- [ ] `GET /api/admin/clients/example.com` → 200 after upsert, 404 for unknown domain
- [ ] `DELETE /api/admin/clients/example.com` → removes record; second delete returns 404
- [ ] Non-admin user gets 403 on all four routes (all Firestore calls mocked)
- [ ] `pytest tests/api_tests/test_admin_clients.py` — all 8 tests pass
- [ ] `make lint && make test-fast` green

**Auth note:** `_require_admin` uses `get_current_user` (Firebase JWT), NOT the SA-allowlist
guard from `admin/auth.py`. It checks `"aitana-admin" in user.group_tags` — same claim
already set on Mark's Firebase account.

**Risks:**
- `get_current_user` dependency in test context — mitigate by overriding with `app.dependency_overrides` (same pattern as `test_buckets.py`)

---

### M2: CLI — `aiplatform client` commands + tests

**Scope:** CLI (Python, no backend changes)
**Goal:** `aiplatform client list/get/set/delete` commands wired to the new admin routes; 5 CLI tests passing.
**Estimated:** ~120 LOC impl + ~100 LOC tests = **220 LOC total**
**Duration:** 2h

**Tasks:**
- [ ] Write failing tests in `cli/tests/test_cli_client.py` (~100 LOC)
- [ ] Create `cli/aiplatform/commands/client.py` — 4 commands following `bucket.py` pattern (~120 LOC)
- [ ] Register `client` group in `cli/aiplatform/cli.py` (~3 LOC delta)
- [ ] Make all tests pass; verify `aiplatform client --help` tree is correct

**Files to Create/Modify:**
- `cli/aiplatform/commands/client.py` (new, ~120 LOC)
- `cli/tests/test_cli_client.py` (new, ~100 LOC)
- `cli/aiplatform/cli.py` (modify, ~3 LOC delta — one import + one `add_command`)

**Acceptance Criteria:**
- [ ] `client list` renders table (domain | display_name | documents_bucket) from mocked GET response
- [ ] `client get example.com` prints JSON of ClientConfig
- [ ] `client set example.com --documents-bucket b --display-name "Acme"` calls PUT with correct payload
- [ ] `client delete example.com --yes` calls DELETE without prompt
- [ ] `client delete example.com` (no `--yes`) prompts for confirmation; aborts on 'n'
- [ ] `pytest cli/tests/test_cli_client.py` — all 5 tests pass

**Risks:** None — pattern is identical to `bucket.py` and `test_cli_bucket.py`.

---

## Day Breakdown

### Half-day (≈5–6h)

| Hour | Work |
|------|------|
| 0–1 | Write `test_admin_clients.py` (8 failing tests) |
| 1–2.5 | Implement `admin/clients.py` + wire into `fast_api_app.py`; make tests pass |
| 2.5–3 | `make lint && make test-fast` — fix any issues |
| 3–4 | Write `test_cli_client.py` (5 failing tests) |
| 4–5.5 | Implement `commands/client.py` + register in `cli.py`; make tests pass |
| 5.5–6 | Final `make lint && make test-fast`; commit |

---

## Quality Gates

After M1:
```bash
cd backend && make lint && make test-fast
```

After M2:
```bash
cd backend && make lint && make test-fast
cd cli && pytest cli/tests/test_cli_client.py -v
```

Final:
```bash
cd backend && make lint && make test-fast
```

---

## Success Criteria (from design doc)

- [ ] `pytest backend/tests/api_tests/test_admin_clients.py` — all 8 tests pass
- [ ] `pytest cli/tests/test_cli_client.py` — all 5 tests pass
- [ ] `make lint && make test-fast` green
- [ ] `aiplatform client --help` shows list/get/set/delete subcommands

---

## Notes

- M1 and M2 are independent (M2 mocks the backend); they can execute sequentially in the same session.
- `_require_admin` does NOT reuse `admin/auth.py` — that's SA-allowlist. The new dep uses Firebase user JWT via `get_current_user`, checking `group_tags` — zero new credential surface.
- All Firestore calls in tests are mocked via `unittest.mock.patch`; no GCP credentials needed.
- The `_require_admin` dep is the most design-sensitive piece: verify `group_tags` is the right field on the `User` model before writing tests.

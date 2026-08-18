# Client / Tenant Management

**Status:** Planned
**Priority:** P1
**Estimated Effort:** 1 day (backend 0.5d, CLI 0.5d)
**Last Updated:** 2026-06-02

---

## Problem Statement

The platform resolves each user's GCS document bucket from Firestore at `clients/{email_domain}`.
The `db/clients.py` module reads this collection on every upload, but **nothing writes it** — no
API, no CLI, no seed. Onboarding a client (e.g. Acme Energy at `acmeenergy.com`) currently
requires opening the Firestore console and hand-writing a document. This violates two hard axioms:

- **API FIRST**: platform state must only be modified through the versioned API, not via console.
- **THIN CLIENT, FAT PROTOCOL**: "curl + Firebase token + JSON by hand" is a debug tool, not an
  onboarding flow.

Concretely, before documents can be stored in a client-specific GCS bucket, a developer must:

1. Open the Firestore console in the right GCP project
2. Navigate to `clients/` and create a document at the right domain key with the right field names
3. Add `sa-platform` IAM on the bucket separately (also manual, no recipe)

There is no way to do this from the terminal, verify it, or hand it to a non-GCP-expert.

---

## Goals

**Primary:** A platform admin can onboard a new client domain entirely from `aiplatform client` CLI
commands with no Firestore console access.

**Success metrics:**
- `aiplatform client set acmeenergy.com --documents-bucket acmeenergy-docs` creates or updates
  the Firestore record and prints the new config in <2s
- `aiplatform client list` shows all registered domains in a table
- `aiplatform client get acmeenergy.com` returns current config as JSON
- `aiplatform client delete acmeenergy.com` removes the record with a confirmation prompt
- Backend unit tests cover the CRUD routes; CLI tests cover the four commands

---

## Non-Goals

- **Bucket provisioning.** Creating the GCS bucket itself and adding IAM remain a gcloud / Terraform
  step (documented in the onboarding recipe below). The API manages the Firestore record; it does not
  touch GCS IAM.
- **Per-tenant skill visibility, channel config, or RAG settings.** `ClientConfig` will grow these
  fields in a future sprint; this doc scopes only `documents_bucket` and `display_name`.
- **Self-service client registration.** Admin-only API; not an end-user feature.

---

## Axiom Alignment

| # | Axiom | Score | Note |
|---|-------|-------|------|
| 1 | INSTANT FEEL | 0 | Admin-only operation, not on the chat latency path |
| 2 | EARNED TRUST | 0 | No factual claims or AI outputs involved |
| 3 | SKILLS, NOT FEATURES | 0 | Platform infrastructure; no end-user skill surface |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model inference involved |
| 5 | GRACEFUL DEGRADATION | +1 | Bucket fallback to `DOCUMENTS_BUCKET` env var already in place; API lets admins configure the override explicitly rather than requiring env changes |
| 6 | PROTOCOL OVER CUSTOM | +1 | No custom protocol invented; standard REST CRUD over existing Firestore collection using existing auth machinery |
| 7 | API FIRST | +1 | Closes the gap: replaces Firestore-console-as-interface with a versioned FastAPI route + CLI client |
| 8 | OBSERVABLE BY DEFAULT | +1 | Admin routes log all mutations (domain, who, what changed) to Cloud Logging; CLI prints structured JSON |
| 9 | SECURE BY CONSTRUCTION | +1 | Gated on `aitana-admin` Firebase group tag (existing custom claim); no new credential surface |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | CLI is a thin httpx wrapper; all business logic in the FastAPI handler |
| | **Net Score** | **+6** | Acceptable — proceed |

---

## Standards Compliance

Custom REST CRUD is appropriate here — there is no published standard for tenant configuration
management. The CLI follows the existing Click subcommand pattern used throughout
`cli/aiplatform/commands/`.

---

## Design

### Data Model

`clients/{domain}` in Firestore — document ID is the email domain (e.g. `acmeenergy.com`).

Existing `ClientConfig` in [backend/db/clients.py](../../../backend/db/clients.py) — **no schema
changes needed**:

```python
class ClientConfig(BaseModel):
    domain: str
    documents_bucket: str | None = None
    display_name: str = ""
```

### Backend — Admin Routes

New file: `backend/admin/clients.py`, router mounted at `/api/admin/clients`.

```
GET    /api/admin/clients              → list all ClientConfig records
GET    /api/admin/clients/{domain}     → get one (404 if not found)
PUT    /api/admin/clients/{domain}     → upsert (create or update)
DELETE /api/admin/clients/{domain}     → delete (404 if not found)
```

Auth: a new `_require_admin` FastAPI dependency that checks `"aitana-admin" in user.group_tags`.
This is the **human-caller variant** of the admin guard — it uses standard Firebase user JWTs
(same `get_current_user` as all other routes), unlike the SA-allowlist guard in `admin/auth.py`
which targets Cloud Build SAs.

```python
# backend/admin/clients.py (representative sketch — actual impl may vary)

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import User, get_current_user
from db.clients import ClientConfig, get_client_sync
from db.firestore import delete_document, query_documents, set_document

router = APIRouter(prefix="/api/admin/clients", tags=["admin-clients"])


def _require_admin(user: User = Depends(get_current_user)) -> User:
    if "aitana-admin" not in user.group_tags:
        raise HTTPException(status_code=403, detail="aitana-admin group required")
    return user


class ClientConfigUpdate(BaseModel):
    documents_bucket: str | None = None
    display_name: str = ""
```

PUT request body is `ClientConfigUpdate`; returns the full `ClientConfig` after write.
All mutations are logged: `admin.clients: upsert domain=%s by uid=%s`.

### CLI Surface

New command group: `aiplatform client` — `cli/aiplatform/commands/client.py` (~120 LOC).
Registered in `cli/aiplatform/cli.py` alongside `bucket`, `docs`, `skill`.

```
aiplatform client list
    → table: domain | display_name | documents_bucket

aiplatform client get <domain>
    → JSON output of ClientConfig

aiplatform client set <domain>
    --documents-bucket <gcs-bucket-name>   (optional)
    --display-name <name>                  (optional)
    → calls PUT /api/admin/clients/{domain}; prints updated config

aiplatform client delete <domain> [--yes]
    → confirmation prompt unless --yes; calls DELETE
```

Follows the exact pattern of [cli/aiplatform/commands/bucket.py](../../../cli/aiplatform/commands/bucket.py).

### Auth Model

All four routes use **Firebase user JWTs** — standard `Authorization: Bearer <id_token>`.
A human admin calls them after `aiplatform auth login`; no `gcloud` incantation needed.
The `aitana-admin` group tag is already set on Mark's Firebase account and the `whoami-test`
admin test user. No new Firebase custom claims work required.

### Full Onboarding Recipe

After this feature ships, onboarding Acme Energy becomes:

```bash
# 1. Create GCS bucket (one-time gcloud or Terraform)
gcloud storage buckets create gs://acmeenergy-docs \
  --location=EU --uniform-bucket-level-access \
  --project=aitana-documents

# 2. Grant sa-platform objectAdmin on the bucket (all envs)
for env in dev test production; do
  gcloud storage buckets add-iam-policy-binding gs://acmeenergy-docs \
    --member="serviceAccount:sa-platform@your-project-id-${env}.iam.gserviceaccount.com" \
    --role="roles/storage.objectAdmin" \
    --project=aitana-documents
done

# 3. Register the client config — replaces Firestore console
aiplatform --env dev client set acmeenergy.com \
  --documents-bucket acmeenergy-docs \
  --display-name "Acme Energy"

# 4. Verify
aiplatform --env dev client get acmeenergy.com
```

Steps 1–2 remain gcloud (IAM is infrastructure, not application state). Step 3 was previously
Firestore console; it is now a single CLI command.

---

## API Changes

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/admin/clients` | `aitana-admin` group tag | List all client configs |
| `GET` | `/api/admin/clients/{domain}` | `aitana-admin` group tag | Get one client config |
| `PUT` | `/api/admin/clients/{domain}` | `aitana-admin` group tag | Upsert client config |
| `DELETE` | `/api/admin/clients/{domain}` | `aitana-admin` group tag | Delete client config |

No existing endpoints are modified. The existing `db/clients.py` read path is unchanged.

---

## Migration

No data migration needed — `clients` collection already exists and the schema is unchanged. The
first `aiplatform client set` call for an existing hand-crafted document will overwrite it cleanly.

**Rollback:** Remove `backend/admin/clients.py` and its import from `fast_api_app.py`. No Firestore
schema changes to revert.

---

## Testing Strategy

### Backend (pytest) — `backend/tests/api_tests/test_admin_clients.py`

- `GET /api/admin/clients` → 200 + list for admin user, 403 for non-admin
- `PUT /api/admin/clients/example.com` → upserts record, returns full config
- `GET /api/admin/clients/example.com` → 200 after upsert, 404 for unknown domain
- `DELETE /api/admin/clients/example.com` → removes record; second delete returns 404
- Non-admin user gets 403 on all four routes (all Firestore calls mocked)

### CLI (pytest + mock backend) — `cli/tests/test_cli_client.py`

- `client list` renders table from mocked GET response
- `client get example.com` prints JSON
- `client set example.com --documents-bucket b` calls PUT with correct payload
- `client delete example.com --yes` calls DELETE without prompt
- `client delete example.com` (no `--yes`) prompts for confirmation; aborts on 'n'

---

## Implementation Plan

| Step | Work | Est |
|------|------|-----|
| 1 | `backend/admin/clients.py` — 4 routes + `_require_admin` dep | 2h |
| 2 | Wire router into `fast_api_app.py` | 15m |
| 3 | `backend/tests/api_tests/test_admin_clients.py` — 8 tests | 1h |
| 4 | `cli/aiplatform/commands/client.py` — 4 commands | 1.5h |
| 5 | Register `client` group in `cli/aiplatform/cli.py` | 10m |
| 6 | `cli/tests/test_cli_client.py` — 5 tests | 1h |
| | **Total** | **~6h** |

---

## Success Criteria

- [ ] `aiplatform --env dev client set acmeenergy.com --documents-bucket acmeenergy-docs --display-name "Acme Energy"` succeeds and prints the config
- [ ] `aiplatform --env dev client list` shows the new entry in a table
- [ ] `aiplatform --env dev client get acmeenergy.com` returns full config as JSON
- [ ] `aiplatform --env dev client delete acmeenergy.com` removes it (with confirmation)
- [ ] Non-admin Firebase user gets 403 on all four backend routes
- [ ] `pytest tests/api_tests/test_admin_clients.py` — all 8 tests pass
- [ ] `pytest cli/tests/test_cli_client.py` — all 5 tests pass
- [ ] `make lint && make test-fast` green

---

## Related Documents

- [db/clients.py](../../../backend/db/clients.py) — existing read-only client resolution
- [admin/auth.py](../../../backend/admin/auth.py) — existing SA-based admin auth (seed endpoint)
- [cli/aiplatform/commands/bucket.py](../../../cli/aiplatform/commands/bucket.py) — CLI pattern to follow
- [local-dev-cli.md](../../v6.1.0/local-dev-cli.md) — CLI design principles
- [rag-document-corpus.md](implemented/rag-document-corpus.md) — sibling v6.3.0 feature that uses the same bucket config

---

## Implementation Report

**Completed**: 2026-06-02
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

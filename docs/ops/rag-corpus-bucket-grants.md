# RAG corpus → customer bucket grants (cross-org, manual)

**Status:** operational runbook · **Owner:** platform · **Last updated:** 2026-07-09

## What / why

When a deployed backend has `RAG_DOCUMENTS_ENABLED=true`, attaching a document to
a chat imports it into a per-user **Vertex AI RAG corpus**. The import is done by
the project's **Vertex RAG service agent**:

```
service-{PROJECT_NUMBER}@gcp-sa-vertex-rag.iam.gserviceaccount.com
```

That agent must be able to **read the source GCS bucket**. For the ONE customer
the source is `gs://multivac-acme-energy-bucket`. Without the grant, every
document turn logs a `403 storage.buckets.get` and (pre-fix) died with no reply:

```
doc loader (RAG): failed to import doc:… 403 … service-…@gcp-sa-vertex-rag…
  does not have storage.buckets.get access to …/multivac-acme-energy-bucket
```

(The backend now **degrades gracefully** on this — see
[`_rag_loader` / `_inject_rag_doc_id_hint`](../../backend/adk/callbacks.py): it
tells the AI "search degraded, use doc_id tools, warn the user" instead of
hanging. The grant restores semantic `search_documents`; `compare_ppa_contracts`
/ `extract_ppa_clauses` read the parsed doc directly and don't need it.)

## Why this can't be Terraform (in our repos)

- `multivac-acme-energy-bucket` lives in project `multivac-acme-energy`,
  **org `53196723689`**. It is **not managed by any Terraform we control** (a
  legacy v5 customer bucket).
- The aitana infra terraform (`sunholo-data/multivac-aitana`) applies as
  `terraform@multivac-deploy` with a **folder cascade in org `1076453579055`** —
  it has no IAM reach into the customer's org. A `google_storage_bucket_iam_member`
  there would fail the apply.
- Codifying it *would* require either (a) the customer project's own IaC (a
  different org/domain), or (b) granting `terraform@multivac-deploy` cross-org
  storage-admin on `multivac-acme-energy` + a provider alias — a larger
  governance step. Until then, this is a **deliberate manual grant**.

## The grant (per environment)

Read-only, two roles (`legacyBucketReader` supplies the failing `storage.buckets.get`;
`objectViewer` supplies object read/list). Run with an account that has
`storage.admin` on the bucket (e.g. `owner@yourcompany.com`):

> **This applies to EVERY bucket RAG imports from — run the block once per
> bucket.** Known instances:
> - `gs://multivac-acme-energy-bucket` (ONE customer docs) — ✅ all 3 envs 2026-07-09
> - `gs://aitana-documents-bucket` (legacy shared fallback for unmapped-domain /
>   internal users — `db/clients.py` `DOCUMENTS_BUCKET` default; lives in legacy
>   project `aitana-documents`) — ⏳ **pending all 3 envs**, found 2026-07-23 when
>   an internal tester's uploaded doc degraded RAG search on test (4× `403 failed
>   to list files, folders`). Any tenant-mapped `documents_bucket` added in
>   Firestore `clients/{domain}` needs the same treatment.

> **This applies to EVERY bucket RAG imports from — run the block once per
> bucket.** Known instances:
> - `gs://multivac-acme-energy-bucket` (ONE customer docs) — ✅ all 3 envs 2026-07-09
> - `gs://aitana-documents-bucket` (legacy shared fallback for unmapped-domain /
>   internal users — `db/clients.py` `DOCUMENTS_BUCKET` default; lives in legacy
>   project `aitana-documents`) — ⏳ **pending all 3 envs**, found 2026-07-23 when
>   an internal tester's uploaded doc degraded RAG search on test (4× `403 failed
>   to list files, folders`). Any tenant-mapped `documents_bucket` added in
>   Firestore `clients/{domain}` needs the same treatment.

```bash
BUCKET=gs://multivac-acme-energy-bucket   # ...or gs://aitana-documents-bucket   # ...or gs://aitana-documents-bucket
grant() {  # $1 = project number
  local SA="serviceAccount:service-$1@gcp-sa-vertex-rag.iam.gserviceaccount.com"
  for ROLE in roles/storage.objectViewer roles/storage.legacyBucketReader; do
    gcloud storage buckets add-iam-policy-binding "$BUCKET" --member="$SA" --role="$ROLE"
  done
}
grant 556246783252   # dev   (your-project-id)          ✅ done 2026-07-09
grant 425997328463   # test  (your-project-id-test)         ✅ done 2026-07-09
grant 251882854450   # prod  (your-project-id-prod)    ✅ done 2026-07-09
```

Verify:
```bash
gcloud storage buckets get-iam-policy gs://multivac-acme-energy-bucket \
  --format=json | python3 -c "import json,sys;[print(b['role']) for b in \
  json.load(sys.stdin)['bindings'] if any('gcp-sa-vertex-rag' in m for m in b['members'])]"
```

## Gotchas

- **The RAG service agent may not exist until the env first uses Vertex RAG.**
  If the binding is rejected with "member does not exist", deploy the env, drive
  one document turn (it degrades gracefully), then apply the grant.
- **Per-env, per-project-number.** Each env has a distinct RAG service agent.
- **Confidential data flow.** This imports ONE's confidential PPAs into that
  env's RAG corpus — inside the GCP project edge (within the privacy boundary),
  but a deliberate per-env decision. Grant only for envs that host the customer.

## Do this at env-cut

Part of cutting a new env that hosts the ONE customer — see
[env-cut-runbook.md](env-cut-runbook.md).

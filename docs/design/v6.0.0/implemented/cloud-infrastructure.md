# Cloud Infrastructure

**Status**: Implemented
**Priority**: P0 (High)
**Estimated**: 2 days
**Actual**: 1 day (code only — Terraform + deploy deferred)
**Scope**: Backend (infrastructure + config)
**Dependencies**: None (foundational — other design docs depend on this)
**Created**: 2026-04-10
**Last Updated**: 2026-04-21

## Problem Statement

v6 introduces ADK-native sessions, memory, artifacts, code execution, and observability — all of which require GCP services that v5 doesn't use. The existing design docs reference these services (Firestore session collections, GCS artifact prefixes, Vertex AI Memory, OTEL exporters) without a unified view of what infrastructure exists, what's new, and what needs provisioning.

Without this doc, each design doc assumes its services exist — leading to runtime failures when the infrastructure hasn't been set up.

**Current State:**
- v5 infrastructure is fully provisioned: 3 GCP projects (dev/test/prod), Cloud Run, Firestore, GCS, Vertex AI Search, Firebase Auth, AlloyDB, BigQuery, Pub/Sub, Secret Manager
- v6 backend code has zero infrastructure provisioned beyond what v5 already provides
- Design docs reference services that don't exist yet (ADK session DB, artifact bucket prefix, OTEL exporters)
- No single document maps ADK features → GCP services → provisioning steps

**Impact:**
- Blocks all backend integration testing (sessions, memory, artifacts need real backends)
- Blocks deployment (Cloud Run env vars, API enablement, service account permissions)
- Risk of discovering missing infrastructure during sprint execution

## Goals

**Primary Goal:** Produce a complete infrastructure map for v6 — what exists (reuse), what's new (provision), and what's deferred — so that sprint execution has zero infrastructure surprises.

**Success Metrics:**
- Every GCP service referenced in design docs is mapped to a provisioning status (exists / new / deferred)
- All new Firestore collections documented with indexes
- All new GCS prefixes documented
- All API enablement requirements listed
- All environment variables documented
- `make dev` works locally with zero GCP dependencies (in-memory fallbacks)

**Non-Goals:**
- Terraform/IaC implementation (handled by existing Multivac infra repo)
- Cost estimation or budget planning
- Multi-region or HA design (single region: `europe-west1`)
- Kubernetes / GKE (v6 stays on Cloud Run)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Infrastructure, not on latency path |
| 2 | EARNED TRUST | 0 | No user-facing claims |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure invisible to users |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Model selection is unaffected |
| 5 | GRACEFUL DEGRADATION | +1 | In-memory fallbacks for all services enable local dev without GCP |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses ADK-native service backends (VertexAiSessionService, GcsArtifactService) — no custom persistence |
| 7 | API FIRST | 0 | Infrastructure, not API surface |
| 8 | OBSERVABLE BY DEFAULT | +1 | OTEL → Cloud Trace + Vertex AI Model Observability — pure GCP, zero extra infra |
| 9 | SECURE BY CONSTRUCTION | +1 | SA credentials, Secret Manager, least-privilege IAM |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Purely backend |
| | **Net Score** | **+4** | Threshold: >= +4 |

## Design

### Overview

v6 reuses most v5 infrastructure (same GCP projects, Cloud Run services, Firestore, GCS, Vertex AI Search, Firebase Auth). New infrastructure is limited to: (1) Vertex AI Agent Engine for sessions + memory (pay-per-use), (2) new Firestore collections + indexes, (3) new GCS artifact prefix, and (4) API enablement for ADK features. Observability is pure GCP — Cloud Trace + Agent Engine tracing + Vertex AI Model Observability — no additional services to deploy.

### Infrastructure Inventory

#### Carried From v5 (No Changes)

| Service | Resource | Used By |
|---------|----------|---------|
| **GCP Projects** | `your-project-id`, `your-project-id-test`, `your-project-id-prod` | All |
| **Cloud Run (v5, still live)** | `frontend` (Next.js), `backend-api` (Python/Sunholo) | v5 traffic until DNS cutover |
| **Cloud Run (v6, new parallel services)** | `platform-frontend` (Next.js), `platform-backend` (FastAPI + ADK) | v6 deployment — added by CI-WIRE sprint |
| **Firebase Auth** | Same Firebase projects (shared with v5 during parallel operation) | Auth |
| **Firestore** | v5 collections (`assistants/`, `templates/`, `users/`, `tags/`) — read-only from v6 | Migration |
| **GCS** | `LOGS_BUCKET_NAME` bucket, `aitana-documents-bucket` | Artifacts, file browser |
| **Vertex AI Search** | Datastore `aitana3` + Discovery Engine API | `ai_search` tool |
| **AlloyDB** | Cluster `aitana-alloydb-cluster-20250121` in `aitana-alloydb` project | Not used by v6 (available for future) |
| **Secret Manager** | All existing secrets (see full list below) | API keys, credentials |
| **Artifact Registry** | `llm-ops` repo in `your-deploy-project-id` | Docker images |
| **Service Accounts** | `sa-emissary@{project}.iam.gserviceaccount.com` | v5 Cloud Run identity (not used by v6) |
| **Pub/Sub** | `qna-to-pubsub-bq-archive`, `app-to-pubsub-chunk`, etc. | Analytics, doc ingestion |
| **BigQuery** | `01RAW_llmops`, `02STAGING_llmops` datasets | Analytics |
| **Region** | `europe-west1` | All services |

#### New For v6

| Service | Resource | Used By | Provisioning |
|---------|----------|---------|-------------|
| **Vertex AI Agent Engine** | ReasoningEngine resource (pay-per-use) | ADK sessions + memory | Create via `client.agent_engines.create()` |
| **Firestore** | `skills/` collection + composite indexes | Skills data model | Create collection + indexes |
| **Firestore** | `tool_permissions/` collection | Auth & permissions | Create collection + seed data |
| **GCS** | `artifacts/v6/` prefix in `LOGS_BUCKET_NAME` | ADK ArtifactService | No provisioning (prefix auto-created) |
| **Service Account** | `platform@{project}.iam.gserviceaccount.com` | v6 Cloud Run identity | Terraform (`google_service_account`) |
| **Cloud Trace** | OTEL exporter via `get_gcp_exporters()` | ADK distributed tracing + Agent Engine tracing | Enable API (`cloudtrace.googleapis.com`) |
| **Cloud Monitoring** | OTEL metrics exporter | ADK metrics (token usage, cost) | Enable API (`monitoring.googleapis.com`) |

#### Deferred (Not in v6.0.0)

| Service | Used By | Why Deferred |
|---------|---------|-------------|
| **Vertex AI RAG Engine** | `VertexAiRagMemoryService` (alternative memory backend) | Agent Engine Memory Bank is sufficient for v6.0.0 |
| **GKE** | `GkeCodeExecutor` (sandboxed code execution) | Use `BuiltInCodeExecutor` (Gemini) + OpenAI containers for now |
| **BigQuery Agent Analytics** | `BigQueryAgentAnalyticsPlugin` | Cloud Trace + Agent Engine tracing sufficient for v6.0.0 |
| **AlloyDB** | `DatabaseSessionService` or Phoenix trace storage | Agent Engine handles sessions; Cloud Trace handles observability — no AlloyDB dependency |
| **Phoenix (Arize)** | Self-hosted LLM trace UI | Cloud Trace + Agent Engine tracing + Vertex AI Model Observability covers the same ground — no extra infra |

### ADK Service Backend Decisions

#### 1. Sessions + Memory → Vertex AI Agent Engine

Both `VertexAiSessionService` and `VertexAiMemoryBankService` share the same Agent Engine resource (ReasoningEngine). Agent Engine is **pay-per-use** — no fixed infrastructure cost, no provisioned clusters.

```python
# backend/adk/session.py

import os
from google.adk.sessions import VertexAiSessionService
from google.adk.memory import VertexAiMemoryBankService

# Agent Engine resource ID — created once per environment
AGENT_ENGINE_ID = os.environ.get("AGENT_ENGINE_ID")

def get_session_service() -> VertexAiSessionService:
    """Get Vertex AI Agent Engine session service (pay-per-use)."""
    return VertexAiSessionService(
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ["GOOGLE_CLOUD_LOCATION"],
        agent_engine_id=AGENT_ENGINE_ID,
    )

def get_memory_service() -> VertexAiMemoryBankService:
    """Get Vertex AI Agent Engine memory service (semantic cross-session recall)."""
    return VertexAiMemoryBankService(
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ["GOOGLE_CLOUD_LOCATION"],
        agent_engine_id=AGENT_ENGINE_ID,
    )
```

**Why Agent Engine over AlloyDB?**
- **Pay-per-use** — no fixed-cost provisioned cluster. AlloyDB runs 24/7 whether or not sessions are active.
- **Sessions + memory in one resource** — a single Agent Engine ID handles both services. No separate database provisioning.
- **Managed by Google** — no schema migrations, no connection pooling, no scaling config.
- **Native ADK integration** — `VertexAiSessionService` and `VertexAiMemoryBankService` are first-party ADK services.

**Agent Engine provisioning** (via Terraform in `multivac-aitana` infra repo):

```hcl
# infrastructure/modules/agent-engine/main.tf

resource "google_vertex_ai_reasoning_engine" "platform" {
  project      = var.project_id
  location     = var.region  # europe-west1
  display_name = "platform"
  description  = "Aitana Platform v6 agent engine"
}

output "agent_engine_id" {
  value = google_vertex_ai_reasoning_engine.platform.name
}
```

The `AGENT_ENGINE_ID` env var for Cloud Run is set from the Terraform output, per environment.

**Infra repo:** `<local-path>`

**Local dev fallback:** `InMemorySessionService` + `InMemoryMemoryService` (no GCP required).

#### 3. Artifacts → GCS (existing bucket)

```python
# backend/adk/session.py

from google.adk.artifacts import GcsArtifactService

def get_artifact_service() -> GcsArtifactService:
    return GcsArtifactService(
        bucket_name=settings.LOGS_BUCKET_NAME,  # Same bucket as v5
    )
```

Artifacts stored at: `gs://{LOGS_BUCKET_NAME}/{app_name}/{user_id}/{session_id}/{filename}/{version}`

No new bucket needed — reuses the existing logging/ops bucket.

#### 4. Observability → Pure GCP (Cloud Trace + Prompt-Response Logging + Model Observability)

Zero additional infrastructure. Four GCP-native layers cover all observability needs.

**Setup pattern** — high-level: enable via `otel_to_cloud=True` when constructing the FastAPI app. The flag wires Cloud Trace exporters into the ADK runner automatically.

```python
# backend/fast_api_app.py (sketch — verify exact arg name against ADK docs)

from google.adk.cli.fast_api import get_fast_api_app

app = get_fast_api_app(
    agent_dir="backend",
    otel_to_cloud=True,  # Cloud Trace exporters wired automatically
    web=True,
)
```

The lower-level `get_gcp_exporters()` API is also available if more control is needed (custom sampling, additional metric pipelines).

**Observability stack:**

| Layer | GCP Service | What It Provides | Default State | Cost |
|-------|-------------|-----------------|---------------|------|
| **Cloud Trace** | Cloud Trace (via ADK OTEL spans) | DAG visualization of `invocation` → `agent_run` → `call_llm` / `execute_tool` spans, inputs/outputs, session/span views in Cloud Console | Always on | Pay-per-use (trace ingestion) |
| **Prompt-Response Logging** | Cloud Logging bucket + GCS NDJSON + linked BigQuery dataset | Structured GenAI interaction logs (model name, tokens, timing); SQL-queryable for compliance/audit | Enabled when deployed (disabled locally unless `LOGS_BUCKET_NAME` set) | Logs ingestion + GCS storage |
| **Model metrics** | Vertex AI Model Observability | QPS, token throughput, first-token latency, error rates — **automatic** for all Vertex AI calls | Always on | Free (built into Vertex AI) |
| **Custom metrics** | Cloud Monitoring | OTEL counters: cost per model, per-skill usage, tool invocations | Configured in `backend/observability/llm_metrics.py` | Pay-per-use |

**Span hierarchy** (from ADK OTEL emission):
```
invocation
  └── agent_run (one per agent in the chain)
        ├── call_llm (model request/response)
        └── execute_tool (tool execution)
```

View traces: **Cloud Console → Trace → Trace explorer** (or Agent Engine → Traces view for Agent Engine deployments).

**Content capture modes** — `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` has three modes:

| Value | Behavior | Use For |
|-------|----------|---------|
| `true` | Full prompt + response captured | **v6 default (all environments)** — full debuggability, eval traces, and audit trail |
| `NO_CONTENT` | Metadata only (model, tokens, timing) | If a customer requires zero PII in telemetry (per-deployment override) |
| `false` | Disabled entirely | Performance-critical paths if telemetry overhead matters |

**v6 default: `true` in all environments.** Our privacy stance restricts data leaving our GCP project (which is why Langfuse Cloud was rejected); Cloud Trace, the GenAI logging bucket, and the linked BigQuery dataset are all inside our own GCP project, so full content capture is acceptable and operationally valuable. Per-customer overrides can use `NO_CONTENT` if a contract requires it.

##### Prompt-Response Logging Infrastructure

This tier needs Terraform-provisioned resources in `multivac-aitana`. Pattern adapted from the Agents CLI scaffolding (`deployment/terraform/telemetry.tf`):

| Resource | Purpose |
|----------|---------|
| Cloud Logging bucket (`platform-genai-telemetry`) | 10-year retention, analytics enabled, dedicated to GenAI logs (separate from app logs) |
| Log sinks | Route GenAI inference + feedback logs from default `_Default` bucket → telemetry bucket |
| Linked BigQuery dataset (`platform_genai_telemetry_logs`) | Cloud Logging bucket linked to BigQuery for SQL-queryable inference logs |
| GCS bucket (`{PROJECT_ID}-platform-logs`) | Stores completions as NDJSON at `completions/` path |
| BigQuery dataset (`platform_telemetry`) | External tables over the GCS NDJSON data |
| BigQuery connection | SA grant for BigQuery to read from GCS |

**Naming caveat:** BigQuery datasets cannot contain hyphens. Convert `platform` → `platform` for BQ dataset names.

**Verification after deploy:**
```bash
PROJECT_ID="your-project-id"

# GCS NDJSON completions
gsutil ls gs://${PROJECT_ID}-platform-logs/completions/

# Cloud Logging telemetry bucket
gcloud logging buckets describe platform-genai-telemetry \
  --location=europe-west1 --project=${PROJECT_ID}

# Query BigQuery for recent completions
bq query --use_legacy_sql=false \
  "SELECT * FROM \`${PROJECT_ID}.platform_telemetry.completions\` LIMIT 10"
```

##### BigQuery Agent Analytics Plugin (Deferred)

Optional plugin (`google.adk.plugins.BigQueryAgentAnalyticsPlugin`) that logs structured agent events directly to BigQuery via the Storage Write API. Captures tool provenance (LOCAL, MCP, SUB_AGENT, A2A, TRANSFER_AGENT) — directly relevant to v6's multi-protocol architecture. Auto-schema upgrades, GCS offloading for multimodal content.

**Status:** Deferred to v6.1.0. The Cloud Trace + Prompt-Response Logging tiers above are sufficient for v6.0.0 launch. Re-evaluate once we have real production traffic and want conversational analytics dashboards.

**Cost tracking** (the one gap vs. Langfuse/Phoenix — handled via custom metric):

```python
# backend/observability/llm_metrics.py

from opentelemetry import metrics

meter = metrics.get_meter("aitana.llm")

cost_counter = meter.create_counter(
    "llm.cost.total",
    description="Estimated cost in USD",
    unit="USD",
)

def record_llm_cost(model: str, input_tokens: int, output_tokens: int):
    """Record LLM cost metric (called from after_agent callback)."""
    cost_counter.add(
        _estimate_cost(model, input_tokens, output_tokens),
        {"model": model},
    )
```

**Why pure GCP over Langfuse / Phoenix?**
- Langfuse v3 requires ClickHouse + Redis — can't deploy to Cloud Run
- Langfuse Cloud (SaaS) sends trace data off-GCP — violates privacy requirements
- Phoenix requires AlloyDB/PostgreSQL — extra infrastructure dependency
- Agent Engine tracing provides the same DAG visualization natively (already provisioned for sessions)
- Vertex AI Model Observability provides token/latency dashboards automatically
- Zero additional services to deploy, manage, or pay for
- ADK evals (`adk eval`) cover the evaluation use case
- v5's custom `langfuse.py` wrapper is dropped — ADK emits OTEL natively

#### 5. Code Execution → BuiltInCodeExecutor + OpenAI Containers

| Model | Code Executor | GCP Service |
|-------|--------------|-------------|
| Gemini 2.0+ | `BuiltInCodeExecutor` (model-native sandbox) | None (model-side) |
| Claude / OpenAI | OpenAI Containers (from Rockwool) | None (OpenAI API) |

No GCP infrastructure needed for code execution. `BuiltInCodeExecutor` runs entirely within the Gemini model call. OpenAI containers are managed by OpenAI's API.

### Firestore Collections & Indexes

#### New Collections

```
# v6 collections (alongside v5 — same Firestore database)

skills/{skillId}                          # Skill configs (Layer 1 + 2)
skills/{skillId}/messages/{msgId}         # Chat messages per skill
tool_permissions/{userId_or_domain}       # Tool access control
```

#### Composite Indexes (`firestore.indexes.json`)

```json
{
  "indexes": [
    {
      "collectionGroup": "skills",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "tags", "arrayConfig": "CONTAINS"},
        {"fieldPath": "usageCount", "order": "DESCENDING"}
      ]
    },
    {
      "collectionGroup": "skills",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "accessControl.type", "order": "ASCENDING"},
        {"fieldPath": "updatedAt", "order": "DESCENDING"}
      ]
    },
    {
      "collectionGroup": "skills",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "ownerId", "order": "ASCENDING"},
        {"fieldPath": "updatedAt", "order": "DESCENDING"}
      ]
    },
    {
      "collectionGroup": "skills",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "accessControl.type", "order": "ASCENDING"},
        {"fieldPath": "accessControl.domain", "order": "ASCENDING"},
        {"fieldPath": "updatedAt", "order": "DESCENDING"}
      ]
    },
    {
      "collectionGroup": "skills",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "featured", "order": "ASCENDING"},
        {"fieldPath": "usageCount", "order": "DESCENDING"}
      ]
    },
    {
      "collectionGroup": "skills",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "v5AssistantId", "order": "ASCENDING"}
      ]
    }
  ]
}
```

### GCP APIs to Enable

Most are already enabled from v5. Verify these are active per environment:

| API | Already Enabled? | Needed For |
|-----|-----------------|-----------|
| `aiplatform.googleapis.com` | Yes | Vertex AI (Gemini, Claude via Vertex, Search) |
| `discoveryengine.googleapis.com` | Yes | Vertex AI Search (ai_search tool) |
| `firestore.googleapis.com` | Yes | Firestore |
| `storage.googleapis.com` | Yes | GCS artifacts |
| `run.googleapis.com` | Yes | Cloud Run |
| `secretmanager.googleapis.com` | Yes | Secrets |
| `cloudtrace.googleapis.com` | **Verify** | OTEL → Cloud Trace (may not be enabled) |
| `monitoring.googleapis.com` | **Verify** | OTEL → Cloud Monitoring metrics |
| `alloydb.googleapis.com` | Yes | Not used by v6 (legacy) |
| `generativelanguage.googleapis.com` | Yes | Gemini API |
| `texttospeech.googleapis.com` | Yes | Future: Gemini Live TTS |

### Secret Manager Secrets

**All existing secrets carry over unchanged.** No new secrets required for v6.0.0.

Key secrets used by v6 backend:

| Secret | Purpose |
|--------|---------|
| `ANTHROPIC_API_KEY` | Claude models (if using direct API via LiteLlm) |
| `OPENAI_API_KEY` | OpenAI models via LiteLlm |
| `TELEGRAM_BOT_TOKEN` | Telegram channel |
| `MAILGUN_API_KEY` + `MAILGUN_WEBHOOK_SECRET` | Email channel |
| `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` | WhatsApp channel |
| `FIREBASE_ENV` | Firebase client config (build-time) |

**Note:** Claude via Vertex AI uses service account identity (ADC), not `ANTHROPIC_API_KEY`. The API key secret is only needed if using `LiteLlm("anthropic/...")` for direct Anthropic API access.

**Removed from v5:** `LANGFUSE_URL`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY` (replaced by GCP-native observability). `ALLOYDB_CONNECTION_STRING` (not used by v6 — sessions use Agent Engine, observability uses Cloud Trace).

### Cloud Run Environment Variables

New env vars for v6 (added to `cloudbuild.yaml` deployment step):

| Variable | Value | Purpose |
|----------|-------|---------|
| `AGENT_ENGINE_ID` | Per-environment Agent Engine resource ID | VertexAiSessionService + VertexAiMemoryBankService |
| `ADK_ARTIFACT_BUCKET` | `${LOGS_BUCKET_NAME}` | GcsArtifactService bucket |
| `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY` | `true` | Enable Agent Engine tracing → Cloud Trace |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | `true` (all envs) | Full prompt/response capture — internal-only, our privacy boundary is the GCP project edge (see Observability section) |
| `LOGS_BUCKET_NAME` | `${PROJECT_ID}-platform-logs` | GCS bucket for prompt-response NDJSON completions |
| `GENAI_TELEMETRY_PATH` | `completions` (default — usually omit) | Optional override of GCS upload path within bucket |

**Existing env vars that v6 continues to use:**

| Variable | Value |
|----------|-------|
| `GOOGLE_CLOUD_PROJECT` | `${_PROJECT_ID}` |
| `GOOGLE_CLOUD_LOCATION` | `europe-west1` |
| `GOOGLE_GENAI_USE_VERTEXAI` | `true` |

### Service Account Permissions

`platform@{project}.iam.gserviceaccount.com` needs these roles (most are already granted):

| Role | Already Granted? | Needed For |
|------|-----------------|-----------|
| `roles/datastore.user` | Yes | Firestore read/write |
| `roles/storage.objectAdmin` | Yes | GCS artifacts |
| `roles/aiplatform.user` | Yes | Vertex AI (Gemini, Search) |
| `roles/secretmanager.secretAccessor` | Yes | Read secrets |
| `roles/aiplatform.serviceAgent` | **Verify** | Agent Engine session/memory/tracing operations |
| `roles/cloudtrace.agent` | **Verify** | Write traces to Cloud Trace |
| `roles/monitoring.metricWriter` | **Verify** | Write metrics to Cloud Monitoring |

### Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        GCP Project                               │
│                   (your-project-id-{env})                        │
│                                                                  │
│  ┌─────────────┐     ┌──────────────────┐    ┌───────────────┐  │
│  │  Cloud Run   │     │    Firestore     │    │     GCS       │  │
│  │             │     │                  │    │               │  │
│  │platform-   │     │  skills/         │    │  artifacts/   │  │
│  │  frontend   │     │  tool_permissions│    │    v6/        │  │
│  │platform-   │────▶│  users/          │    │               │  │
│  │  backend    │     │                  │    │               │  │
│  └──────┬──────┘     └──────────────────┘    └───────────────┘  │
│         │                                                        │
│         │            ┌──────────────────┐    ┌───────────────┐  │
│         │            │  Agent Engine    │    │  Vertex AI    │  │
│         ├───────────▶│  (Vertex AI)     │    │               │  │
│         │            │  Sessions        │    │  Search (ds)  │  │
│         │            │  Memory Bank     │    │  Gemini API   │  │
│         │            │  Tracing         │    │  Claude (VX)  │  │
│         │            └──────────────────┘    │  Model Obs.   │  │
│         │                                    └───────────────┘  │
│         │                                                        │
│         │            ┌──────────────────┐    ┌───────────────┐  │
│         │            │  Firebase Auth   │    │  Cloud Trace  │  │
│         ├───────────▶│                  │    │  + Monitoring │  │
│         │            │  (JWT verify)    │    │  (OTEL sinks) │  │
│         │            └──────────────────┘    └───────────────┘  │
│         │                                                        │
│         │            ┌──────────────────┐                       │
│         └───────────▶│  Secret Manager  │                       │
│                      │  (API keys)      │                       │
│                      └──────────────────┘                       │
└──────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │ Telegram │   │ Mailgun  │   │  Twilio  │
        │ Bot API  │   │  (email) │   │(WhatsApp)│
        └──────────┘   └──────────┘   └──────────┘
```

### Local Development (No GCP)

`make dev` must work without GCP credentials. All ADK services have in-memory fallbacks:

```python
# backend/adk/session.py — dev vs production

import os
from google.adk.sessions import InMemorySessionService, VertexAiSessionService
from google.adk.memory import InMemoryMemoryService, VertexAiMemoryBankService
from google.adk.artifacts import InMemoryArtifactService, GcsArtifactService

AGENT_ENGINE_ID = os.environ.get("AGENT_ENGINE_ID")

def get_session_service():
    if AGENT_ENGINE_ID:
        return VertexAiSessionService(
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.environ["GOOGLE_CLOUD_LOCATION"],
            agent_engine_id=AGENT_ENGINE_ID,
        )
    return InMemorySessionService()

def get_memory_service():
    if AGENT_ENGINE_ID:
        return VertexAiMemoryBankService(
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.environ["GOOGLE_CLOUD_LOCATION"],
            agent_engine_id=AGENT_ENGINE_ID,
        )
    return InMemoryMemoryService()

def get_artifact_service():
    bucket = os.getenv("ADK_ARTIFACT_BUCKET")
    if bucket:
        return GcsArtifactService(bucket_name=bucket)
    return InMemoryArtifactService()
```

## Implementation Plan

### Phase 1: Firestore + GCS (~0.5 day)
- [x] Create `firestore.indexes.json` with all composite indexes
- [ ] Deploy indexes to dev: `firebase deploy --only firestore:indexes --project your-project-id`
- [x] Update `firestore.rules` with `skills/` and `tool_permissions/` rules
- [ ] Deploy rules to dev: `firebase deploy --only firestore:rules --project your-project-id`
- [ ] Verify GCS `artifacts/v6/` prefix works (auto-created on first write)

### Phase 2: Service Account + Agent Engine (~0.5 day)
- [ ] Add `google_service_account` for `platform` to `multivac-aitana` Terraform module
- [ ] Add IAM role bindings (see Service Account Permissions table)
- [ ] Add `google_vertex_ai_reasoning_engine` resource to `multivac-aitana` Terraform module
- [ ] `terraform apply` for dev environment
- [ ] Wire `AGENT_ENGINE_ID` from Terraform output → Cloud Run env var in `cloudbuild.yaml`
- [ ] Test `VertexAiSessionService` session creation
- [ ] Test `VertexAiMemoryBankService` memory recall
- [ ] `terraform apply` for test and production environments

### Phase 3: OTEL + Observability (~1 day)
- [ ] Verify `cloudtrace.googleapis.com`, `monitoring.googleapis.com`, `logging.googleapis.com`, `bigquery.googleapis.com` APIs enabled
- [ ] Grant `platform` Cloud Trace Agent + Monitoring Metric Writer + Logs Writer + BigQuery Data Editor roles
- [ ] Add `telemetry` Terraform module to `multivac-aitana` (Cloud Logging bucket, log sinks, GCS completions bucket, BQ dataset, BQ connection)
- [ ] Wire `otel_to_cloud=True` into `backend/fast_api_app.py` (verify exact ADK arg name)
- [x] Implement `backend/observability/llm_metrics.py` with cost tracking counter
- [x] Add telemetry env vars to `cloudbuild.yaml` (`GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true`, `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`, `LOGS_BUCKET_NAME`)
- [ ] Test traces appear in Cloud Trace console with `invocation` → `agent_run` → `call_llm`/`execute_tool` spans
- [ ] Verify Vertex AI Model Observability dashboard shows token/latency metrics
- [ ] Verify GCS NDJSON completions: `gsutil ls gs://${PROJECT_ID}-platform-logs/completions/`
- [ ] Verify BigQuery linked dataset: `bq query "SELECT * FROM \`${PROJECT_ID}.platform_telemetry.completions\` LIMIT 10"`

### Phase 4: Deploy to Dev (~0.5 day)
- [x] Update `cloudbuild.yaml` with new env vars (`AGENT_ENGINE_ID`, telemetry flags)
- [ ] Deploy to dev Cloud Run
- [ ] Verify: Firestore collections accessible, Agent Engine sessions persist, artifacts write to GCS, traces visible in Cloud Trace
- [ ] Repeat for test environment

## Implementation Notes (2026-04-13)

**What was implemented (code-side):**

1. **Firestore indexes + rules** — `firestore.indexes.json` with 6 composite indexes for skills queries (tags ARRAY_CONTAINS, usageCount DESC, accessControl filters, v5AssistantId lookup). `firestore.rules` updated with `tool_permissions/` collection (authenticated read, admin write).

2. **ADK service factories** — `backend/adk/session.py` with 6 functions: 3 constructors (`get_session_service`, `get_memory_service`, `get_artifact_service`) returning InMemory or Vertex AI/GCS backends based on env vars, plus 3 URI helpers (`get_session_service_uri`, `get_artifact_service_uri`, `get_memory_service_uri`) returning `agentengine://` or `gs://` URIs for `get_fast_api_app()`. `fast_api_app.py` wired to use factories.

3. **LLM cost tracking** — `backend/observability/llm_metrics.py` with OTEL counters for cost and tokens across 8 model pricing tiers (Gemini, Claude, OpenAI). Substring matching handles version suffixes and Vertex AI prefixes.

4. **Cloud Build config** — `cloudbuild.yaml` updated with `AGENT_ENGINE_ID` (secret), `ADK_ARTIFACT_BUCKET`, `LOGS_BUCKET_NAME`, telemetry env vars.

5. **Infrastructure verification** — `backend/scripts/verify_infra.py` checks env vars, GCP API enablement, and SA permissions. `--dry-run` mode lists checks without GCP credentials.

**What remains (Terraform + deploy):**
- Service account creation (`platform@`) and IAM role bindings
- Agent Engine provisioning (Terraform `google_vertex_ai_reasoning_engine`)
- Telemetry Terraform module (Cloud Logging bucket, log sinks, BQ dataset)
- Firebase deploy of indexes and rules to dev/test/prod
- End-to-end verification in Cloud Console

**Test coverage:** 90 tests passing (10 session factory tests, 10 LLM metrics tests, plus existing suite). All tests run without GCP credentials using in-memory fallbacks.

## Migration & Rollout

**Firestore:**
- New collections created alongside v5 collections — no migration needed
- v5 data untouched (read-only from v6)
- `skills/` populated by migration script (see [v5-data-migration.md](v5-data-migration.md))

**Agent Engine:**
- Created via `client.agent_engines.create()` — one resource per environment
- ADK manages session/memory state within the Agent Engine resource

**Rollback Plan:**
- Remove `AGENT_ENGINE_ID` env var → falls back to `InMemorySessionService` + `InMemoryMemoryService`
- Delete `skills/` Firestore collection → v5 `assistants/` still works

## Testing Strategy

### Infrastructure Tests
- [ ] Firestore: create/read/delete skill document in dev project
- [ ] Firestore: composite index query works (list skills by tag + usage)
- [ ] Agent Engine: `VertexAiSessionService` creates session, resumes session
- [ ] GCS: `GcsArtifactService` saves and loads artifact
- [ ] OTEL: traces appear in Cloud Trace after agent invocation

### Local Dev Tests
- [ ] `make dev` starts without GCP credentials
- [ ] `InMemorySessionService` works for chat
- [ ] `InMemoryArtifactService` works for large content
- [ ] All unit tests pass without GCP

## Security Considerations

- Agent Engine uses service account identity (ADC) — no password secrets
- Firestore security rules as safety net (primary enforcement in API layer)
- GCS artifacts scoped per-user-per-session — no cross-user access via bucket structure
- OTEL traces may contain user messages — Cloud Trace access restricted to ops team via IAM
- No new secrets introduced — reuses existing Secret Manager entries

## Performance Considerations

- Agent Engine session read: ~50ms (Vertex AI API, same region)
- Firestore skill config read: ~50ms (cached in-memory with 60s TTL)
- GCS artifact write: ~100ms
- GCS artifact read: ~50ms
- OTEL export: async, non-blocking — zero impact on request latency
- Firestore composite indexes: required for marketplace queries to avoid full-collection scans
- Agent Engine tracing: async, non-blocking — zero impact on request latency

## Success Criteria

- [ ] Firestore indexes deployed to all 3 environments
- [ ] Firestore security rules deployed to all 3 environments
- [ ] Agent Engine created and accessible in all 3 environments
- [ ] `platform` has all required IAM roles
- [ ] Cloud Trace API enabled and receiving agent traces
- [ ] Vertex AI Model Observability dashboard showing token/latency metrics
- [x] `cloudbuild.yaml` updated with new env vars
- [x] `make dev` works without GCP credentials (in-memory fallbacks)
- [ ] Integration test: skill CRUD → session persistence → artifact storage → trace visible in Cloud Trace

## Resolved Questions

- **Content capture in production traces** — **`true` (full content) in all environments.** Our privacy boundary is the **GCP project edge**: data leaving the project to third-party SaaS (Langfuse Cloud, etc.) is forbidden, but full telemetry inside our own GCP project (Cloud Trace, Cloud Logging, BigQuery) is acceptable and operationally valuable. See Axiom #2 (EARNED TRUST) and Axiom #9 (SECURE BY CONSTRUCTION) in [product-axioms.md](../../product-axioms.md).

## Open Questions

- Should OTEL traces be sampled in production (e.g., 10%) to reduce Cloud Trace costs?
- Should we create a separate GCS bucket for v6 artifacts vs the GenAI completions bucket? (Currently both go to `${PROJECT_ID}-platform-logs` — splitting would simplify retention policies.)
- Should we enable BigQuery Agent Analytics Plugin in v6.0.0 for tool provenance tracking, or defer to v6.1.0 once we have real production traffic?


## Related Documents

- [Session & Memory](../session-and-memory.md) — ADK session/memory/artifact service configuration
- [Skills Data Model](skills-data-model.md) — Firestore `skills/` collection schema
- [Auth & Permissions](../auth-and-permissions.md) — Firestore `tool_permissions/` collection, security rules
- [Agent Factory](agent-factory.md) — Model routing, service wiring
- [v5 Data Migration](v5-data-migration.md) — Migration script populates `skills/` from `assistants/`
- [Migration to v6](../v5.0.0/migration-to-v6.md) — Full v5 → v6 architecture decisions
- **Terraform repo:** `<local-path>` — IaC for provisioning

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

# MCP Toolbox for Databases

> ⚠️ **SUPERSEDED (2026-07-17) by
> [v6.14.0/mcp-toolbox-database-gateway.md](../v6.14.0/mcp-toolbox-database-gateway.md).**
> Never built. A same-day spike against ONE's real ENTSO-E dataset disproved this
> doc's core security claim: it scores SECURE BY CONSTRUCTION +2 for
> "parameterized queries (no SQL injection), pre-defined statements", but Toolbox's
> `allowedValues` on a `templateParameter` is a **bypassable substring match** and
> `allowedDatasets` does **not** gate hand-authored `bigquery-sql` tools — so IAM
> (per-client SA scope), not Toolbox config, is the real boundary. This doc also
> lists multi-tenant isolation as a non-goal, which the "roll out per client"
> requirement inverts. Read the v6.14.0 doc instead; the content below is retained
> for history only.

**Status**: Superseded
**Priority**: P2 (Low)
**Estimated**: 3 days
**Scope**: Backend + Infrastructure
**Dependencies**: [Agent Factory](agent-factory.md), [Tools Porting Guide](tools-porting-guide.md)
**Created**: 2026-04-13
**Last Updated**: 2026-07-17

## Problem Statement

v6 agents need structured database access for several use cases: querying user data in Firestore, running analytics on BigQuery, and potentially exposing read-only views of operational data to skills. Today, each of these would require a hand-written FunctionTool per query, managing connection pools, credentials, and parameterization ourselves.

Google's **MCP Toolbox for Databases** (v1.0.0, released 2026-04-10) is an open-source Go server that sits between agents and databases, exposing pre-defined parameterized queries as MCP tools. It handles connection pooling, IAM auth, credential lifecycle, and OpenTelemetry tracing out of the box.

**Current State:**
- No database tools exist in v6 yet
- v5 had no structured DB query tools (all DB access was in backend Python code)
- Firestore access is via the Python SDK directly in backend services
- BigQuery access would require new tooling

**Impact:**
- Agents can't query databases today — all data must be pre-fetched or embedded in prompts
- Adding DB query tools one-by-one means duplicating connection pooling, auth, and security concerns
- Toolbox would provide a single, secured gateway for all database access

## Goals

**Primary Goal:** Deploy MCP Toolbox on Cloud Run as a secure database gateway, enabling v6 agents to query Firestore and BigQuery through pre-defined, parameterized tools.

**Success Metrics:**
- Toolbox deployed on Cloud Run in dev environment
- At least 3 useful query tools defined (e.g. user profile lookup, skill usage stats, conversation history)
- Agent can invoke DB tools via ADK `ToolboxToolset` with <500ms overhead per query
- IAM-authenticated end-to-end (no passwords in config)

**Non-Goals:**
- Write operations (INSERT/UPDATE/DELETE) — read-only in v1
- Replacing direct Firestore SDK usage in backend services (Toolbox is for agent-facing queries only)
- Prebuilt/generic tools in production (only explicit parameterized queries)
- Multi-tenant isolation (single-tenant for now, revisit if we add customer-facing agents)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Adds ~5-10ms overhead per query; dominated by actual DB latency |
| 2 | EARNED TRUST | +1 | Agent can cite real data from queries, not hallucinated facts |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure behind skills, not user-facing |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Model-agnostic — any LLM can use the tools |
| 5 | GRACEFUL DEGRADATION | +1 | If Toolbox is down, agent loses DB tools but continues working with other tools |
| 6 | PROTOCOL OVER CUSTOM | +2 | MCP standard protocol; ADK-native ToolboxToolset; no custom DB-to-agent code |
| 7 | API FIRST | +1 | Toolbox exposes REST + MCP endpoints, usable by any client |
| 8 | OBSERVABLE BY DEFAULT | +1 | Built-in OpenTelemetry tracing exports to Cloud Trace (matches our observability stack) |
| 9 | SECURE BY CONSTRUCTION | +2 | IAM auth, parameterized queries (no SQL injection), pre-defined statements (agent can't run arbitrary SQL) |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Purely backend |
| | **Net Score** | **+8** | Threshold: >= +4 |

## Suitability Assessment

### Why Toolbox is a good fit for v6

1. **ADK-native integration** — `toolbox-adk` SDK wraps Toolbox as an ADK `ToolboxToolset`. One line to add all DB tools to an agent. This aligns with our "protocol over custom" axiom.

2. **Security model matches GCP** — IAM database auth (no passwords), service-to-service auth via Google ID tokens, Cloud Run's built-in auth. Fits our existing SA model (`platform@{project_id}.iam.gserviceaccount.com`).

3. **Observability for free** — OpenTelemetry traces with `otel_to_cloud=True` export to Cloud Trace, matching our observability stack (see agents-cli-observability patterns).

4. **Supports our databases** — Firestore, BigQuery, Cloud SQL (if we add one later), and Spanner are all supported.

5. **Pre-defined queries = controlled blast radius** — Agent can only execute queries we've defined in `tools.yaml`. No arbitrary SQL. This is critical for security.

6. **Cloud Run deployment** — Same infra we already use. One more service, same patterns.

### Risks and mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Toolbox is brand new (v1.0.0, 3 days old) | Medium | High GitHub activity (14.5k stars), Google-backed. Start in dev only, evaluate before prod |
| No PII masking | Medium | Define queries that exclude PII fields. Review every tool definition |
| Agent query volume spikes DB costs | Low | BigQuery: use LIMIT in all statements. Firestore: query on indexed fields only |
| Another service to maintain | Low | Stateless Go binary, minimal ops. Config in Secret Manager |
| Connection pool exhaustion under load | Low | Tune pool size, monitor utilization, alert at 80% |

### When NOT to use Toolbox

- **Backend service code** — Continue using Firestore SDK directly for backend logic (session management, skill CRUD, auth). Toolbox is for agent-initiated queries only.
- **Real-time subscriptions** — Toolbox is request/response. Use Firestore listeners for real-time updates.
- **Complex transactions** — Multi-step read-modify-write should stay in backend code.

## Design

### Overview

Deploy MCP Toolbox as a Cloud Run service in each environment. Define query tools in a `tools.yaml` config stored in Secret Manager. Connect v6 agents to Toolbox via the `toolbox-adk` SDK.

### Architecture

```
[v6 Agent (ADK)]
       |
       | ToolboxToolset (HTTP + Google ID token)
       v
[MCP Toolbox (Cloud Run)]  <-- tools.yaml from Secret Manager
       |
       | IAM Database Auth (no passwords)
       v
[Firestore]  [BigQuery]  [Cloud SQL (future)]
```

### Toolbox Configuration (`tools.yaml`)

```yaml
# Source: Firestore
kind: source
name: firestore
type: firestore
project: ${GCP_PROJECT_ID}
---
# Source: BigQuery
kind: source
name: bigquery
type: bigquery
project: ${GCP_PROJECT_ID}
---
# Tool: Look up user profile by email
kind: tool
name: get-user-profile
type: firestore-query
source: firestore
description: Look up a user's profile by their email address. Returns display name, role, and active skills.
parameters:
  - name: email
    type: string
    description: The user's email address.
statement: |
  collection: users
  where: email == @email
  select: displayName, email, role, activeSkills, createdAt
---
# Tool: Get skill usage statistics
kind: tool
name: get-skill-usage-stats
type: bigquery-sql
source: bigquery
description: Get usage statistics for a specific skill over the last 30 days.
parameters:
  - name: skill_id
    type: string
    description: The skill identifier.
statement: |
  SELECT skill_id, COUNT(*) as invocations,
         AVG(latency_ms) as avg_latency_ms,
         COUNT(DISTINCT user_id) as unique_users
  FROM `${GCP_PROJECT_ID}.analytics.skill_invocations`
  WHERE skill_id = @skill_id
    AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  GROUP BY skill_id
---
# Tool: Search conversation history
kind: tool
name: search-conversations
type: firestore-query
source: firestore
description: Search a user's recent conversation history by keyword.
parameters:
  - name: user_id
    type: string
    description: The user's ID.
statement: |
  collection: conversations
  where: userId == @user_id
  orderBy: updatedAt DESC
  limit: 20
  select: title, skillId, updatedAt, messageCount
---
# Toolset: agent-facing database tools
kind: toolset
name: agent_db_tools
tools:
  - get-user-profile
  - get-skill-usage-stats
  - search-conversations
```

### ADK Integration

```python
# backend/tools/database.py
import os
from toolbox_adk import ToolboxToolset

TOOLBOX_URL = os.environ.get("TOOLBOX_URL", "http://localhost:5000")

def get_db_toolset() -> ToolboxToolset:
    """Database tools via MCP Toolbox. Agent-facing queries only."""
    return ToolboxToolset(
        TOOLBOX_URL,
        toolset="agent_db_tools",
    )
```

```python
# In agent factory, when building a skill agent that needs DB access:
from backend.tools.database import get_db_toolset

agent = LlmAgent(
    name="analytics_agent",
    model="gemini-2.5-flash",
    instruction="You help users understand their usage data...",
    tools=[get_db_toolset()],
)
```

### Cloud Run Deployment (via Terraform)

Toolbox is a pre-built Docker image from Google — no Cloud Build trigger needed. Deploy via the existing `cloud_run_system` pattern in `multivac-aitana/infrastructure/`.

**Terraform config** (`environments/common/terraform.tfvars`, add to `cloud_run_system`):

```hcl
toolbox = {
  cpu                = "1"
  memory             = "512Mi"
  max_instance_count = 2
  min_instance_count = 0
  timeout_seconds    = 300
  port               = 8080
  service_account    = "sa-toolbox"
  invokers           = ["sa-llmops"]  # Only backend API SA can invoke
  environment_variables = {}
  secrets = {}
  cloud_build = {
    disabled = true  # Pre-built image, no build trigger
  }
}
```

**Service account** (add to `service_accounts` in same file):

```hcl
sa-toolbox = {
  roles = [
    "roles/datastore.viewer",        # Read Firestore
    "roles/bigquery.dataViewer",      # Read BigQuery tables
    "roles/bigquery.jobUser",         # Run BigQuery queries
    "roles/storage.objectViewer",     # Read tools.yaml from config bucket
  ]
  display_name = "MCP Toolbox for Databases"
  create       = true
}
```

**Image pinning** — since there's no Cloud Build, the image version needs to be managed explicitly. Options:
1. Pin in Terraform: `image = "us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:1.0.0"`
2. Use a variable: `image = var.toolbox_image` (update in tfvars when upgrading)

Option 2 is preferred — version bumps are a one-line tfvars change.

### Configuration via GCS Bucket Mount

Use the existing config bucket (`{parent_project_id}-config-bucket`) with a Cloud Run GCS FUSE volume mount. This avoids extending the Terraform `run` module for secret file mounts.

**Upload config:**

```bash
# Upload tools.yaml to the per-environment config bucket
gsutil cp tools.yaml gs://${PROJECT_ID}-config-bucket/toolbox/tools.yaml
```

**Mount the bucket on the Cloud Run service:**

```bash
# The run module ignores volume_mounts and volumes in lifecycle,
# so this is managed via gcloud after Terraform creates the service.
gcloud run services update toolbox \
  --region=us-central1 \
  --add-volume=name=config,type=cloud-storage,bucket=${PROJECT_ID}-config-bucket,readonly=true \
  --add-volume-mount=volume=config,mount-path=/mnt/config \
  --args=--tools-file=/mnt/config/toolbox/tools.yaml,--address=0.0.0.0,--port=8080
```

**Why GCS mount over Secret Manager:**
- **No module changes** — the `run` module's `lifecycle` block already ignores `volume_mounts` and `volumes`, so the mount can be configured outside Terraform
- **Easy iteration** — update config with `gsutil cp` and redeploy (or let Toolbox's dynamic reload pick it up without restart)
- **Reuses existing infra** — the config bucket already exists in every environment, wired into Cloud Build via `_GCS_BUCKET`
- **Better for structured config** — `tools.yaml` is a multi-document YAML file, not a single secret value

**Config reload:** Toolbox supports dynamic config reloading by default. When `tools.yaml` changes in GCS, new container instances pick it up automatically. Existing instances reload on the next poll cycle. To force-disable reload: add `--disable-reload` to args.

**IAM:** The `sa-toolbox` service account needs `roles/storage.objectViewer` on the config bucket (included in the SA definition above).

### IAM Requirements

Handled by the Terraform service account definitions above. The key bindings:

| Principal | Role | Target | Purpose |
|-----------|------|--------|---------|
| `sa-toolbox` | `roles/datastore.viewer` | Project | Read Firestore |
| `sa-toolbox` | `roles/bigquery.dataViewer` | Project | Read BigQuery tables |
| `sa-toolbox` | `roles/bigquery.jobUser` | Project | Run BigQuery queries |
| `sa-llmops` (backend API) | `roles/run.invoker` | Toolbox service | Call Toolbox from backend |

### Service-to-Service Auth

```python
# backend/tools/database.py — authenticated version
import google.auth.transport.requests
import google.oauth2.id_token

def _get_id_token(audience: str) -> str:
    request = google.auth.transport.requests.Request()
    return google.oauth2.id_token.fetch_id_token(request, audience)

def get_db_toolset() -> ToolboxToolset:
    return ToolboxToolset(
        TOOLBOX_URL,
        toolset="agent_db_tools",
        client_headers={
            "Authorization": f"Bearer {_get_id_token(TOOLBOX_URL)}"
        },
    )
```

## Implementation Plan

### Phase 1: Local Dev (1 day)
- [ ] Install Toolbox binary locally (`brew install mcp-toolbox`)
- [ ] Write initial `tools.yaml` with Firestore source and one test tool
- [ ] Run Toolbox locally, verify with `--ui` flag
- [ ] Add `toolbox-adk` to `backend/pyproject.toml`
- [ ] Create `backend/tools/database.py` with `get_db_toolset()`
- [ ] Test tool invocation via ADK playground

### Phase 2: Cloud Run Deploy via Terraform (1 day)
- [ ] Add `sa-toolbox` service account to `common/terraform.tfvars`
- [ ] Add `toolbox` entry to `cloud_run_system` in `common/terraform.tfvars`
- [ ] `terraform plan` and `terraform apply` in dev
- [ ] Upload `tools.yaml` to `gs://${PROJECT_ID}-config-bucket/toolbox/tools.yaml`
- [ ] Configure GCS FUSE volume mount via `gcloud run services update`
- [ ] Add `TOOLBOX_URL` env var to backend API Cloud Run service
- [ ] Verify end-to-end: agent -> backend -> Toolbox -> Firestore

### Phase 3: Expand Tools (1 day)
- [ ] Add BigQuery source and analytics tools
- [ ] Add conversation search tool
- [ ] Write integration tests
- [ ] Document tools in skill configuration (which skills get DB access)

## Testing Strategy

**Backend Tests:**
- Unit test: `get_db_toolset()` returns a `ToolboxToolset` with correct URL and toolset name
- Mock test: verify auth header generation

**Integration Tests (require Toolbox running):**
- Start Toolbox locally with test `tools.yaml` pointing at Firestore emulator
- Invoke each tool via `ToolboxToolset` and verify response structure
- Verify parameterized queries prevent injection (pass SQL in parameters, confirm it's treated as data)

**Manual Testing:**
- Run Toolbox with `--ui` flag, test each tool interactively
- Test via ADK playground with agent that uses DB tools

## Security Considerations

- **Read-only in v1** — No INSERT/UPDATE/DELETE statements in `tools.yaml`. Enforce at both query level and IAM level (`datastore.viewer`, not `datastore.user`).
- **No arbitrary SQL** — Agent can only invoke pre-defined parameterized queries. Toolbox does not accept ad-hoc SQL.
- **IAM auth end-to-end** — No database passwords. Service account auth to Cloud Run, IAM database auth to Firestore/BigQuery.
- **PII exclusion** — Define queries with explicit `select` fields that exclude PII. Review every tool definition before deployment.
- **No public access** — Toolbox Cloud Run service uses `--no-allow-unauthenticated`.
- **Audit trail** — OpenTelemetry traces capture every query execution, exported to Cloud Trace.

## Performance Considerations

- **Overhead**: ~5-10ms per query from Toolbox + OpenTelemetry instrumentation
- **Connection pooling**: Default 10 min / 50 max per source. Sufficient for dev; tune for production
- **BigQuery**: All queries must include `LIMIT` to prevent runaway costs and latency
- **Firestore**: Query on indexed fields only. Use `limit` in query definitions
- **Cold start**: Go binary, fast cold start (~200ms). Cloud Run min-instances=0 is fine for dev

## Success Criteria

- [ ] Toolbox running on Cloud Run in dev
- [ ] At least 3 query tools defined and working
- [ ] Agent can query Firestore and BigQuery through Toolbox
- [ ] IAM-authenticated end-to-end, no passwords
- [ ] Traces visible in Cloud Trace
- [ ] Integration test passing

## Open Questions

1. **Firestore query syntax** — Toolbox v1.0.0 Firestore support is new. Need to verify the exact query DSL it uses (the `collection/where/select` syntax above is illustrative, actual format may differ).
2. **BigQuery cost controls** — Should we set a per-query byte scan limit? Toolbox doesn't enforce this; may need a BigQuery reservation or custom quota.
3. **Tool access control per skill** — Should all skills get all DB tools, or should we scope toolsets per skill type? Leaning toward scoped toolsets.
4. **Embedding support** — Toolbox supports vector search via `embeddedBy`. Useful if we move skill/conversation search to vector similarity. Defer to a future iteration.
5. **GCS FUSE cold start cost** — GCS FUSE adds a small amount of latency on cold start to fetch the mounted files. For a tiny `tools.yaml` this should be negligible, but worth measuring. If it's a problem, switch to baking the config into a custom image layer.
6. **Image version management** — No Cloud Build trigger means version bumps are manual. Should we add a simple CI job that checks for new Toolbox releases and opens a PR to bump the tfvars?
7. **Volume mount lifecycle** — The GCS FUSE mount is configured via `gcloud` outside Terraform (since the `run` module ignores volume changes). Should we document this as a one-time setup step per environment, or add volume support to the Terraform module?

## Related Documents

- [Tools Porting Guide](tools-porting-guide.md) — FunctionTool patterns for other tools
- [Agent Factory](agent-factory.md) — How tools are registered with agents
- [Cloud Infrastructure](cloud-infrastructure.md) — Cloud Run deployment patterns
- [MCP App Integrations](mcp-app-integrations.md) — MCP protocol usage in v6
- Terraform: `<local-path>` — `cloud_run_system` in `environments/common/terraform.tfvars`
- [MCP Toolbox GitHub](https://github.com/googleapis/mcp-toolbox) — Source, docs, `tools.yaml` reference
- [ADK Toolbox integration](https://google.github.io/adk-docs/tools/google-cloud/mcp-toolbox-for-databases/) — `toolbox-adk` SDK docs

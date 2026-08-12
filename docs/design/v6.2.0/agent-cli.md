# Agent CLI — Letting an Agent Drive `aiplatform`

**Status**: Planned (decision-only — no code lands until a primary use case appears)
**Priority**: P2 (Low) — strategic option, not bring-up critical
**Estimated**: 0.5 day (decision + spike) for the **chosen** option once a real use case exists; the four options below have very different build costs (~0.5 day to ~1 week)
**Scope**: Decision document. Picks one of four execution backends for "an LLM agent that can shell out to the `aiplatform` CLI" and captures *why* the others were rejected.
**Dependencies**:
- [local-dev-cli.md](local-dev-cli.md) — `aiplatform` CLI must exist before any of these options are useful
- [mcp-app-integrations.md](mcp-app-integrations.md) — pattern for integrating external tooling that this doc inherits
- [auth-and-permissions.md](auth-and-permissions.md) — auth context propagation
- AILANG Cloud reference: [`<local-path>`](file://<local-path>)
**Created**: 2026-04-11
**Last Updated**: 2026-04-11

## Problem Statement

Once the [local-dev CLI](local-dev-cli.md) ships, every developer-facing v6 affordance — `dev up`, `skill new/list/push/diff`, `doc parse`, `mcp probe`, `bulk extract`, `eval run`, `deploy` — will be a typed Click command. This unlocks an interesting second-order capability: **an LLM agent could drive Aitana itself by emitting `aiplatform` commands.**

Concrete examples that would become possible:
- A "skill curator" agent that watches Firestore for new skill drafts, runs `aiplatform skill diff`, and posts a review summary to a Slack channel.
- A "release manager" agent that runs `aiplatform eval run`, `aiplatform deploy status dev`, and `aiplatform deploy dev` in sequence after a passing CI run.
- A "doc harvester" agent that crawls a GCS bucket of new uploads, runs `aiplatform doc parse` on each, and writes the structured A2UI JSON back to a sibling location.
- A user-facing skill (e.g. a "DevOps assistant") that exposes a subset of `aiplatform` commands as tools and lets a developer talk to it instead of memorizing flags.

The technical question is: **what runtime executes the agent loop and the shell calls?** There are four credible answers and they have very different cost, complexity, and lock-in profiles. This doc compares them and picks one — but does NOT build anything until a concrete use case shows up. **No agent CLI code lands in v6.0.0.**

**Current State:**
- v6 has no agent runtime that can run shell commands
- AILANG Cloud (a separate sibling system Mark already operates) has a complete coordinator + Cloud Run Jobs + Pub/Sub + Claude/Gemini CLI executor stack already running in production at ~$60/month
- Google ADK ships `ContainerCodeExecutor` (verified — `google.adk.code_executors.container_code_executor` in `google/adk-python@v1.24.1`) which lets an ADK agent emit Python and have it `subprocess.run(...)` inside a Docker container we control
- Vertex AI Agent Engine, Anthropic `code_execution`, Anthropic Managed Agents, and Gemini code-execution all exist as native sandbox options in April 2026 — but most are locked-down images that cannot host a custom binary

**Impact:**
- Affects: future v6 features that want to compose Aitana commands automatically (none today; ~3-5 plausible candidates over the next year)
- Significance: low urgency, high optionality. Picking the right backend now means the *first* agent-driven use case ships in <1 day instead of becoming a multi-week build
- Strategic: the answer touches whether v6 reuses the existing AILANG Cloud infrastructure, builds a parallel stack, or commits to a Google-managed runtime — a decision worth recording even if no code is written

## Goals

**Primary Goal:** Pick the execution backend for "agent that runs `aiplatform` commands" that minimizes new infrastructure, preserves the v6 privacy boundary (GCP project edge), and lets the *first* concrete use case ship in <1 day of work — and document the rejected alternatives so the decision survives a year of memory loss.

**Success Metrics:**
- Time from "we have a concrete use case" to "first agent-driven `aiplatform` command running in dev": <1 day (achieved by picking the right backend now and resolving auth + image-baking questions before they're urgent)
- Net new infrastructure for v6: zero or near-zero (ideally reuse what's already running)
- Privacy boundary: every shell execution stays inside a GCP project we operate; no `aiplatform` command runs on third-party hosted infrastructure
- Decision durability: a year from now, the doc explains why each rejected option was rejected, with verifiable links

**Non-Goals:**
- **Not building a Claude-Code-style coding agent for editing the v6 codebase.** That's a separate product. This doc is exclusively about *operating Aitana* via CLI, not about *editing Aitana source code*.
- **Not replacing the [local-dev CLI](local-dev-cli.md).** Developers still type `aiplatform dev up` themselves. This doc is about *adding* an agent capability on top.
- **Not standing up a new Coordinator service for v6.** If we need a coordinator, we use the one AILANG Cloud already runs.
- **Not scoping the actual first use case.** This doc picks the *backend*; the *use case* comes in a follow-up doc when one materializes.
- **Not a v6.0.0 deliverable.** The bring-up sprint must finish first.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Doesn't directly affect user-perceived latency. Agent-driven flows are inherently background work. |
| 2 | EARNED TRUST | +1 | Audit trail is straightforward — every `aiplatform` command the agent runs lands in the same Cloud Logging stream + request ID chain a developer would produce. The agent has no privileged path. |
| 3 | SKILLS, NOT FEATURES | +1 | An agent driving `aiplatform` is itself a skill (or a workflow chain of skills). Reinforces the abstraction. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Doesn't change model selection — that's per-agent config. |
| 5 | GRACEFUL DEGRADATION | +1 | Each option below has a clean failure mode (job fails → message lands in dead-letter; container exec returns non-zero → ADK reports `CodeExecutionResult.stderr`; etc.). The CLI itself already has retry/dry-run semantics. |
| 6 | PROTOCOL OVER CUSTOM | +2 | The whole point is to pick a runtime that already exists — ADK executors, AILANG Cloud, or Vertex Agent Engine — rather than building a custom orchestrator. The doc explicitly rejects "build a new coordinator". |
| 7 | API FIRST | +1 | The agent talks to v6 via the same HTTP surface a developer or browser would. No backdoor. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Whatever option wins, it has to inherit Aitana's request-ID-per-call discipline. AILANG Cloud already does this; ADK code executors emit events through ADK's tracing. |
| 9 | SECURE BY CONSTRUCTION | +1 | Privacy boundary at the GCP project edge holds for three of the four options. Option D-Vertex preserves it; Option D-Anthropic-hosted breaks it (called out as the disqualifier). |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The "client" here is the agent loop; all the heavy lifting (CLI execution, Aitana backend, Firestore, GCS) lives behind the protocol. |
| | **Net Score** | **+8** | Strong alignment — proceed with the decision |

**Conflict Justifications:** None.

**Standards compliance check:** No new protocols. The decision is between existing runtimes. The selected option (see §Recommendation) reuses AILANG Cloud's existing REST + Pub/Sub message protocol, which is already documented in [`<local-path>`](file://<local-path>).

## Design

### Overview

Four candidate backends, evaluated against five hard requirements:
1. Can host a custom binary (`aiplatform` baked into the runtime image)
2. Can shell out — actual `subprocess` access, not Python-only sandbox
3. GCP/Firebase auth propagated as ambient credentials, not passed as tool input
4. Privacy boundary: execution stays inside a GCP project we operate
5. Lock-in: portable away from the runtime if the vendor or pricing shifts

### Option A — ADK `ContainerCodeExecutor` (build it ourselves on v6 infra)

Wire a v6 ADK agent with `code_executor=ContainerCodeExecutor(docker_path="infrastructure/aitana-tools/")`. Build a Docker image that bakes `aiplatform` plus a mounted SA key (or workload identity). The agent emits Python; the executor runs `python3 -c <code>` inside the container; the Python invokes `subprocess.run(["aitana", ...])`.

**Verified API (against `google/adk-python@v1.24.1` source via `adk-mcp` MCP server, 2026-04-11):**

```python
# google.adk.code_executors.container_code_executor.ContainerCodeExecutor
class ContainerCodeExecutor(BaseCodeExecutor):
    base_url: Optional[str] = None       # Docker daemon URL
    image: str = None                    # Pre-built image tag
    docker_path: str = None              # OR: path to a Dockerfile to build
    stateful: bool = Field(default=False, frozen=True, exclude=True)
    optimize_data_file: bool = Field(default=False, frozen=True, exclude=True)

    def execute_code(self, invocation_context, code_execution_input):
        exec_result = self._container.exec_run(
            ['python3', '-c', code_execution_input.code],
            demux=True,
        )
        # ...returns CodeExecutionResult(stdout, stderr, output_files)
```

Notes from the verified source:
- `stateful=False` is a **frozen field** — every code execution is a fresh `exec_run`, no persistent shell session. Long-running interactive workflows would need a subclass.
- Only runs `python3 -c <code>`, not arbitrary shell. The agent emits Python; Python invokes `subprocess`. That's a feature — it forces typed orchestration.
- The container is started in `__init__` and torn down via `atexit.register(self.__cleanup_container)`. One container per executor instance, reused across invocations.

| Criterion | Verdict |
|---|---|
| Custom binary | ✓ — bake `aiplatform` into Dockerfile via `pip install -e cli/` |
| Shell access | ✓ — Python `subprocess` inside the container |
| GCP/Firebase auth | ✓ — workload identity if running on GKE/Cloud Run; mounted SA key for local dev |
| Privacy boundary | ✓ — runs in our GCP project (or our laptop) |
| Lock-in | Low — ADK is open source; the `ContainerCodeExecutor` is ~150 LOC and the pattern is portable |
| New infrastructure | **Yes** — a new container image, a new Cloud Run service or Cloud Run Job to host the ADK agent, a new auth path, a new observability hookup |
| Build cost | ~3-5 days for a v0 (image + agent skeleton + smoke test + auth wiring + observability) |

**Strength:** Maximum integration with v6 — same ADK runtime as the rest of the platform, same skill abstraction, same `Runner`/`SessionService`. If v6 ever wants to surface "agent that can run aiplatform commands" as a *user-facing skill*, this is the only option that fits cleanly.

**Weakness:** It's a parallel runtime to AILANG Cloud, which already does almost the same thing. We'd be paying the bring-up cost (image, infra, auth, observability, dispatch) for capabilities AILANG Cloud already has in production.

### Option B — Cloud Run Jobs directly (no ADK)

Skip ADK entirely. Build a Cloud Run Job that takes a prompt + a model choice + an `aiplatform` invocation budget as env vars, runs Claude Code CLI (or Gemini CLI, or Codex CLI) inside the container, and exits when done. Trigger it via `gcloud run jobs execute` from anywhere — backend, GitHub Actions, manual.

| Criterion | Verdict |
|---|---|
| Custom binary | ✓ — bake `aiplatform` into the job's image |
| Shell access | ✓ — full shell, the agent IS a CLI |
| GCP/Firebase auth | ✓ — Cloud Run Jobs run with a service account by default |
| Privacy boundary | ✓ — Cloud Run is in our project |
| Lock-in | Low — Cloud Run Jobs is portable, the agent CLI choice is swappable |
| New infrastructure | **Yes** — Dockerfile, Cloud Run Job definition, dispatch mechanism, completion sink, dead-letter handling |
| Build cost | ~3-4 days for a v0 |

**Strength:** Vendor-agnostic at the agent layer — can run Claude Code, Gemini CLI, or `codex` interchangeably. Stateless and easy to reason about. No Coordinator service to maintain.

**Weakness:** No dispatch primitive — every invocation needs a trigger (manual, GitHub event, Cloud Scheduler, etc.). No completion routing back to the requester. No workflow chains. We'd be reinventing exactly the half of AILANG Cloud that's the *Coordinator*. The first time we need "trigger another job when this one finishes" we're back to building a coordinator, badly.

### Option C — Reuse AILANG Cloud (RECOMMENDED)

Treat the existing AILANG Cloud infrastructure as the v6 agent CLI runtime. AILANG Cloud already runs in production at `ailang-multivac-{dev,test,prod}` GCP projects with:

- Coordinator service (Cloud Run, Go, REST API at `POST /api/messages`)
- Agent executor (Cloud Run Jobs, Docker image with Claude Code CLI / Gemini CLI / git / plugin system pre-installed)
- Pub/Sub (5 topics, 6 subscriptions: `messages`, `events`, `tasks`, `completions`, `dead-letter`)
- Firestore (messages, tasks, chains)
- Workflow chains (`design-doc-creator → sprint-planner → sprint-executor`)
- Git guardrails (PreToolUse hooks via `git_guard.sh`)
- OAuth (Claude Max subscription, flat fee — no per-token API costs)
- Total run cost: **~$60/month flat** (Coordinator + Dashboard always-on; Agent Jobs scale-to-zero)

To make v6 a participant we add **two** small things:

1. **An `aitana-runner` agent definition** in AILANG's `config.cloud.yaml`:
   ```yaml
   agents:
     - id: aitana-runner
       inbox: aitana-runner
       workspace: sunholo-data/ai-protocol-platform   # the v6 repo
       provider: claude
       model: sonnet
       timeout: "30m"
       invoke:
         type: skill
         name: aitana-runner   # a new skill in ailang_bootstrap that knows about `aiplatform`
   ```

2. **A new skill in `ailang_bootstrap`** (`skills/aitana-runner/SKILL.md`) that documents the `aiplatform` command surface, sets up auth (mounts the v6 SA key from Secret Manager), and gives the agent the relevant permissions. The skill is the contract between AILANG and v6 — when AILANG dispatches the `aitana-runner` agent, the Claude Code instance loads this skill and immediately knows what it can do.

The bake step: the existing AILANG agent Docker image (`docker/Dockerfile.agent` in the `ailang` repo) needs `aiplatform` installed. Either (a) `pip install aiplatform` is added to the Dockerfile and triggers an image rebuild via `ailang-dev` Cloud Build, or (b) the skill's session-start hook does `pip install --user aiplatform` on first run. Option (a) is cleaner; option (b) keeps the AILANG image lean.

**v6 calls into AILANG via the existing REST API:**

```python
# v6 backend, anywhere it wants to dispatch an agent task
import httpx

resp = httpx.post(
    f"{AILANG_COORDINATOR_URL}/api/messages",
    headers={"Authorization": f"Bearer {AILANG_API_KEY}"},
    json={
        "inbox": "aitana-runner",
        "title": "Run nightly skill diff",
        "content": "Run `aiplatform skill diff` for every skill in dev and report any drift to #aitana-dev.",
        "from": "platform-backend",
        "category": "general",
    },
)
message_id = resp.json()["message_id"]
```

Results land back in v6 by either polling `GET /api/messages?inbox=platform&status=unread` or subscribing to a per-client Pub/Sub pull subscription via the `client_subscriptions` Terraform var (documented in `messaging-integration.md`).

| Criterion | Verdict |
|---|---|
| Custom binary | ✓ — add `pip install aiplatform` to AILANG's `docker/Dockerfile.agent`, image rebuild on next AILANG deploy |
| Shell access | ✓ — full Claude Code CLI (or Gemini CLI / Codex), with the existing git guardrails hooks, plugin system, observatory telemetry |
| GCP/Firebase auth | ✓ — AILANG agent jobs already mount a service account; add the v6 SA key to Secret Manager and reference it from the skill |
| Privacy boundary | ✓ — AILANG runs in `ailang-multivac-{env}` GCP projects which Mark operates; *not* a third party |
| Lock-in | Low — AILANG is a Sunholo system, source is in `github.com/sunholo-data/ailang`, we own it |
| New infrastructure | **Near-zero** — one new agent definition (YAML), one new skill (Markdown), one Dockerfile line (`pip install aiplatform`), one v6 API key reference |
| Build cost | **~0.5 day** for a v0 — just config, plus a smoke test |

**Strength:** It's already running. Coordinator, dispatch, completion routing, dead-letter, observability, workflow chains, git guardrails, the plugin system — all built and battle-tested. Adding `aitana-runner` is a configuration change, not an infrastructure project. The privacy boundary is preserved because AILANG lives in GCP projects Mark operates. Workflow chains mean we get composition for free: `aitana-runner` could feed into `design-doc-creator` could feed into `sprint-planner` if a use case wants that.

**Weakness:** The agent runs in a sibling repo's runtime, not v6's. This means:
- Two systems to keep in sync — when v6 ships a new `aiplatform` command, the AILANG agent image needs a rebuild to pick it up (or it does `pip install --user` every session).
- The agent loop is not an ADK agent, so user-facing v6 skills can't directly use this — it's a *background* / *operator* / *workflow* runtime, not a chat-surface runtime.
- If v6 ever wants a *user-facing* "talk to a DevOps assistant skill", that skill lives in v6's ADK runtime (Option A), not in AILANG. So we may end up with both eventually.

**This is the recommendation. See §Recommendation below.**

### Option D — Vendor-native managed sandboxes

Surveyed the four candidate native offerings in April 2026 (full research bundled separately). The verdict is sharp: **only one survives the "custom binary" requirement**.

| Offering | Hosted by | Custom binary | Shell | Auth passthrough | Verdict |
|---|---|---|---|---|---|
| **Vertex AI Agent Engine** (deploy from Dockerfile) | Google, in our GCP project | ✓ build-time | ✓ via subprocess in our container | ✓ ambient SA | **Viable** — but it's a managed *hosting target for Option A*, not a different sandbox primitive |
| Gemini API `code_execution` tool | Google | ✗ locked image | ✗ Python only, fixed libs | ✗ | Dead end |
| Vertex AI Code Interpreter extension | Google | ✗ | ✗ Python only | ✗ | Dead end (still Preview April 2026) |
| ADK `VertexAiCodeExecutor` | Google (wraps Code Interpreter) | ✗ | ✗ Python only | ✗ | Dead end |
| ADK `GkeCodeExecutor` | Us, on our GKE cluster | ✓ | ✓ | ✓ | This is just Option A on GKE |
| Anthropic `code_execution` (`code_execution_20260120`) | Anthropic | ✗ — only runtime `pip install`, no custom base image | ✓ Bash + Python (since `20250825`) | ✗ — closed sandbox, no GCP auth | **Disqualified** — execution leaves the GCP project edge |
| Anthropic Managed Agents (beta `2026-04-08`) | Anthropic — managed "environments" | ⚠ pre-installed packages only, no custom Docker base image | ✓ Bash + file ops + web tools | ✗ no GCP passthrough | **Disqualified** — execution leaves the GCP project edge; can't bake `aiplatform` |
| Anthropic `bash_20250124` (bash tool) | **Us** — Anthropic ships a schema, we host the runtime | ✓ | ✓ | ✓ | This is a schema, not a runtime — collapses back to Options A/B/C |
| Claude Computer Use (`computer_20251124`) | Us (reference Docker image) | ✓ | ✓ | ✓ | Massive overkill — it's a GUI automation tool |
| Claude Agent SDK | Us (Python/TS process) | ✓ | ✓ Read/Write/Bash/Edit/Grep + MCP | ✓ | Self-hosted; collapses back to Options A/B/C |
| Gemini CLI | Us (Node CLI, local or container) | ✓ | ✓ | ✓ | It's a *peer agent*, not a sandbox host. Could run inside Option C's container alongside Claude Code CLI. |

**Why only Vertex AI Agent Engine survives the surface check:** every Anthropic-hosted offering and every Google-stock-image offering fails on at least one of "custom binary baked into image" or "auth propagated from our GCP project". The two Anthropic hosted offerings (`code_execution`, Managed Agents) execute on Anthropic's infrastructure — they cross the privacy boundary, which is a hard no for a tool that has full credentials to our backend. Vertex Agent Engine is the only managed runtime where we can deploy from a Dockerfile, install custom binaries at build time, and run with a service account in our project.

**But Vertex Agent Engine is not actually a different sandbox primitive — it's a managed hosting target for the same ADK code you'd write in Option A.** You'd define your agent as an `Agent`, deploy it via the Agent Engine SDK, and the runtime inside Vertex would be an ADK Runner with `ContainerCodeExecutor` (or equivalent) calling `aiplatform` inside *your* container image. So Option D effectively reduces to "Option A, but Google manages the Cloud Run service for us instead of us deploying it ourselves". It's a deployment-target choice, not an architectural alternative.

| Criterion | Vertex AI Agent Engine verdict |
|---|---|
| Custom binary | ✓ |
| Shell access | ✓ |
| GCP/Firebase auth | ✓ |
| Privacy boundary | ✓ |
| Lock-in | Medium — Agent Engine is a Google-specific managed runtime; portability away from it would mean redeploying the same image to plain Cloud Run |
| New infrastructure | **Yes** — same as Option A plus Vertex Agent Engine wiring |
| Build cost | ~3-5 days (Option A's cost) + ~1 day Vertex Agent Engine deployment plumbing |

**Recommendation on Option D:** worth noting as the right *deployment target* if we ever build Option A. Not worth picking instead of Option C.

### Option Comparison Summary

| Dimension | A (ADK Container) | B (Cloud Run Jobs) | C (AILANG Cloud) | D (Vertex Agent Engine) |
|---|---|---|---|---|
| Custom binary | ✓ | ✓ | ✓ | ✓ |
| Shell access | ✓ via Python | ✓ | ✓ | ✓ via Python |
| GCP/Firebase auth | ✓ | ✓ | ✓ | ✓ |
| Privacy boundary | ✓ | ✓ | ✓ | ✓ |
| Coordinator / dispatch | ✗ build it | ✗ build it | **✓ already exists** | ✗ build it |
| Workflow chains | ✗ build them | ✗ | **✓ already exist** | ✗ |
| Git guardrails | ✗ | ✗ | **✓ already exist** | ✗ |
| Observatory / telemetry | partial (ADK tracing) | partial | **✓ already exists** | partial (Cloud Trace) |
| Already running in production | ✗ | ✗ | **✓** | ✗ |
| Net new monthly cost | ~$15-30 | ~$5-15 | **~$0** (existing ~$60/mo flat absorbs it) | ~$15-30 + Vertex |
| Build cost for v0 | 3-5 days | 3-4 days | **~0.5 day** | 4-6 days |
| User-facing skill fit | **✓** (lives in v6 ADK runtime) | ✗ | partial (background only) | **✓** |
| Background / operator fit | partial | ✓ | **✓** | partial |

### Architecture Diagram (recommended path — Option C)

```
┌────────────── v6 BACKEND (FastAPI + ADK) ──────────────┐
│                                                        │
│  any v6 service that wants to dispatch an agent task   │
│    └── httpx.post(AILANG_COORDINATOR_URL/api/messages, │
│          json={inbox: "aitana-runner", ...})           │
└──────────────────────┬─────────────────────────────────┘
                       │ HTTPS + Bearer COORDINATOR_API_KEY
                       ▼
┌──────────────── AILANG CLOUD (sibling system) ─────────┐
│                                                        │
│  Coordinator (Cloud Run Service, Go)                   │
│    ├── stores message in Firestore                     │
│    ├── publishes to Pub/Sub `ailang-tasks` topic       │
│    └── dispatches Cloud Run Job for matching agent     │
│                                  │                     │
│  Cloud Run Job: agent-executor   │                     │
│    container image:              │                     │
│      ├── Node.js + Claude Code CLI                     │
│      ├── git + ailang_bootstrap plugin                 │
│      └── pip install aiplatform  ◄── NEW (1 line)      │
│                                  │                     │
│    job execution:                │                     │
│      ├── git_guard.sh hook (if touching v6 repo)       │
│      ├── load skill: aitana-runner ◄── NEW (Markdown)  │
│      ├── auth: SA key from Secret Manager              │
│      └── shell: aiplatform skill diff, deploy status, etc. │
│                                  │                     │
│  Pub/Sub: completion → coordinator → response          │
└──────────────────────┬─────────────────────────────────┘
                       │ Pub/Sub pull (or REST poll)
                       ▼
                v6 backend receives result
                in `platform` inbox
```

### What "Build It" Looks Like for Option C

**On the AILANG side (one PR to `sunholo-data/ailang_bootstrap` + one PR to `sunholo-data/ailang-multivac`):**

1. New skill `skills/aitana-runner/SKILL.md` in `ailang_bootstrap`. Contents: a description of the `aiplatform` command surface, links to v6 design docs, the auth setup snippet, the safety constraints (e.g., "you may run `aiplatform skill diff` and `aiplatform eval run` without confirmation; you must NOT run `aiplatform deploy prod` ever; `aiplatform skill push` requires explicit approval in the message"). Estimated: ~150 lines of Markdown.

2. New agent entry in `ailang-multivac`'s `config/config.cloud.yaml`:
   ```yaml
   agents:
     - id: aitana-runner
       inbox: aitana-runner
       workspace: sunholo-data/ai-protocol-platform
       provider: claude
       model: sonnet
       timeout: "30m"
       max_cost_usd: "1.00"
       invoke:
         type: skill
         name: aitana-runner
   ```

3. One line in `ailang/docker/Dockerfile.agent`:
   ```dockerfile
   RUN pip install --no-cache-dir aiplatform
   ```
   (Triggers an image rebuild via the `ailang-dev` Cloud Build trigger on next push.)

4. New secret in `ailang-multivac/terraform/secrets.tf`: `platform-sa-key` containing a service account key for `platform@your-project-id.iam.gserviceaccount.com`. Or — preferably — workload identity federation between `ailang-multivac-dev` and `your-project-id` so no key material crosses projects. The skill mounts the secret at runtime.

**On the v6 side (one PR to `sunholo-data/ai-protocol-platform`):**

1. New env vars in `backend/.env.example` and Terraform: `AILANG_COORDINATOR_URL`, `AILANG_API_KEY` (Secret Manager reference).

2. New helper module `backend/integrations/ailang.py` (~60 LOC) — thin httpx wrapper around `POST /api/messages` and `GET /api/messages?inbox=platform&status=unread`. No business logic; just the integration boundary.

3. New design doc *for the first concrete use case* (NOT this doc) when one materializes — e.g., `docs/design/v6.x.x/skill-drift-watcher.md` or similar. That doc consumes the integration.

4. CLI hook: `aiplatform ailang dispatch <agent-id> --content "..."` as a developer convenience for manually firing tasks (added to `local-dev-cli.md` as a follow-up command — see the design-doc-creator skill's CLI affordance step).

**Total v0 cost: ~0.5 day across both repos.** Spike-grade. The cost only goes up if a use case demands more than the existing AILANG capabilities offer (chain workflows, custom hooks, longer timeouts, etc.) — and then the cost is borne by the use case's design doc, not this one.

## Implementation Plan

This doc deliberately **does not** ship implementation. The plan is staged:

### Phase 0: Decision durability (this doc) — 0 days
- [x] Capture all four options with verified APIs and pricing
- [x] Pick Option C with rationale
- [x] Document the rejected alternatives so the decision survives a year of memory loss
- [x] Verify ADK `ContainerCodeExecutor` API against `google/adk-python@v1.24.1` source
- [x] Verify AILANG Cloud REST + dispatch flow against `ailang-multivac/internal-docs/architecture.md` and `messaging-integration.md`
- [x] Verify Vertex Agent Engine, Anthropic `code_execution`, and Anthropic Managed Agents capabilities (research bundle April 2026)

### Phase 1: Wait for a use case
- [ ] No code lands until at least one of these triggers fires:
  - A v6 feature design doc identifies a workflow that wants to compose `aiplatform` commands (skill drift watcher, release manager, doc harvester, etc.)
  - A workshop demo wants to show "agent operating Aitana itself" as a wow moment
  - The v6 platform team has a recurring operational task (eval batch, deploy gate, drift detection) that's burning developer time

### Phase 2: Spike (~0.5 day, only when Phase 1 triggers)
- [ ] Add `pip install aiplatform` to `ailang/docker/Dockerfile.agent` (~1 line)
- [ ] Create `skills/aitana-runner/SKILL.md` in `ailang_bootstrap` (~150 lines Markdown)
- [ ] Add `aitana-runner` agent to `ailang-multivac/config/config.cloud.yaml` (~10 lines)
- [ ] Provision `platform-sa-key` secret (or workload identity federation) (~30 min Terraform)
- [ ] Add `backend/integrations/ailang.py` to v6 (~60 LOC)
- [ ] Smoke test: from a v6 dev shell, `POST /api/messages` to AILANG with a "run `aiplatform skill list --env=dev` and report back" task; verify the result lands in the v6 inbox

### Phase 3: First real use case (covered by that use case's own design doc)
- Whatever it is, it gets its own doc, its own axiom score, and its own implementation plan. This doc is finished.

## Migration & Rollout

**Database Migrations:** None.

**Feature Flags:** None — no code ships in v6.0.0.

**Rollback Plan:** If Phase 2 spike reveals AILANG can't accommodate the use case (e.g., needs a longer timeout than 30m, or needs a model AILANG doesn't support), fall back to Option A on a per-use-case basis. The decision in this doc is "Option C is the default; revisit per-use-case if it doesn't fit". We don't lose anything by defaulting to C — the costs are reversible.

**Environment Variables (when Phase 2 ships):**
- `AILANG_COORDINATOR_URL` — per-env URL of the AILANG coordinator (`ailang-multivac-dev` for v6 dev, etc.)
- `AILANG_API_KEY` — Secret Manager reference for the coordinator's bearer token
- `AILANG_DEFAULT_INBOX` — default inbox for v6 dispatched tasks (`aitana-runner`)

## Testing Strategy

### When no code is shipped (the current state)
- **Documentation review:** verify the four option descriptions match each backend's actual capabilities. Re-verify the ADK `ContainerCodeExecutor` API and AILANG REST API against their sources at the start of any Phase 2 spike (these APIs change).

### When Phase 2 spike runs (~0.5 day)
- [ ] Manual: `gh repo clone sunholo-data/ailang_bootstrap`, add the skill, push to `dev`
- [ ] Manual: `gh repo clone sunholo-data/ailang-multivac`, add the agent + secret, push to `dev`, wait for image rebuild
- [ ] Manual: from a v6 dev shell, dispatch a no-op task to `aitana-runner`, verify completion
- [ ] Manual: dispatch an `aiplatform skill list --env=dev` task, verify the output appears in the response message
- [ ] Manual: verify the `git_guard.sh` hook prevents the agent from pushing to v6's `main` branch (sanity check on the existing safety nets)

### When the first real use case ships
- That use case's design doc owns its own test plan.

## Security Considerations

- **Coordinator API key:** `AILANG_API_KEY` is a single shared bearer token across all AILANG clients. Inbox-level filtering provides functional isolation but not authentication isolation. Treat it as a moderate-sensitivity secret. Rotate via Secret Manager.
- **Service account key vs workload identity:** prefer workload identity federation between `ailang-multivac-{env}` and `your-project-id-{env}` over a long-lived SA key. If WIF isn't available across these projects, the SA key path is acceptable but the key must be scoped tightly (only the permissions `aitana-runner` needs to call v6 APIs — typically `roles/run.invoker` on `platform-backend` and read-only Firestore access).
- **Git guardrails:** the existing `git_guard.sh` hook in `ailang_bootstrap` already prevents arbitrary pushes. Confirm during Phase 2 that v6's repo is in the guardrail's allowlist with `default` mode (read-only diff/log/status; no push without explicit approval).
- **`aiplatform deploy prod` is forbidden:** the `aitana-runner` skill's SKILL.md must explicitly forbid `aiplatform deploy prod` (and ideally `aiplatform deploy test` too, if we want to be cautious). Hard rule, encoded in the skill's system prompt and enforced by a PreToolUse hook if needed.
- **Cost budget:** `max_cost_usd: "1.00"` in the agent definition caps the per-task spend. For OAuth (Claude Max subscription) this is moot — flat fee — but it's a defence-in-depth setting in case the agent runs in API-key mode.
- **Privacy boundary:** Option C keeps all execution inside `ailang-multivac-{env}` GCP projects. No part of an agent task crosses to a third-party hosted runtime. Confirmed against the rejected Option D Anthropic offerings (`code_execution`, Managed Agents) which would have crossed the boundary.

## Performance Considerations

- **Dispatch latency:** AILANG's REST → Coordinator → Cloud Run Jobs dispatch takes ~5-15s for a cold start (Cloud Run Jobs cold-start time + container pull). Warm: ~2-5s. Acceptable for background workflows; not acceptable for chat-surface latency. This is why Option C is for *operator/background* use cases, not for user-facing skills.
- **Image rebuild on `aiplatform` updates:** every release of `aiplatform` to PyPI requires an AILANG agent image rebuild (~5 min via the `ailang-dev` Cloud Build trigger). Alternative: have the skill's session-start hook do `pip install --user --upgrade aiplatform` to pick up the latest PyPI release on every job. Trade-off: convenience vs reproducibility. Default to image rebuilds; document the override for testing.
- **Concurrency:** Cloud Run Jobs in AILANG are scale-to-zero with a default max of 3 parallel executions per agent. Sufficient for v6's volume — we'd need ~50 parallel `aitana-runner` tasks/day to start hitting limits.

## Success Criteria

**For Phase 0 (this doc):**
- [x] All four options described with verified API references
- [x] Recommendation made (Option C)
- [x] Rejected alternatives documented with reasoning that survives a year of memory loss
- [x] No code shipped in v6.0.0

**For Phase 2 (when triggered):**
- [ ] AILANG agent image contains `aiplatform`
- [ ] `aitana-runner` agent definition exists in `config.cloud.yaml`
- [ ] `aitana-runner` skill exists in `ailang_bootstrap`
- [ ] Smoke test: v6 backend can dispatch `aiplatform skill list --env=dev` and receive the output back in <30s

## Open Questions

- **Workload identity federation between `ailang-multivac-{env}` and `your-project-id-{env}` projects:** is this set up, or do we need an SA key path for v0? Defer to Phase 2 — the skill's auth setup section is what unblocks this.
- **`pip install aiplatform` in the AILANG agent image vs. session-start hook:** image rebuild (reproducible, slower iteration) vs. dynamic install (fast iteration, less reproducible). Pick during Phase 2 based on how often `aiplatform` is changing.
- **AILANG's Claude Max subscription scope:** confirm during Phase 2 that the Claude Max OAuth covers the additional `aitana-runner` agent without bumping cost. (Likely yes — flat fee — but worth a 5-minute check before adding the agent.)
- **Vertex AI Agent Engine as a future deployment target for v6's *user-facing* skills:** if v6 ever wants a user-facing "DevOps assistant" skill that runs `aiplatform` interactively, that skill lives in v6's ADK runtime, not in AILANG. Vertex Agent Engine would be the natural deployment target. This is a v6.x.x decision, not a v6.0.0 decision — note it here so it's not lost.
- **Coexistence with v6's own ADK code executors:** if a v6 ADK skill ever wants to use `ContainerCodeExecutor` for unrelated reasons (e.g., a data analyst skill that runs Python on user data), the two patterns coexist cleanly — AILANG hosts the *operator* agents, v6 ADK hosts the *user-facing* agents. Document the split in the v6.x.x doc when it lands.

## Related Documents

- [local-dev-cli.md](local-dev-cli.md) — the `aiplatform` CLI this doc presumes exists
- [mcp-app-integrations.md](mcp-app-integrations.md) — pattern for integrating external tooling that this doc inherits (sidecar Cloud Run, allowlist registry, header-injected auth context)
- [auth-and-permissions.md](auth-and-permissions.md) — auth context propagation
- [agent-factory.md](agent-factory.md) — the v6 ADK agent factory (relevant if the future user-facing path picks Option A)
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — the workshop narrative; "Aitana operates Aitana via Aitana" is a credible Phase 3 demo if a use case materializes
- **External (AILANG Cloud — sibling system, Sunholo-operated):**
  - [`<local-path>`](file://<local-path>)
  - [`<local-path>`](file://<local-path>)
  - `github.com/sunholo-data/ailang` (coordinator + executor source)
  - `github.com/sunholo-data/ailang_bootstrap` (skill plugin)
  - `github.com/sunholo-data/ailang-multivac` (Terraform + Cloud Build)
- **External (verified APIs):**
  - ADK `ContainerCodeExecutor` source: `google.adk.code_executors.container_code_executor.ContainerCodeExecutor` in `google/adk-python@v1.24.1`
  - Vertex AI Agent Engine: https://docs.cloud.google.com/agent-builder/agent-engine/overview
  - Gemini code execution (rejected): https://ai.google.dev/gemini-api/docs/code-execution
  - Claude `code_execution` tool (rejected): https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool
  - Claude Managed Agents (rejected): https://platform.claude.com/docs/en/managed-agents/overview
  - Claude `bash_20250124` tool: https://platform.claude.com/docs/en/agents-and-tools/tool-use/bash-tool
  - Claude Agent SDK: https://code.claude.com/docs/en/agent-sdk/overview

## Recommendation

**Pick Option C — reuse AILANG Cloud.**

Three reasons:

1. **It already exists.** Coordinator, dispatch, completion routing, dead-letter, observability, workflow chains, git guardrails, plugin system, OAuth — all running in production at ~$60/month flat. Adding `aitana-runner` is a configuration change (~0.5 day), not an infrastructure project (3-5 days for Option A or D).

2. **Privacy boundary preserved.** AILANG runs in `ailang-multivac-{env}` GCP projects Mark operates. No execution crosses to a third-party runtime. The two Anthropic hosted offerings (Option D) were disqualified specifically because they cross this boundary; Option C does not.

3. **Reversibility.** If Option C turns out wrong for a specific use case (longer timeouts, different model, user-facing rather than background), we fall back to Option A *for that use case only* — without losing anything we built for Option C, because Option C's "build" is just config. The decision is cheap to revisit.

**Caveat — when to revisit:**
- If v6 ever wants a *user-facing* skill where the user chats with an agent that runs `aiplatform` commands inline (not "kick off a background task"), that skill lives in v6's ADK runtime (Option A), not in AILANG. Option C is for *operator/background* work; Option A is for *chat-surface* work. Both can coexist; the split is by use case, not a global choice.
- If AILANG Cloud is ever decommissioned or migrated, this doc is the index of what we'd need to rebuild — the contents of Phase 2 above.

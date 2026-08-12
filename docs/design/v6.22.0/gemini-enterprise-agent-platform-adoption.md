# Gemini Enterprise Agent Platform — Adoption from Cloud Run

**Status**: Planned
**Priority**: P1 (Medium) — no user-facing blocker, but two tracks close standing security/observability gaps
**Estimated**: ~4.5 days across 4 tracks (Track A ~0.5d, Track B ~1.5d, Track C ~1.5d, Track D ~1d)
**Scope**: Backend + infrastructure (no frontend)
**Dependencies**: None hard. Track A consumes the A2A card shipped by G43 (`docs/design/template/template-a2a-spec-compliance.md`); Track C overlaps with the confidential-content rule in [CLAUDE.md](../../../CLAUDE.md).
**Created**: 2026-08-04
**Last Updated**: 2026-08-04

## Problem Statement

Google's [What's new in Gemini Enterprise Agent Platform](https://cloud.google.com/blog/products/ai-machine-learning/whats-new-in-gemini-enterprise-agent-platform)
post (August 2026) GA'd eight capabilities under a platform brand that did not
exist when v6 was designed. The post is written for people deploying to the
**managed runtime**. We deploy the backend as a **Cloud Run service** and drive
ADK ourselves, so the immediate question — asked and answered in the audit that
produced this doc — is *which of these are reachable without moving off Cloud
Run, and which are runtime-locked*.

The answer is that **almost all of them are reachable**, and we are already
using two of them at their default configuration without ever having audited
that default.

**Naming change that makes the post hard to read against our code:** as of this
release **Agent Engine was renamed Agent Runtime**, *Agent Builder Sessions* →
*Agent Platform Sessions*, *Memory Bank* → *Agent Platform Memory Bank*. Our
code, env vars (`AGENT_ENGINE_ID`), scripts (`bootstrap_agent_engine.py`) and
docs all still say "Agent Engine". Anyone reading the new Google docs against
this repo has to do that translation by hand, every time.

**Current State:**

| Capability | Where we stand | Evidence |
|---|---|---|
| Agent Platform Sessions | ✅ In use, standalone from Cloud Run | [`session.py:104-140`](../../../backend/adk/session.py#L104-L140), `agentengine://` URI, `AGENT_ENGINE_ID` from Secret Manager ([`backend/cloudbuild.yaml:159`](../../../backend/cloudbuild.yaml#L159)) |
| Agent Memory Bank | ⚠️ Wired at **stock defaults** — never configured | [`session.py:143-152`](../../../backend/adk/session.py#L143-L152), `load_memory_tool` + `preload_memory_tool` at [`agent.py:945`](../../../backend/adk/agent.py#L945) |
| Agent Observability | ⚠️ Substrate only — OTel → Cloud Trace/Logging; no topology graph, no dashboards | [`telemetry.py`](../../../backend/observability/telemetry.py), `otel_to_cloud` at [`fast_api_app.py:117`](../../../backend/fast_api_app.py#L117) |
| Agent Evaluation | ⚠️ Offline `adk eval` only — no production monitors | [`tests/eval/eval_config.json`](../../../backend/tests/eval/eval_config.json) |
| Agent Registry | ❌ Discovery surfaces exist, nothing registered | [`a2a.py`](../../../backend/protocols/a2a.py), [`mcp_server.py`](../../../backend/protocols/mcp_server.py) |
| Agent Identity | ❌ Nothing | — |
| Agent Gateway / Model Armor | ❌ Nothing | — |
| Agent Runtime (7-day ops, BYOC) | ❌ N/A — requires abandoning our FastAPI/AG-UI ingress | See Non-Goals |
| CodeMender | ❌ Out of scope — dev tooling, not runtime | — |

**Impact:**

Three concrete gaps, in descending order of how much they should worry us:

1. **We persist model-extracted content derived from confidential customer
   documents into Memory Bank under an unaudited default policy.** The stock
   config extracts `USER_PERSONAL_INFO`, `USER_PREFERENCES`,
   `KEY_CONVERSATION_DETAILS` and `EXPLICIT_INSTRUCTIONS`, with a **365-day
   revision TTL** and a default generation model of `gemini-3.5-flash`. Nobody
   chose any of that. For a platform whose hard rule is "never make confidential
   customer content publicly accessible", *what we durably persist, for how long,
   and which model reads it to decide* are security parameters — not defaults to
   inherit. The ONE PPA corpus runs through this path.
2. **No inline prompt-injection or data-leakage defense.** We feed untrusted
   document content into tool-calling agents that can read private buckets. Our
   only defense today is agent instructions. Model Armor is the first available
   architectural control — which is what Axiom #9 demands over developer
   discipline.
3. **Our A2A card and MCP server are discoverable by nobody.** Both surfaces are
   built, spec-compliant, and deployed; no catalog points at them.

## Goals

**Primary Goal:** Determine by spike — not by reading — exactly which Gemini
Enterprise Agent Platform capabilities work against our Cloud Run deployment,
then adopt the ones that close a real gap, leaving Agent Runtime unadopted.

**Success Metrics:**
- Memory Bank runs under a **written, reviewed configuration** (memory topics,
  TTL, generation model, revision policy) rather than stock defaults — 0 → 1.
- Backend + MCP server registered in Agent Registry and returned by
  `agents:search` in the dev project — 0 → 2 entries.
- Model Armor floor-tested against a prompt-injection corpus, with a **measured**
  detection rate recorded in this doc (adopt/reject decision, not a guess).
- Time-to-answer for "which GEAP feature can we use from Cloud Run?" drops from
  a half-day audit to reading one table.

**Non-Goals:**
- **Migrating to Agent Runtime.** The 7-day long-running operations, sub-second
  cold starts, <1min provisioning and BYOC container deploys are real, and all of
  them are properties of the runtime we deliberately do not use. Adopting it
  means giving up the FastAPI app, the AG-UI SSE ingress, and the Cloud Run
  sidecar topology — the three things v6's architecture is built on. Revisit only
  if a customer journey genuinely needs multi-day autonomous execution.
- **CodeMender.** Developer tooling; unrelated to the runtime question.
- **Buying a Gemini Enterprise subscription.** Agent Registry is a Cloud API and
  does not require the $30/seat GE app; the GE-app registration path in
  [gemini-enterprise.md](../../integrations/gemini-enterprise.md) stays a
  separate, unrelated decision.
- **Renaming `AGENT_ENGINE_ID`.** The env var is in Secret Manager across three
  environments and one template. Docs get a translation note; the identifier
  stays.

## Spike Findings (run 2026-08-04, before writing this doc)

Per the skill's §5c rule, everything below was probed against the real SDK and
the real dev project rather than recalled. These findings are why the phase
ordering is what it is.

| Probe | Result | Consequence |
|---|---|---|
| `gcloud agent-registry` | ❌ Not in GA surface (SDK 557.0.0) | — |
| `gcloud alpha agent-registry services create` | ✅ Exists, with `--agent-spec-type={a2a-agent-card,no-spec}`, `--mcp-server-spec-type`, `--interfaces=url=,protocolBinding=` | Track A is one command, not an SDK integration. **Alpha** — do not put it in a deploy pipeline yet. |
| `agentregistry.googleapis.com` in `your-project-id` | ✅ Available, ❌ **not enabled** | One-line terraform service-list change |
| `modelarmor.googleapis.com` in `your-project-id` | ✅ Available, ❌ not enabled | Same |
| `gcloud model-armor` | ✅ Present in the **GA** surface | Model Armor is testable standalone, **without** Agent Gateway — this is what splits Track C in two |
| `agentplatform` on PyPI | ❌ Does not exist | The new docs' `import agentplatform` examples are **not runnable**. Use `vertexai.Client` or REST. |
| `vertexai` pinned version | 1.148.1 | — |
| `client.agent_engines.memories` methods | ✅ `create, delete, generate, get, ingest_events, list, purge, retrieve, retrieve_profiles, revisions, rollback` | **Every Memory Bank capability in the blog post is already callable from our pinned SDK.** Track B needs no dependency bump. |
| `google.adk.memory.VertexAiMemoryBankService` (ADK 1.31.1) | ✅ `add_events_to_memory, add_memory, add_session_to_memory, search_memory` | ADK already exposes the streaming-ingest path; we only call `add_session_to_memory` |
| **Live dev instance** `reasoningEngines/6224370509212024832` (`platform`, europe-west1) | ⚠️ **`context_spec: null`** | **Confirmed, not assumed:** there is no Memory Bank configuration on the instance at all — not a customised one, not a partial one. Every topic, TTL, model and revision setting is Google's default. This is the empirical basis for Track B. |

**Two findings that changed the plan:**

1. **Track B is a config change, not an integration.** We assumed advanced Memory
   Bank needed a new SDK. It does not — `ingest_events`, `revisions` and
   `rollback` are in the version we already ship. The work is deciding the
   policy and writing the config, not plumbing.
2. **Model Armor is reachable without Agent Gateway.** `gcloud model-armor` is GA
   and standalone. That decouples the security win (Track C1) from adopting a
   preview networking component that would sit in our ingress path (Track C2) —
   so C1 can ship and C2 can stay a spike.

**Status discrepancy, unresolved:** the blog post presents Agent Gateway and
Agent Registry as GA. The GEAP release notes list Agent Gateway as **Private
Preview** and Agent Registry as **Public Preview**, and the only Registry CLI
surface is `gcloud alpha`. The observed CLI supports the release notes, not the
blog. **Treat both as preview** until a Google rep or the console says otherwise.
This is why nothing here puts either on the critical path of a deploy.

## Axiom Alignment

Scored per [Product Axioms](../../product-axioms.md). Net must be >= +4, max 2 conflicts.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Genuinely mixed. `ingest_events` moves memory generation **off** the turn (today `add_session_to_memory` is synchronous work on the request path). Agent Gateway would **add** an ingress hop. Net neutral — and that is part of why C2 stays a spike. |
| 2 | EARNED TRUST | +1 | Memory revisions give durable memories an immutable version history plus `rollback`. Today a memory the agent recalls has no provenance and no correction path — a bad extraction is permanent and invisible. |
| 3 | SKILLS, NOT FEATURES | +1 | Agent Registry indexes our A2A skills straight from the existing agent card; memory topics can be scoped, so a skill's memory policy can differ from the platform default. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Memory extraction currently runs on an **unchosen** default model. `generation_config` lets us pin it deliberately — cheap model, correct region — which is the axiom exactly. |
| 5 | GRACEFUL DEGRADATION | 0 | Tracks A/B/D add no failure modes on the request path. Agent Gateway **would** introduce a new single point of failure in ingress — noted, and the reason C2 ships as a written verdict rather than an adoption. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Registry consumes the A2A card and MCP server we already serve, at spec versions we already emit (A2A 0.3/1.0). Zero new formats — this is the payoff for G43's spec-compliance work. |
| 7 | API FIRST | 0 | Backend/infrastructure only; channel-agnostic by nature. |
| 8 | OBSERVABLE BY DEFAULT | +1 | The topology graph keys off a Cloud Run **resource URI**, so our existing OTel export lights it up. Online eval monitors extend evaluation from pre-merge to production. All sinks stay inside the GCP project edge. |
| 9 | SECURE BY CONSTRUCTION | +1 | Two architectural controls where we have only developer discipline today: Model Armor for prompt injection / tool poisoning / data leakage, and an explicit retention policy over confidential-document-derived memories. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | No frontend change. |
| | **Net Score** | **+6** | Threshold: >= +4 ✅ — Acceptable. No conflicts. |

**Conflict Justifications:** None — no axiom scored -1.

**Note on the two zeros that could have been conflicts:** Agent Gateway is the
component that would push #1 and #5 negative. It is deliberately scoped as a
spike with a written adopt/reject verdict (Track C2), not an adoption. If that
spike recommends adopting it, it needs its own design doc with those two axioms
re-scored honestly — not an amendment to this one.

## Design

### Overview

Four independent tracks, ordered by (security value ÷ risk). Each is separately
shippable and separately revertible; none blocks another. Everything stays on
Cloud Run — the deployment topology does not change in any track.

### Track A — Register in Agent Registry (~0.5d)

Our A2A card and MCP server are already spec-compliant and deployed. Registry
does the rest.

```bash
# A2A agent card — the registry scans the card and indexes our public skills
gcloud alpha agent-registry services create platform-backend \
  --project=your-project-id --location=europe-west1 \
  --display-name="Aitana Platform v6" \
  --agent-spec-type=a2a-agent-card \
  --agent-spec-content="$(curl -s https://<host>/.well-known/agent.json)"
# NOTE: --interfaces must be EMPTY when --agent-spec-type=a2a-agent-card.

# MCP server — a separate Service resource
gcloud alpha agent-registry services create platform-mcp \
  --project=your-project-id --location=europe-west1 \
  --display-name="Aitana Platform MCP" \
  --mcp-server-spec-type=... \
  --interfaces=url=https://<host>/mcp,protocolBinding=HTTP_JSON
```

Writes go through the `Service` resource; reads come back as read-only `Agent` /
`McpServer` / `Endpoint` resources via `agents:search`. **Terraform
(`google_agent_registry_service`) exists** — but the API is alpha, so v6.22.0
registers dev by hand and records the terraform block for a later promotion once
the API is at least GA-beta. Do not wire an alpha API into `cloudbuild.yaml`.

**Access-control check before registering anything:** `_skill_to_a2a()` already
filters to marketplace-public skills. Track A must assert that filter still
holds — a registry entry is a *discovery* surface, and the whole point is that
other teams find it. Nothing group-tagged or customer-confidential may appear.

### Track B — Memory Bank configuration (~1.5d) — the security track

Today: stock defaults, chosen by nobody. Target: a reviewed, version-controlled
config applied via `client.agent_engines.update(config={"context_spec":
{"memory_bank_config": ...}})`.

Four decisions to make explicitly, each currently defaulted:

| Setting | Stock default | Why it needs a decision |
|---|---|---|
| `memory_topics` | `USER_PERSONAL_INFO`, `USER_PREFERENCES`, `KEY_CONVERSATION_DETAILS`, `EXPLICIT_INSTRUCTIONS` | `KEY_CONVERSATION_DETAILS` over a PPA session persists **contract terms** as durable memory. That is a retention decision about customer-confidential content. |
| `ttl_config` | 365-day revision TTL | Nobody agreed to a year. Should track the customer contract, not a Google default. |
| `generation_config.model` | `gemini-3.5-flash` | An unaudited model reading confidential content — and unverified against `MODEL_RESIDENCY_POLICY` (see Open Questions). |
| `disable_memory_revisions` | `False` (revisions kept) | Correct default, but should be a *decision* given the TTL above. |

Config lives in the repo (`backend/config/memory_bank.yaml`), is applied by a
script, and is diffable against the live instance — same shape as
`config/models.yaml`. Per-scope customization is supported
(`customization_configs[].scope_keys`), so a stricter policy for the ONE tenant
than for general skills is expressible without a second Memory Bank.

**Deferred within this track:** switching `add_session_to_memory` →
`add_events_to_memory` / `ingest_events`. It is a genuine INSTANT FEEL win
(generation moves off the turn, with `event_count` / `idle_duration` triggers and
automatic dedup by event ID), but it changes *when* memories exist relative to a
turn — which changes what `preload_memory_tool` sees. Land the policy first,
measure, then move the trigger. Scoped as a **stretch**, not a commitment.

### Track C — Model Armor, then a Gateway verdict (~1.5d)

**C1 — Model Armor standalone (~1d).** `gcloud model-armor` is GA and does not
require Agent Gateway. Build a floor test: a prompt-injection corpus aimed at the
document-analysis path (the realistic threat — a malicious PPA PDF instructing
the agent to exfiltrate to a public bucket), run it through a Model Armor
template, record the detection rate here. **Adopt only if measured.** The
integration point is a callback on the untrusted-content seam, not the ingress.

**C2 — Agent Gateway spike (~0.5d), verdict only, no adoption.** Gateway is the
enforcement point for client→agent ingress and "agent-to-anywhere" egress
governance, with Model Armor inline, and it explicitly supports Cloud Run agents.
It also lands in our ingress path and is Private Preview. The output of this
track is a written adopt/reject with a latency estimate — not a deployment.

### Track D — Observability + online evaluation (~1d)

The topology graph builds queries from a Cloud Run **service resource URI**, and
Agent Observability reads Cloud Trace / Cloud Logging — both of which we already
export ([`telemetry.py`](../../../backend/observability/telemetry.py), plus tenant
span attribution). Track D is verification-and-wire-up, not a build: confirm our
spans actually populate the dashboards, then evaluate online evaluation monitors
against the rubrics already in
[`eval_config.json`](../../../backend/tests/eval/eval_config.json).

**Known unknown, stated as such:** the GEAP docs are *silent* on whether Agent
Evaluation's production monitors support non-Agent-Runtime deployments. Every
other capability in this doc has a documented Cloud Run path; this one does not.
Track D's first task is to answer that question, and the track is sized assuming
the answer may be "no".

### CLI Surface

Per the skill's §5b-bis — each of these otherwise requires a multi-flag
`gcloud alpha` incantation or a hand-written `vertexai.Client` script.

| Command | Purpose | Track |
|---|---|---|
| `aiplatform registry register [--dry-run]` | Register/refresh the A2A card + MCP server from the live deployment | A |
| `aiplatform registry list` | Show what this project has catalogued | A |
| `aiplatform memory config show \| diff \| apply` | Dump live Memory Bank config, diff vs `memory_bank.yaml`, apply | B |
| `aiplatform memory inspect --user <id> [--revisions]` | List memories + revision history for a user — the debug tool for "why did it remember that?" | B |
| `aiplatform armor scan <file>` | Run a document through the Model Armor template and print the verdict | C1 |

`--dry-run` on `registry register` is not garnish: it prints the exact card that
would be published, which is the review gate for the access-control check above.

### Architecture Diagram

```
                        ┌───────────────────── unchanged ─────────────────────┐
[User] → [Frontend] → [/api/proxy] → [Cloud Run: platform-backend (FastAPI + ADK)]
                                              │
      ┌───────────────────────────────────────┼───────────────────────────────┐
      │                                       │                               │
 Sessions (today)                    Memory Bank (today,                OTel → Cloud Trace
 VertexAiSessionService               stock defaults)                    / Cloud Logging
 agentengine://$AGENT_ENGINE_ID       VertexAiMemoryBankService                │
      │                                       │                               │
      │                              ┌── TRACK B ──┐                   ┌── TRACK D ──┐
      │                              │ memory_bank │                   │  topology   │
      │                              │   .yaml     │                   │   graph +   │
      │                              │ topics/TTL/ │                   │ online eval │
      │                              │ model/revs  │                   │  monitors   │
      │                              └─────────────┘                   └─────────────┘
      │
 ┌── TRACK A ────────────────┐   ┌── TRACK C1 ───────────┐  ┌── TRACK C2 (spike only) ─┐
 │ /.well-known/agent.json ┐ │   │ Model Armor on the    │  │ Agent Gateway in the     │
 │ /mcp ───────────────────┼─┼─► │ untrusted-document    │  │ ingress path — VERDICT   │
 │   → Agent Registry Svc  │ │   │ seam (GA, standalone) │  │ ONLY, not adopted        │
 └─────────────────────────┘ │   └───────────────────────┘  └──────────────────────────┘
```

## Implementation Plan

### Track A — Agent Registry (~0.5d)
- [ ] Enable `agentregistry.googleapis.com` in dev (terraform service list)
- [ ] Assert the `_skill_to_a2a()` public-only filter holds; add a test that fails if a group-tagged skill reaches the card (~40 LOC)
- [ ] `aiplatform registry register --dry-run` / `register` / `list` (~120 LOC + tests)
- [ ] Register backend + MCP server in dev; verify via `agents:search`
- [ ] Record the `google_agent_registry_service` terraform block **as a comment**, un-applied, pending GA

### Track B — Memory Bank config (~1.5d)
- [ ] Write `backend/config/memory_bank.yaml` with the four decisions above, each with a one-line rationale (~60 LOC)
- [ ] **Review gate:** memory topics + TTL reviewed against the confidential-content rule before any apply
- [ ] `aiplatform memory config show|diff|apply` (~150 LOC + tests)
- [ ] `aiplatform memory inspect --user <id> [--revisions]` (~80 LOC)
- [ ] Apply to dev; verify the extraction change with a real session against the ONE corpus
- [ ] *(Stretch)* `add_session_to_memory` → `ingest_events` with an `idle_duration` trigger; measure the TTFT delta before committing

### Track C — Model Armor + Gateway verdict (~1.5d)
- [ ] Enable `modelarmor.googleapis.com` in dev
- [ ] Build the prompt-injection corpus against the document path (~10 cases, real PDF-shaped)
- [ ] Create a Model Armor template; run the corpus; **record the detection rate in this doc**
- [ ] `aiplatform armor scan <file>` (~80 LOC)
- [ ] Adopt-or-reject decision for C1, written down either way
- [ ] C2 spike: Agent Gateway latency + SPOF assessment → written verdict, no deployment

### Track D — Observability + eval (~1d)
- [ ] **First:** answer whether online eval monitors support non-Agent-Runtime deployments. If no, stop the eval half and say so here.
- [ ] Verify our Cloud Run resource URI populates the topology graph with real spans
- [ ] Assess online eval monitors against the existing `eval_config.json` rubrics
- [ ] Document findings in [docs/ops/](../../ops/) — including the negative results

### Cross-cutting (~0.25d)
- [ ] Translation note in [CLAUDE.md](../../../CLAUDE.md) + [deployed-urls.md](../../ops/deployed-urls.md): Agent Engine → Agent Runtime, and why `AGENT_ENGINE_ID` keeps its name

## Migration & Rollout

**Database Migrations:** None. Memory Bank config changes apply to the existing
instance; **existing memories are not retroactively re-extracted**, so narrowing
topics does not purge what is already stored. If the review concludes stored
memories are over-broad, that is an explicit `memories.purge` call — call it out,
do not assume the config change did it.

**Feature Flags:**
- `MEMORY_BANK_CONFIG_ENABLED` — when unset, the stock default applies (today's behaviour)
- `MODEL_ARMOR_ENABLED` — C1 ships gated; default off until the detection rate justifies on

**Rollback Plan:** Each track is independently revertible. Track A: delete the
`Service` resources. Track B: re-apply the previous `memory_bank.yaml` (it is in
git; `memories.rollback` exists for individual memories). Track C: flip the flag.
Track D: read-only, nothing to roll back.

**Environment Variables:** No new **required** vars. Two optional flags above.
`AGENT_ENGINE_ID` semantics are unchanged.

## Testing Strategy

### Backend Tests (pytest)
- [ ] `memory_bank.yaml` schema validation — a malformed config fails at load, not at apply
- [ ] `config diff` correctly detects drift between file and live instance (mocked client)
- [ ] Registry `--dry-run` emits a card containing **only** marketplace-public skills — the access-control regression test
- [ ] Model Armor scan wrapper: clean input passes, known-injection input is flagged

### Manual / Live Verification

Per this repo's standing bar — **unit tests passing is not evidence a cloud
integration works**:
- [ ] `agents:search` in dev actually returns both registered entries
- [ ] A real session against the ONE corpus produces memories matching the **new** topic policy, verified via `aiplatform memory inspect`
- [ ] The topology graph shows real spans from the deployed Cloud Run service
- [ ] Model Armor detection rate measured on the corpus and **written into this doc** — a pass rate, not a vibe

## Security Considerations

This doc is more security-shaped than most, so the analysis is the point rather
than a checklist:

- **Memory Bank is a retention surface for confidential content.** Model-extracted
  memories derived from customer contracts persist for 365 days under an
  inherited default. Track B makes that an explicit, reviewed decision. This is
  the single strongest reason to do this work.
- **Registry entries are discovery surfaces.** Only marketplace-public skills may
  appear. Enforced by a test, not by remembering — the entire content of Axiom #9.
- **Model Armor is an architectural control replacing developer discipline.**
  Today prompt-injection defense is agent instructions, which this repo has
  already learned do not bind model behaviour (see
  [`backend/adk/CLAUDE.md`](../../../backend/adk/CLAUDE.md) gotcha #3: a model
  printed A2UI into chat with "do NOT author any UI" in its prompt).
- **Data egress: none.** Every service here is a Google Cloud API inside our own
  project edge — Agent Registry, Memory Bank, Model Armor, Cloud Trace. No
  third-party SaaS, no new trust relationship. Per Axiom #9 this needs no egress
  justification, but it is worth stating given how much confidential content
  flows through the Memory Bank path.
- **Agent Identity (OBO)** is noted but not scoped here: letting the agent act
  *on behalf of the end user* when reading buckets would move our
  `request.auth.uid == doc.userId` check from application code into IAM. That is
  architecturally attractive and too large for this doc. Its own doc, later.

## Performance Considerations

- Tracks A and D add **zero** request-path work.
- Track B changes what the memory-generation model extracts, not when it runs
  (unless the `ingest_events` stretch lands, which would *reduce* turn work).
- Track C1's scan sits on the document-ingestion path, not the chat path — budget
  it against upload latency, not TTFT.
- Track C2 is the only item that would add ingress latency, which is precisely
  why it is a spike.

## Success Criteria

- [ ] Backend tests passing (`cd backend && make test-fast`)
- [ ] Lint + format clean (`cd backend && make lint`)
- [ ] `memory_bank.yaml` exists, is reviewed, and `aiplatform memory config diff` reports no drift against dev
- [ ] Both Registry entries discoverable via `agents:search` in dev
- [ ] A public-only assertion test exists and fails when a group-tagged skill reaches the A2A card
- [ ] Model Armor detection rate measured and recorded here (adopt/reject decision written either way)
- [ ] Agent Gateway verdict written — adopt or reject, with reasoning
- [ ] Online-eval-monitor support question answered, including if the answer is "not supported"
- [ ] Agent Engine → Agent Runtime naming note landed in CLAUDE.md

## Open Questions

1. **Memory generation model residency.** The default is `gemini-3.5-flash`. Our
   `MODEL_RESIDENCY_POLICY` constrains where models may run for EU customer data,
   and `gemini-3.x` has been global-only in our experience. Does Memory Bank's
   generation model honour the instance region, or does it run globally? **This is
   a blocker for Track B's apply step** — the answer decides whether we pin a
   different model or cannot enable memory for EU customers at all. Verify before
   applying, not after.
2. **Preview status.** The blog says GA for Agent Gateway and Agent Registry; the
   release notes and the `gcloud alpha`-only CLI say otherwise. Worth asking our
   Google contact directly.
3. **Same-project constraint.** The GE app, Agent Gateway and Agent Registry must
   share one project. We run `your-project-id-{dev,test,production}` plus a
   separate `sunholo-gemini-enterprise` demo project. Which project owns the
   registry, and does that force a per-env registry?
4. **Regional availability** of Agent Registry and Agent Gateway in
   `europe-west1` — unverified. Registry entries take an explicit `--location`;
   whether ours is supported is untested.
5. **Does Memory Bank's `retrieve_profiles`** (present in the SDK, absent from the
   blog post) do anything useful for our per-skill persona work? Unexplored.

## Related Documents

- [Consuming this ADK agent from Gemini Enterprise](../../integrations/gemini-enterprise.md) — the GE-app registration path (distinct from Agent Registry)
- [Local dev CLI](../v6.1.0/local-dev-cli.md) — where the new `aiplatform` commands live
- [Product Axioms](../../product-axioms.md)
- [backend/adk/CLAUDE.md](../../../backend/adk/CLAUDE.md) — why instruction-level defenses are not trusted
- [docs/ops/env-promotion-audit.md](../../ops/env-promotion-audit.md) — the promotion gate any of this must pass to reach test/prod

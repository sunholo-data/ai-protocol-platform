# v6.22.0 — Build Sequence

Platform-services adoption. Opened 2026-08-04 after Google's *What's new in
Gemini Enterprise Agent Platform* post GA'd eight capabilities under a brand
that did not exist when v6 was designed, and a code audit asked the only
question that matters to us: **which of these work from Cloud Run, and which are
runtime-locked?**

The audit's answer reframed the version. We assumed this would be a "should we
move to Agent Engine?" debate. It isn't. **Almost everything in the post is
reachable from Cloud Run** — Google's own docs name Cloud Run explicitly for
Memory Bank, Agent Registry, Agent Gateway and Agent Identity. Only the runtime's
own properties (7-day long-running operations, sub-second cold starts, BYOC
containers) are locked to Agent Runtime, and adopting those means giving up the
FastAPI app, the AG-UI SSE ingress and the sidecar topology. So the version is
not a migration. It is a **catch-up on services we can already call**.

Two spike findings did most of the reordering, and both cut work rather than
adding it:

1. **Memory Bank's advanced surface is already in our pinned SDK.** `vertexai`
   1.148.1 exposes `memories.ingest_events`, `.revisions`, `.rollback`,
   `.retrieve_profiles`; ADK 1.31.1 already has `add_events_to_memory`. The
   "advanced Memory Bank" track needs **no dependency bump** — it is a config
   decision, not an integration. (The docs' `import agentplatform` examples are
   not runnable; that package does not exist on PyPI.)
2. **Model Armor is GA and standalone** — `gcloud model-armor` exists in the GA
   surface and does not require Agent Gateway. That splits the security work
   cleanly from the preview networking component that would sit in our ingress
   path, so the win can ship while the risky part stays a spike.

The uncomfortable finding is the reason this version is P1 rather than P2:
**we have been persisting model-extracted content derived from confidential
customer documents into Memory Bank under a default policy nobody chose** —
`KEY_CONVERSATION_DETAILS` topics, a 365-day revision TTL, and an unaudited
generation model. For a repo whose first hard rule is about confidential customer
content, that is a retention decision we inherited instead of making.

One thing this version deliberately does not resolve: the blog says Agent Gateway
and Agent Registry are GA; the release notes say Private Preview and Public
Preview, and the only Registry CLI is `gcloud alpha`. The CLI supports the
release notes. Both are treated as preview here, and neither goes near a deploy
pipeline.

## Ordering

| # | Doc | Priority | Est. | Depends on | Notes |
|---|-----|----------|------|------------|-------|
| 1 | [gemini-enterprise-agent-platform-adoption](gemini-enterprise-agent-platform-adoption.md) | P1 | ~4.5d (0.5 + 1.5 + 1.5 + 1) | None hard. Track A consumes the A2A card from [template-a2a-spec-compliance](../template/template-a2a-spec-compliance.md) (G43) — already shipped and spec-compliant | Four **independent, separately revertible** tracks; none blocks another, so they can be picked up in any order or partially. Net axiom score **+6**, no conflicts. Track B is the security track and carries a hard blocker (memory-generation model residency) that must be answered *before* apply. Track C2 (Agent Gateway) is scoped as a **written verdict, not an adoption** — it is the only item that would add ingress latency and a new SPOF, and adopting it would need its own doc with axioms #1/#5 re-scored. |

## Timeline estimate

| Track | Work | Est. | Status |
|-------|------|------|--------|
| A | Agent Registry — register the A2A card + MCP server; public-only assertion test; `aiplatform registry` commands | ~0.5d | Planned |
| B | Memory Bank config — `memory_bank.yaml` (topics/TTL/model/revisions), `aiplatform memory config` + `inspect`; *(stretch)* `ingest_events` | ~1.5d | Planned |
| C | Model Armor floor test + measured detection rate (C1, GA); Agent Gateway adopt/reject verdict (C2, spike only) | ~1.5d | Planned |
| D | Observability topology graph verification; answer whether online eval monitors support non-Agent-Runtime deployments | ~1d | Planned |
| — | Cross-cutting: Agent Engine → Agent Runtime naming note | ~0.25d | Planned |

## What ships in v6.22.0

- **A memory-retention policy we actually chose.** What gets extracted from
  confidential sessions, how long it lives, and which model decides — written
  down, version-controlled in `backend/config/memory_bank.yaml`, diffable against
  the live instance. Today all four are Google's defaults.
- **`aiplatform memory inspect --user <id> --revisions`** — the first answer to
  "why did it remember that?" that doesn't involve a hand-written
  `vertexai.Client` script. Memory revisions give a durable memory provenance
  and a `rollback`, which it has never had.
- **Our A2A card and MCP server become discoverable.** Both have been built,
  spec-compliant and deployed since G43, catalogued by nobody. Registry indexes
  our public skills straight from the existing card — zero new formats, which is
  the payoff for having done the spec work properly.
- **A measured answer on prompt-injection defense.** Model Armor run against a
  document-path injection corpus with a **recorded detection rate**, and an
  adopt-or-reject decision written down either way. Today the only defense on
  that path is agent instructions — which this repo has already proven do not
  bind model behaviour.
- **A public-only regression test on the agent card**, so a group-tagged skill
  can never reach a discovery surface by accident.
- **Negative results, written down.** Whether online eval monitors work outside
  Agent Runtime, whether Agent Gateway is worth an ingress hop, whether
  `europe-west1` is even supported — recorded in the doc including when the
  answer is "no". The half-day audit that produced this version should not need
  repeating.
- **Not shipping:** Agent Runtime migration (explicit non-goal — see the doc's
  Non-Goals), Agent Gateway deployment (verdict only), Agent Identity OBO
  (attractive, too large, its own doc), CodeMender, or a Gemini Enterprise
  subscription (unrelated decision).

## Dependency graph

```
All four tracks are independent. There is no critical path.

Track A — Agent Registry ──► register A2A card + MCP server
    │                        (consumes G43's card as-is; adds a
    └── public-only test      public-only assertion before publishing)

Track B — Memory Bank config ──► OPEN QUESTION #1 (model residency)
    │                             │
    │                             └─► BLOCKS the apply step, not the
    │                                 writing of the config
    └── (stretch) ingest_events ──► deferred: changes WHEN memories exist
                                    relative to a turn, which changes what
                                    preload_memory_tool sees. Policy first,
                                    measure, then move the trigger.

Track C1 — Model Armor (GA, standalone) ──► ships flag-gated if measured
    │
    └─► independent of C2 — this is the whole point of the split
        │
        C2 — Agent Gateway ──► VERDICT ONLY. If "adopt", it needs its
                               own design doc: it is the one component
                               that pushes axioms #1 (ingress hop) and
                               #5 (new SPOF) negative.

Track D — Observability ──► first task is a question, not a build:
                            do online eval monitors work outside
                            Agent Runtime? Sized assuming "no".
```

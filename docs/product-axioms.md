# Product Axioms — Aitana Platform

**Version:** 1.1
**Effective:** 2026-04-10
**Status:** Active — changes require written justification and team review

These axioms guide every product decision on the Aitana platform. Every design doc must be scored against them. They encode explicit tradeoffs — what we prioritize and what we consciously deprioritize. They are modifiable only with justification.

## Scoring

Each design doc includes an **Axiom Alignment** table. Score each axiom:

- **+1** — Aligned: the feature actively supports this axiom
- **0** — Neutral: the feature neither helps nor hinders
- **-1** — Conflicts: the feature works against this axiom (requires written justification)

| Net Score | Decision |
|-----------|----------|
| +7 to +10 | Strong alignment. Proceed. |
| +4 to +6 | Acceptable. Proceed with attention to conflict justifications. |
| +1 to +3 | Weak alignment. Redesign recommended before implementation. |
| 0 or below | Misaligned with product strategy. Do not proceed without significant redesign. |

### Hard-Fail Rules

Regardless of net score, a design doc is rejected if:

- More than **2 axioms** score -1
- **EARNED TRUST** scores -1 and the feature involves user-facing data or factual claims
- **SECURE BY CONSTRUCTION** scores -1 and the feature introduces new data access patterns

---

## The Axioms

### 1. INSTANT FEEL

**Principle:** The user must perceive the system as instantly responsive, even when the work is slow.

**Why:** AI workloads are inherently variable — a tool-heavy query may take 10 seconds of real work. But perceived speed determines whether users trust the system or abandon it. Streaming a partial answer immediately changes the experience from "waiting" to "watching progress." The platform's AG-UI streaming commitment exists precisely for this reason.

**KPIs:**
- First token latency: <1s (no tools), <3s (with tools)
- AG-UI first event: <300ms
- Frontend chat overhead: <500ms

**Scoring guide:**
- **+1**: Feature reduces latency, adds streaming, or improves perceived responsiveness (progress indicators, skeleton states, optimistic UI)
- **0**: Feature does not affect the latency path
- **-1**: Feature adds synchronous blocking steps to the request path, requires waiting for full completion before showing anything, or adds cold-start dependencies

**Tradeoff:** Deprioritizes completeness of first response. It is better to stream a partial answer quickly and refine it than to wait for a perfect answer. Deprioritizes backend simplicity — streaming adds complexity, but the UX payoff justifies it.

---

### 2. EARNED TRUST

**Principle:** Always show sources. Never present uncertain information with false confidence; uncertainty must be visible and calibrated.

**Why:** AI assistants can be fluently, confidently wrong. For B2B users making business decisions based on extracted data, a hallucinated number is worse than no number at all. Every factual claim must cite its source — unsourced claims are a bug, not a style choice. Over-trust is as dangerous as under-trust; the system must actively calibrate user expectations.

**KPIs:**
- Citation rate: >90% of factual claims cite a source (document, search result, tool output)
- Hallucination rate on factual extraction: <2% (measured via ADK eval suite)
- Confidence signaling: 100% of extraction outputs include confidence indicators

**Scoring guide:**
- **+1**: Feature adds source attribution, confidence scores, verification steps, or lets users correct AI outputs
- **0**: Feature does not involve factual claims or data extraction
- **-1**: Feature presents AI-generated data without provenance, removes human verification checkpoints, or auto-executes based on unverified AI output

**Tradeoff:** Deprioritizes autonomy and speed of AI actions. Requiring citations and confidence signals adds tokens and latency. Deprioritizes the "magic" feeling of AI just knowing things. Better to feel reliable than magical.

---

### 3. SKILLS, NOT FEATURES

**Principle:** Every capability must be expressible as a skill that a non-technical user can discover, understand, and configure in under 60 seconds.

**Why:** The v6 architecture bets on skills as the organizing abstraction. Assistants confused users. Skills are the industry standard — each does one thing well. If a capability requires developer intervention, custom code, or understanding of agent orchestration, it has failed the abstraction test.

**KPIs:**
- Skill creation time (non-technical user via wizard): <5 minutes
- Comprehensibility: 90%+ of marketplace users can predict what a skill does from its name and description
- Concept count: a user never needs to understand more than 3 concepts to use a skill (skill, tool, model)

**Scoring guide:**
- **+1**: Feature simplifies skill creation, improves discoverability, reduces concept count, or makes skills more self-describing
- **0**: Feature is infrastructure invisible to end users
- **-1**: Feature introduces new user-facing abstractions beyond skills, requires users to understand agent internals (sub-agents, callbacks, context windows), or cannot be exposed through the skill builder

**Tradeoff:** Deprioritizes power-user flexibility. Some expert workflows (custom agent chains, raw prompt engineering, direct model API access) are deliberately omitted from the user-facing product. The platform optimizes for the 80% case. Developer-level extensibility happens through MCP tools and custom integrations, not through exposing orchestration primitives.

---

### 4. RIGHT MODEL, RIGHT MOMENT

**Principle:** Deploy maximum intelligence where it creates differentiation; optimize ruthlessly everywhere else.

**Why:** Not all tokens are equal. A thinking model on a complex multi-step reasoning task produces outsized value. The same model summarizing a document is waste. The platform has three providers (Gemini, Claude, OpenAI) — the axiom is about routing: use advanced reasoning for tool orchestration, ambiguous interpretation, and complex planning; use fast models for extraction, summarization, and formatting; use deterministic processing (AILANG Parse) where no LLM is needed at all.

**KPIs:**
- Model routing coverage: 100% of skill invocations use model selection appropriate to task complexity
- Advanced reasoning deployment: thinking/reasoning models used for multi-step planning, tool selection, and ambiguous queries
- Deterministic processing: document formats handled by AILANG Parse (zero LLM tokens) where possible

**Scoring guide:**
- **+1**: Feature uses model routing (fast model for simple tasks, reasoning model for complex ones), adds deterministic processing paths, or reduces unnecessary LLM calls
- **0**: Feature uses a single model appropriately and doesn't affect routing
- **-1**: Feature uses expensive reasoning models for simple tasks, sends large contexts to thinking models for extraction/summarization, or bypasses deterministic processing in favor of LLM calls

**Tradeoff:** Deprioritizes uniform simplicity. Model routing adds architectural complexity — you need selection logic, fallback chains, and per-model prompt tuning. But the quality difference between a reasoning model on a hard problem vs. a fast model on a simple one is the product's competitive edge.

---

### 5. GRACEFUL DEGRADATION

**Principle:** When any component fails, the system must fall to a useful state, never to a broken one.

**Why:** AI systems have a uniquely diverse failure surface: model APIs go down, context windows overflow, tools time out, rate limits hit, MCP servers disconnect, and models produce unparseable outputs. The platform already designs for this (InMemorySessionService as fallback, plain text when A2UI fails, AILANG Parse falling back to Gemini). But graceful degradation must be a conscious design principle in every feature, not an afterthought.

**KPIs:**
- Zero user-facing 500 errors from model API failures (fallback to alternative model or informative message)
- Recovery time from model provider outage: <30s (automatic failover)
- Degradation visibility: 100% of degraded states show a user-comprehensible explanation

**Scoring guide:**
- **+1**: Feature defines explicit fallback behavior for each failure mode, includes timeout handling, or adds redundancy
- **0**: Feature has no failure modes beyond standard HTTP errors
- **-1**: Feature has single points of failure with no fallback, assumes model APIs are always available, or creates cascading failure chains without circuit breakers

**Tradeoff:** Deprioritizes feature velocity. Engineering time spent on fallback behaviors is time not spent on new features. A feature that works 99% of the time but crashes horribly the other 1% is worse than one that works 95% of the time but degrades gracefully always.

---

### 6. PROTOCOL OVER CUSTOM

**Principle:** Adopt open protocols before building custom solutions; custom code is liability, protocol compliance is leverage.

**Why:** v5's biggest architectural debt was custom implementations of things that now have standards: custom SSE streaming (now AG-UI), bespoke rendering (now A2UI), custom tool discovery (now MCP), custom agent cards (now A2A). Protocol adoption means ecosystem compatibility — other AI products can discover Aitana skills via A2A, external tools integrate via MCP, any AG-UI client can connect. Custom code forfeits this leverage.

**KPIs:**
- Protocol coverage: 100% of agent-to-user communication flows through AG-UI
- MCP compatibility: all tool integrations use MCP-standard interfaces
- A2A discoverability: all public skills listed in a valid agent card

**Scoring guide:**
- **+1**: Feature uses an existing protocol (AG-UI, A2UI, MCP, A2A), extends protocol compliance, or replaces custom code with protocol-standard code
- **0**: Feature is internal infrastructure that does not touch protocol boundaries
- **-1**: Feature introduces custom communication protocols, bypasses AG-UI for streaming, or creates proprietary interfaces where open standards exist

**Tradeoff:** Deprioritizes time-to-ship for novel features. Protocols move slower than custom code. Waiting for a protocol to support a feature (or contributing to the protocol) takes longer than building a bespoke solution. The long-term leverage is worth the short-term delay.

---

### 7. API FIRST

**Principle:** One API surface serves all channels; parity is a consequence, not a goal.

**Why:** Aitana users interact across web, Telegram, email, WhatsApp, and CLI. If features are built channel-first, drift is inevitable — web gets features Telegram doesn't, the CLI becomes a second-class citizen. By designing the API surface first, every channel is a thin transport adapter over the same contract. Channel-specific code handles only transport and rendering, never business logic. The v5 pattern of a unified `process_assistant_request()` (now `process_skill_request()`) proved this works.

**Channels:** Web, Telegram, Email, WhatsApp, CLI

**KPIs:**
- Feature parity: 100% of skill capabilities available through the API (channels are rendering choices)
- Channel-specific code: limited to transport adaptation and display formatting
- API test coverage: every skill endpoint tested independent of any channel

**Scoring guide:**
- **+1**: Feature is designed API-first with channel rendering as a separate concern, or reduces channel-specific business logic
- **0**: Feature is backend-only and channel-agnostic by nature
- **-1**: Feature is built for a specific channel with no API abstraction, or embeds business logic in channel-specific code

**Tradeoff:** Deprioritizes channel-specific optimizations. A Telegram-native feature (inline keyboards, bot commands) might deliver a better UX than a generic API response rendered on Telegram. The constraint forces universal interaction patterns, which sometimes means the best possible UX on one channel is sacrificed for consistency across all.

---

### 8. OBSERVABLE BY DEFAULT

**Principle:** Every agent action, tool invocation, and model decision must be traceable without adding instrumentation after the fact. Telemetry is **rich inside our GCP project, never leaks outside it**.

**Why:** AI systems are stochastic — the same input can produce different outputs. Debugging production issues requires complete traces: which model was selected, what tools were called, what the context window contained, how many tokens were consumed, and what the user saw. The platform commits to OpenTelemetry exporting to Cloud Trace, Cloud Logging, BigQuery, and Cloud Monitoring — all inside our own GCP project. **Full prompt/response capture is the default** (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`), because internal observability is operationally essential and the data never leaves our trust boundary. This is why Langfuse Cloud and other third-party SaaS observability platforms were rejected — see Axiom #9 for the data egress boundary.

**KPIs:**
- Trace coverage: 100% of skill invocations have end-to-end traces (user message → model call → tool execution → response)
- Token accounting: per-invocation token counts (input + output) logged for every model call
- Content capture: full prompt and response stored in Cloud Trace + GenAI logging bucket for all production traffic
- Error attribution: 100% of user-facing errors traceable to root cause within 5 minutes

**Scoring guide:**
- **+1**: Feature emits structured traces/spans, logs token counts, captures full content to internal sinks, or adds diagnostic metadata (model routing decisions, tool selection rationale)
- **0**: Feature is covered by existing instrumentation
- **-1**: Feature introduces opaque processing steps, makes debugging harder (fire-and-forget async without trace propagation), reduces internal content capture without a customer contract requiring it, or adds latency-sensitive paths where tracing overhead is unacceptable

**Tradeoff:** Deprioritizes minimal overhead and storage cost. Tracing adds bytes to every request, milliseconds to every operation, and GCS/BQ storage bills. The cost is worth paying because deploying AI features you cannot debug in production is far more expensive. Per-customer overrides may use `NO_CONTENT` mode if a contract demands it, but the default is full visibility.

---

### 9. SECURE BY CONSTRUCTION

**Principle:** Security boundaries must be enforced by architecture, not by developer discipline; if it can be misconfigured, it will be. **The trust boundary is the GCP project edge** — anything inside our GCP project is trusted; anything that crosses out (third-party SaaS, external APIs, telemetry sinks) requires explicit justification.

**Why:** The platform handles business data via document processing, AI search over private corpora, and tool executions that may access external services. The permission model (skill access control, tool permissions, MCP App sandboxing) must be architecturally enforced. Every new feature must be evaluated for privilege escalation paths, data leakage across skill boundaries, and prompt injection vectors.

**The Privacy Boundary (internal vs external):**

| Zone | Examples | Allowed Data |
|------|----------|--------------|
| **Inside GCP project** (trusted) | Cloud Trace, Cloud Logging, GCS buckets, BigQuery, Firestore, Vertex AI, Agent Engine, Cloud Run | **Full content** — prompts, responses, documents, user messages, tool outputs. Captured by default for observability, debugging, eval, and audit. |
| **Outside GCP project** (untrusted) | Langfuse Cloud, Datadog SaaS, Phoenix Cloud, AgentOps, third-party LLM APIs (OpenAI, Anthropic direct), public webhooks | **Minimal data only** — request metadata, anonymized identifiers. **No prompts, no responses, no PII** unless the customer has signed a DPA covering that vendor. |

This distinction is the basis for two concrete v6 decisions: (1) Langfuse Cloud was rejected and replaced with Cloud Trace + Cloud Logging + BigQuery (all internal); (2) `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` is the default, because the captured content stays inside our project.

**Design docs that send data outside the GCP project must explicitly call this out** in their security section and justify why the egress is necessary. Design docs that capture telemetry inside GCP do not need special justification — that is the default.

**KPIs:**
- Zero privilege escalation paths: no way for a user to access skills or tools they are not authorized for
- Prompt injection resistance: agent instructions include injection defenses, tested in eval suite
- Data isolation: sessions and memory scoped per-user-per-skill, never shared across users
- Egress audit: 100% of data flows that leave the GCP project documented in design docs and security review

**Scoring guide:**
- **+1**: Feature enforces security through architecture (sandboxing, type-safe boundaries, deny-by-default), adds input validation, reduces the trust surface, or keeps data inside the GCP project edge
- **0**: Feature operates within existing security boundaries without changing them
- **-1**: Feature requires relaxing security boundaries, introduces user-controlled inputs that reach model context without sanitization, adds new trust relationships (loading external code), **or sends prompts/responses/PII outside the GCP project without an explicit egress justification**

**Tradeoff:** Deprioritizes flexibility and ease of integration. Sandboxed iframes are more restrictive than inline rendering. Deny-by-default tool permissions mean new tools require explicit enablement. Choosing GCP-native services over best-of-breed third-party SaaS means foregoing features in vendor tools (Langfuse's prompt management UI, Datadog's APM polish). These constraints slow development but prevent the class of security failures and data-egress incidents that kill B2B products.

---

### 10. THIN CLIENT, FAT PROTOCOL

**Principle:** The frontend must be a thin rendering layer over protocol events; business logic belongs in the backend, never in the browser.

**Why:** The <200KB initial JS budget exists because Aitana users access via varied devices and connections (including mobile web from Telegram links). The frontend renders AG-UI events, A2UI components, and MCP App frames. It does not make model decisions, manage context windows, or orchestrate tools. This keeps the frontend simple, testable, and fast — and means channels (Telegram, email, CLI) get the same backend logic automatically.

**KPIs:**
- Initial JS bundle: <200KB
- Frontend business logic: zero lines of model selection, tool orchestration, or data transformation in React components
- State management: only UI state in frontend (auth, routing, display); all domain state in backend sessions

**Scoring guide:**
- **+1**: Feature keeps logic in the backend, sends pre-computed data to the frontend, or reduces client-side complexity
- **0**: Feature is entirely backend or does not change frontend
- **-1**: Feature moves business logic to the frontend (client-side model routing, local tool execution, complex data transformations in React), or adds >20KB bundle size for a single feature

**Tradeoff:** Deprioritizes client-side responsiveness for complex interactions. Some features (real-time collaboration, local-first editing) benefit from client-side logic. The platform chooses server-authority over client autonomy because it simplifies the multi-channel story and keeps the security boundary in controlled territory.

---

## Axiom Alignment Template

Copy this table into every design doc, placed after **Goals** and before **Design**:

```markdown
## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | | |
| 2 | EARNED TRUST | | |
| 3 | SKILLS, NOT FEATURES | | |
| 4 | RIGHT MODEL, RIGHT MOMENT | | |
| 5 | GRACEFUL DEGRADATION | | |
| 6 | PROTOCOL OVER CUSTOM | | |
| 7 | API FIRST | | |
| 8 | OBSERVABLE BY DEFAULT | | |
| 9 | SECURE BY CONSTRUCTION | | |
| 10 | THIN CLIENT, FAT PROTOCOL | | |
| | **Net Score** | **—** | Threshold: >= +4 |

**Conflict Justifications:**
- [If any axiom scored -1, explain why the tradeoff is acceptable for this feature]
```

---

## Changelog

| Date | Version | Change | Justification |
|------|---------|--------|---------------|
| 2026-04-10 | 1.0 | Initial axiom set | Established product strategy framework |
| 2026-04-10 | 1.1 | Clarified internal/external privacy boundary in Axioms #8 (OBSERVABLE BY DEFAULT) and #9 (SECURE BY CONSTRUCTION) | Resolved ambiguity surfaced during cloud-infrastructure.md design: full prompt/response capture is the default inside GCP, but data crossing the project edge to third-party SaaS requires explicit justification. Same principle that ruled out Langfuse Cloud also permits aggressive internal observability. |

# ADK 2 upgrade · 10 — Prompt prefix + context caching

**Status**: Proposed
**Phase**: 2, after 03b
**Estimated**: 2–3 days
**Parent**: [template-adk-2-upgrade.md](template-adk-2-upgrade.md)
**Pattern source**: Horizon's "3-tier system prompt" (`horizon/conversation/system_prompt.py` → `build_static_instruction`; `docs/context-budget.md`)
**Last Updated**: 2026-10-16

## Problem

- We use no context caching. `ContextCacheConfig` and `static_instruction` appear nowhere in `backend/`.
- The system instruction is assembled per request by `compose_instruction_providers(wrap_with_platform_preamble, …)` (`adk/agent.py` ~1264). Anything volatile inside it (date, user context, per-turn state) changes the prefix bytes and defeats both Gemini implicit caching and explicit caches.
- For long-lived skills with big instructions and tool declarations, the prefix is the bulk of input tokens on every turn.

## What ADK 2.x gives

- `Agent(static_instruction=…)` puts a constant prefix ahead of every callback.
  - **Caution (2.12 `llm_agent.py`):** when `static_instruction` is set, the dynamic `instruction` moves from `system_instruction` into **user content**.
  - That changes what the model sees as "system". Run an eval before/after; don't assume it is neutral.
- `ContextCacheConfig` on the `App` (`cache_intervals`, `ttl_seconds`, `min_tokens`).
  - Caching starts on a session's second turn at the earliest, and needs a prefix ≥2048 tokens (Gemini 2.5) or ≥4096 (Gemini 3).
  - 2.8 adds **Anthropic cache breakpoints** (changelog line 844), so Claude skills benefit too.
  - The config propagates to `AgentTool` (1110).
- Non-text `static_instruction` is kept as a stable request prefix (859).

## Design

1. **Split the instruction into three tiers.**
   - **Static**: the platform preamble + the skill's SKILL.md instruction + tool usage rules. A pure function of the skill config and the admin preamble, built when the agent is built.
   - **Context**: per-session context, such as the documents in scope. It goes in a `before_model_callback`.
   - **Volatile**: date, iteration, notices. It rides a trailing reminder content part, so the prefix stays byte-stable.
2. **Enable `ContextCacheConfig`** on the App, with `min_tokens` tuned so short skills don't pay cache-creation cost.
3. **Admin preamble edits must invalidate predictably.** The static tier is keyed on the preamble version. The cache refreshes via `cache_intervals`/TTL, so a preamble edit takes effect within one TTL. Document this in the admin UI note (admin config is the durable control plane).
4. **Confidentiality:** cached contents stay inside our GCP project, and Vertex context caches are project-scoped. Do not put retrieved confidential document text in the static tier. It belongs in the context tier, which is per session.

## Measure

| Metric | How |
|---|---|
| Cached-token ratio | Native `gen_ai` usage metrics (`cached_content_token_count`) |
| First-token latency (TTFT) p50/p95 | `aiplatform skill probe` 10× on turn 2+ of a session, before/after |
| Cost per turn | Usage metadata × price, on a fixed 10-turn script |
| Quality | `adk eval`: no rubric regression, especially from the instruction moving system → user content |

## Exit

Cached-token ratio > 50% on turn ≥3 for the largest skills, no eval regression, and TTFT measured and recorded.

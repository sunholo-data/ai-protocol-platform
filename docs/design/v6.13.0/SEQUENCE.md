# v6.13.0 Build Sequence — Cross-Provider Model Fallback

**Origin:** 2026-07-16 — the Anthropic org usage cap took down every Claude-tier
skill; two shipped fixes (capacity-4xx→fallbackable, per-member `llm_request.model`
rewrite) made the Gemini fallback fire correctly, but a tool-using thinking agent
then crashed on `AnthropicException: 'tool_call_id'`. Mark: *"I want the fallback
to work cross provider if possible."*

**Theme:** *The safety net must survive the outage it exists for.* Cross-provider
tool exchanges don't round-trip (Anthropic/OpenAI require matched tool ids; Gemini
has none). Make cross-provider fallback valid for tool-using agents — without a
new format, without a quality cliff on the common single-model blip.

---

## Ordering

| Order | Doc | Priority | Est | Depends on | Notes |
|-------|-----|----------|-----|-----------|-------|
| 1 | [cross-provider-model-fallback.md](cross-provider-model-fallback.md) | P1 | ~2–3d | v6.7.0 model-reliability (shipped); the per-member model-rewrite fix (983fe1c, shipped) | Two-layer: same-provider ladder first + cross-provider request sanitizer (id backfill / orphan drop) + last-resort tool-history strip. Backend-only. |

## Timeline estimate

| Doc | Status | Est |
|-----|--------|-----|
| cross-provider-model-fallback.md | Planned | ~2–3d (0.5 ladder · 1–1.5 sanitizer · 0.5 strip + e2e) |

## What ships in v6.13.0

- Same-provider fallback ladder (claude-opus → claude-sonnet → claude-haiku → gemini last).
- A cross-provider request sanitizer so a tool-using agent's history validates on the fallback provider.
- `make model-fallback-e2e` — real cross-provider stream, asserts a tool-using skill answers via the fallback provider (the definition of done).

## Dependency Graph

```
v6.7.0 model-reliability (ResilientLlm + chains) ─┐
per-member llm_request.model rewrite (983fe1c) ───┼─► v6.13.0 cross-provider-model-fallback
v6.8.0 complexity-graded-model-routing ───────────┘
```

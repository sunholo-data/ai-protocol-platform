"""LLM cost tracking via OpenTelemetry metrics.

Records estimated cost per model call as an OTEL counter.
Called from ADK after_agent callbacks or middleware.

Cost estimates are approximate — based on published pricing verified 2026-08-13
(ai.google.dev/gemini-api/docs/pricing, ai.google.dev/gemini-api/docs/latest-model,
the claude-api skill's cached model table, and OpenAI pricing aggregators for the
GPT-5.6 family). Model keys must match `str(llm_request.model)` — the ADK model
object's api_name (dotted for Gemini: "gemini-3.7-flash"; provider-prefixed for
Claude/OpenAI via LiteLlm: "anthropic/claude-opus-4-8", "openai/gpt-5.6-sol" —
substring matching below doesn't need the prefix). Not every registry model has
confirmed pricing; an unmatched model returns 0.0 by design (see estimate_cost)
rather than a guess — extend this table instead of estimating.
"""

from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("aitana.llm")

cost_counter = _meter.create_counter(
    "llm.cost.total",
    description="Estimated LLM cost in USD",
    unit="USD",
)

token_counter = _meter.create_counter(
    "llm.tokens.total",
    description="Total tokens consumed",
    unit="tokens",
)

# Approximate cost per 1M tokens (input, output) — USD. Verified 2026-08-13.
# ORDER MATTERS: matching is substring containment (`known in key`), first hit
# wins — a "-lite" variant MUST precede its non-lite prefix (e.g.
# "gemini-2.5-flash" is a literal substring of "gemini-2.5-flash-lite") or the
# lite call prices at the non-lite rate. Every key below is currently
# collision-free by full-model-id specificity; keep it that way when adding
# entries — no generic short buckets ("claude-sonnet", "gpt-4o-mini"-style).
_COST_PER_1M: dict[str, tuple[float, float]] = {
    # Gemini — lite variants first (substring-of-non-lite hazard, see above)
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),  # prompts <= 200K; 200K+ is (2.50, 15.00), not modeled
    "gemini-3.6-flash": (1.50, 7.50),
    "gemini-3.7-flash": (0.75, 3.75),  # introductory through 2026-12-31; then (1.50, 7.50)
    # Claude (direct Anthropic API — see backend/CLAUDE.md privacy boundary)
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (3.00, 15.00),  # (2.00, 10.00) introductory through 2026-08-31, not modeled
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-fable-5": (10.00, 50.00),
    # OpenAI (via LiteLlm)
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-sol": (5.00, 30.00),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5.1-chat-latest": (1.25, 10.00),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a model call.

    Returns 0.0 for unknown models — we don't want to block on missing pricing.
    """
    # Normalize model name: strip version suffixes, provider prefixes
    key = model.lower()
    for known in _COST_PER_1M:
        if known in key:
            input_rate, output_rate = _COST_PER_1M[known]
            return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    return 0.0


def record_llm_cost(model: str, input_tokens: int, output_tokens: int) -> None:
    """Record LLM cost and token metrics. Call from after_agent callback."""
    cost = estimate_cost(model, input_tokens, output_tokens)
    attrs = {"model": model}
    cost_counter.add(cost, attrs)
    token_counter.add(input_tokens + output_tokens, attrs)

"""LLM cost tracking via OpenTelemetry metrics.

Records estimated cost per model call as an OTEL counter.
Called from ADK after_agent callbacks or middleware.

Cost estimates are approximate — based on published pricing as of 2026-04.
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

# Approximate cost per 1M tokens (input, output) — USD
# Source: published pricing pages as of 2026-04
_COST_PER_1M: dict[str, tuple[float, float]] = {
    # Gemini
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.0-flash": (0.10, 0.40),
    # Claude (via Vertex AI)
    "claude-sonnet": (3.00, 15.00),
    "claude-haiku": (0.25, 1.25),
    "claude-opus": (15.00, 75.00),
    # OpenAI (via LiteLlm)
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
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

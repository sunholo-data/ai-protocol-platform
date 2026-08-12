"""OTel SpanProcessor that stamps every started span with the current
task's tenant attributes.

Sprint 2.14 (v6.2.0) M1. Pairs with ``tenant_context.py``'s contextvar
+ ``set_tenant_context`` API.

Wire-up: registered on the OTel TracerProvider in
``observability.telemetry`` (M2). Once registered, every span started
on any tracer derived from that provider — request, agent, tool, LLM
— gets the current contextvar's attrs stamped at ``on_start``.

Performance budget: ≤50µs per span per the OTel reference (single
dict lookup + N set_attribute calls). Fork enrichers run in
``set_tenant_context``, not here — this processor is hot-path safe.
"""

from __future__ import annotations

from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

from observability.tenant_context import get_tenant_context


class TenantAttributeSpanProcessor(SpanProcessor):
    """Stamps every started span with the current task's tenant attrs.

    Standard OTel SpanProcessor — no surprises. Implements all four
    contract methods. ``on_start`` is where the work happens; the
    other three are no-ops or trivially-true returns.
    """

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        attrs = get_tenant_context()
        if not attrs:
            return
        for key, value in attrs.items():
            span.set_attribute(key, value)

    def on_end(self, span: ReadableSpan) -> None:
        # No-op: tenant attrs are set at start; end is too late to
        # influence what the exporter sees on a synchronous flush.
        return

    def shutdown(self) -> None:
        return

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

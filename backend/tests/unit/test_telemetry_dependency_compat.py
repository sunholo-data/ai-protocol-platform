"""Security and startup checks for the coordinated OpenTelemetry upgrade."""

from __future__ import annotations

import subprocess
import sys
from importlib import import_module


def test_oversized_tracestate_is_discarded_without_blocking_the_worker():
    # A subprocess deadline makes a vulnerable parser fail rather than hanging
    # the test worker. Exercise the real inbound propagator, not a version check.
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
from opentelemetry.trace import get_current_span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
propagator = TraceContextTextMapPropagator()
parent = '00-' + '1' * 32 + '-' + '2' * 16 + '-01'
valid = propagator.extract({'traceparent': parent, 'tracestate': 'vendor=platform'})
assert get_current_span(valid).get_span_context().trace_state['vendor'] == 'platform'
hostile = propagator.extract({'traceparent': parent, 'tracestate': ' ' * 100_000 + 'vendor=platform'})
span = get_current_span(hostile).get_span_context()
assert span.is_valid
assert len(span.trace_state) == 0
""",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_adk_and_gcp_telemetry_import_with_the_patched_sdk():
    # ADK 1.x imports the removed OTel Events API. Cloud-only imports must also
    # work: LOCAL_MODE tests alone do not exercise the deployed telemetry path.
    for module in (
        "google.adk.telemetry.google_cloud",
        "google.adk.telemetry.setup",
        "opentelemetry.exporter.cloud_logging",
        "opentelemetry.exporter.cloud_monitoring",
        "opentelemetry.exporter.cloud_trace",
        "opentelemetry.instrumentation.google_genai",
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    ):
        import_module(module)

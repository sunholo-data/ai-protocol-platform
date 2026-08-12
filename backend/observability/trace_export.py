"""Cloud Trace span export (issue #39).

The platform believed it was tracing. It was not: `setup_telemetry()` only
registered a tenant-attribution *span processor* on "whichever TracerProvider is
active", and nothing ever installed one. The OTel default is
``ProxyTracerProvider``, which has no ``add_span_processor`` — so that wire-up
silently no-op'd (exactly as its own comment warned it would) and no exporter
existed anywhere in runtime code. Cloud Trace held **2 traces for test and 5 for
dev across seven days**, which is what "not tracing" looks like.

ADK and the GenAI instrumentation emit spans regardless — model calls, tool
calls, delegations. Without an SDK provider they are created and dropped. This
module installs the provider and the Cloud Trace exporter so they land, which
also makes the pre-existing tenant-attribution processor start working.

**Sampling.** Default is sample-everything, which is right at current volume
(tens of turns a day) and is what makes a single reported session analysable
after the fact. Set ``OTEL_TRACES_SAMPLER_ARG`` to a 0..1 ratio to reduce it if
volume grows; ``TRACE_EXPORT_ENABLED=0`` disables export entirely.

**Failure policy.** Every failure here is swallowed and logged. Telemetry must
never take the request path down — if the exporter cannot start, the platform
runs untraced rather than not at all.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_installed = False


def _enabled() -> bool:
    return (os.environ.get("TRACE_EXPORT_ENABLED", "1").strip().lower()) not in {"0", "false", "no", "off"}


def _sampler():
    """ParentBased(TraceIdRatio) — ratio from OTEL_TRACES_SAMPLER_ARG, default 1.0.

    ParentBased so a sampled parent keeps its children: half a trace is worse
    than none when the question is "where did this turn spend its time".
    """
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, TraceIdRatioBased

    raw = os.environ.get("OTEL_TRACES_SAMPLER_ARG", "").strip()
    if not raw:
        return ParentBased(ALWAYS_ON)
    try:
        ratio = float(raw)
    except ValueError:
        logger.warning("OTEL_TRACES_SAMPLER_ARG=%r is not a number — sampling everything", raw)
        return ParentBased(ALWAYS_ON)
    if ratio >= 1.0:
        return ParentBased(ALWAYS_ON)
    if ratio <= 0.0:
        logger.warning("OTEL_TRACES_SAMPLER_ARG=%s disables sampling entirely", ratio)
    return ParentBased(TraceIdRatioBased(max(0.0, min(1.0, ratio))))


def install_trace_export(project_id: str | None = None, service_name: str | None = None) -> bool:
    """Install an SDK TracerProvider exporting to Cloud Trace. Returns True if installed.

    Idempotent, and deliberately does NOT replace a provider someone else already
    installed — if ADK (or a fork) has set up a real SDK provider, we attach our
    exporter to theirs instead of stealing the global.
    """
    global _installed
    if _installed:
        return True
    if not _enabled():
        logger.info("trace export disabled via TRACE_EXPORT_ENABLED")
        return False

    project = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if not project:
        logger.info("trace export: no GCP project resolved — skipping (spans stay local)")
        return False

    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        exporter = CloudTraceSpanExporter(project_id=project)
        # BatchSpanProcessor: export happens on a background thread, so a slow or
        # failing Cloud Trace call cannot add latency to a chat turn.
        processor = BatchSpanProcessor(exporter)

        provider = otel_trace.get_tracer_provider()
        if hasattr(provider, "add_span_processor"):
            # Someone already installed a real SDK provider — attach to it.
            provider.add_span_processor(processor)
            logger.info("trace export: attached Cloud Trace exporter to existing %s", type(provider).__name__)
        else:
            resource = Resource.create(
                {
                    "service.name": service_name
                    or os.environ.get("K_SERVICE")
                    or os.environ.get("SERVICE_NAME")
                    or "aitana-backend",
                    "service.namespace": "aitana",
                    "deployment.environment": os.environ.get("DEPLOY_ENV") or _env_from_project(project),
                }
            )
            new_provider = TracerProvider(resource=resource, sampler=_sampler())
            new_provider.add_span_processor(processor)
            otel_trace.set_tracer_provider(new_provider)
            logger.info("trace export: installed TracerProvider → Cloud Trace (project=%s)", project)

        _installed = True
        return True
    except Exception as exc:  # never let telemetry break startup
        logger.warning("trace export: setup failed (%s: %s) — running untraced", type(exc).__name__, exc)
        return False


def _env_from_project(project: str) -> str:
    """`your-project-id-test` → `test`, so traces are filterable by env."""
    for env in ("production", "prod", "test", "dev"):
        if project.endswith(f"-{env}"):
            return "prod" if env == "production" else env
    return "unknown"


__all__ = ["install_trace_export"]

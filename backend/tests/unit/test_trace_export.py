"""Cloud Trace export contract (issue #39).

The bug: `setup_telemetry()` registered a tenant-attribution span processor on
"whichever TracerProvider is active" and nothing ever installed one. OTel's
default is `ProxyTracerProvider`, which has no `add_span_processor`, so that
wire-up silently no-op'd and no exporter existed in runtime code at all. ADK
emitted spans for every model call, tool call and delegation; all were dropped.
Cloud Trace held 2 traces for test across seven days.

The load-bearing assertion is `test_installs_a_provider_that_can_take_processors`
— that is the exact precondition the old code assumed and never had.
"""

from __future__ import annotations

import importlib

import pytest
from opentelemetry import trace as otel_trace


def mod_trace(_mod):
    """The `opentelemetry.trace` module object that trace_export imports lazily."""
    import opentelemetry.trace as t

    return t


@pytest.fixture
def fresh(monkeypatch):
    """Reload the module (clearing its `_installed` latch) and restore the global provider."""
    saved = otel_trace._TRACER_PROVIDER  # restoring global state the SDK owns

    def _load(**env):
        for k in ("TRACE_EXPORT_ENABLED", "OTEL_TRACES_SAMPLER_ARG", "GOOGLE_CLOUD_PROJECT", "GCP_PROJECT"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        import observability.trace_export as mod

        return importlib.reload(mod)

    yield _load
    otel_trace._TRACER_PROVIDER = saved


class TestInstall:
    def test_installs_a_provider_when_the_active_one_takes_no_processors(self, fresh, monkeypatch):
        """THE REGRESSION, driven explicitly.

        OTel's default `ProxyTracerProvider` has no `add_span_processor`, so the
        pre-existing tenant-attribution wire-up silently no-op'd and nothing was
        ever exported. Asserting on the real global would make this test
        order-dependent — the provider is process-wide and set-once, and other
        test modules install their own — so the proxy case is injected.
        """
        mod = fresh(GOOGLE_CLOUD_PROJECT="your-project-id")

        class ProxyLike:  # no add_span_processor, exactly like the OTel default
            pass

        installed: dict = {}
        monkeypatch.setattr(mod_trace(mod), "get_tracer_provider", lambda: ProxyLike())
        monkeypatch.setattr(mod_trace(mod), "set_tracer_provider", lambda p: installed.setdefault("provider", p))

        assert mod.install_trace_export() is True
        provider = installed.get("provider")
        assert provider is not None, "must install an SDK provider when the active one cannot take processors"
        assert hasattr(provider, "add_span_processor"), "tenant attribution silently no-ops without this"

    def test_attaches_to_an_existing_sdk_provider_instead_of_stealing_it(self, fresh, monkeypatch):
        """If ADK (or a fork) already installed a real provider, don't replace it."""
        mod = fresh(GOOGLE_CLOUD_PROJECT="your-project-id")

        added: list = []

        class SdkLike:
            def add_span_processor(self, p):
                added.append(p)

        stolen: list = []
        monkeypatch.setattr(mod_trace(mod), "get_tracer_provider", lambda: SdkLike())
        monkeypatch.setattr(mod_trace(mod), "set_tracer_provider", lambda p: stolen.append(p))

        assert mod.install_trace_export() is True
        assert len(added) == 1, "exporter must attach to the existing provider"
        assert not stolen, "must not replace a provider someone else installed"

    def test_idempotent(self, fresh):
        mod = fresh(GOOGLE_CLOUD_PROJECT="your-project-id")
        assert mod.install_trace_export() is True
        assert mod.install_trace_export() is True  # latched, no second provider

    def test_no_project_is_skipped_not_crashed(self, fresh):
        """Local/CI without a project must boot, untraced."""
        mod = fresh()
        assert mod.install_trace_export() is False

    def test_disable_switch(self, fresh):
        mod = fresh(GOOGLE_CLOUD_PROJECT="your-project-id", TRACE_EXPORT_ENABLED="0")
        assert mod.install_trace_export() is False

    def test_exporter_failure_does_not_raise(self, fresh, monkeypatch):
        """Telemetry must never take the request path down."""
        mod = fresh(GOOGLE_CLOUD_PROJECT="your-project-id")

        def boom(*a, **k):
            raise RuntimeError("credentials unavailable")

        monkeypatch.setattr("opentelemetry.exporter.cloud_trace.CloudTraceSpanExporter", boom)
        assert mod.install_trace_export() is False  # logged, swallowed


class TestSampling:
    def test_defaults_to_everything(self, fresh):
        """At this volume, a reported session must be analysable after the fact."""
        mod = fresh(GOOGLE_CLOUD_PROJECT="p")
        assert "root:AlwaysOnSampler" in mod._sampler().get_description()

    def test_ratio_is_honoured(self, fresh):
        mod = fresh(GOOGLE_CLOUD_PROJECT="p", OTEL_TRACES_SAMPLER_ARG="0.1")
        assert "root:TraceIdRatioBased{0.1}" in mod._sampler().get_description()

    def test_garbage_ratio_falls_back_to_everything(self, fresh):
        """A typo must not silently switch tracing off."""
        mod = fresh(GOOGLE_CLOUD_PROJECT="p", OTEL_TRACES_SAMPLER_ARG="lots")
        assert "root:AlwaysOnSampler" in mod._sampler().get_description()


class TestEnvLabel:
    @pytest.mark.parametrize(
        ("project", "expected"),
        [
            ("your-project-id-dev", "dev"),
            ("your-project-id-test", "test"),
            ("your-project-id-prod", "prod"),
            ("some-fork-project", "unknown"),
        ],
    )
    def test_deployment_environment_is_filterable(self, fresh, project, expected):
        mod = fresh(GOOGLE_CLOUD_PROJECT=project)
        assert mod._env_from_project(project) == expected

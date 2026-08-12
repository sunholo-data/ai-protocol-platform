"""Runtime compaction settings (tuning console 1b) — precedence and defence.

Compaction runs inside a user's turn, so the governing rule is that NO stored
value can fail a turn: every invalid input falls back to the coded default and
says so. The second rule is trap 5 — never mutate the shared config object; a
per-request override must be a copy or one session's experiment leaks into every
other session in the container.
"""

from __future__ import annotations

import pytest

from adk import compaction_settings as cs
from db.models import CompactionSettings

# Captured before the autouse fixture stubs it out, so the fail-open test below
# can exercise the REAL loader.
_real_get_compaction_settings = cs.get_compaction_settings


@pytest.fixture(autouse=True)
def _no_stored_settings(monkeypatch):
    """Default to 'admin has configured nothing' so each test opts in."""
    monkeypatch.setattr(cs, "get_compaction_settings", lambda: CompactionSettings())


def _stored(monkeypatch, **kwargs):
    monkeypatch.setattr(cs, "get_compaction_settings", lambda: CompactionSettings(**kwargs))


class TestSummarizerModel:
    def test_unset_uses_the_coded_tier(self):
        assert cs.summarizer_model_ref("pro") == "pro"

    def test_admin_tier_wins(self, monkeypatch):
        _stored(monkeypatch, summarizer_model="lite")
        assert cs.summarizer_model_ref("pro") == "lite"

    @pytest.mark.parametrize("bad", ["gemini-3.6-flash-preview-11-2025", "publishers/google/models/gemini-2.5-pro"])
    def test_raw_api_name_is_refused(self, monkeypatch, bad):
        """`entry_for()` returns None for api names by design, so passing one on
        would silently take a fallback chain and the admin would see no effect
        (findings log trap 8)."""
        _stored(monkeypatch, summarizer_model=bad)
        assert cs.summarizer_model_ref("pro") == "pro"


class TestSummarizerPrompt:
    def test_unset_uses_the_shipped_prompt(self):
        assert cs.summarizer_prompt("SHIPPED {conversation_history}") == "SHIPPED {conversation_history}"

    def test_admin_prompt_wins(self, monkeypatch):
        _stored(monkeypatch, summarizer_prompt="CUSTOM {conversation_history}")
        assert cs.summarizer_prompt("SHIPPED {conversation_history}") == "CUSTOM {conversation_history}"

    def test_missing_placeholder_falls_back_instead_of_raising_mid_turn(self, monkeypatch):
        """Without the placeholder `str.format` raises INSIDE compaction, i.e.
        inside a user's turn. The admin route rejects it; this is the second gate
        for a doc written by an older build or edited by hand in the console."""
        _stored(monkeypatch, summarizer_prompt="no placeholder here")
        assert cs.summarizer_prompt("SHIPPED {conversation_history}") == "SHIPPED {conversation_history}"

    def test_whitespace_only_is_treated_as_unset(self, monkeypatch):
        _stored(monkeypatch, summarizer_prompt="   \n ")
        assert cs.summarizer_prompt("SHIPPED {conversation_history}") == "SHIPPED {conversation_history}"


class TestSecondPassPolicy:
    def test_env_is_the_default_when_admin_has_not_decided(self, monkeypatch):
        monkeypatch.setenv("COMPACTION_SECOND_PASS_ENABLED", "true")
        assert cs.second_pass_enabled() is True
        monkeypatch.delenv("COMPACTION_SECOND_PASS_ENABLED")
        assert cs.second_pass_enabled() is False

    def test_admin_toggle_overrides_env_in_both_directions(self, monkeypatch):
        """The point of the knob: switch a provisioned env on (or off) without a
        redeploy."""
        monkeypatch.delenv("COMPACTION_SECOND_PASS_ENABLED", raising=False)
        _stored(monkeypatch, second_pass_enabled=True)
        assert cs.second_pass_enabled() is True

        monkeypatch.setenv("COMPACTION_SECOND_PASS_ENABLED", "true")
        _stored(monkeypatch, second_pass_enabled=False)
        assert cs.second_pass_enabled() is False

    def test_idle_seconds_precedence(self, monkeypatch):
        assert cs.second_pass_idle_seconds(2700) == 2700
        _stored(monkeypatch, second_pass_idle_seconds=600)
        assert cs.second_pass_idle_seconds(2700) == 600


class TestThresholdOverrides:
    """`apply_threshold_overrides` is what reaches ADK's per-invocation config."""

    def _config(self, **kwargs):
        from google.adk.apps.app import EventsCompactionConfig

        base = {"compaction_interval": 40, "overlap_size": 5, "token_threshold": 250_000, "event_retention_size": 60}
        base.update(kwargs)
        return EventsCompactionConfig(**base)

    def test_no_overrides_returns_the_same_object(self):
        config = self._config()
        assert cs.apply_threshold_overrides(config) is config

    def test_overrides_apply_to_a_copy_never_the_shared_object(self, monkeypatch):
        """Trap 5: ADK mutates these in place and ours are shared module-level,
        so an in-place edit would leak one experiment into every session."""
        _stored(monkeypatch, token_threshold=3000, event_retention_size=5)
        config = self._config()
        updated = cs.apply_threshold_overrides(config)

        assert updated is not config
        assert updated.token_threshold == 3000
        assert updated.event_retention_size == 5
        assert config.token_threshold == 250_000, "shared config was mutated"
        assert config.event_retention_size == 60

    def test_one_threshold_alone_is_fine_when_the_other_is_already_set(self, monkeypatch):
        _stored(monkeypatch, token_threshold=3000)
        updated = cs.apply_threshold_overrides(self._config())
        assert updated.token_threshold == 3000
        assert updated.event_retention_size == 60

    def test_half_configured_override_is_refused_not_invalid(self, monkeypatch):
        """ADK's validator rejects token_threshold without event_retention_size.
        Refusing beats writing a config that raises on construction."""
        _stored(monkeypatch, token_threshold=3000)
        config = self._config(token_threshold=None, event_retention_size=None)
        assert cs.apply_threshold_overrides(config) is config

    def test_a_broken_settings_read_never_propagates(self, monkeypatch):
        def _boom():
            raise RuntimeError("firestore down")

        monkeypatch.setattr(cs, "get_compaction_settings", _boom)
        config = self._config()
        assert cs.apply_threshold_overrides(config) is config


def test_settings_read_failure_yields_coded_defaults(monkeypatch):
    """Fail-open at the source: a store blip must not change behaviour."""
    import config.platform_config as pc

    monkeypatch.setattr(pc, "get_platform_config", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert _real_get_compaction_settings() == CompactionSettings()


class TestOverridesReachTheRoutinePath:
    """The guard that was missing when the knob shipped inert (2026-08-11).

    ADK's two compaction paths read DIFFERENT objects. The one that does the
    ROUTINE work — post-invocation, `runners.py:622` — reads
    `app.events_compaction_config`. An override applied only to the
    per-invocation config (the pre-request path) has NO effect on it, and the
    original tests missed that by asserting on the override FUNCTION rather than
    on the object production builds. Assert against the built App instead.
    """

    def _built_app(self, monkeypatch):
        from google.adk.agents import BaseAgent

        from adk import agui

        built = {}

        class _FakeADKAgent:
            @classmethod
            def from_app(cls, app, **kwargs):
                built["app"] = app
                return cls()

        monkeypatch.setattr(agui, "ADKAgent", _FakeADKAgent)
        agui.build_agui_adk_agent(BaseAgent(name="probe_agent"), user_id="u1")
        return built["app"]

    def test_admin_thresholds_reach_the_app_the_runner_compacts_with(self, monkeypatch):
        _stored(monkeypatch, token_threshold=3000, event_retention_size=5)
        app = self._built_app(monkeypatch)

        assert app.events_compaction_config is not None, "no compaction config on the request App"
        assert app.events_compaction_config.token_threshold == 3000
        assert app.events_compaction_config.event_retention_size == 5

    def test_disabling_compaction_clears_the_token_trigger(self, monkeypatch):
        _stored(monkeypatch, enabled=False)
        app = self._built_app(monkeypatch)
        assert app.events_compaction_config.token_threshold is None

    def test_the_shared_deployment_app_is_never_mutated(self, monkeypatch):
        """Per-request copy only — `base_app` is process-wide (trap 5)."""
        from adk import agui

        base_before = agui._deployment_app().events_compaction_config
        original_threshold = base_before.token_threshold

        _stored(monkeypatch, token_threshold=3000, event_retention_size=5)
        self._built_app(monkeypatch)

        assert agui._deployment_app().events_compaction_config.token_threshold == original_threshold

"""Unit tests for ADK service factories — env-var-driven backend selection."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from adk import session as session_mod


class TestGetSessionService:
    def setup_method(self):
        session_mod._reset_session_service_for_tests()

    def teardown_method(self):
        session_mod._reset_session_service_for_tests()

    def test_returns_in_memory_when_no_env(self):
        with patch.dict("os.environ", {}, clear=True):
            svc = session_mod.get_session_service()
        assert type(svc).__name__ == "InMemorySessionService"

    def test_returns_vertex_ai_when_env_set(self):
        env = {
            "AGENT_ENGINE_ID": "projects/p/locations/l/reasoningEngines/123",
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "europe-west1",
        }
        with patch.dict("os.environ", env, clear=True):
            svc = session_mod.get_session_service()
        # Assert the CONTRACT (a Vertex-backed service), not an exact class name:
        # since 8dcbaf5 the factory returns ResilientVertexSessionService, which
        # SUBCLASSES VertexAiSessionService to add write retry + loud failure
        # (issue #30). isinstance still catches the regression that matters —
        # silently falling back to InMemory, which loses every session.
        from google.adk.sessions import VertexAiSessionService

        assert isinstance(svc, VertexAiSessionService)
        assert type(svc).__name__ == "ResilientVertexSessionService"


class TestGetMemoryService:
    def test_returns_in_memory_when_no_env(self):
        with patch.dict("os.environ", {}, clear=True):
            svc = session_mod.get_memory_service()
        assert type(svc).__name__ == "InMemoryMemoryService"

    def test_returns_vertex_ai_when_env_set(self):
        env = {
            "AGENT_ENGINE_ID": "projects/p/locations/l/reasoningEngines/123",
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "europe-west1",
        }
        with patch.dict("os.environ", env, clear=True):
            svc = session_mod.get_memory_service()
        assert type(svc).__name__ == "VertexAiMemoryBankService"


class TestGetArtifactService:
    def setup_method(self):
        session_mod._reset_artifact_service_for_tests()

    def teardown_method(self):
        session_mod._reset_artifact_service_for_tests()

    def test_returns_in_memory_when_no_env(self):
        with patch.dict("os.environ", {}, clear=True):
            svc = session_mod.get_artifact_service()
        assert type(svc).__name__ == "InMemoryArtifactService"

    def test_returns_gcs_when_bucket_set(self):
        # GcsArtifactService instantiates a storage.Client in __init__, which
        # calls google.auth.default() — fine on Cloud Run, fatal on CI runners
        # without ADC. Mock the client so the factory branch is exercised
        # without touching real credentials.
        env = {"ADK_ARTIFACT_BUCKET": "my-bucket", "GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict("os.environ", env, clear=True), patch("google.cloud.storage.Client"):
            svc = session_mod.get_artifact_service()
        assert type(svc).__name__ == "GcsArtifactService"


class TestGetServiceUris:
    """Test the URI helpers used by get_fast_api_app()."""

    def test_session_uri_none_when_no_env(self):
        with patch.dict("os.environ", {}, clear=True):
            assert session_mod.get_session_service_uri() is None

    def test_session_uri_agent_engine_when_set(self):
        env = {
            "AGENT_ENGINE_ID": "projects/p/locations/l/reasoningEngines/123",
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "europe-west1",
        }
        with patch.dict("os.environ", env, clear=True):
            uri = session_mod.get_session_service_uri()
        assert uri is not None
        assert "agentengine://" in uri

    def test_artifact_uri_none_when_no_env(self):
        with patch.dict("os.environ", {}, clear=True):
            assert session_mod.get_artifact_service_uri() is None

    def test_artifact_uri_gcs_when_bucket_set(self):
        env = {"ADK_ARTIFACT_BUCKET": "my-bucket"}
        with patch.dict("os.environ", env, clear=True):
            uri = session_mod.get_artifact_service_uri()
        assert uri == "gs://my-bucket"


class TestGetCompactionConfig:
    """Compaction tuning per model family.

    Compaction is LOSSY AND INVISIBLE: ADK filters the compacted raw events out
    of the model's request while the UI keeps showing them, so an over-eager
    setting degrades answers with no visible symptom. That failure mode is why
    these assertions are specific rather than "returns something sensible".
    See docs/design/v6.23.0/conversation-context-fidelity.md.
    """

    def test_gemini_3_flash_gets_large_window_config(self):
        cfg = session_mod.get_compaction_config("gemini-3-flash-preview")
        assert cfg.token_threshold == 250_000
        assert cfg.event_retention_size == 60
        assert cfg.compaction_interval == 40
        assert cfg.overlap_size == 5

    def test_gemini_3_1_pro_gets_large_window_config(self):
        cfg = session_mod.get_compaction_config("gemini-3.1-pro-preview")
        assert cfg.token_threshold == 250_000

    def test_gpt_5_4_gets_large_window_config(self):
        # GPT-5.4 has a 1M context window — same tier as Gemini
        cfg = session_mod.get_compaction_config("gpt-5.4")
        assert cfg.token_threshold == 250_000
        assert cfg.compaction_interval == 40

    def test_claude_gets_small_window_config(self):
        cfg = session_mod.get_compaction_config("claude-sonnet-4-6")
        assert cfg.token_threshold == 120_000
        assert cfg.compaction_interval == 20
        assert cfg.overlap_size == 4

    def test_claude_opus_gets_small_window_config(self):
        cfg = session_mod.get_compaction_config("claude-opus-4-7")
        assert cfg.token_threshold == 120_000

    def test_gpt_5_1_gets_small_window_config(self):
        # GPT-5.1 has 400K context — smaller threshold than the 1M tier
        cfg = session_mod.get_compaction_config("gpt-5.1-chat-latest")
        assert cfg.token_threshold == 120_000

    def test_unknown_model_assumes_the_smallest_window(self):
        # Fail safe: compacting too eagerly degrades an answer, overflowing the
        # context fails the turn outright. Unknown models get the tighter config.
        cfg = session_mod.get_compaction_config("unknown-future-model")
        assert cfg.token_threshold == 120_000
        assert cfg.compaction_interval == 20

    def test_every_family_sets_a_token_threshold(self):
        """The regression guard for the actual 2026-08-06 defect.

        Before this change every family relied on `compaction_interval` alone,
        so compaction fired on a raw count of turns regardless of how small
        those turns were — discarding a 12-turn, few-thousand-token
        conversation on a model with a 1M-token window.
        """
        for model in (
            "gemini-2.5-flash",
            "gemini-3-flash-preview",
            "gpt-5.4",
            "claude-opus-4-7",
            "gpt-5.1-chat-latest",
            "some-unknown-model",
        ):
            cfg = session_mod.get_compaction_config(model)
            assert cfg.token_threshold is not None, f"{model} has no token trigger"
            assert cfg.token_threshold > 0
            # ADK's validator requires the pair; assert it explicitly so a
            # future edit can't drop retention and silently keep zero raw
            # events after a token compaction.
            assert cfg.event_retention_size is not None, f"{model} retains no raw events"
            assert cfg.event_retention_size > 0

    def test_large_window_never_compacts_sooner_than_small_window(self):
        """A bigger context must never be the more aggressive setting.

        Cheap invariant that catches a copy-paste transposition of the two
        tiers — the kind of edit that is invisible in review and only shows up
        as mysteriously worse answers on the flagship model.
        """
        large = session_mod.get_compaction_config("gemini-3-flash-preview")
        small = session_mod.get_compaction_config("claude-opus-4-7")
        assert large.token_threshold > small.token_threshold
        assert large.compaction_interval >= small.compaction_interval

    def test_token_threshold_env_override_applies(self):
        with patch.dict("os.environ", {"COMPACTION_TOKEN_THRESHOLD": "40000"}, clear=True):
            cfg = session_mod.get_compaction_config("gemini-3-flash-preview")
        assert cfg.token_threshold == 40_000
        # Only the threshold moves; the backstop is untouched.
        assert cfg.compaction_interval == 40

    def test_override_does_not_mutate_the_shared_config(self):
        """`model_copy`, not in-place mutation.

        The configs are module-level singletons shared by every session, so an
        in-place override would leak across models and outlive the patched env.
        """
        with patch.dict("os.environ", {"COMPACTION_TOKEN_THRESHOLD": "40000"}, clear=True):
            session_mod.get_compaction_config("gemini-3-flash-preview")
        with patch.dict("os.environ", {}, clear=True):
            assert session_mod.get_compaction_config("gemini-3-flash-preview").token_threshold == 250_000

    @pytest.mark.parametrize("bad", ["not-a-number", "0", "-5", ""])
    def test_bad_override_falls_back_to_the_default(self, bad):
        # A typo must never silently restore turn-count-only compaction: that
        # regression is invisible at runtime and reads as a model quality bug.
        with patch.dict("os.environ", {"COMPACTION_TOKEN_THRESHOLD": bad}, clear=True):
            cfg = session_mod.get_compaction_config("gemini-3-flash-preview")
        assert cfg.token_threshold == 250_000


class TestCompactionConfigReachesTheApp:
    """The second half of the 2026-08-06 defect.

    `get_compaction_config` was correct and *never applied*: app.py passed a
    hardcoded `gemini-2-5-flash` lookup, so every session — including Claude
    sessions on a ~200K window — ran the config computed for a 1M window. The
    tuning table above had never once reached a session.
    """

    def test_app_compaction_config_is_not_hardcoded_to_gemini(self):
        import app as app_mod

        cfg = app_mod.app.events_compaction_config
        assert cfg is not None, "App must carry a compaction config"
        # Whatever the deploy default resolves to, it must carry a token
        # trigger — the property that makes compaction size-aware.
        assert cfg.token_threshold is not None
        assert cfg.token_threshold > 0

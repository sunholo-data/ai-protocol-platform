"""Platform-config loader: default fallback, TTL cache, update-invalidates (v6.14.0)."""

from __future__ import annotations

import pytest

import config.platform_config as pc


@pytest.fixture(autouse=True)
def _reset_cache():
    pc.invalidate_cache()
    yield
    pc.invalidate_cache()


def test_returns_code_default_when_no_doc(monkeypatch):
    monkeypatch.setattr(pc.fs, "get_document", lambda *a, **k: None)
    config = pc.get_platform_config()
    assert config.enabled is True
    assert config.preamble == pc.DEFAULT_PREAMBLE
    assert "Aitana" in config.preamble


def test_reads_stored_override(monkeypatch):
    monkeypatch.setattr(
        pc.fs,
        "get_document",
        lambda *a, **k: {"preamble": "CUSTOM", "enabled": False},
    )
    config = pc.get_platform_config()
    assert config.preamble == "CUSTOM"
    assert config.enabled is False


def test_fails_open_to_default_on_read_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(pc.fs, "get_document", _boom)
    config = pc.get_platform_config()
    assert config.preamble == pc.DEFAULT_PREAMBLE


def test_cache_avoids_second_read(monkeypatch):
    calls = {"n": 0}

    def _get(*a, **k):
        calls["n"] += 1
        return {"preamble": "CACHED", "enabled": True}

    monkeypatch.setattr(pc.fs, "get_document", _get)
    assert pc.get_platform_config().preamble == "CACHED"
    assert pc.get_platform_config().preamble == "CACHED"
    assert calls["n"] == 1  # second read served from cache


def test_update_persists_stamps_and_invalidates(monkeypatch):
    written: dict = {}

    monkeypatch.setattr(pc.fs, "get_document", lambda *a, **k: None)
    monkeypatch.setattr(pc.fs, "set_document", lambda coll, doc, data, **k: written.update(data))

    config = pc.update_platform_config({"preamble": "NEW", "enabled": True}, updated_by="uid-123")
    assert config.preamble == "NEW"
    assert config.updated_by == "uid-123"
    assert config.updated_at > 0
    # Persisted with alias keys for Firestore.
    assert written["preamble"] == "NEW"
    assert written["updatedBy"] == "uid-123"


def test_update_rejects_over_cap_preamble(monkeypatch):
    monkeypatch.setattr(pc.fs, "get_document", lambda *a, **k: None)
    monkeypatch.setattr(pc.fs, "set_document", lambda *a, **k: None)
    with pytest.raises(ValueError):
        pc.update_platform_config({"preamble": "x" * 20_001})


# --- Compaction settings block (tuning console 1b) -------------------------


def test_pre_1b_doc_without_compaction_still_loads(monkeypatch):
    """Every doc written before v6.23.0 lacks the block. It must load with the
    settings empty (= all coded defaults), not blow up the hot prompt path."""
    monkeypatch.setattr(pc.fs, "get_document", lambda *a, **k: {"preamble": "OLD", "enabled": True})
    config = pc.get_platform_config()
    assert config.preamble == "OLD"
    assert config.compaction.token_threshold is None
    assert config.compaction.second_pass_enabled is None


def test_stored_compaction_block_round_trips_camel_case(monkeypatch):
    """Firestore stores camelCase (by_alias); the model must read it back."""
    monkeypatch.setattr(
        pc.fs,
        "get_document",
        lambda *a, **k: {
            "preamble": "P",
            "enabled": True,
            "compaction": {
                "enabled": True,
                "tokenThreshold": 3000,
                "eventRetentionSize": 5,
                "summarizerModel": "lite",
                "secondPassEnabled": True,
                "secondPassIdleSeconds": 600,
            },
        },
    )
    settings = pc.get_platform_config().compaction
    assert settings.token_threshold == 3000
    assert settings.event_retention_size == 5
    assert settings.summarizer_model == "lite"
    assert settings.second_pass_enabled is True
    assert settings.second_pass_idle_seconds == 600


def test_an_invalid_stored_compaction_value_falls_back_to_default_config(monkeypatch):
    """A zero threshold (ADK rejects it) must not strand the whole config —
    the loader degrades to the code default rather than raising on every turn."""
    monkeypatch.setattr(
        pc.fs,
        "get_document",
        lambda *a, **k: {"preamble": "P", "enabled": True, "compaction": {"tokenThreshold": 0}},
    )
    config = pc.get_platform_config()
    assert config.preamble == pc.DEFAULT_PREAMBLE
    assert config.compaction.token_threshold is None

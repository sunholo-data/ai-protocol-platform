"""Tests for config.gcp.neutralize_api_key_in_vertex_mode.

A deploy that mounts GOOGLE_API_KEY alongside GOOGLE_GENAI_USE_VERTEXAI=true
makes the genai client attach the key to Vertex calls → Vertex Sessions/Memory
401 CREDENTIALS_MISSING → every chat turn silently breaks. The app self-heals by
popping the offending vars at startup (before any genai client is created).
"""

from __future__ import annotations

from config.gcp import neutralize_api_key_in_vertex_mode


def test_pops_api_key_in_vertex_mode(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-secret")

    popped = neutralize_api_key_in_vertex_mode()

    assert popped == ["GOOGLE_API_KEY"]
    import os

    assert "GOOGLE_API_KEY" not in os.environ  # actively unset, not just logged


def test_pops_all_key_variants_in_vertex_mode(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")  # case-insensitive
    monkeypatch.setenv("GOOGLE_API_KEY", "k1")
    monkeypatch.setenv("GEMINI_API_KEY", "k2")
    monkeypatch.setenv("GOOGLE_GENAI_API_KEY", "k3")

    popped = neutralize_api_key_in_vertex_mode()

    assert set(popped) == {"GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_API_KEY"}
    import os

    assert not any(v in os.environ for v in popped)


def test_noop_when_no_key_set(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_API_KEY", raising=False)

    assert neutralize_api_key_in_vertex_mode() == []


def test_leaves_key_alone_when_not_vertex_mode(monkeypatch):
    """Non-Vertex (AI Studio / Express) mode legitimately uses GOOGLE_API_KEY —
    do NOT unset it there."""
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-keep-me")

    assert neutralize_api_key_in_vertex_mode() == []
    import os

    assert os.environ["GOOGLE_API_KEY"] == "AIza-keep-me"  # preserved

"""Platform preamble prepend behaviour (v6.14.0).

The preamble leads every skill's prompt (prefix), and is a transparent no-op when
disabled or empty. Precedence: because the preamble comes FIRST, the skill body
that follows can override it for the skill's domain.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import adk.platform_preamble_context as ppc
from db.models import PlatformConfig

_BASE = "You are a PPA expert."


def _patch_config(monkeypatch, config: PlatformConfig) -> None:
    monkeypatch.setattr(ppc, "get_platform_config", lambda: config)


def test_prepends_preamble_before_skill_body(monkeypatch):
    _patch_config(monkeypatch, PlatformConfig(preamble="You are part of Aitana.", enabled=True))
    out = ppc.render_instruction_with_platform_preamble(_BASE)
    assert out == "You are part of Aitana.\n\nYou are a PPA expert."
    # Prefix ordering: platform identity leads, skill body follows (precedence).
    assert out.index("Aitana") < out.index("PPA expert")


def test_no_op_when_disabled(monkeypatch):
    _patch_config(monkeypatch, PlatformConfig(preamble="You are part of Aitana.", enabled=False))
    assert ppc.render_instruction_with_platform_preamble(_BASE) == _BASE


def test_no_op_when_empty(monkeypatch):
    _patch_config(monkeypatch, PlatformConfig(preamble="   ", enabled=True))
    assert ppc.render_instruction_with_platform_preamble(_BASE) == _BASE


def test_fails_open_when_config_read_raises(monkeypatch):
    def _boom():
        raise RuntimeError("firestore down")

    monkeypatch.setattr(ppc, "get_platform_config", _boom)
    # A config failure must never strip the skill's own instructions.
    assert ppc.render_instruction_with_platform_preamble(_BASE) == _BASE


def test_wrapper_chains_over_a_provider(monkeypatch):
    _patch_config(monkeypatch, PlatformConfig(preamble="PLATFORM", enabled=True))

    async def _base_provider(_ctx):
        return _BASE

    provider = ppc.wrap_with_platform_preamble(_base_provider)
    out = asyncio.run(provider(SimpleNamespace(state={})))
    assert out == "PLATFORM\n\nYou are a PPA expert."


def test_preamble_length_is_capped():
    # The model enforces the platform-wide TTFT budget on the write path.
    with pytest.raises(ValueError):
        PlatformConfig(preamble="x" * 20_001)

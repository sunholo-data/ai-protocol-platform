"""Retry + Gemini failover for tool-internal structured-output calls (v6.14.0).

The state machine (retry transient, fall back across the chain, raise typed when
exhausted) is validated here by patching ``classify`` to drive each branch — the
classifier itself is covered by test_model_errors. The chain builder is validated
against the real registry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import tools.resilient_genai as rg
from adk.model_errors import ErrorClass, ModelTurnError


def _resp(text: str = "{}") -> MagicMock:
    r = MagicMock()
    r.text = text
    return r


_TRANSIENT = ErrorClass(transient=True, fallbackable=True, code="MODEL_RATE_LIMITED")
_FALLBACK_ONLY = ErrorClass(transient=False, fallbackable=True, code="MODEL_UNAVAILABLE")
_FATAL = ErrorClass(transient=False, fallbackable=False, code="MODEL_REQUEST_INVALID")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # Don't actually sleep through backoff in tests.
    monkeypatch.setattr(rg, "_async_sleep", AsyncMock())


def _patch_client(gen):
    client = MagicMock()
    client.aio.models.generate_content = gen
    return patch.object(rg.genai, "Client", return_value=client)


@pytest.mark.asyncio
async def test_success_first_try_no_retry_no_fallback():
    gen = AsyncMock(return_value=_resp('{"ok":1}'))
    with _patch_client(gen):
        resp = await rg.generate_content_resilient(prompt="p", model_ref="pro", config={})
    assert resp.text == '{"ok":1}'
    assert gen.await_count == 1


@pytest.mark.asyncio
async def test_retries_transient_then_succeeds():
    calls = {"n": 0}

    async def gen(**_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return _resp('{"ok":1}')

    with _patch_client(gen), patch.object(rg, "classify", return_value=_TRANSIENT):
        resp = await rg.generate_content_resilient(prompt="p", model_ref="pro", config={}, max_retries_per_model=2)
    assert resp.text == '{"ok":1}'
    assert calls["n"] == 2  # one retry, then success on the SAME model


@pytest.mark.asyncio
async def test_falls_back_to_next_rung_when_not_transient():
    seen_models: list[str] = []

    async def gen(*, model, **_kw):
        seen_models.append(model)
        if len(seen_models) == 1:
            raise RuntimeError("boom")  # first rung fails, fallbackable
        return _resp('{"ok":1}')

    with _patch_client(gen), patch.object(rg, "classify", return_value=_FALLBACK_ONLY):
        resp = await rg.generate_content_resilient(prompt="p", model_ref="pro", config={})
    assert resp.text == '{"ok":1}'
    assert len(seen_models) >= 2  # advanced to the next chain member


@pytest.mark.asyncio
async def test_chain_exhausted_raises_typed_error():
    async def gen(**_kw):
        raise RuntimeError("boom everywhere")

    with _patch_client(gen), patch.object(rg, "classify", return_value=_FALLBACK_ONLY):
        with pytest.raises(ModelTurnError):
            await rg.generate_content_resilient(prompt="p", model_ref="pro", config={})


@pytest.mark.asyncio
async def test_fatal_error_raises_without_fallback():
    calls = {"n": 0}

    async def gen(**_kw):
        calls["n"] += 1
        raise RuntimeError("invalid request")

    with _patch_client(gen), patch.object(rg, "classify", return_value=_FATAL):
        with pytest.raises(ModelTurnError):
            await rg.generate_content_resilient(prompt="p", model_ref="pro", config={})
    assert calls["n"] == 1  # no retry, no fallback on a fatal (non-fallbackable) error


def test_chain_for_pro_has_eu_pin_and_legacy_model_fallback(monkeypatch):
    # eu-strict (prod): pro is gemini-3.7-flash pinned to the "eu" jurisdictional
    # multi-region endpoint (2026-08-13 — was 2.5-pro, which EOLs 2026-10-16). No
    # same-model cross-region rung is possible (no second EU location for this
    # endpoint type — see the gemini-3-7-flash-eu registry entry), so the chain
    # leans on the legacy gemini-2-5-pro sibling until it retires.
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
    chain = rg.gemini_chain_for("pro")
    labels = [r.label for r in chain]
    assert chain[0].api_name == "gemini-3.7-flash"
    assert chain[0].location == "eu"
    assert any("gemini-2.5-pro" in label for label in labels[1:])  # legacy model fallback rung


def test_chain_for_pro_under_unrestricted_pins_global_and_has_model_fallback(monkeypatch):
    # unrestricted (dev/test): pro is the faster global-endpoint 3.7-flash
    # (2026-08-13 — was 3.6-flash). Rung 0 MUST pin location="global" — the
    # global endpoint is where these models serve; the default region-pinned
    # client (europe-west1) 404s them (the live PPA-pipeline failure this fix
    # addresses). A cross-*region* rung is still impossible (global serves
    # nowhere else), so the chain is SHORTER (2 rungs) and leans on the EU
    # sibling model fallback at the default region.
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "unrestricted")
    chain = rg.gemini_chain_for("pro")
    labels = [r.label for r in chain]
    assert chain[0].api_name.startswith("gemini-3.7-flash")
    assert chain[0].location == "global"  # pinned to the global endpoint, not europe-west1
    assert not any("@europe-west" in label for label in labels)  # no cross-region rung possible
    assert any("flash" in label for label in labels[1:])  # EU sibling model fallback survives
    assert chain[-1].location is None  # EU fallback rides the default region-pinned client


def test_chain_for_lite_under_unrestricted_pins_global(monkeypatch):
    # The reported failure: clause extraction runs the `lite` tier, which under
    # unrestricted is the global-endpoint gemini-3.5-flash-lite. Rung 0 must be
    # location="global" or it 404s in europe-west1 (the exact error the user hit).
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "unrestricted")
    chain = rg.gemini_chain_for("lite")
    assert chain[0].api_name == "gemini-3.5-flash-lite"
    assert chain[0].location == "global"
    # Falls back to the EU sibling (gemini-2.5-flash-lite) at the default region.
    assert chain[1].api_name == "gemini-2.5-flash-lite"
    assert chain[1].location is None


def test_chain_for_lite_under_eu_strict_stays_eu_no_global(monkeypatch):
    # eu-strict (prod): `lite` resolves to gemini-3.5-flash-lite pinned to the
    # "eu" jurisdictional multi-region endpoint (2026-08-13 — was 2.5-flash-lite
    # at the default region, which EOLs 2026-10-16). Still not "global" — no
    # residency escape.
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
    chain = rg.gemini_chain_for("lite")
    assert chain[0].api_name == "gemini-3.5-flash-lite"
    assert chain[0].location == "eu"
    assert not any(r.location == "global" for r in chain)


def test_chain_for_raw_model_synthesizes_one_cross_region_rung():
    chain = rg.gemini_chain_for("gemini-2.5-flash")
    assert len(chain) == 2
    assert chain[0].location is None
    assert chain[1].location == rg._CROSS_REGION


def test_non_gemini_ref_raises_loudly():
    # Structured output is Gemini-only — a non-Gemini ref is a config error.
    with pytest.raises(ValueError):
        rg.gemini_chain_for("gpt-4o")

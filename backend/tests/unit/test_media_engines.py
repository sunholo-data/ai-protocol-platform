"""Speech and image engines behind registries, extendable by private packages
(voice/registry.py, imagegen/, extensions.py), and the tools that use them
(adk/speak.py, adk/generate_image.py)."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from google.genai import types
from PIL import Image

import extensions
from adk import generate_image as gen_mod
from adk import speak as speak_mod
from adk.a2ui_result_render import is_render_payload_tool
from imagegen import GeneratedImage, ImageResult, available_image_providers, get_image_provider
from voice import get_tts


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 120, 200)).save(out, format="PNG")
    return out.getvalue()


class FakeTTS:
    name = "local_tts"

    def __init__(self):
        self.calls = []

    async def synthesize(self, text, lang, voice, extras):
        self.calls.append((text, lang, voice, extras))
        return b"RIFFfakeWAVE", "audio/wav"

    def describe(self):
        return {"tts": True, "stt": False, "streaming": False, "languages": []}


class FakeImages:
    name = "local_images"

    def __init__(self, status="done"):
        self.status = status
        self.requests = []

    async def generate(self, request, progress=None):
        self.requests.append(request)
        if progress:
            progress("step 1/2")
        if self.status == "pending":
            return ImageResult(status="pending", job="job42", detail="waiting for the GPU")
        return ImageResult(
            status="done",
            images=[GeneratedImage(data=_png(), mime="image/png", seed=7)] * request.count,
            model="fast-model",
        )

    async def resume(self, job, progress=None):
        assert job == "job42"
        return ImageResult(status="done", images=[GeneratedImage(data=_png(), mime="image/png", seed=9)], model="m")

    def describe(self):
        return {
            "models": {"fast-model": {"label": "Fast", "modes": ["generate", "edit"]}},
            "default_model": "fast-model",
        }


@pytest.fixture
def private_engines(monkeypatch):
    """One private package offering a speech and an image engine."""
    tts, images = FakeTTS(), FakeImages()
    modules = {
        "voice": SimpleNamespace(TTS_PROVIDERS={"local_tts": lambda: tts}),
        "imagegen": SimpleNamespace(IMAGE_PROVIDERS={"local_images": lambda: images}),
    }
    monkeypatch.setattr(extensions, "private_packages", lambda: ["media_pkg"])
    monkeypatch.setattr(extensions, "_optional_module", lambda pkg, mod: modules.get(mod))
    return tts, images


class Ctx(SimpleNamespace):
    def __init__(self, user_artifacts=None):
        super().__init__(state={}, saved={}, user_artifacts=user_artifacts or {})

    async def save_artifact(self, filename, artifact):
        self.saved[filename] = artifact
        return 0

    async def load_artifact(self, filename, version=None):
        return self.saved.get(filename) or self.user_artifacts.get(filename)


# --- registries -----------------------------------------------------------------


def test_the_voice_registry_offers_a_private_engine_by_name(private_engines):
    tts, _ = private_engines
    assert get_tts("local_tts") is tts
    with pytest.raises(ValueError, match="local_tts"):
        get_tts("nope")


def test_without_private_packages_nothing_changes(monkeypatch):
    monkeypatch.setattr(extensions, "private_packages", lambda: [])
    with pytest.raises(ValueError, match="browser, gcp_<tier>"):
        get_tts("nope")
    assert available_image_providers() == []
    with pytest.raises(ValueError, match="No image engine"):
        get_image_provider()


def test_the_image_registry_defaults_to_the_first_engine_or_the_env(private_engines, monkeypatch):
    _, images = private_engines
    monkeypatch.delenv("IMAGE_GEN_PROVIDER", raising=False)
    assert available_image_providers() == ["local_images"]
    assert get_image_provider() is images
    monkeypatch.setenv("IMAGE_GEN_PROVIDER", "elsewhere")
    with pytest.raises(ValueError, match="Available: local_images"):
        get_image_provider()


def test_a_private_engine_that_fails_to_build_is_skipped(monkeypatch):
    def broken():
        raise RuntimeError("no GPU")

    monkeypatch.setattr(extensions, "private_packages", lambda: ["media_pkg"])
    monkeypatch.setattr(extensions, "_optional_module", lambda pkg, mod: SimpleNamespace(IMAGE_PROVIDERS={"x": broken}))
    assert extensions.private_image_provider("x") is None


# --- speak ----------------------------------------------------------------------------


async def test_speak_uses_the_chosen_engine_and_stores_the_recording(private_engines, monkeypatch):
    tts, _ = private_engines
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "gcp_chirp3hd")
    monkeypatch.setenv("VOICE_TTS_VOICE", "en-GB-Chirp3-HD-Aoede")
    ctx = Ctx()
    out = await speak_mod.speak("Good morning.", source="local_tts", voice="Narrator", style="calm", tool_context=ctx)
    assert out["status"] == "spoken"
    assert tts.calls == [("Good morning.", tts.calls[0][1], "Narrator", {"prompt": "calm"})]
    [item] = out["items"]
    assert item["kind"] == "audio" and item["url"].startswith("/api/media/") and item["url"].endswith(".wav")
    assert ctx.saved["user:" + item["url"].rsplit("/", 1)[-1]].inline_data.mime_type == "audio/wav"


async def test_speak_never_sends_the_default_engines_voice_to_another_engine(private_engines, monkeypatch):
    tts, _ = private_engines
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "gcp_chirp3hd")
    monkeypatch.setenv("VOICE_TTS_VOICE", "en-GB-Chirp3-HD-Aoede")
    await speak_mod.speak("Hi.", source="local_tts", tool_context=Ctx())
    assert tts.calls[0][2] is None


async def test_speak_refuses_with_reasons(private_engines):
    assert "split it" in (await speak_mod.speak("x" * 5001, tool_context=Ctx()))["problems"][0]
    assert "browser" in (await speak_mod.speak("hi", source="browser", tool_context=Ctx()))["problems"][0]
    assert "Unknown TTS provider" in (await speak_mod.speak("hi", source="nope", tool_context=Ctx()))["problems"][0]


# --- generate_image -------------------------------------------------------------------


async def test_generated_pictures_are_stored_and_described(private_engines):
    _, images = private_engines
    ctx = Ctx()
    out = await gen_mod.generate_image(prompt="a red bike by a canal", count=2, tool_context=ctx)
    assert out["status"] == "made"
    assert [i["kind"] for i in out["items"]] == ["image", "image"]
    assert all(i["url"].startswith("/api/media/") for i in out["items"])
    assert "fast-model" in out["items"][0]["caption"] and "seed 7" in out["items"][0]["caption"]
    assert images.requests[0].mode == "generate" and images.requests[0].count == 2


async def test_a_slow_engine_returns_the_job_and_a_later_turn_collects_it(private_engines):
    _, images = private_engines
    images.status = "pending"
    turn = Ctx()
    out = await gen_mod.generate_image(prompt="a lighthouse", tool_context=turn)
    assert out["status"] == "pending" and out["job"] == "local_images:job42"
    assert "END your reply" in out["message"] and "Do not start a new job" in out["message"]
    # The same reply calling again does not wait again (it looped live, 4 min a call).
    again = await gen_mod.generate_image(job=out["job"], tool_context=turn)
    assert again["status"] == "pending" and again["message"].startswith("You already waited")
    # A later turn (fresh temp state) collects it.
    done = await gen_mod.generate_image(job=out["job"], tool_context=Ctx())
    assert done["status"] == "made" and len(done["items"]) == 1
    assert (await gen_mod.generate_image(job="../etc", tool_context=Ctx()))["status"] == "nothing_made"


async def test_an_edit_reads_its_input_picture_from_the_callers_artefacts(private_engines):
    _, images = private_engines
    art = types.Part.from_bytes(data=_png(), mime_type="image/png")
    ctx = Ctx(user_artifacts={"user:hall_20261111_abcd.png": art})
    out = await gen_mod.generate_image(
        prompt="remove the coat rack", artefact="/api/media/hall_20261111_abcd.png", tool_context=ctx
    )
    assert out["status"] == "made"
    request = images.requests[0]
    assert request.mode == "edit" and len(request.inputs) == 1 and request.inputs[0][1] == "image/jpeg"


async def test_generate_image_refuses_with_reasons(private_engines):
    assert "Describe" in (await gen_mod.generate_image(tool_context=Ctx()))["problems"][0]
    assert (
        "needs a picture" in (await gen_mod.generate_image(prompt="x", mode="vary", tool_context=Ctx()))["problems"][0]
    )
    assert (
        "Unknown image engine"
        in (await gen_mod.generate_image(prompt="x", source="nope", tool_context=Ctx()))["problems"][0]
    )


def test_both_tools_draw_in_the_media_tab():
    assert is_render_payload_tool("speak") and is_render_payload_tool("generate_image")


def test_both_tools_are_opt_in_registry_tools():
    from adk.tools import TOOL_CATALOG, TOOL_REGISTRY

    assert {"speak", "generate_image"} <= set(TOOL_REGISTRY)
    assert {"speak", "generate_image"} <= {t["name"] for t in TOOL_CATALOG}

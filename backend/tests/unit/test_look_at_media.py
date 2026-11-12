"""look_at_media and attached image tabs: the MODEL sees pictures and hears clips
(adk/look_at_media.py, tools/documents/media_bytes.py, adk/callbacks.py)."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types
from PIL import Image

from adk import look_at_media as mod
from tools.documents import media_bytes

UID = "alice"


def _png(size=(3000, 2000), mode="RGBA") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)).save(out, format="PNG")
    return out.getvalue()


DOCS = {
    "photo": {"userId": UID, "originalFilename": "hall-01.png", "sourceUrl": "gs://example-docs/u/hall-01.png"},
    "clip": {"userId": UID, "originalFilename": "walkthrough.mp4", "sourceUrl": "gs://example-docs/u/w.mp4"},
    "report": {"userId": UID, "originalFilename": "survey.pdf", "sourceUrl": "gs://example-docs/u/survey.pdf"},
    "bobs": {"userId": "bob", "originalFilename": "bob.png", "sourceUrl": "gs://example-docs/b/bob.png"},
}
BYTES = {"gs://example-docs/u/hall-01.png": _png(), "gs://example-docs/u/w.mp4": b"\x00\x00\x00\x18ftypmp42"}


class Ctx(SimpleNamespace):
    """A tool/callback context with an in-memory artifact store."""

    def __init__(self, user_artifacts=None):
        super().__init__(state={}, saved={}, user_artifacts=user_artifacts or {})

    async def save_artifact(self, filename, artifact):
        self.saved[filename] = artifact
        return 0

    async def load_artifact(self, filename, version=None):
        return self.saved.get(filename) or self.user_artifacts.get(filename)


@pytest.fixture(autouse=True)
def _docs(monkeypatch):
    monkeypatch.setattr("adk.show_media._load_document", lambda doc_id: DOCS.get(doc_id))
    monkeypatch.setattr("tools.documents.context.resolve_caller_uid", lambda ctx: UID)
    monkeypatch.setattr("tools.documents.paths.document_path", lambda raw: "pics/" + raw["originalFilename"])
    monkeypatch.setattr(media_bytes, "_download", lambda url: BYTES[url])


# --- reading and shrinking ------------------------------------------------------------


def test_images_are_shrunk_to_what_a_model_uses():
    data, mime = media_bytes.prepare_image_for_model(_png())
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as img:
        assert max(img.size) == media_bytes.MAX_IMAGE_EDGE
        assert img.mode == "RGB"


def test_reading_media_keeps_the_document_access_rule():
    from tools.documents.context import DocumentAccessError

    with pytest.raises(DocumentAccessError):
        media_bytes.read_document_media(DOCS["bobs"], "bobs", UID)
    with pytest.raises(media_bytes.MediaUnavailable):
        media_bytes.read_document_media(DOCS["report"], "report", UID)


# --- the tool ------------------------------------------------------------------------------


async def test_a_picture_is_saved_for_the_next_request_not_returned_as_bytes():
    ctx = Ctx()
    out = await mod.look_at_media(doc_ids=["[ref: photo]"], tool_context=ctx)
    assert out["status"] == "looked"
    [item] = out["items"]
    assert item["caption"] == "pics/hall-01.png" and item["kind"] == "image"
    assert item["attached"].startswith(mod.ARTIFACT_PREFIX) and item["attached"] in ctx.saved
    assert ctx.saved[item["attached"]].inline_data.mime_type == "image/jpeg"
    # The result itself is small JSON: no pixels in the transcript.
    assert "inline_data" not in str(out) and len(str(out)) < 2000


async def test_audio_and_video_come_back_as_a_report(monkeypatch):
    calls = []

    async def fake_understand(data, mime, caption, question):
        calls.append((mime, caption, question))
        return "1. Transcript: Good morning.\n2. Description: a woman speaks."

    monkeypatch.setattr(mod, "_understand_av", fake_understand)
    out = await mod.look_at_media(doc_ids=["clip"], question="who speaks?", tool_context=Ctx())
    assert calls == [("video/mp4", "pics/walkthrough.mp4", "who speaks?")]
    assert out["items"][0]["report"].startswith("1. Transcript")


async def test_refusals_say_why_and_never_reveal_ownership():
    out = await mod.look_at_media(doc_ids=["bobs", "report", "missing"], tool_context=Ctx())
    assert out["status"] == "nothing_read"
    assert "no such document" in out["problems"][0]
    assert "not a picture" in out["problems"][1]
    assert "no such document" in out["problems"][2]


async def test_paths_resolve_like_get_document_content(monkeypatch):
    async def fake_resolve(uid, path, ctx):
        return ("photo", "") if path.endswith("hall-01.png") else ("", f"No document at the path '{path}'.")

    monkeypatch.setattr("adk.tools._resolve_document_path", fake_resolve)
    out = await mod.look_at_media(paths=["pics/hall-01.png", "pics/nope.png"], tool_context=Ctx())
    assert [i["caption"] for i in out["items"]] == ["pics/hall-01.png"]
    assert out["problems"] == ["No document at the path 'pics/nope.png'."]


async def test_a_generated_or_screenshot_artefact_can_be_looked_at():
    art = types.Part.from_bytes(data=_png((800, 600), "RGB"), mime_type="image/png")
    ctx = Ctx(user_artifacts={"user:render_20261110_abcd.png": art})
    out = await mod.look_at_media(
        artefacts=["/api/media/render_20261110_abcd.png", "/api/media/gone.png"], tool_context=ctx
    )
    assert [i["kind"] for i in out["items"]] == ["image"]
    assert "no such artefact" in out["problems"][0]


async def test_the_same_picture_twice_is_attached_once_and_the_count_is_capped():
    ctx = Ctx()
    out = await mod.look_at_media(doc_ids=["photo", "photo"], tool_context=ctx)
    assert len(out["items"]) == 1


async def test_nothing_requested_says_what_to_pass():
    assert (await mod.look_at_media(tool_context=Ctx()))["status"] == "nothing_requested"


# --- attaching to the request -------------------------------------------------------------


def _request_after(response: dict, name: str = mod.TOOL_NAME) -> LlmRequest:
    return LlmRequest(
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text="what's in the hall photo?")]),
            types.Content(role="model", parts=[types.Part.from_function_call(name=name, args={})]),
            types.Content(role="user", parts=[types.Part.from_function_response(name=name, response=response)]),
        ]
    )


async def test_pictures_are_attached_inside_the_tool_result_of_the_next_request():
    ctx = Ctx()
    out = await mod.look_at_media(doc_ids=["photo"], tool_context=ctx)
    req = _request_after(out)
    session_part = req.contents[-1].parts[0]
    assert await mod.attach_looked_at_media(ctx, req) == 1
    [part] = req.contents[-1].parts
    # Inside the function_response, not beside it: Gemini 3 rejects pictures next to one.
    [picture] = part.function_response.parts
    assert picture.inline_data.mime_type == "image/jpeg"
    # The request part was replaced, never mutated: the session's copy stays picture-free.
    assert part is not session_part and not session_part.function_response.parts


async def test_nothing_is_attached_on_later_turns_or_for_other_tools():
    ctx = Ctx()
    out = await mod.look_at_media(doc_ids=["photo"], tool_context=ctx)
    later = LlmRequest(contents=[types.Content(role="user", parts=[types.Part.from_text(text="and now?")])])
    assert await mod.attach_looked_at_media(ctx, later) == 0
    other = _request_after(out, name="show_media")
    assert await mod.attach_looked_at_media(ctx, other) == 0
    # A forged response naming something outside the look: namespace is ignored.
    forged = _request_after({"items": [{"attached": "doc:secret.json"}]})
    assert await mod.attach_looked_at_media(ctx, forged) == 0


async def test_the_tool_attaches_through_process_llm_request():
    ctx = Ctx()
    out = await mod.look_at_media(doc_ids=["photo"], tool_context=ctx)
    req = _request_after(out)
    await mod.LookAtMediaTool().process_llm_request(tool_context=ctx, llm_request=req)
    assert req.contents[-1].parts[0].function_response.parts
    assert "look_at_media" in {d.name for d in req.config.tools[0].function_declarations}


# --- attached image tabs -------------------------------------------------------------------


async def test_an_attached_image_tab_is_saved_and_injected_as_pixels(monkeypatch):
    from adk import callbacks

    monkeypatch.setattr("db.firestore.get_document", lambda coll, doc_id: DOCS.get(doc_id))
    ctx = Ctx()
    assert await callbacks._save_image_artifact(ctx, "photo", UID) is True
    assert await callbacks._save_image_artifact(ctx, "report", UID) is False
    assert await callbacks._save_image_artifact(ctx, "bobs", UID) is False

    ctx.saved["doc:photo.json"] = types.Part(
        inline_data=types.Blob(data=b'[{"type": "paragraph", "text": "a hall"}]', mime_type="application/json")
    )
    ctx.state[callbacks._STATE_DOCS_LOADED] = ["photo"]
    ctx.state[callbacks._STATE_DOCS_WITH_PICTURE] = ["photo"]
    req = LlmRequest(contents=[types.Content(role="user", parts=[types.Part.from_text(text="what is this?")])])
    await callbacks.make_document_injector()(ctx, req)
    texts = [p.text for c in req.contents for p in c.parts if p.text]
    assert any(t.startswith("[Attached document: doc:photo.json") for t in texts)
    assert any(t.startswith("[Attached picture: doc:photo") for t in texts)
    pictures = [p for c in req.contents for p in c.parts if p.inline_data]
    assert len(pictures) == 1 and pictures[0].inline_data.mime_type == "image/jpeg"
    assert req.contents[-1].parts[0].text == "what is this?"


async def test_the_loader_keeps_an_image_with_no_description_and_records_its_picture(monkeypatch):
    from adk import callbacks

    monkeypatch.setattr("db.firestore.get_document", lambda coll, doc_id: DOCS.get(doc_id))
    monkeypatch.setattr("tools.documents.context.build_document_context", lambda *a, **k: ("", []))
    monkeypatch.setattr(callbacks, "_resolve_user_id", lambda ctx, state: UID)
    ctx = Ctx()
    ctx.state["document_ids"] = ["photo", "report"]
    await callbacks.make_document_loader()(ctx)
    assert ctx.state[callbacks._STATE_DOCS_LOADED] == ["photo"]
    assert ctx.state[callbacks._STATE_DOCS_WITH_PICTURE] == ["photo"]
    assert {"doc:photo.json", "doc:photo.image"} <= set(ctx.saved)
    assert "report" in ctx.state[callbacks._STATE_DOC_LOAD_ERROR]

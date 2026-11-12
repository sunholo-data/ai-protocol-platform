"""show_media: pictures, video and audio the agent can reach, as references the
viewer's own credentials fetch (adk/show_media.py, tools/documents/media.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from adk import project_floor, project_scope
from adk import show_media as mod
from adk.a2ui_result_render import is_render_payload_tool
from adk.media_results import save_user_media
from adk.project_floor import make_project_floor_callback
from tests.support.state_doubles import ScopedState
from tools.documents import media

UID = "alice"
W = mod.DISPLAY_WIDTH

DOCS = {
    "photo": {"userId": UID, "originalFilename": "living-room-01.jpg", "sourceUrl": "gs://example-docs/u/a.jpg"},
    "clip": {"userId": UID, "originalFilename": "walkthrough.mp4", "sourceUrl": "gs://example-docs/u/w.mp4"},
    "memo": {"userId": UID, "originalFilename": "memo.m4a", "sourceUrl": "gs://example-docs/u/memo.m4a"},
    "report": {"userId": UID, "originalFilename": "survey.pdf", "sourceUrl": "gs://example-docs/u/survey.pdf"},
    "bobs": {"userId": "bob", "originalFilename": "bob.png", "sourceUrl": "gs://example-docs/b/bob.png"},
    "no-original": {"userId": UID, "originalFilename": "linked.png", "sourceUrl": "https://example.com/linked.png"},
}


@pytest.fixture(autouse=True)
def _docs(monkeypatch):
    monkeypatch.setattr(mod, "_load_document", lambda doc_id: DOCS.get(doc_id))
    monkeypatch.setattr("tools.documents.context.resolve_caller_uid", lambda ctx: UID)
    monkeypatch.setattr("tools.documents.paths.document_path", lambda raw: "media/" + raw["originalFilename"])


async def _show(**kwargs):
    return await mod.show_media(**kwargs, tool_context=SimpleNamespace(state={}))


async def test_each_kind_of_document_gets_its_route_and_kind():
    out = await _show(doc_ids=["[ref: photo]", "clip", "memo"])
    assert out["status"] == "shown"
    assert [(i["kind"], i["url"]) for i in out["items"]] == [
        ("image", f"/api/documents/photo/thumbnail?width={W}"),
        ("video", "/api/documents/clip/preview"),
        ("audio", "/api/documents/memo/preview"),
    ]
    first = out["items"][0]
    assert first["caption"] == "media/living-room-01.jpg"
    assert first["markdown"] == f"![media/living-room-01.jpg]({first['url']})"
    assert out["problems"] == []


async def test_someone_elses_document_reads_as_not_found():
    out = await _show(doc_ids=["bobs"])
    assert out["status"] == "nothing_shown"
    assert "no such document" in out["problems"][0]


async def test_a_document_that_is_not_media_is_refused_with_a_reason():
    out = await _show(doc_ids=["report", "no-original", "missing"])
    assert out["items"] == []
    assert "not a picture, video or audio file" in out["problems"][0]
    assert "not stored" in out["problems"][1]
    assert "no such document" in out["problems"][2]


async def test_paths_resolve_through_the_same_lookup_as_get_document_content(monkeypatch):
    seen = {}

    async def fake_resolve(uid, path, ctx):
        seen["args"] = (uid, path)
        return ("clip", "") if path.endswith("walkthrough.mp4") else ("", f"No document at the path '{path}'.")

    monkeypatch.setattr("adk.tools._resolve_document_path", fake_resolve)
    out = await _show(paths=["media/walkthrough.mp4", "media/nope.mp4"])
    assert seen["args"][0] == UID
    assert [i["url"] for i in out["items"]] == ["/api/documents/clip/preview"]
    assert out["problems"] == ["No document at the path 'media/nope.mp4'."]


async def test_bucket_objects_use_thumbnail_or_preview():
    out = await _show(
        gs_urls=[
            "gs://example-bucket/site/kitchen 1.JPG",
            "gs://example-bucket/site/tour.webm",
            "gs://example-bucket/notes.txt",
        ]
    )
    assert [i["url"] for i in out["items"]] == [
        f"/api/buckets/example-bucket/thumbnail?object=site%2Fkitchen%201.JPG&width={W}",
        "/api/buckets/example-bucket/preview?object=site%2Ftour.webm",
    ]
    assert len(out["problems"]) == 1


async def test_artefacts_keep_their_route_and_reject_traversal():
    out = await _show(
        artefacts=[
            "/api/browser/artefacts/browser_screenshot_example.com_20261110-101010_abcd1234.png",
            "/api/media/narration_20261110-101010_abcd1234.wav",
            "kitchen-render_20261110-101010_abcd1234.png",
            "../etc",
            "/api/media/notes.bin",
        ],
    )
    assert [(i["source"], i["kind"], i["url"]) for i in out["items"]] == [
        (
            "screenshot",
            "image",
            "/api/browser/artefacts/browser_screenshot_example.com_20261110-101010_abcd1234.png",
        ),
        ("artefact", "audio", "/api/media/narration_20261110-101010_abcd1234.wav"),
        ("artefact", "image", "/api/media/kitchen-render_20261110-101010_abcd1234.png"),
    ]
    assert len(out["problems"]) == 2


async def test_duplicates_are_shown_once_and_the_count_is_capped():
    out = await _show(doc_ids=["photo", "photo"])
    assert len(out["items"]) == 1
    many = await _show(artefacts=[f"shot{i}.png" for i in range(mod.MAX_ITEMS + 3)])
    assert len(many["items"]) == mod.MAX_ITEMS
    assert "Only the first" in many["problems"][0]


async def test_nothing_requested_says_what_to_pass():
    out = await _show()
    assert out["status"] == "nothing_requested"


def test_captions_cannot_break_the_markdown():
    assert mod._markdown("a]b[c\nd", "/api/x") == "![a b c d](/api/x)"


# --- the Media tab -------------------------------------------------------------


def test_the_tab_draws_a_player_per_kind_and_lists_problems():
    msgs = mod.media_to_a2ui(
        {
            "title": "West living room",
            "items": [
                {"caption": "a.jpg", "kind": "image", "url": f"/api/documents/photo/thumbnail?width={W}"},
                {"caption": "w.mp4", "kind": "video", "url": "/api/documents/clip/preview"},
                {"caption": "memo.m4a", "kind": "audio", "url": "/api/documents/memo/preview"},
            ],
            "problems": ["survey.pdf: not a picture, video or audio file"],
        }
    )
    components = msgs[1]["updateComponents"]["components"]
    players = [(c["component"], c["url"]) for c in components if c["component"] in ("Image", "Video", "AudioPlayer")]
    # Urls stay backend-relative: the workbench fetches them with the viewer's credentials.
    assert players == [
        ("Image", f"/api/documents/photo/thumbnail?width={W}"),
        ("Video", "/api/documents/clip/preview"),
        ("AudioPlayer", "/api/documents/memo/preview"),
    ]
    assert components[-1]["id"] == "root"
    assert any("not a picture" in str(c.get("text")) for c in components)
    assert len({c["id"] for c in components}) == len(components)


def test_nothing_to_draw_draws_nothing():
    assert mod.media_to_a2ui({"items": []}) is None
    assert mod.media_to_a2ui("not json") is None


def test_the_tool_is_a_render_payload_tool():
    # Registered mapping: never offloaded, and its result reaches group sessions.
    assert is_render_payload_tool("show_media")


def test_the_tool_rides_the_artifacts_default():
    import inspect

    from adk import agent

    assert "_show_media.show_media" in inspect.getsource(agent)


# --- media in the library ------------------------------------------------------------


def test_media_kinds():
    assert media.media_kind("a/b/clip.MOV") == "video"
    assert media.media_kind("/api/x/memo.m4a?width=1") == "audio"
    assert media.media_kind("photo.webp") == "image"
    assert media.media_kind("mp3") == "audio"
    assert media.media_kind("notes.txt") is None


def test_uploads_and_sources_accept_video_and_audio():
    from tools.documents import ingest

    for ext in (".mp4", ".mov", ".webm", ".mp3", ".wav", ".m4a"):
        assert ext in ingest._ALLOWED_EXTENSIONS
        assert ingest._resolve_content_type(f"x{ext}", "application/octet-stream") == media.MEDIA_TYPES[ext]


async def test_media_is_stored_as_parsed_without_a_parser(monkeypatch):
    from tools.documents import ingest

    async def no_parser(*a, **k):
        raise AssertionError("a video must never reach the parser")

    monkeypatch.setattr("tools.documents.ailang_parse.parse_gcs_file", no_parser)
    status, blocks, _ms, error = await ingest._run_parse("gs://example-docs/u/walkthrough.mp4")
    assert (status, error) == ("parsed", None)
    assert "show_media" in blocks[0]["text"] and "walkthrough.mp4" in blocks[0]["text"]


def test_preview_routes_serve_media_with_its_type():
    from buckets import routes as bucket_routes
    from tools.documents import routes as doc_routes

    assert doc_routes._CONTENT_TYPE_BY_FORMAT["mp4"] == "video/mp4"
    assert bucket_routes._CONTENT_TYPE_BY_EXT["wav"] == "audio/wav"


async def test_save_user_media_stores_in_the_callers_namespace():
    ctx = SimpleNamespace(save_artifact=AsyncMock(return_value=0))
    ref = await save_user_media(ctx, b"RIFFxxxxWAVE", "audio/wav", label="Kitchen narration!")
    assert ref["url"].startswith("/api/media/Kitchen-narration_") and ref["url"].endswith(".wav")
    saved = ctx.save_artifact.await_args.kwargs
    assert saved["filename"] == "user:" + ref["artefact_id"]
    assert saved["artifact"].inline_data.mime_type == "audio/wav"
    # The reference is what show_media takes.
    item, problem = mod._artefact_item(ref["url"])
    assert problem == "" and item["kind"] == "audio"


def test_the_media_route_serves_the_same_store_as_screenshots():
    from tools.browser_artefact_routes import router

    paths = {r.path for r in router.routes}
    assert {"/api/browser/artefacts/{artefact_id}", "/api/media/{artefact_id}"} <= paths


# --- the project floor -----------------------------------------------------------


def test_the_project_floor_checks_bucket_urls_passed_as_a_list(monkeypatch):
    project_scope.clear_cache()
    monkeypatch.setattr(project_floor, "_get_doc", lambda doc_id: None)
    monkeypatch.setattr(project_floor, "_doc_by_source_url", lambda uid, url: None)
    ctx = SimpleNamespace(
        _invocation_context=SimpleNamespace(user_id=UID, app_name="aitana_platform"),
        state=ScopedState(app_store={}),
    )
    with patch.object(project_floor, "current_project", return_value={"id": "p1", "name": "House"}):
        out = make_project_floor_callback()(
            tool=SimpleNamespace(name="show_media"),
            args={"gs_urls": ["gs://example-bucket/elsewhere.jpg"]},
            tool_context=ctx,
        )
    project_scope.clear_cache()
    assert out is not None and out["status"] == "outside_project"

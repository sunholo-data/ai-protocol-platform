"""Tests for adk/a2ui_sources_render.py — web-search Sources tab (6.11)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from adk.a2ui_sources_render import (
    WEB_SOURCES_SURFACE_ID,
    clear_resolution_cache,
    remap_source_bucket,
    resolve_vertex_document,
    sources_from_grounding,
    sources_to_a2ui,
    web_search_sources_to_a2ui,
)


def _web(title, uri, snippet=""):
    return {"title": title, "uri": uri, "kind": "web", "bucket": "", "object": "", "filename": "", "snippet": snippet}


def _components(messages):
    return messages[1]["updateComponents"]["components"]


def _web_chunk(uri, title):
    return SimpleNamespace(web=SimpleNamespace(uri=uri, title=title, domain=None), retrieved_context=None)


def _rc_chunk(uri, title, document_name=None, text=None):
    return SimpleNamespace(
        web=None,
        retrieved_context=SimpleNamespace(uri=uri, title=title, document_name=document_name, text=text),
    )


def _grounding(chunks):
    return SimpleNamespace(grounding_chunks=chunks)


class TestSourcesFromGrounding:
    def test_web_and_retrieved_context(self):
        gm = _grounding([_web_chunk("https://a.test/x", "X"), _rc_chunk("gs://b/doc.pdf", "Doc")])
        assert sources_from_grounding(gm) == [
            _web("X", "https://a.test/x"),
            {
                "title": "Doc",
                "uri": "gs://b/doc.pdf",
                "kind": "gcs",
                "bucket": "b",
                "object": "doc.pdf",
                "filename": "doc.pdf",
                "snippet": "",
            },
        ]

    def test_dedup_and_none(self):
        gm = _grounding([_web_chunk("https://a.test/x", "X"), _web_chunk("https://a.test/x", "X2")])
        assert sources_from_grounding(gm) == [_web("X", "https://a.test/x")]
        assert sources_from_grounding(None) == []

    def test_gcs_source_enriched_remapped_and_decoded(self, monkeypatch):
        monkeypatch.setenv("AI_SEARCH_SOURCE_BUCKET_OVERRIDE", "dev-bucket")
        # Vertex gives a prod-bucket, percent-encoded URI and no title.
        gm = _grounding([_rc_chunk("gs://prod-bucket/aitana3/cases/EU%20Regulation/Report%202024.pdf", None)])
        assert sources_from_grounding(gm) == [
            {
                "title": "Report 2024.pdf",  # falls back to filename when datastore gives none
                "uri": "gs://dev-bucket/aitana3/cases/EU%20Regulation/Report%202024.pdf",  # bucket remapped
                "kind": "gcs",
                "bucket": "dev-bucket",
                "object": "aitana3/cases/EU Regulation/Report 2024.pdf",  # decoded for import-by-reference
                "filename": "Report 2024.pdf",
                "snippet": "",
            }
        ]

    def test_resource_name_snippet_label_when_unresolvable(self, monkeypatch):
        # Phase B resolution returns None (IAM/missing) → Phase A fallback: labelled
        # from the retrieved text, snippet captured, raw resource name never shown.
        monkeypatch.setattr("adk.a2ui_sources_render.resolve_vertex_document", lambda rn: None)
        rn = "projects/32509213101/locations/eu/collections/default_collection/dataStores/aitana3/branches/0/documents/abc123"
        gm = _grounding(
            [_rc_chunk("", None, document_name=rn, text="German BESS capacity grew sharply in 2025.\nMore.")]
        )
        out = sources_from_grounding(gm)
        assert len(out) == 1
        src = out[0]
        assert src["kind"] == "web" and src["uri"] == ""  # not openable, no link
        assert src["title"] == "German BESS capacity grew sharply in 2025."  # snippet preview, NOT the resource name
        assert "projects/" not in src["title"]
        assert src["snippet"].startswith("German BESS capacity grew")  # content retained

    def test_resource_name_resolves_to_openable_gcs(self, monkeypatch):
        # Phase B: resolving the resource name yields a gs:// uri + title → the
        # source becomes an openable gcs doc (bucket/object populated), snippet kept.
        monkeypatch.setattr(
            "adk.a2ui_sources_render.resolve_vertex_document",
            lambda rn: ("gs://prod-bucket/aitana3/docs/Report.pdf", "Q1 Market Report"),
        )
        rn = "projects/p/locations/eu/collections/default_collection/dataStores/aitana3/branches/0/documents/x"
        gm = _grounding([_rc_chunk("", None, document_name=rn, text="snippet body")])
        out = sources_from_grounding(gm)
        assert len(out) == 1
        src = out[0]
        assert src["kind"] == "gcs" and src["bucket"] == "prod-bucket" and src["object"] == "aitana3/docs/Report.pdf"
        assert src["title"] == "Q1 Market Report"
        assert src["snippet"] == "snippet body"

    def test_two_resource_name_sources_do_not_collapse(self, monkeypatch):
        monkeypatch.setattr("adk.a2ui_sources_render.resolve_vertex_document", lambda rn: None)
        base = "projects/p/locations/eu/collections/default_collection/dataStores/aitana3/branches/0/documents/"
        gm = _grounding(
            [
                _rc_chunk("", None, document_name=base + "aaa", text="First result."),
                _rc_chunk("", None, document_name=base + "bbb", text="Second result."),
            ]
        )
        assert len(sources_from_grounding(gm)) == 2


class TestResolveVertexDocument:
    _RN = "projects/p/locations/eu/collections/default_collection/dataStores/aitana3/branches/0/documents/x"

    def _doc(self, uri="", title="", struct=None):
        doc = MagicMock()
        doc.content.uri = uri
        doc.derived_struct_data = {"title": title} if title else {}
        doc.struct_data = struct or {}
        return doc

    def test_kill_switch_disables_resolution(self, monkeypatch):
        clear_resolution_cache()
        monkeypatch.setenv("AI_SEARCH_RESOLVE_SOURCES", "0")
        client_factory = MagicMock()
        monkeypatch.setattr("adk.a2ui_sources_render._document_client", client_factory)
        assert resolve_vertex_document(self._RN) is None
        client_factory.assert_not_called()  # no API touched

    def test_success_returns_uri_and_title(self, monkeypatch):
        clear_resolution_cache()
        client = MagicMock()
        client.get_document.return_value = self._doc(uri="gs://b/o.pdf", title="T")
        monkeypatch.setattr("adk.a2ui_sources_render._document_client", lambda _loc: client)
        assert resolve_vertex_document(self._RN) == ("gs://b/o.pdf", "T")

    def test_error_returns_none(self, monkeypatch):
        clear_resolution_cache()
        client = MagicMock()
        client.get_document.side_effect = RuntimeError("permission denied")
        monkeypatch.setattr("adk.a2ui_sources_render._document_client", lambda _loc: client)
        assert resolve_vertex_document(self._RN) is None

    def test_inline_doc_uses_struct_data_filename_as_title(self, monkeypatch):
        # Docs ingested as inline text have no content.uri / derived title — the
        # struct_data filename is the real name and becomes the (uri-less) title.
        clear_resolution_cache()
        client = MagicMock()
        client.get_document.return_value = self._doc(uri="", title="", struct={"filename": "news_day_2025_07.txt"})
        monkeypatch.setattr("adk.a2ui_sources_render._document_client", lambda _loc: client)
        assert resolve_vertex_document(self._RN) == ("", "news_day_2025_07.txt")

    def test_caches_result(self, monkeypatch):
        clear_resolution_cache()
        client = MagicMock()
        client.get_document.return_value = self._doc(uri="gs://b/o.pdf")
        monkeypatch.setattr("adk.a2ui_sources_render._document_client", lambda _loc: client)
        resolve_vertex_document(self._RN)
        resolve_vertex_document(self._RN)
        assert client.get_document.call_count == 1  # second call served from cache


class TestRemapSourceBucket:
    def test_no_op_without_override(self, monkeypatch):
        monkeypatch.delenv("AI_SEARCH_SOURCE_BUCKET_OVERRIDE", raising=False)
        assert remap_source_bucket("gs://prod-bucket/a/b.pdf") == "gs://prod-bucket/a/b.pdf"

    def test_swaps_bucket_preserving_object(self, monkeypatch):
        monkeypatch.setenv("AI_SEARCH_SOURCE_BUCKET_OVERRIDE", "dev-bucket")
        assert remap_source_bucket("gs://prod-bucket/aitana3/c/x%20y.pdf") == "gs://dev-bucket/aitana3/c/x%20y.pdf"

    def test_non_gs_uri_untouched(self, monkeypatch):
        monkeypatch.setenv("AI_SEARCH_SOURCE_BUCKET_OVERRIDE", "dev-bucket")
        assert remap_source_bucket("https://a.test/x") == "https://a.test/x"


class TestSourcesToA2ui:
    def test_targets_web_sources_surface_by_default(self):
        msgs = sources_to_a2ui([{"title": "X", "uri": "https://a.test/x"}])
        assert msgs[0]["createSurface"]["surfaceId"] == WEB_SOURCES_SURFACE_ID
        assert msgs[1]["updateComponents"]["surfaceId"] == WEB_SOURCES_SURFACE_ID

    def test_plain_domain_text_and_dedup(self):
        # Text is plain (Basic catalog renders no markdown) — the clickable card
        # is the workbench SourcesArtefactTab, from the data model.
        msgs = sources_to_a2ui(
            [
                {"title": "X", "uri": "https://a.test/x"},
                {"title": "Y", "uri": "https://b.test/y"},
                {"title": "X2", "uri": "https://a.test/x"},
            ]
        )
        texts = [c["text"] for c in _components(msgs) if c["component"] == "Text"]
        assert "X" in texts and "Y" in texts
        assert not any("](" in t for t in texts)  # no raw markdown links
        assert texts.count("X") == 1  # deduped

    def test_empty_returns_no_messages(self):
        assert sources_to_a2ui([]) == []
        assert sources_to_a2ui([{"title": "", "uri": ""}]) == []


class TestWebSearchTransform:
    """The registered result→A2UI transform reads grounding from tool_context.state."""

    def test_renders_from_state_grounding(self):
        tc = SimpleNamespace(state={"temp:_adk_grounding_metadata": _grounding([_web_chunk("https://a.test/x", "X")])})
        msgs = web_search_sources_to_a2ui("free text answer", tc)
        assert msgs is not None
        assert msgs[0]["createSurface"]["surfaceId"] == WEB_SOURCES_SURFACE_ID
        # The raw sources are stashed in the data model for SourcesArtefactTab.
        dm = next(m for m in msgs if "updateDataModel" in m)
        assert dm["updateDataModel"]["value"]["sources"] == [_web("X", "https://a.test/x")]

    def test_none_when_no_tool_context(self):
        assert web_search_sources_to_a2ui("text", None) is None

    def test_none_when_no_grounding_in_state(self):
        assert web_search_sources_to_a2ui("text", SimpleNamespace(state={})) is None


class TestRegistration:
    def test_web_search_tools_are_artifact_tier_and_render_payload(self):
        from adk import a2ui_result_render as rr
        from adk.notability import tool_tier

        # Importing the module (above) registered the mapping.
        assert rr.is_render_payload_tool("web_search_agent")
        assert rr.tool_produces_artifact("web_search_agent")
        assert tool_tier("web_search_agent") == "artifact"
        assert tool_tier("enterprise_search_agent") == "artifact"

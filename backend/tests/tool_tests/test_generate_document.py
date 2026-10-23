"""Generation + templating — v6.32.0 document-creation M2/M4.

The two things these pin that unit tests usually miss:

* the result must be **offload-exempt**, or a large result never reaches the
  transform and the workbench tab silently never appears (backend/adk/CLAUDE.md
  trap 4);
* a conversion failure must return the source markdown and a visible reason —
  never an empty success (NEVER-SILENT #8).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.documents.ailang_parse import ConvertOutcome


def _ok_outcome(**over) -> ConvertOutcome:
    base = {
        "content": b"PK\x03\x04fake-docx",
        "filename": "brief.docx",
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "target": "docx",
        "source_markdown": "# Brief",
    }
    base.update(over)
    return ConvertOutcome(**base)


class _Response:
    """Stand-in for ParsedDocumentResponse."""

    def __init__(self, doc_id="doc-1", folder_id="created-folder", status="parsed", blocks_count=3):
        self.doc_id = doc_id
        self.folder_id = folder_id
        self.status = status
        self.blocks_count = blocks_count
        self.original_filename = "brief.docx"


class _User:
    uid = "user123"
    email = "test@example.com"
    domain = "example.com"


def _real_response():
    """A genuine ParsedDocumentResponse — the stub above cannot carry the
    template outcome fields the HTTP routes now return."""
    from tools.documents.ingest import ParsedDocumentResponse

    return ParsedDocumentResponse(
        docId="doc-1",
        status="parsed",
        originalFilename="brief.docx",
        blocksCount=3,
        storagePath="users/user123/docs/created-folder/brief.docx",
        folderId="created-folder",
    )


def _generate_patches(convert=None, ingest=None, template=None, house=None):
    from contextlib import ExitStack

    stack = ExitStack()
    # The tenant house style is consulted on EVERY path now, not just the tool
    # one, so a fixture that does not say what the tenant has would reach GCS.
    stack.enter_context(patch("tools.documents.generate._tenant_house_style", return_value=house))
    stack.enter_context(patch("tools.documents.context.resolve_caller_uid", return_value="user123"))
    stack.enter_context(patch("auth.firebase_auth.resolve_user_by_uid", return_value=_User()))
    stack.enter_context(
        patch("tools.documents.generate.convert_markdown", return_value=convert if convert else _ok_outcome())
    )
    stack.enter_context(
        patch("tools.documents.generate.ingest_document", return_value=ingest if ingest else _Response())
    )
    stack.enter_context(patch("tools.documents.generate.folders_db.ensure_named_folder", return_value="created-folder"))
    stack.enter_context(patch("tools.documents.generate._find_template", return_value=template))
    stack.enter_context(patch("tools.documents.generate._download_to_tempfile", return_value="/tmp/tmpl.docx"))
    stack.enter_context(patch("tools.documents.generate.shutil.rmtree"))
    return stack


class TestGenerationHappyPath:
    @pytest.mark.asyncio
    async def test_markdown_becomes_a_document_in_the_created_folder(self):
        from tools.documents.generate import generate_document

        with _generate_patches():
            result = await generate_document("# Brief\n\nBody.", filename="brief.docx")

        assert result["status"] == "created"
        assert result["docId"] == "doc-1"
        assert result["folder"] == "Created"
        assert result["target"] == "docx"

    @pytest.mark.asyncio
    async def test_it_goes_through_the_shared_ingest_marked_generated(self):
        """The load-bearing arrow: same door as an upload, flagged as generated."""
        from tools.documents.generate import generate_document

        captured: dict = {}

        async def fake_ingest(**kwargs):
            captured.update(kwargs)
            return _Response()

        with _generate_patches() as stack:
            stack.enter_context(patch("tools.documents.generate.ingest_document", side_effect=fake_ingest))
            await generate_document("# Brief", filename="brief.docx")

        assert captured["source"] == "generated"
        assert captured["folder_id"] == "created-folder"
        assert captured["generated_from"]["target"] == "docx"

    @pytest.mark.asyncio
    async def test_extension_is_forced_to_match_the_target(self):
        from tools.documents.generate import generate_document

        captured: dict = {}

        async def fake_convert(md, **kwargs):
            captured.update(kwargs)
            return _ok_outcome()

        with _generate_patches() as stack:
            stack.enter_context(patch("tools.documents.generate.convert_markdown", side_effect=fake_convert))
            await generate_document("# Brief", filename="Offer for Northwind")

        assert captured["filename"] == "Offer for Northwind.docx"


class TestFailureIsNeverSilent:
    @pytest.mark.asyncio
    async def test_conversion_failure_returns_source_markdown_and_reason(self):
        from tools.documents.generate import generate_document

        failed = ConvertOutcome(source_markdown="# Brief", target="docx", error="boom", error_code="api")
        with _generate_patches(convert=failed):
            result = await generate_document("# Brief", filename="brief.docx")

        assert result["status"] == "failed"
        assert result["error"] == "boom"
        assert result["sourceMarkdown"] == "# Brief", "the model's work must not be destroyed"

    @pytest.mark.asyncio
    async def test_unknown_template_names_the_problem(self):
        from tools.documents.generate import generate_document

        with _generate_patches(template=None):
            result = await generate_document("# Brief", filename="b.docx", template="Nonexistent letterhead")

        assert result["status"] == "failed"
        assert "Nonexistent letterhead" in result["error"]
        assert result["sourceMarkdown"] == "# Brief"

    @pytest.mark.asyncio
    async def test_unidentified_caller_refuses_rather_than_guessing(self):
        from tools.documents.generate import generate_document

        with _generate_patches() as stack:
            stack.enter_context(patch("tools.documents.context.resolve_caller_uid", return_value=""))
            result = await generate_document("# Brief")

        assert result["status"] == "failed"
        assert "identify" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_tenant_403_surfaces_as_a_visible_failure_not_a_crash(self):
        from fastapi import HTTPException

        from tools.documents.generate import generate_document

        boom = HTTPException(status_code=403, detail={"code": "TENANT_NOT_PROVISIONED", "message": "not set up"})
        with _generate_patches() as stack:
            stack.enter_context(patch("tools.documents.generate.ingest_document", side_effect=boom))
            result = await generate_document("# Brief")

        assert result["status"] == "failed"
        assert "not set up" in result["error"]


class TestTemplateResolution:
    """CLAUDE.md #9 — friendly in, canonical id internally, never the reverse."""

    def test_aliases_cover_filename_stem_and_title(self):
        from tools.documents.generate import _template_aliases

        doc = {
            "__id": "26124699-f558-4a1b-9c33-000000000000",
            "originalFilename": "Acme letterhead.docx",
            "title": "Acme Letterhead Template",
        }
        aliases = _template_aliases(doc)
        assert "acme letterhead.docx" in aliases
        assert "acme letterhead" in aliases
        assert "acme letterhead template" in aliases
        assert "26124699-f558-4a1b-9c33-000000000000" in aliases

    def test_resolves_by_filename_without_extension(self):
        from tools.documents.generate import _find_template

        docs = [
            {"__id": "other", "originalFilename": "contract.docx"},
            {"__id": "tmpl-id", "originalFilename": "Acme letterhead.docx"},
        ]
        with patch("db.firestore.query_documents", return_value=docs):
            assert _find_template("user123", "Acme letterhead")["__id"] == "tmpl-id"

    def test_resolution_is_case_insensitive(self):
        from tools.documents.generate import _find_template

        docs = [{"__id": "tmpl-id", "originalFilename": "Acme Letterhead.docx"}]
        with patch("db.firestore.query_documents", return_value=docs):
            assert _find_template("user123", "acme letterhead.docx")["__id"] == "tmpl-id"

    def test_deployed_uuid_doc_ids_resolve_too(self):
        """Test with the DEPLOYED id format, not a slug-as-doc-id fixture."""
        from tools.documents.generate import _find_template

        uuid_id = "af4c5dd0-1111-2222-3333-444455556666"
        docs = [{"__id": uuid_id, "originalFilename": "letterhead.docx"}]
        with patch("db.firestore.query_documents", return_value=docs):
            assert _find_template("user123", uuid_id)["__id"] == uuid_id

    @pytest.mark.asyncio
    async def test_named_template_is_reported_by_its_friendly_name(self):
        from tools.documents.generate import generate_document

        record = {"__id": "tmpl-id", "originalFilename": "Acme letterhead.docx"}
        outcome = _ok_outcome(reference_doc_applied=True, template_parts_carried=12)
        with _generate_patches(convert=outcome, template=record):
            result = await generate_document("# Brief", filename="b.docx", template="Acme letterhead")

        assert result["templateUsed"] == "Acme letterhead.docx"
        assert result["referenceDocApplied"] is True
        assert result["templatePartsCarried"] == 12
        # The doc-id is internal addressing; it is not what the user is shown.
        assert result["templateUsed"] != "tmpl-id"


class TestConvertWrapperGuards:
    @pytest.mark.asyncio
    async def test_empty_markdown_is_refused_before_a_network_call(self):
        from tools.documents.ailang_parse import convert_markdown

        outcome = await convert_markdown("   ", target="docx")
        assert not outcome.ok
        assert outcome.error_code == "empty"

    @pytest.mark.asyncio
    async def test_unsupported_target_is_refused_locally(self):
        from tools.documents.ailang_parse import convert_markdown

        outcome = await convert_markdown("# Hi", target="pdf")
        assert not outcome.ok
        assert "pdf" in outcome.error
        assert outcome.source_markdown == "# Hi"

    @pytest.mark.asyncio
    async def test_template_on_a_non_docx_target_is_refused_with_a_reason(self):
        """Mirrors the API's REFERENCE_DOC_NOT_APPLICABLE, without the round-trip.

        Verified live 2026-09-01: referenceDoc + target=pptx returns
        "Target 'pptx' has no such container." There is no slide-templating
        mechanism upstream.
        """
        from tools.documents.ailang_parse import convert_markdown

        outcome = await convert_markdown("# Hi", target="pptx", reference_doc_path="/tmp/t.docx")
        assert not outcome.ok
        assert "docx" in outcome.error

    @pytest.mark.asyncio
    async def test_target_aliases_normalise(self):
        from tools.documents.ailang_parse import convert_markdown

        # "word" and ".DOCX" both mean docx; neither should be rejected as unsupported.
        for alias in ("word", ".DOCX", "Docx"):
            outcome = await convert_markdown("# Hi", target=alias, reference_doc_path="/nope/t.docx")
            # It gets past target validation (fails later on the missing client/file).
            assert outcome.error_code != "api" or "Unsupported target" not in (outcome.error or "")


class TestOffloadExemptionAndArtifact:
    """Trap 4: without these the tab silently never appears."""

    def test_generate_document_is_offload_exempt(self):
        import adk.a2ui_document_render  # noqa: F401 — registers the mapping
        from adk.a2ui_result_render import is_render_payload_tool

        assert is_render_payload_tool("generate_document")

    def test_generate_document_produces_an_artifact(self):
        import adk.a2ui_document_render  # noqa: F401
        from adk.a2ui_result_render import tool_produces_artifact

        assert tool_produces_artifact("generate_document")

    def test_registered_in_the_tool_registry_and_catalogue(self):
        from adk.tools import TOOL_CATALOG, TOOL_REGISTRY

        assert "generate_document" in TOOL_REGISTRY
        assert any(t["name"] == "generate_document" for t in TOOL_CATALOG)


class TestDocumentA2UIRender:
    def _result(self, **over):
        base = {
            "status": "created",
            "docId": "doc-1",
            "filename": "Offer.docx",
            "folderId": "f1",
            "folder": "Created",
            "target": "docx",
            "templateUsed": "Acme letterhead.docx",
            "referenceDocApplied": True,
            "templatePartsCarried": 12,
            "blockCount": 9,
            "sizeBytes": 42330,
        }
        base.update(over)
        return base

    def test_success_renders_a_surface_with_the_filename(self):
        from adk.a2ui_document_render import document_to_a2ui

        messages = document_to_a2ui(self._result())
        assert messages
        assert messages[0]["createSurface"]["surfaceId"] == "created_document:doc-1"
        blob = str(messages)
        assert "Offer.docx" in blob
        assert "Acme letterhead.docx" in blob

    def test_failure_still_renders_something_visible(self):
        """NEVER-SILENT: an error must reach the user, not just the log."""
        from adk.a2ui_document_render import document_to_a2ui

        messages = document_to_a2ui(self._result(status="failed", error="Conversion failed: bad table"))
        assert messages
        assert "bad table" in str(messages)

    def test_surface_is_stable_per_document(self):
        from adk.a2ui_document_render import _document_surface

        assert _document_surface(self._result()) == _document_surface(self._result(sizeBytes=1))
        assert _document_surface(self._result(docId="doc-2")) != _document_surface(self._result())

    def test_artifact_title_is_the_filename_not_the_doc_id(self):
        from adk.a2ui_document_render import _document_artifact

        meta = _document_artifact(self._result())
        assert meta["kind"] == "document"
        assert meta["title"] == "Offer.docx"
        assert "doc-1" not in meta["title"]

    def test_unrecognised_result_renders_nothing(self):
        from adk.a2ui_document_render import document_to_a2ui

        assert document_to_a2ui({"unrelated": True}) is None
        assert document_to_a2ui(None) is None

    def test_data_model_carries_no_public_url(self):
        """Security rule: a generated document is never handed out as a public URL."""
        from adk.a2ui_document_render import document_to_a2ui

        blob = str(document_to_a2ui(self._result()))
        assert "storage.googleapis.com" not in blob
        assert "gs://" not in blob


class TestSdkContract:
    """M0 guard — the templating feature lives in the SDK's signature.

    `ailang-parse` is pinned `>=0.13.1` because 0.12.0's `convert_file` has no
    `reference_doc`. A downgrade would not raise: the kwarg would simply never be
    passed and every generated document would come back unstyled, silently. This
    turns that into a loud failure.
    """

    def test_convert_file_accepts_the_three_templating_params(self):
        import inspect

        from ailang_parse import DocParse

        params = inspect.signature(DocParse.convert_file).parameters
        for name in ("reference_doc", "reference_section", "table_style"):
            assert name in params, (
                f"ailang-parse SDK is missing {name!r} on convert_file — "
                "templating would silently no-op. Check the >=0.13.1 pin."
            )

    def test_convert_is_not_the_uploading_variant(self):
        """`convert()` sends filepath as a server-side string (sample-id
        semantics); a local path returns "File not found" FROM THE SERVER. The
        wrapper must call convert_file. Verified live 2026-09-01."""
        import inspect

        from tools.documents.ailang_parse import _convert_sync

        source = inspect.getsource(_convert_sync)
        assert "convert_file(" in source


class TestTemplateProvenanceAndOutcome:
    """What styled a document has to survive the conversion that styled it.

    The bytes cannot answer it: a restyled .docx carries the template's theme
    and header, but nothing in the file says which document those came from. So
    the Formatting panel had nothing to show, opened on "Plain (no template)"
    whatever had been applied, and could only ever report "Updated" — the same
    word for a template that carried twelve parts and one that carried none.

    Two properties, both violations of CLAUDE.md #9 / NEVER-SILENT #8 before:
    the FRIENDLY filename is what gets stored, and the converter's verdict
    reaches the caller.
    """

    @pytest.mark.asyncio
    async def test_the_friendly_filename_is_stored_next_to_the_id(self):
        from tools.documents.generate import _generate_into_workspace

        captured: dict = {}

        async def fake_ingest(**kwargs):
            captured.update(kwargs)
            return _real_response()

        with _generate_patches(
            convert=_ok_outcome(reference_doc_applied=True, template_parts_carried=12),
            template={"__id": "tmpl-uuid-9999", "originalFilename": "Acme letterhead.docx"},
        ) as stack:
            stack.enter_context(patch("tools.documents.generate.ingest_document", side_effect=fake_ingest))
            await _generate_into_workspace(
                user=_User(),
                content_markdown="# Brief",
                filename="brief.docx",
                target="docx",
                template="acme letterhead",
                via="revise",
            )

        provenance = captured["generated_from"]
        assert provenance["templateFilename"] == "Acme letterhead.docx", (
            "a doc-id is not something a human or an AI can read (CLAUDE.md #9)"
        )
        assert provenance["templateDocId"] == "tmpl-uuid-9999", "the id stays, for addressing"
        assert provenance["referenceDocApplied"] is True
        assert provenance["templatePartsCarried"] == 12

    @pytest.mark.asyncio
    async def test_the_converters_verdict_reaches_the_caller(self):
        from tools.documents.generate import _generate_into_workspace

        with _generate_patches(
            convert=_ok_outcome(reference_doc_applied=True, template_parts_carried=12),
            template={"__id": "t1", "originalFilename": "Acme letterhead.docx"},
            ingest=_real_response(),
        ):
            result = await _generate_into_workspace(
                user=_User(),
                content_markdown="# Brief",
                filename="brief.docx",
                target="docx",
                template="Acme letterhead.docx",
                via="revise",
            )

        assert result.template_used == "Acme letterhead.docx"
        assert result.reference_doc_applied is True
        assert result.template_parts_carried == 12

    @pytest.mark.asyncio
    async def test_applied_with_nothing_to_carry_is_reported_as_such(self):
        """`applied: true, carried: 0` is a legitimate result for a template
        that has no styling to give — and it must not look like a success that
        changed the document. The live templating test makes the same point
        about not trusting the flag alone."""
        from tools.documents.generate import _generate_into_workspace

        with _generate_patches(
            convert=_ok_outcome(reference_doc_applied=True, template_parts_carried=0),
            template={"__id": "t1", "originalFilename": "Empty.docx"},
            ingest=_real_response(),
        ):
            result = await _generate_into_workspace(
                user=_User(),
                content_markdown="# Brief",
                filename="brief.docx",
                target="docx",
                template="Empty.docx",
                via="revise",
            )

        assert result.reference_doc_applied is True
        assert result.template_parts_carried == 0

    @pytest.mark.asyncio
    async def test_no_template_reports_no_template(self):
        from tools.documents.generate import _generate_into_workspace

        with _generate_patches(ingest=_real_response()):
            result = await _generate_into_workspace(
                user=_User(),
                content_markdown="# Brief",
                filename="brief.docx",
                target="docx",
                template="",
                via="revise",
            )

        assert result.template_used == ""
        assert result.reference_doc_applied is False


class TestRevisionKeepsTheHouseStyle:
    """The 2026-09-09 regression: generate on-brand, edit one passage, lose it.

    Four defects, one shape — the tenant brand layer was wired into the tool
    path and every other template-touching path still assumed the caller's own
    workspace was the whole universe. Each of these passed the suite before,
    which is why they are pinned here by behaviour rather than by call.
    """

    @pytest.mark.asyncio
    async def test_a_revision_applies_the_house_style_by_default(self):
        """The defect: `if template.strip():` meant a revision with no template
        named applied NOTHING, while generation applied the tenant default. So
        editing a passage of an on-brand document silently returned it to Word's
        defaults."""
        from tools.documents.generate import _generate_into_workspace

        house = {
            "__id": "brand:example.com/templates/house-style.docx",
            "originalFilename": "house-style.docx",
            "sourceUrl": "gs://bucket/brand/example.com/templates/house-style.docx",
        }
        captured: dict = {}

        async def fake_ingest(**kwargs):
            captured.update(kwargs)
            return _real_response()

        with _generate_patches(
            convert=_ok_outcome(reference_doc_applied=True, template_parts_carried=12),
            house=house,
        ) as stack:
            stack.enter_context(patch("tools.documents.generate.ingest_document", side_effect=fake_ingest))
            await _generate_into_workspace(
                user=_User(),
                content_markdown="# Brief",
                filename="brief.docx",
                target="docx",
                template="",
                via="revise",
            )

        assert captured["generated_from"]["templateFilename"] == "house-style.docx", (
            "a revision that names no template must inherit the tenant house style, "
            "exactly as generation does — otherwise the first edit strips the branding"
        )

    @pytest.mark.asyncio
    async def test_a_document_is_never_its_own_template(self):
        """The destructive one. The picker offered the open document as a
        template candidate; the converter accepted it, reported
        `referenceDocApplied: True`, carried ZERO parts, and overwrote the
        styling with the unstyled bytes it had just read."""
        from tools.documents.generate import _generate_into_workspace

        itself = {"__id": "doc-being-edited", "originalFilename": "Poem.docx"}
        house = {
            "__id": "brand:example.com/templates/house-style.docx",
            "originalFilename": "house-style.docx",
            "sourceUrl": "gs://bucket/brand/example.com/templates/house-style.docx",
        }
        captured: dict = {}

        async def fake_ingest(**kwargs):
            captured.update(kwargs)
            return _real_response()

        with _generate_patches(convert=_ok_outcome(), template=itself, house=house) as stack:
            stack.enter_context(patch("tools.documents.generate.ingest_document", side_effect=fake_ingest))
            await _generate_into_workspace(
                user=_User(),
                content_markdown="# Poem",
                filename="Poem.docx",
                target="docx",
                template="Poem.docx",
                via="revise",
                exclude_doc_id="doc-being-edited",
            )

        used = captured["generated_from"]["templateDocId"]
        assert used != "doc-being-edited", "a document as its own reference doc destroys its styling"
        assert used == "brand:example.com/templates/house-style.docx", "it falls back to the default instead"

    @pytest.mark.asyncio
    async def test_a_revision_can_reach_the_tenants_brand_templates(self):
        """`_find_template(user.uid, template)` on the revise paths omitted the
        domain, so `_find_brand_template` returned None on an empty domain and a
        named house style 404'd with 'No document called X is in your
        workspace.' That is why there was no way back to a styled document."""
        from tools.documents.generate import _choose_template

        seen: dict = {}

        def fake_find(uid, wanted, domain=""):
            seen["domain"] = domain
            return {"__id": f"brand:{domain}/templates/house-style.docx", "originalFilename": "house-style.docx"}

        with patch("tools.documents.generate._find_template", side_effect=fake_find):
            record, reason = _choose_template(uid="user123", domain="example.com", wanted="house-style", target="docx")

        assert seen["domain"] == "example.com", "the tenant domain must reach the brand lookup"
        assert reason == "named"
        assert record["originalFilename"] == "house-style.docx"

    def test_the_picker_offers_the_tenants_templates_too(self):
        """The picker listed only `userId == caller`, and the house style is not
        a workspace document — so nothing a user could click would restore their
        branding."""
        from tools.documents.brand_assets import BrandAssets
        from tools.documents.generate import list_document_templates

        docs = [{"__id": "d1", "originalFilename": "Notes.docx", "folderId": "f1", "source": "uploaded"}]
        assets = BrandAssets(domain="example.com", bucket="bucket", names=["house-style.docx"], error=None)

        with (
            patch("db.firestore.query_documents", return_value=docs),
            patch("tools.documents.brand_assets.list_brand_assets", return_value=assets),
        ):
            result = list_document_templates(_User())

        names = {t["filename"]: t["source"] for t in result["templates"]}
        assert names.get("house-style.docx") == "organisation", "the tenant's house style must be selectable"
        assert names.get("Notes.docx") == "uploaded", "the caller's own documents stay listed"

    def test_the_picker_does_not_offer_the_document_being_edited(self):
        from tools.documents.brand_assets import BrandAssets
        from tools.documents.generate import list_document_templates

        docs = [
            {"__id": "open-doc", "originalFilename": "Poem.docx", "folderId": "f1", "source": "generated"},
            {"__id": "other", "originalFilename": "Notes.docx", "folderId": "f1", "source": "uploaded"},
        ]
        assets = BrandAssets(domain="example.com", bucket="bucket", names=[], error=None)

        with (
            patch("db.firestore.query_documents", return_value=docs),
            patch("tools.documents.brand_assets.list_brand_assets", return_value=assets),
        ):
            result = list_document_templates(_User(), exclude="open-doc")

        names = {t["filename"] for t in result["templates"]}
        assert "Poem.docx" not in names, "offering the open document as its own template is the trap"
        assert "Notes.docx" in names

    @pytest.mark.asyncio
    async def test_applied_but_carrying_nothing_is_logged_loudly(self, caplog):
        """The signal that would have caught all of this on its first occurrence:
        a 200, a document, and no styling looks identical to success."""
        import logging

        from tools.documents.generate import _generate_into_workspace

        with _generate_patches(
            convert=_ok_outcome(reference_doc_applied=True, template_parts_carried=0),
            template={"__id": "t1", "originalFilename": "Empty.docx"},
            ingest=_real_response(),
        ):
            with caplog.at_level(logging.WARNING, logger="tools.documents.generate"):
                await _generate_into_workspace(
                    user=_User(),
                    content_markdown="# Brief",
                    filename="brief.docx",
                    target="docx",
                    template="Empty.docx",
                    via="revise",
                )

        assert any("carried 0 parts" in r.getMessage() for r in caplog.records), (
            "an applied template that carried nothing must say so"
        )


class TestProvenanceIsTheSameOnEveryPath:
    @pytest.mark.asyncio
    async def test_the_tool_path_records_the_template_name_too(self):
        """The tool path hand-built its provenance and carried only the doc-id,
        so a model-generated document showed as styled-but-nameless in the
        Formatting panel, and the applied-but-carried-nothing warning could
        never fire on the path users actually hit."""
        from tools.documents.generate import generate_document

        captured: dict = {}

        async def fake_ingest(**kwargs):
            captured.update(kwargs)
            return _Response()

        house = {
            "__id": "brand:example.com/templates/house-style.docx",
            "originalFilename": "house-style.docx",
            "sourceUrl": "gs://bucket/brand/example.com/templates/house-style.docx",
        }
        with _generate_patches(
            convert=_ok_outcome(reference_doc_applied=True, template_parts_carried=6),
            house=house,
        ) as stack:
            stack.enter_context(patch("tools.documents.generate.ingest_document", side_effect=fake_ingest))
            await generate_document("# Brief", filename="brief.docx")

        prov = captured["generated_from"]
        assert prov["templateFilename"] == "house-style.docx", "a doc-id is not a name a human can read (#9)"
        assert prov["templatePartsCarried"] == 6, "the converter's verdict must survive on this path too"
        assert prov["referenceDocApplied"] is True
        assert prov["via"] == "tool"
        assert "skillId" in prov and "sessionId" in prov, "the tool path's own fields must not be lost"

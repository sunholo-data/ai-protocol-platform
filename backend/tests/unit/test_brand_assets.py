"""Tests for tools/documents/brand_assets.py — tenant brand asset resolution.

The emphasis is on the FAILURE paths. A brand asset that cannot be fetched must
say why (CLAUDE.md #8): a caller has to be able to tell "this tenant has no
photos" apart from "this environment has no bucket mapped for them", because the
second is a misconfiguration someone must fix and the first is normal.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

DOMAIN = "example-tenant.com"
BUCKET = "example-tenant-documents"


def _blob(name: str) -> MagicMock:
    b = MagicMock()
    b.name = name
    return b


class TestAddressing:
    def test_prefix_is_keyed_on_domain_not_a_codename(self):
        """No codename mapping table to drift — the domain is already the key
        `clients/{domain}` uses."""
        from tools.documents.brand_assets import brand_prefix_for

        assert brand_prefix_for(DOMAIN, "photos") == f"brand/{DOMAIN}/photos/"

    def test_gs_url_round_trip(self):
        from tools.documents.brand_assets import gs_url_for

        assert (
            gs_url_for(BUCKET, DOMAIN, "photos", "Ada Lovelace.jpg")
            == f"gs://{BUCKET}/brand/{DOMAIN}/photos/Ada Lovelace.jpg"
        )


class TestListing:
    def test_returns_friendly_names_not_full_paths(self):
        """CLAUDE.md #9 — callers speak filenames, not gs:// paths."""
        from tools.documents.brand_assets import list_brand_assets

        prefix = f"brand/{DOMAIN}/photos/"
        blobs = [_blob(prefix + "Grace Hopper.jpg"), _blob(prefix + "Alan Turing.jpg")]
        client = MagicMock()
        client.list_blobs.return_value = blobs

        with (
            patch("db.clients.documents_bucket_for_domain", return_value=BUCKET),
            patch("google.cloud.storage.Client", return_value=client),
        ):
            result = list_brand_assets(DOMAIN)

        assert result.ok
        assert result.names == ["Alan Turing.jpg", "Grace Hopper.jpg"]

    def test_skips_the_prefix_placeholder_blob(self):
        """A zero-size blob named exactly the prefix is a GCS artefact, not an asset."""
        from tools.documents.brand_assets import list_brand_assets

        prefix = f"brand/{DOMAIN}/photos/"
        client = MagicMock()
        client.list_blobs.return_value = [_blob(prefix), _blob(prefix + "Real.jpg")]

        with (
            patch("db.clients.documents_bucket_for_domain", return_value=BUCKET),
            patch("google.cloud.storage.Client", return_value=client),
        ):
            result = list_brand_assets(DOMAIN)

        assert result.names == ["Real.jpg"]


class TestNeverSilent:
    def test_unmapped_tenant_explains_itself_and_names_the_fix(self):
        """The env-drift case. An empty list here would look like "no photos"
        and send someone hunting in the wrong place."""
        from tools.documents.brand_assets import list_brand_assets

        with patch("db.clients.documents_bucket_for_domain", return_value=None):
            result = list_brand_assets(DOMAIN)

        assert not result.ok
        assert result.names == []
        assert DOMAIN in result.error
        assert "documents_bucket" in result.error

    def test_unknown_category_is_rejected_before_any_bucket_call(self):
        from tools.documents.brand_assets import list_brand_assets

        with patch("db.clients.documents_bucket_for_domain") as resolver:
            result = list_brand_assets(DOMAIN, "secrets")

        assert not result.ok
        assert "secrets" in result.error
        resolver.assert_not_called()

    def test_storage_failure_surfaces_rather_than_returning_empty(self):
        from tools.documents.brand_assets import list_brand_assets

        client = MagicMock()
        client.list_blobs.side_effect = RuntimeError("boom")

        with (
            patch("db.clients.documents_bucket_for_domain", return_value=BUCKET),
            patch("google.cloud.storage.Client", return_value=client),
        ):
            result = list_brand_assets(DOMAIN)

        assert not result.ok
        assert "boom" in result.error

    def test_missing_asset_reports_the_name(self):
        from tools.documents.brand_assets import fetch_brand_asset

        blob = MagicMock()
        blob.exists.return_value = False
        client = MagicMock()
        client.bucket.return_value.blob.return_value = blob

        with (
            patch("db.clients.documents_bucket_for_domain", return_value=BUCKET),
            patch("google.cloud.storage.Client", return_value=client),
        ):
            data, error = fetch_brand_asset(DOMAIN, "Nobody.jpg")

        assert data is None
        assert "Nobody.jpg" in error


class TestTraversal:
    def test_a_name_cannot_escape_the_tenant_prefix(self):
        """Without this, `../../users/someone/private.pdf` would read another
        tenant's content through a brand-asset call."""
        from tools.documents.brand_assets import fetch_brand_asset

        for bad in ["../secret.jpg", "a/b.jpg", "", ".hidden"]:
            with patch("db.clients.documents_bucket_for_domain") as resolver:
                data, error = fetch_brand_asset(DOMAIN, bad)
            assert data is None, f"{bad!r} should be rejected"
            assert error
            resolver.assert_not_called()


class TestManifest:
    def test_manifest_preserves_per_category_errors(self):
        from tools.documents.brand_assets import brand_asset_manifest

        with patch("db.clients.documents_bucket_for_domain", return_value=None):
            manifest = brand_asset_manifest(DOMAIN)

        assert set(manifest["categories"]) == {"photos", "logos", "covers", "templates"}
        for entry in manifest["categories"].values():
            assert entry["names"] == []
            assert "error" in entry


class TestTenantTemplateFallback:
    """`_find_template` searches the caller's workspace, then the tenant's
    shared brand templates.

    Without the fallback a letterhead belongs to whoever uploaded it, and every
    colleague has to upload their own copy and keep it in step — which is not a
    house style, it is thirteen private ones.
    """

    def test_a_shared_template_resolves_for_a_user_who_never_uploaded_it(self):
        from tools.documents.generate import _find_template

        assets = MagicMock()
        assets.ok = True
        assets.names = ["sample-letterhead.docx"]
        assets.bucket = BUCKET

        with (
            patch("db.firestore.query_documents", return_value=[]),
            patch("tools.documents.brand_assets.list_brand_assets", return_value=assets),
        ):
            record = _find_template("uid-with-empty-workspace", "sample-letterhead", DOMAIN)

        assert record is not None
        assert record["originalFilename"] == "sample-letterhead.docx"
        assert record["sourceUrl"] == f"gs://{BUCKET}/brand/{DOMAIN}/templates/sample-letterhead.docx"

    def test_the_users_own_document_wins_over_the_shared_one(self):
        """A user who uploaded their own "letterhead" means THAT one. A shared
        default silently overriding it would be the surprising direction."""
        from tools.documents.generate import _find_template

        own = {"__id": "doc-1", "originalFilename": "sample-letterhead.docx", "sourceUrl": "gs://mine/x.docx"}
        assets = MagicMock()
        assets.ok = True
        assets.names = ["sample-letterhead.docx"]
        assets.bucket = BUCKET

        with (
            patch("db.firestore.query_documents", return_value=[own]),
            patch("tools.documents.brand_assets.list_brand_assets", return_value=assets),
        ):
            record = _find_template("uid", "sample-letterhead", DOMAIN)

        assert record["sourceUrl"] == "gs://mine/x.docx"

    def test_matches_on_the_stem_so_the_extension_is_optional(self):
        from tools.documents.generate import _find_template

        assets = MagicMock()
        assets.ok = True
        assets.names = ["sample-letterhead.docx"]
        assets.bucket = BUCKET

        with (
            patch("db.firestore.query_documents", return_value=[]),
            patch("tools.documents.brand_assets.list_brand_assets", return_value=assets),
        ):
            for spoken in ["sample-letterhead", "Sample-Letterhead", "sample-letterhead.docx"]:
                assert _find_template("uid", spoken, DOMAIN) is not None, spoken

    def test_no_domain_means_no_fallback_rather_than_a_wrong_tenants_template(self):
        from tools.documents.generate import _find_template

        with (
            patch("db.firestore.query_documents", return_value=[]),
            patch("tools.documents.brand_assets.list_brand_assets") as lister,
        ):
            assert _find_template("uid", "sample-letterhead", "") is None
        lister.assert_not_called()

    def test_an_unmapped_tenant_returns_none_instead_of_raising(self):
        """The generation should fail with "no such template", not a stack trace."""
        from tools.documents.generate import _find_template

        assets = MagicMock()
        assets.ok = False
        assets.error = "No documents bucket is mapped"
        assets.names = []

        with (
            patch("db.firestore.query_documents", return_value=[]),
            patch("tools.documents.brand_assets.list_brand_assets", return_value=assets),
        ):
            assert _find_template("uid", "sample-letterhead", DOMAIN) is None


class TestHouseStyleDefault:
    """A document with no template named comes out in the tenant's house style.

    "On brand" has to be the default rather than something the model remembers
    to ask for — a document in a stranger's default styling reads as a draft
    someone forgot to finish.
    """

    def _assets(self, names):
        a = MagicMock()
        a.ok = True
        a.names = names
        a.bucket = BUCKET
        return a

    def test_a_single_template_is_the_house_style(self):
        from tools.documents.generate import _tenant_house_style

        with patch(
            "tools.documents.brand_assets.list_brand_assets",
            return_value=self._assets(["sample-letterhead.docx"]),
        ):
            house = _tenant_house_style(DOMAIN)

        assert house is not None
        assert house["originalFilename"] == "sample-letterhead.docx"
        assert house["sourceUrl"].endswith("/templates/sample-letterhead.docx")

    def test_house_style_stem_wins_when_there_are_several(self):
        from tools.documents.generate import _tenant_house_style

        with patch(
            "tools.documents.brand_assets.list_brand_assets",
            return_value=self._assets(["letterhead.docx", "house-style.docx", "report.docx"]),
        ):
            house = _tenant_house_style(DOMAIN)

        assert house["originalFilename"] == "house-style.docx"

    def test_several_templates_and_no_designated_one_yields_no_default(self):
        """Silently picking one of three letterheads is worse than picking none."""
        from tools.documents.generate import _tenant_house_style

        with patch(
            "tools.documents.brand_assets.list_brand_assets",
            return_value=self._assets(["letterhead.docx", "report.docx"]),
        ):
            assert _tenant_house_style(DOMAIN) is None

    def test_no_templates_means_no_default(self):
        from tools.documents.generate import _tenant_house_style

        with patch("tools.documents.brand_assets.list_brand_assets", return_value=self._assets([])):
            assert _tenant_house_style(DOMAIN) is None

    def test_unmapped_tenant_yields_no_default_rather_than_raising(self):
        from tools.documents.generate import _tenant_house_style

        broken = MagicMock()
        broken.ok = False
        broken.error = "No documents bucket is mapped"
        broken.names = []

        with patch("tools.documents.brand_assets.list_brand_assets", return_value=broken):
            assert _tenant_house_style(DOMAIN) is None

    def test_no_domain_never_reaches_the_bucket(self):
        from tools.documents.generate import _tenant_house_style

        with patch("tools.documents.brand_assets.list_brand_assets") as lister:
            assert _tenant_house_style("") is None
        lister.assert_not_called()

    def test_opt_out_words_are_recognised(self):
        """ "plain" must mean plain — a user who asks for no branding gets none."""
        from tools.documents.generate import _NO_TEMPLATE

        for spoken in ["none", "plain", "no template", "No Template", "PLAIN"]:
            assert spoken.strip().casefold() in _NO_TEMPLATE, spoken


class TestSpokenTemplateNames:
    """The model passes back what the USER said, not a filename.

    A live run against dev asked for "our house-style template" and got
    "no document by that name" — the matcher wanted the exact stem. Accepting
    aliases on input is the standing rule (CLAUDE.md #9) and this is the
    regression guard for it.
    """

    def test_spoken_variants_all_reach_one_template(self):
        from tools.documents.generate import _normalise_template_name as norm

        spoken = [
            "house-style template",
            "House Style",
            "the house-style template",
            "our house style doc",
            "house_style",
            "house-style.docx",
        ]
        assert len({norm(s) for s in spoken}) == 1

    def test_style_is_not_stripped_as_noise(self):
        """First cut listed "style" as a noise word, which collapsed
        house-style onto anything called "house" — a wrong-template match."""
        from tools.documents.generate import _normalise_template_name as norm

        assert norm("house-style") != norm("house")

    def test_distinct_templates_stay_distinct(self):
        from tools.documents.generate import _normalise_template_name as norm

        assert norm("our letterhead doc") != norm("House Style")
        assert norm("offer") != norm("offer v2")

    def test_no_substring_matching(self):
        """Fuzzy matching would resolve "offer" to "offer-v2-old" and style a
        document from the wrong file. Normalisation is equality, not search."""
        from tools.documents.generate import _find_template

        assets = MagicMock()
        assets.ok = True
        assets.names = ["offer-v2-old.docx"]
        assets.bucket = BUCKET

        with (
            patch("db.firestore.query_documents", return_value=[]),
            patch("tools.documents.brand_assets.list_brand_assets", return_value=assets),
        ):
            assert _find_template("uid", "offer", DOMAIN) is None

    def test_a_spoken_name_resolves_the_shared_template(self):
        from tools.documents.generate import _find_template

        assets = MagicMock()
        assets.ok = True
        assets.names = ["house-style.docx"]
        assets.bucket = BUCKET

        with (
            patch("db.firestore.query_documents", return_value=[]),
            patch("tools.documents.brand_assets.list_brand_assets", return_value=assets),
        ):
            for spoken in ["house-style template", "our house style", "House Style", "house-style.docx"]:
                assert _find_template("uid", spoken, DOMAIN) is not None, spoken

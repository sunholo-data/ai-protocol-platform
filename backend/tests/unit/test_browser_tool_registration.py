"""The browser must be reachable by skill authors, not just deployed.

There is a gap between "the service is running" and "a user can build a skill
with it", and it is easy to declare victory in it. These pin the last mile.
"""

from __future__ import annotations

from adk.tools import TOOL_CATALOG, TOOL_REGISTRY


class TestSelectableAsASkillTool:
    def test_browse_is_in_the_tool_registry(self):
        """Without this a skill config naming `browse` resolves to nothing."""
        assert "browse" in TOOL_REGISTRY

    def test_browse_builds_a_function_tool(self):
        tool = TOOL_REGISTRY["browse"]({})
        assert tool.name == "browse"

    def test_browse_is_in_the_catalog(self):
        """TOOL_CATALOG is what the skill builder shows. A tool absent from it
        exists but is invisible, which is indistinguishable from missing."""
        entry = next((t for t in TOOL_CATALOG if t["name"] == "browse"), None)
        assert entry is not None
        assert entry["label"]
        assert entry["category"]

    def test_catalog_steers_away_from_the_expensive_option(self):
        """`browse` costs a subprocess and a page load; url_processing is a GET.
        The description has to say which to reach for, or every skill will tick
        the browser and pay for it on every URL."""
        entry = next(t for t in TOOL_CATALOG if t["name"] == "browse")
        assert "url_processing" in entry["description"] or "Read a web page" in entry["description"]

    def test_url_processing_is_still_offered(self):
        """browse COMPLEMENTS the JS-blind fetch and is its fallback; removing
        url_processing would delete the degradation path."""
        assert "url_processing" in TOOL_REGISTRY
        assert any(t["name"] == "url_processing" for t in TOOL_CATALOG)


class TestDeployWiring:
    """Env the backend needs to reach the service, in BOTH places it runs."""

    def _cloudbuilds(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        return [root / "cloudbuild.yaml", root / "backend" / "cloudbuild.yaml"]

    def test_both_backend_deploys_pass_the_browser_env(self):
        """The backend runs twice: as a sidecar inside platform-frontend and as
        a standalone service. Wiring only one leaves the other silently
        falling back to the JS-blind fetch."""
        for path in self._cloudbuilds():
            text = path.read_text()
            assert "BROWSER_SERVICE_ENABLED" in text, f"{path.name} does not pass BROWSER_SERVICE_ENABLED"
            assert "BROWSER_SERVICE_URL" in text, f"{path.name} does not pass BROWSER_SERVICE_URL"

    def test_disabled_by_default(self):
        """A fork that has not deployed the browser must not get a tool that
        points at nothing."""
        for path in self._cloudbuilds():
            assert "_BROWSER_SERVICE_ENABLED: 'false'" in path.read_text()

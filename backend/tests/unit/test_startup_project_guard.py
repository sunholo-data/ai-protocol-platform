"""Startup project guard must fail LOUD and not be brand-anchored (AIPLA #42).

The guard this replaces compared the resolved project against a hardcoded
``your-project-id`` prefix and only logged a warning. Two defects in one:

* **Brand-anchored** — every correctly-configured fork got a spurious warning.
* **Fail-open** — a genuinely wrong project still booted, read and wrote.

So it warned exactly when it shouldn't and stayed quiet when it should fire.
These tests pin the inverse: silent when correct, refuses to boot when wrong.
"""

from __future__ import annotations

import pytest

from config.gcp import ProjectGuardError, check_startup_project


@pytest.fixture(autouse=True)
def _clear_project_env(monkeypatch: pytest.MonkeyPatch):
    for var in ("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT", "PLATFORM_EXPECTED_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    # Neutralise ADC so tests never depend on the developer's gcloud login.
    monkeypatch.setattr("config.gcp.resolve_gcp_credentials", lambda: None)


class TestFailsLoud:
    def test_no_project_outside_local_mode_refuses_to_boot(self):
        """Fail-open was the bug. No project => Firestore/GCS/ADK all misfire."""
        with pytest.raises(ProjectGuardError, match="No GCP project resolved"):
            check_startup_project(local_mode=False)

    def test_mismatch_against_expected_refuses_to_boot(self, monkeypatch):
        """The documented shell-shadow gotcha: GCP_PROJECT points elsewhere."""
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "someone-elses-project")
        monkeypatch.setenv("PLATFORM_EXPECTED_PROJECT", "my-project")

        with pytest.raises(ProjectGuardError, match="mismatch"):
            check_startup_project(local_mode=False)

    def test_mismatch_message_names_both_projects(self, monkeypatch):
        """An actionable error names what it got AND what it wanted."""
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "wrong-one")
        monkeypatch.setenv("PLATFORM_EXPECTED_PROJECT", "right-one")

        with pytest.raises(ProjectGuardError) as exc:
            check_startup_project(local_mode=False)

        assert "wrong-one" in str(exc.value)
        assert "right-one" in str(exc.value)


class TestSilentWhenCorrect:
    def test_matching_expected_project_passes(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
        monkeypatch.setenv("PLATFORM_EXPECTED_PROJECT", "my-project")

        assert check_startup_project(local_mode=False) == "my-project"

    def test_no_expectation_configured_makes_no_claim(self, monkeypatch):
        """Unset PLATFORM_EXPECTED_PROJECT => nothing to compare => don't guess.

        This is the anti-brand-anchoring assertion: any project name must be
        acceptable when the deployment hasn't declared one.
        """
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "a-totally-unrelated-fork-project")

        assert check_startup_project(local_mode=False) == "a-totally-unrelated-fork-project"

    @pytest.mark.parametrize(
        "project",
        ["acme-prod", "cphu-aipla-dev", "gde-ap-agent", "some-fork-123"],
    )
    def test_no_brand_prefix_is_required(self, monkeypatch, project):
        """A fork's project must not trip the guard just for not being ours."""
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", project)

        assert check_startup_project(local_mode=False) == project


class TestLocalMode:
    def test_local_mode_needs_no_project(self):
        """LOCAL_MODE has no GCP backing by design — must never block boot."""
        assert check_startup_project(local_mode=True) == "(local-mode)"

    def test_local_mode_ignores_a_mismatch(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "anything")
        monkeypatch.setenv("PLATFORM_EXPECTED_PROJECT", "something-else")

        assert check_startup_project(local_mode=True) == "anything"


def test_no_brand_string_survives_in_the_guard_logic():
    """Regression guard for the actual defect: a baked-in brand prefix.

    Checks the executable body only — the docstring deliberately names the old
    ``your-project-id`` prefix to explain what was wrong with it, and that
    prose is worth keeping.
    """
    import ast
    import inspect

    from config import gcp

    tree = ast.parse(inspect.getsource(gcp.check_startup_project).lstrip())
    func = tree.body[0]
    body = func.body[1:] if ast.get_docstring(func) else func.body  # drop docstring

    literals = [
        node.value
        for stmt in body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    offenders = [s for s in literals if "aitana" in s.lower()]
    assert not offenders, f"brand string baked into guard logic: {offenders}"


def test_placeholder_project_is_treated_as_unconfigured(monkeypatch):
    """`app.py`'s CI-import fallback must not look like a real project at boot."""
    from config.gcp import PLACEHOLDER_PROJECT

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PLACEHOLDER_PROJECT)

    with pytest.raises(ProjectGuardError, match="placeholder"):
        check_startup_project(local_mode=False)

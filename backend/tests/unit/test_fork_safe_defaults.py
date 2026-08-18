"""A fork's defaults must be the FORK's, never this deployment's.

Sprint TEMPLATE-INVERT, M3. Design:
`docs/design/template/template-repo-topology.md`.

Until now these values were correct here and wrong everywhere else, and the
template sanitizer patched them on the way out. That patching cannot survive
the upstream/downstream inversion: a value rewritten at publish time means the
same tracked path holds different bytes upstream and downstream, which is a
permanent merge conflict on every sync.

So the defaults themselves have to be fork-safe, and this deployment's real
values move into deploy config. These are not cosmetic — each one below is a
live misconfiguration in any fork that inherits it:

  AUTH_OPERATOR_DOMAINS  a fork enabling AUTH_REQUIRE_KNOWN_DOMAIN would admit
                         *our* domain as its operators (AIPLA #42)
  bq _DATA_PROJECT       CLI queries aimed at a BigQuery project the fork
                         cannot read, with a confusing permission error
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestOperatorDomains:
    """`AUTH_OPERATOR_DOMAINS` — the auth default that leaked our identity."""

    def test_default_is_empty_not_our_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail CLOSED. An unconfigured deployment grants no operator domain.

        The previous default named this deployment's own staff + smoke
        domains, so a fork that switched on the domain gate silently
        trusted them.
        Empty is the only default that is correct for an unknown deployment;
        an operator domain is deployment identity and must be declared.
        """
        monkeypatch.delenv("AUTH_OPERATOR_DOMAINS", raising=False)
        from auth import _operator_domains

        assert _operator_domains() == frozenset()

    def test_configured_value_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_OPERATOR_DOMAINS", "Example.com, ops.example.org ")
        from auth import _operator_domains

        assert _operator_domains() == frozenset({"example.com", "ops.example.org"})

    def test_the_env_lookup_has_no_fallback_value(self) -> None:
        """Assert the PROPERTY (no hardcoded default), not the absence of one
        specific string.

        A grep for our own domain would itself be rewritten by the sanitizer —
        this test file would join the parity gap it exists to help close, the
        same self-reference trap the parity harness hit in M1. Reading the
        default out of the AST is both scrubber-proof and a stronger assertion:
        it catches ANY hardcoded fallback, not just today's.
        """
        import ast

        src = (REPO_ROOT / "backend" / "auth" / "__init__.py").read_text()
        tree = ast.parse(src)

        defaults = [
            node.args[1].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "AUTH_OPERATOR_DOMAINS"
            and isinstance(node.args[1], ast.Constant)
        ]

        assert defaults, "AUTH_OPERATOR_DOMAINS lookup not found — did it move?"
        assert all(d == "" for d in defaults), (
            f"AUTH_OPERATOR_DOMAINS has a hardcoded fallback {defaults!r}. "
            "Operator domains are deployment identity and belong in deploy "
            "config; any default here is inherited by every fork."
        )

    def test_deploy_config_supplies_the_real_value(self) -> None:
        """We removed the default, so the deployed envs MUST pass it explicitly.

        Without this, dev and test (which both set AUTH_REQUIRE_KNOWN_DOMAIN=1)
        would 403 every operator on the next deploy. This is the half of the
        change that is easy to forget and expensive to discover.
        """
        cloudbuild = (REPO_ROOT / "cloudbuild.yaml").read_text()
        assert "AUTH_OPERATOR_DOMAINS=" in cloudbuild, (
            "cloudbuild.yaml must set AUTH_OPERATOR_DOMAINS now that the code "
            "default is empty, or the domain gate locks operators out"
        )


class TestBigQueryDataProject:
    """The `aiplatform bq` CLI pointed at a project only we can read."""

    # The behavioural test lives in cli/tests/test_bq_data_project.py — the CLI
    # is a separate package with its own venv and is not importable from the
    # backend suite. What the backend suite CAN enforce is that the literal
    # never comes back, which is the regression that matters here.

    def test_module_declares_no_project_constant(self) -> None:
        """Structural, for the same scrubber-proofing reason as above: assert no
        module-level string constant holds a project id, rather than grepping
        for the customer's project name (which the sanitizer would rewrite,
        putting this file in the parity gap it exists to help close)."""
        import ast

        src = (REPO_ROOT / "cli" / "aiplatform" / "commands" / "bq.py").read_text()
        tree = ast.parse(src)

        offenders = [
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id == "_DATA_PROJECT"
        ]

        assert offenders == [], (
            "bq.py declares a module-level _DATA_PROJECT constant again — "
            "the data project is deployment identity, read it from the env"
        )


class TestNoExampleRenameDependency:
    """The `.example` rename is a THIRD sanitize mechanism that cannot survive.

    `sanitize-for-template.sh` deletes the real file and renames the `.example`
    over it. That produces the same tracked path holding generic content
    upstream and real content downstream — exactly the permanent-conflict case
    the inversion exists to remove.

    The shipped path must BE the `.example` one, with the real file generated
    or supplied downstream.
    """

    @pytest.mark.parametrize(
        "example,real",
        [
            ("infrastructure/mcp-toolbox/tools.example.yaml", "infrastructure/mcp-toolbox/tools.yaml"),
            ("docs/ops/deployed-urls.example.md", "docs/ops/deployed-urls.md"),
        ],
    )
    def test_example_is_tracked_and_real_is_not(self, example: str, real: str) -> None:
        import subprocess

        try:
            result = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "ls-files", example, real],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            # No git BINARY at all. The Cloud Build CI-gate step runs on
            # python:3.12-slim, which ships without git — so this raises rather
            # than returning non-zero, and the returncode check below never sees
            # it. GitHub Actions' runner HAS git, so both it and a laptop passed
            # while the deploy gate failed.
            pytest.skip("git is not installed — nothing to assert about tracking")
        if result.returncode != 0:
            # The published template is an unpacked tree, not a git checkout —
            # "which files are tracked" has no meaning there. Self-skip rather
            # than fail: this asserts a property of the SOURCE repo's publish
            # machinery, and the repo's standing rule is that a test whose
            # subject is absent skips itself (sanitize-time source surgery rots;
            # self-skipping tests do not).
            pytest.skip("not a git checkout — nothing to assert about tracking")
        tracked = result.stdout.split()

        assert example in tracked, f"{example} must be tracked — it is what ships"
        assert real not in tracked, (
            f"{real} must NOT be tracked: it holds this deployment's real values, "
            "so tracking it upstream AND downstream is the permanent-conflict case. "
            "Generate it from the .example instead."
        )

    def test_sanitizer_no_longer_renames_examples_over_real_files(self) -> None:
        sanitizer = REPO_ROOT / "scripts" / "sanitize-for-template.sh"
        if not sanitizer.is_file():
            pytest.skip("publish tooling is not shipped — nothing to assert")
        src = sanitizer.read_text()
        assert "tb_example.rename" not in src
        assert "du_example.rename" not in src

    def test_generator_exists_and_is_executable(self) -> None:
        gen = REPO_ROOT / "scripts" / "materialize-config.sh"
        assert gen.is_file(), "scripts/materialize-config.sh missing"
        assert gen.stat().st_mode & 0o111


class TestGeneratedRealFilesAreIgnored:
    def test_gitignore_covers_the_generated_files(self) -> None:
        ignored = (REPO_ROOT / ".gitignore").read_text()
        for path in ("infrastructure/mcp-toolbox/tools.yaml", "docs/ops/deployed-urls.md"):
            assert path in ignored, f"{path} is generated — it must be gitignored"


def test_no_local_developer_path_in_shipped_python() -> None:
    """A developer's home directory in shipped source is deployment identity.

    Matched by SHAPE, not by a literal. The first version of this test hardcoded
    one developer's home path — and the M4 scrub duly rewrote that literal to
    the placeholder, silently inverting the test into "does source contain the
    placeholder". A regex assembled from parts is immune, and it also catches
    any *other* developer's path rather than only the one who wrote it.
    """
    import re

    home = re.compile(r"/(?:Users|home)/[a-z][a-z0-9_.-]*/", re.IGNORECASE)

    offenders = []
    for path in (REPO_ROOT / "backend").rglob("*.py"):
        if ".venv" in str(path) or "/tests/" in str(path):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if home.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == [], f"local developer paths in shipped source: {offenders}"


def test_environment_is_not_relied_on_by_these_tests() -> None:
    """Guard: these assertions must hold regardless of the developer's shell."""
    assert os.environ.get("AUTH_OPERATOR_DOMAINS") in (None, ""), (
        "unset AUTH_OPERATOR_DOMAINS locally — it masks the default under test"
    )

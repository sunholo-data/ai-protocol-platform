"""Tests for `aiplatform deploy` — status / promote / release (M3, AIPLA #46/#47).

Two properties matter most here and both are asserted rather than assumed:

  * **Nothing mutates without an explicit --yes.** A promotion is an
    outward-facing, hard-to-reverse action; a default-dry-run that silently
    became default-execute is the kind of regression that only shows up in
    production.
  * **Promotion runs a TRIGGER, never `gcloud builds submit`.** `builds submit`
    uploads the operator's local working tree as the build source — that is how
    AIPLA shipped a prod frontend built from an untagged laptop commit — and it
    drags in the two-identity / storage.objectViewer traps of #46. There is a
    test below that greps the whole repo for it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from aiplatform.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


# --------------------------------------------------------------------------
# promote
# --------------------------------------------------------------------------


def test_promote_rejects_invalid_edge_without_running_anything():
    """dev -> prod must be refused: prod receives what test verified, not a shortcut."""
    with patch("aiplatform.commands.deploy.subprocess.run") as run:
        result = CliRunner().invoke(main, ["deploy", "promote", "--from", "dev", "--to", "prod", "--version", "v1.0.0"])
    assert result.exit_code != 0
    assert "not a valid promotion edge" in result.output
    run.assert_not_called()


def test_promote_dry_run_by_default_passes_no_yes_flag():
    """The wrapper must not smuggle --yes in: dry run is the default all the way down."""
    with patch("aiplatform.commands.deploy.subprocess.run", return_value=_ok()) as run:
        result = CliRunner().invoke(
            main, ["deploy", "promote", "--from", "test", "--to", "prod", "--version", "v6.20.0"]
        )
    assert result.exit_code == 0
    cmd = run.call_args[0][0]
    assert "--yes" not in cmd
    assert cmd[1:] == ["--from", "test", "--to", "prod", "--version", "v6.20.0"]


def test_promote_with_yes_forwards_the_confirmation():
    with patch("aiplatform.commands.deploy.subprocess.run", return_value=_ok()) as run:
        result = CliRunner().invoke(
            main, ["deploy", "promote", "--from", "test", "--to", "prod", "--version", "v6.20.0", "--yes"]
        )
    assert result.exit_code == 0
    assert "--yes" in run.call_args[0][0]


def test_promote_delegates_to_the_shell_script():
    """One implementation of promotion logic, not two that drift."""
    with patch("aiplatform.commands.deploy.subprocess.run", return_value=_ok()) as run:
        CliRunner().invoke(main, ["deploy", "promote", "--from", "dev", "--to", "test", "--version", "v6.20.0"])
    assert run.call_args[0][0][0].endswith("scripts/promote-env.sh")


def test_promote_surfaces_script_failure():
    with patch("aiplatform.commands.deploy.subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
        result = CliRunner().invoke(
            main, ["deploy", "promote", "--from", "dev", "--to", "test", "--version", "v6.20.0"]
        )
    assert result.exit_code != 0
    assert "Promotion failed" in result.output


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


def test_status_reports_digest_per_container():
    images = (
        "eu-docker.pkg.dev/p/r/svc/ui@sha256:aaa;"
        "eu-docker.pkg.dev/p/r/svc/backend@sha256:bbb;"
        "eu-docker.pkg.dev/p/r/svc/toolbox@sha256:ccc"
    )
    with patch("aiplatform.commands.deploy.shutil.which", return_value="/usr/bin/gcloud"):
        with patch("aiplatform.commands.deploy.subprocess.run", return_value=_ok(f"svc-00042-abc\t{images}")):
            result = CliRunner().invoke(main, ["deploy", "status", "--env", "test"])
    assert result.exit_code == 0
    assert "svc-00042-abc" in result.output
    assert "sha256:aaa" in result.output
    assert "sha256:bbb" in result.output
    assert "sha256:ccc" in result.output
    assert "your-project-id-test" in result.output


def test_status_flags_an_unpinned_tag_rather_than_printing_it_as_an_identity():
    """A tag is not an identity. Saying so is the whole point of M1."""
    with patch("aiplatform.commands.deploy.shutil.which", return_value="/usr/bin/gcloud"):
        with patch(
            "aiplatform.commands.deploy.subprocess.run",
            return_value=_ok("svc-00001-xyz\teu-docker.pkg.dev/p/r/svc/backend:dev"),
        ):
            result = CliRunner().invoke(main, ["deploy", "status", "--env", "dev"])
    assert result.exit_code == 0
    assert "NOT PINNED" in result.output


def test_status_requires_gcloud():
    with patch("aiplatform.commands.deploy.shutil.which", return_value=None):
        result = CliRunner().invoke(main, ["deploy", "status", "--env", "dev"])
    assert result.exit_code != 0
    assert "gcloud CLI not found" in result.output


def test_status_surfaces_gcloud_failure():
    with patch("aiplatform.commands.deploy.shutil.which", return_value="/usr/bin/gcloud"):
        with patch(
            "aiplatform.commands.deploy.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="PERMISSION_DENIED"),
        ):
            result = CliRunner().invoke(main, ["deploy", "status", "--env", "prod"])
    assert result.exit_code != 0
    assert "PERMISSION_DENIED" in result.output


# --------------------------------------------------------------------------
# release
# --------------------------------------------------------------------------


def test_release_dry_run_touches_nothing():
    with patch("aiplatform.commands.deploy.subprocess.run") as run:
        result = CliRunner().invoke(main, ["deploy", "release", "--version", "v6.20.0"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    run.assert_not_called()


def test_release_with_yes_tags_then_pushes():
    with patch("aiplatform.commands.deploy.subprocess.run", return_value=_ok()) as run:
        result = CliRunner().invoke(main, ["deploy", "release", "--version", "v6.20.0", "--yes"])
    assert result.exit_code == 0
    calls = [c[0][0] for c in run.call_args_list]
    assert calls[0][:3] == ["git", "tag", "-a"]
    assert calls[1] == ["git", "push", "origin", "v6.20.0"]


# --------------------------------------------------------------------------
# The repo-wide invariant
# --------------------------------------------------------------------------


def test_repo_never_uses_gcloud_builds_submit():
    """#46: `builds submit` uploads the operator's LOCAL WORKING TREE as build
    source, runs as a different SA than triggers unless --service-account is
    passed, and additionally needs storage.objectViewer on <project>_cloudbuild.
    Promotion runs as a trigger precisely to avoid all three. This asserts we
    never reintroduce it.
    """
    # --untracked matters: without it git grep searches only tracked files, so a
    # brand-new script could smuggle `builds submit` in and the guard would pass
    # until the moment it got committed. (Caught exactly that way while writing
    # this test — the first version silently passed against an injected probe.)
    result = subprocess.run(
        # Scoped to paths that EXECUTE. This test file necessarily contains the
        # string in its own prose, and so would match itself.
        [
            "git",
            "grep",
            "-n",
            "--untracked",
            "builds submit",
            "--",
            "scripts/",
            "Makefile",
            "cli/aiplatform/",
            "*.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # Prose is fine and in fact expected — the pipelines carry comments
    # explaining WHY this command is avoided, and those must not trip the
    # guard. Only an actual invocation counts: the match must not sit behind
    # a comment marker on its line.
    invocations = []
    for line in result.stdout.splitlines():
        _, _, text = line.partition(":")
        _, _, code = text.partition(":")
        stripped = code.strip()
        if stripped.startswith("#") or stripped.startswith("*"):
            continue
        invocations.append(line)

    assert not invocations, "`gcloud builds submit` reintroduced:\n" + "\n".join(invocations)

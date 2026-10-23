"""Reject remote/credential-bearing inputs and preserve partial verification status."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from aiplatform.cli import main


@pytest.mark.parametrize(
    "url",
    [
        "https://assistant.example.com",
        "http://localhost.evil:3000",
        "file:///tmp/test",
        "http://user:secret@localhost:3000",
        "http://localhost:3000/?token=secret",
        "http://localhost:3000/#token",
        "http://localhost",
        "http://localhost:abc",
    ],
)
def test_reject_unsafe_url(url):
    with patch("aiplatform.commands.webmcp.subprocess.run") as run:
        result = CliRunner().invoke(main, ["webmcp", "inspect", "--url", url])
    assert result.exit_code != 0
    run.assert_not_called()


def test_partial_status_is_not_success(tmp_path: Path):
    script = tmp_path / "scripts" / "verify-visual-feedback.mjs"
    with (
        patch("aiplatform.commands.webmcp._find_script", return_value=script),
        patch("aiplatform.commands.webmcp.subprocess.run", return_value=SimpleNamespace(returncode=2)) as run,
    ):
        result = CliRunner().invoke(main, ["webmcp", "inspect", "--url", "http://localhost:3000"])
    assert result.exit_code == 2
    assert run.call_args.args[0] == ["node", str(script), "--mode", "inspect", "--url", "http://localhost:3000"]
    assert run.call_args.kwargs["timeout"] == 90


def test_capture_mode_is_explicit():
    with (
        patch("aiplatform.commands.webmcp._find_script", return_value=Path("/repo/scripts/verify-visual-feedback.mjs")),
        patch("aiplatform.commands.webmcp.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run,
    ):
        result = CliRunner().invoke(main, ["webmcp", "verify", "--mode", "capture"])
    assert result.exit_code == 0
    assert run.call_args.args[0][-2:] == ["--mode", "capture"]


def test_missing_node_is_actionable():
    with (
        patch("aiplatform.commands.webmcp._find_script", return_value=Path("/repo/scripts/verify-visual-feedback.mjs")),
        patch("aiplatform.commands.webmcp.subprocess.run", side_effect=FileNotFoundError),
    ):
        result = CliRunner().invoke(main, ["webmcp", "verify"])
    assert result.exit_code == 1
    assert "Node.js is required" in result.output


def test_missing_checkout():
    with patch("aiplatform.commands.webmcp._find_script", return_value=None):
        result = CliRunner().invoke(main, ["webmcp", "verify"])
    assert result.exit_code == 1
    assert "platform checkout" in result.output


def test_timeout_is_reported():
    import subprocess

    with (
        patch("aiplatform.commands.webmcp._find_script", return_value=Path("/repo/scripts/verify-visual-feedback.mjs")),
        patch("aiplatform.commands.webmcp.subprocess.run", side_effect=subprocess.TimeoutExpired("node", 90)),
    ):
        result = CliRunner().invoke(main, ["webmcp", "verify"])
    assert result.exit_code == 1
    assert "timed out" in result.output


def test_experimental_flags_require_explicit_option():
    with (
        patch("aiplatform.commands.webmcp._find_script", return_value=Path("/repo/scripts/verify-visual-feedback.mjs")),
        patch("aiplatform.commands.webmcp.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run,
    ):
        result = CliRunner().invoke(main, ["webmcp", "verify", "--experimental-webmcp"])
    assert result.exit_code == 0
    assert "--experimental-webmcp" in run.call_args.args[0]


def test_ui_mode_runs_real_component_fixture(tmp_path: Path):
    script = tmp_path / "scripts" / "verify-visual-feedback.mjs"
    script.parent.mkdir()
    ui = script.with_name("verify-visual-feedback-ui.mjs")
    ui.touch()
    with (
        patch("aiplatform.commands.webmcp._find_script", return_value=script),
        patch("aiplatform.commands.webmcp.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run,
    ):
        result = CliRunner().invoke(main, ["webmcp", "verify", "--mode", "ui"])
    assert result.exit_code == 0
    assert run.call_args.args[0] == ["node", str(ui)]

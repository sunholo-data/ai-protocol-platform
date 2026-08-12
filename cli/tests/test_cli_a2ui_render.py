"""Tests for `aiplatform a2ui render` (tool-results-as-a2ui / 7.3, M3).

The verb is a thin dev wrapper: it locates the monorepo backend and shells to
`python -m adk.a2ui_render_preview` (the real render+validate logic lives there,
tested in backend/tests/unit/test_a2ui_render_preview.py). These tests pin the
argument wiring + the missing-backend guard by mocking the boundary.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from aiplatform.cli import main


def _ok(returncode: int = 0):
    return SimpleNamespace(returncode=returncode)


def test_render_list_shells_to_preview_with_list_flag(tmp_path: Path) -> None:
    with (
        patch("aiplatform.commands.a2ui._find_backend_dir", return_value=tmp_path),
        patch("aiplatform.commands.a2ui.subprocess.run", return_value=_ok()) as run,
    ):
        result = CliRunner().invoke(main, ["a2ui", "render", "--list"])
    assert result.exit_code == 0, result.output
    cmd = run.call_args.args[0]
    assert cmd == ["uv", "run", "python", "-m", "adk.a2ui_render_preview", "--list"]
    assert run.call_args.kwargs["cwd"] == str(tmp_path)


def test_render_mapping_shells_with_mapping_and_result(tmp_path: Path) -> None:
    with (
        patch("aiplatform.commands.a2ui._find_backend_dir", return_value=tmp_path),
        patch("aiplatform.commands.a2ui.subprocess.run", return_value=_ok()) as run,
    ):
        result = CliRunner().invoke(
            main, ["a2ui", "render", "ppa_comparison", "--result", "comparison.json"]
        )
    assert result.exit_code == 0, result.output
    cmd = run.call_args.args[0]
    assert cmd[:6] == ["uv", "run", "python", "-m", "adk.a2ui_render_preview", "--mapping"]
    assert cmd[6] == "ppa_comparison"
    assert cmd[7] == "--result"
    assert cmd[8].endswith("comparison.json")


def test_render_propagates_nonzero_exit_code(tmp_path: Path) -> None:
    with (
        patch("aiplatform.commands.a2ui._find_backend_dir", return_value=tmp_path),
        patch("aiplatform.commands.a2ui.subprocess.run", return_value=_ok(2)),
    ):
        result = CliRunner().invoke(main, ["a2ui", "render", "--list"])
    assert result.exit_code == 2


def test_render_errors_when_backend_not_found() -> None:
    with patch("aiplatform.commands.a2ui._find_backend_dir", return_value=None):
        result = CliRunner().invoke(main, ["a2ui", "render", "--list"])
    assert result.exit_code != 0
    assert "backend not found" in result.output


def test_render_requires_mapping_and_result_without_list(tmp_path: Path) -> None:
    with patch("aiplatform.commands.a2ui._find_backend_dir", return_value=tmp_path):
        result = CliRunner().invoke(main, ["a2ui", "render", "ppa_comparison"])
    assert result.exit_code != 0
    assert "required" in result.output

"""Tests for `aiplatform logs`."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from click.testing import CliRunner

from aiplatform.cli import main
from aiplatform.commands.logs import _build_filter


def test_build_filter_scopes_service_container_and_grep() -> None:
    f = _build_filter("platform-frontend", "sidecar", ("doc loader", "extract_ppa"))
    assert 'resource.type="cloud_run_revision"' in f
    assert 'resource.labels.service_name="platform-frontend"' in f
    assert 'labels.container_name="sidecar"' in f
    assert 'textPayload:"doc loader" OR textPayload:"extract_ppa"' in f


def test_build_filter_no_grep_omits_payload_clause() -> None:
    f = _build_filter("svc", "sidecar", ())
    assert "textPayload" not in f


def test_logs_local_env_errors() -> None:
    result = CliRunner().invoke(main, ["--env", "local", "logs"])
    assert result.exit_code != 0
    assert ".dev-logs" in result.output


@patch("aiplatform.commands.logs.shutil.which", return_value="/usr/bin/gcloud")
@patch("aiplatform.commands.logs.subprocess.run")
def test_logs_invokes_gcloud_with_resolved_target(mock_run, _which) -> None:
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="2026-07-09 line one\n", stderr=""
    )
    result = CliRunner().invoke(
        main, ["--env", "dev", "logs", "--container", "backend", "-g", "doc loader", "--since", "10m", "-n", "20"]
    )
    assert result.exit_code == 0, result.output
    cmd = mock_run.call_args.args[0]
    assert cmd[0:3] == ["gcloud", "logging", "read"]
    log_filter = cmd[3]
    assert 'service_name="platform-frontend"' in log_filter
    assert 'container_name="sidecar"' in log_filter
    assert 'textPayload:"doc loader"' in log_filter
    assert "--project=your-project-id" in cmd
    assert "--freshness=10m" in cmd
    assert "--limit=20" in cmd
    assert "line one" in result.output


@patch("aiplatform.commands.logs.shutil.which", return_value="/usr/bin/gcloud")
@patch("aiplatform.commands.logs.subprocess.run")
def test_logs_ui_container_maps_to_main(mock_run, _which) -> None:
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    result = CliRunner().invoke(main, ["--env", "dev", "logs", "--container", "ui"])
    assert result.exit_code == 0, result.output
    assert 'container_name="main"' in mock_run.call_args.args[0][3]
    assert "(no matching log entries)" in result.output


@patch("aiplatform.commands.logs.shutil.which", return_value=None)
def test_logs_missing_gcloud_errors(_which) -> None:
    result = CliRunner().invoke(main, ["--env", "dev", "logs"])
    assert result.exit_code != 0
    assert "gcloud not found" in result.output


@patch("aiplatform.commands.logs.shutil.which", return_value="/usr/bin/gcloud")
@patch("aiplatform.commands.logs.subprocess.run")
def test_logs_surfaces_gcloud_failure(mock_run, _which) -> None:
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="PERMISSION_DENIED")
    result = CliRunner().invoke(main, ["--env", "dev", "logs"])
    assert result.exit_code != 0
    assert "PERMISSION_DENIED" in result.output

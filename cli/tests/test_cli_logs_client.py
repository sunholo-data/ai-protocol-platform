"""`aiplatform logs client` — read browser events, or emit one (FRONTEND-OBS M4).

`logs` becomes a group so the existing `aiplatform --env dev logs -g …` keeps
working unchanged (test_cli_logs.py pins that); `client` is the subcommand.
`--emit` is what keeps scripts/verify-client-logs.sh thin: the CLI already
carries the token, so the script is assertions rather than curl incantations.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from aiplatform.cli import main
from aiplatform.commands.logs import _LOG_CFG, _build_browser_filter

# --- filter construction (pure) ----------------------------------------------


def test_browser_filter_selects_browser_source_on_the_backend_container() -> None:
    f = _build_browser_filter("svc", "sidecar")
    assert 'resource.type="cloud_run_revision"' in f
    assert 'resource.labels.service_name="svc"' in f
    assert 'labels.container_name="sidecar"' in f
    assert 'jsonPayload.source="browser"' in f
    assert "textPayload" not in f, "browser events are structured; textPayload would match nothing"


def test_browser_filter_scopes_by_uid_session_and_severity() -> None:
    f = _build_browser_filter("svc", "sidecar", uid="u-1", session="s-1", severity="WARN")
    assert 'jsonPayload.uid="u-1"' in f
    assert 'jsonPayload.sessionId="s-1"' in f
    # Cloud Logging's severity name is WARNING; the client's is WARN.
    assert "severity>=WARNING" in f


def test_browser_filter_grep_matches_message_or_event() -> None:
    f = _build_browser_filter("svc", "sidecar", patterns=("nonce-123",))
    assert 'jsonPayload.message:"nonce-123"' in f
    assert 'jsonPayload.event:"nonce-123"' in f


# --- reading -------------------------------------------------------------------


@patch("aiplatform.commands.logs.shutil.which", return_value="/usr/bin/gcloud")
@patch("aiplatform.commands.logs.subprocess.run")
def test_logs_client_reads_browser_events_via_gcloud(mock_run, _which) -> None:
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
    result = CliRunner().invoke(
        main, ["--env", "dev", "logs", "client", "--since", "15m", "-n", "5", "--uid", "u-1", "--raw"]
    )
    assert result.exit_code == 0, result.output
    cmd = mock_run.call_args.args[0]
    assert cmd[0:3] == ["gcloud", "logging", "read"]
    assert 'jsonPayload.source="browser"' in cmd[3]
    assert 'jsonPayload.uid="u-1"' in cmd[3]
    assert f'service_name="{_LOG_CFG["service"]}"' in cmd[3]
    assert 'container_name="sidecar"' in cmd[3], "browser events are re-emitted by the BACKEND container"
    assert f"--project={_LOG_CFG['projects']['dev']}" in cmd
    assert "--freshness=15m" in cmd
    assert "--limit=5" in cmd
    assert "--format=json" in cmd


def test_logs_client_read_refuses_local_env() -> None:
    result = CliRunner().invoke(main, ["--env", "local", "logs", "client"])
    assert result.exit_code != 0
    assert "Cloud Run" in result.output


# --- emitting -------------------------------------------------------------------


@patch("aiplatform.commands.logs.AIPlatformClient")
def test_logs_client_emit_posts_one_error_event_and_prints_the_nonce(mock_client_cls) -> None:
    client = MagicMock()
    client.post.return_value = {"accepted": 1, "dropped": 0, "droppedFields": 0}
    mock_client_cls.return_value = client

    result = CliRunner().invoke(main, ["--env", "dev", "logs", "client", "--emit", "--nonce", "abc-nonce"])
    assert result.exit_code == 0, result.output

    path = client.post.call_args.args[0]
    body = client.post.call_args.kwargs["json"]
    assert path == "/api/client-logs"
    (event,) = body["events"]
    assert event["severityText"] == "ERROR"
    assert "abc-nonce" in event["attributes"]["errorMessage"]
    assert "abc-nonce" in event["attributes"]["stack"], "the stack is what Error Reporting groups on"

    out = json.loads(result.output.strip().splitlines()[-1])
    assert out["nonce"] == "abc-nonce"
    assert out["response"] == {"accepted": 1, "dropped": 0, "droppedFields": 0}


@patch("aiplatform.commands.logs.AIPlatformClient")
def test_logs_client_emit_generates_a_nonce_when_none_given(mock_client_cls) -> None:
    client = MagicMock()
    client.post.return_value = {"accepted": 1, "dropped": 0, "droppedFields": 0}
    mock_client_cls.return_value = client
    result = CliRunner().invoke(main, ["--env", "dev", "logs", "client", "--emit"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output.strip().splitlines()[-1])
    assert len(out["nonce"]) >= 8
    assert out["nonce"] in client.post.call_args.kwargs["json"]["events"][0]["attributes"]["errorMessage"]


@patch("aiplatform.commands.logs.AIPlatformClient")
def test_logs_client_emit_supports_the_negative_probes(mock_client_cls) -> None:
    """--severity INFO (must NOT reach Error Reporting), --forge-uid (must be
    dropped server-side), --count N (over-cap must be refused whole)."""
    client = MagicMock()
    client.post.return_value = {"accepted": 3, "dropped": 0, "droppedFields": 6}
    mock_client_cls.return_value = client
    result = CliRunner().invoke(
        main,
        ["--env", "dev", "logs", "client", "--emit", "--severity", "INFO", "--forge-uid", "u-forged", "--count", "3"],
    )
    assert result.exit_code == 0, result.output
    events = client.post.call_args.kwargs["json"]["events"]
    assert len(events) == 3
    assert all(e["severityText"] == "INFO" for e in events)
    assert all(e["attributes"]["uid"] == "u-forged" for e in events)
    assert all(e["attributes"]["userId"] == "u-forged" for e in events)


@patch("aiplatform.commands.logs.AIPlatformClient")
def test_logs_client_emit_surfaces_a_refusal_as_a_nonzero_exit(mock_client_cls) -> None:
    from aiplatform.http import APIError

    client = MagicMock()
    client.post.side_effect = APIError("POST /api/client-logs returned 413: batch has 51 events; max is 50")
    mock_client_cls.return_value = client
    result = CliRunner().invoke(main, ["--env", "dev", "logs", "client", "--emit", "--count", "51"])
    assert result.exit_code != 0
    assert "413" in result.output


@patch("aiplatform.commands.logs.AIPlatformClient")
def test_logs_client_emit_works_against_local(mock_client_cls) -> None:
    """Emitting is just an HTTP POST — it must not inherit the Cloud-Run-only rule of reads."""
    client = MagicMock()
    client.post.return_value = {"accepted": 1, "dropped": 0, "droppedFields": 0}
    mock_client_cls.return_value = client
    result = CliRunner().invoke(main, ["--env", "local", "logs", "client", "--emit"])
    assert result.exit_code == 0, result.output

"""Pytest harness for the mock-backend self-test.

Two layers:

1. **MockBackend fixture** — boots the real SSE mock on a real socket and
   runs the *installed* ``aiplatform`` binary as a subprocess against it.
   Catches transport-level regressions (SSE buffering, ``httpx.stream``
   lifecycle, stdin/stdout pipe semantics) that the existing respx-mocked
   tests cannot see.

2. **Bash wrapper smoke** — a single test that runs
   ``scripts/cli-selftest-mock.sh`` end-to-end and asserts exit 0. Locks
   in the contract that the runner exists, has +x, and stays self-contained.

The pytest is **skipped** if the global ``aiplatform`` binary isn't
installed — running ``make cli-install`` is the prerequisite, and we don't
want to fail CI for a setup step the user controls. ``cli-doctor`` is the
matching pre-flight check in the bash wrapper.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.mock_backend import MockBackend, canonical_events

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "cli"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "cli-selftest-mock.sh"

# Skip the whole module when the global binary isn't installed — running
# `make cli-install` is the documented prerequisite.
_BINARY = shutil.which("aiplatform")
pytestmark = pytest.mark.skipif(
    _BINARY is None,
    reason="`aiplatform` not on PATH — run `make cli-install` first",
)


def _probe(api_url: str, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Invoke the installed `aiplatform` binary with a captured environment."""
    env = {
        **os.environ,
        "AIPLATFORM_API_URL": api_url,
        "AIPLATFORM_ID_TOKEN": "selftest-fake-token",
    }
    return subprocess.run(
        [_BINARY, "--env", "local", "skill", "probe", "mock-skill", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# --- Library-mode tests (MockBackend used directly) ---


def test_probe_against_mock_backend_prints_table() -> None:
    """End-to-end: real binary + real socket → expected table on stdout."""
    with MockBackend() as mb:
        result = _probe(mb.url, "--message", "hi")
    assert result.returncode == 0, (
        f"probe exited {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    out = result.stdout
    assert "TTFT breakdown" in out
    assert "first_model_token" in out
    assert "412.30ms" in out  # canonical first_model_token_ms
    assert "← TTFT" in out
    assert "gemini-2.5-flash" in out
    assert "routing: fast" in out


def test_probe_json_flag_outputs_machine_readable_payload() -> None:
    """--json must produce a valid JSON document with first_model_token_ms."""
    with MockBackend() as mb:
        result = _probe(mb.url, "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["model_used"] == "gemini-2.5-flash"
    assert payload["first_model_token_ms"] == 412.3
    assert payload["ttft_mode"] == "full"


def test_probe_against_closed_port_exits_nonzero() -> None:
    """Transport-level failure: the API URL points at a port nothing
    listens on. The CLI must fail loudly, not silently no-op."""
    # Port 1 is privileged on macOS/Linux — never listening.
    result = _probe("http://127.0.0.1:1")
    assert result.returncode != 0, f"probe should have failed against closed port; got 0\nstdout:\n{result.stdout}"
    # The error message should mention transport / connection refused.
    combined = (result.stdout + result.stderr).lower()
    assert any(token in combined for token in ("transport", "connection", "refused", "could not")), (
        f"expected transport-error keyword in output:\n{combined}"
    )


def test_probe_against_mock_without_latency_report_exits_2() -> None:
    """When the backend doesn't emit a LATENCY_REPORT (e.g. the operator
    has AITANA_TTFT_MODE=off), the CLI exits with code 2 and a hint."""
    events = [e for e in canonical_events() if e.get("name") != "LATENCY_REPORT"]
    with MockBackend(events=events) as mb:
        result = _probe(mb.url)
    assert result.returncode == 2, (
        f"expected exit 2 for missing LATENCY_REPORT; got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "No LATENCY_REPORT" in (result.stdout + result.stderr)


# --- Bash-wrapper smoke (locks in scripts/cli-selftest-mock.sh) ---


def test_bash_smoke_script_passes() -> None:
    """The committed scripts/cli-selftest-mock.sh runs the full smoke
    end-to-end and exits 0. This locks in: the script is +x, the python
    -m fixture import resolves, the FIFO port-readback handshake works."""
    assert SMOKE_SCRIPT.exists(), f"missing: {SMOKE_SCRIPT}"
    assert os.access(SMOKE_SCRIPT, os.X_OK), f"not executable: {SMOKE_SCRIPT}"
    result = subprocess.run(
        ["bash", str(SMOKE_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30.0,
        check=False,
    )
    assert result.returncode == 0, (
        f"smoke script exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "passed" in result.stdout.lower()

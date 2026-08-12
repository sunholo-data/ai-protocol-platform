"""Locks in the skip-don't-fail policy of scripts/cli-selftest-live.sh.

The live smoke is operator-driven (needs a running backend, a Firebase
token, and a seed skill). This test suite asserts that every "missing
prereq" path exits 0 with a clear message — so CI sweeps can run the
script unattended without breaking the build.

The happy path (real probe) is intentionally not unit-tested here; that
requires a real backend and is the operator's manual-verification step.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "cli-selftest-live.sh"


def _run(env_overrides: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "AIPLATFORM_ID_TOKEN"}
    env.pop("AIPLATFORM_SELFTEST_SKILL_ID", None)
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SMOKE_SCRIPT), *args],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15.0,
        check=False,
    )


def test_script_exists_and_is_executable() -> None:
    assert SMOKE_SCRIPT.exists()
    assert os.access(SMOKE_SCRIPT, os.X_OK)


def test_script_documents_all_skip_paths_in_help_block() -> None:
    """The script's header documents every skip-don't-fail path. Locks
    that contract so future edits can't quietly drop a skip without
    updating the docs (the operator reads the header to know what env
    vars to set)."""
    text = SMOKE_SCRIPT.read_text()
    for required in (
        "backend not on :1956",
        "AIPLATFORM_ID_TOKEN unset",
        "No seed skill id",
        "make cli-install",
    ):
        assert required in text, f"script header missing: {required!r}"


def test_skip_when_backend_unreachable() -> None:
    """Pointing AIPLATFORM_API_URL at a closed port → skip."""
    result = _run(
        {
            "AIPLATFORM_API_URL": "http://127.0.0.1:1",
            "AIPLATFORM_ID_TOKEN": "irrelevant",
            "AIPLATFORM_SELFTEST_SKILL_ID": "irrelevant",
        }
    )
    assert result.returncode == 0, result.stderr
    assert "skipping live smoke" in result.stderr
    assert "backend not reachable" in result.stderr


def test_skip_when_token_missing() -> None:
    """When AIPLATFORM_ID_TOKEN is unset (and backend is up), skip."""
    # The repo's local backend may or may not be running; if it isn't, the
    # earlier health-check skip path fires first — both outcomes are
    # acceptable skips. We just need exit 0 and a skip message.
    result = _run({})
    assert result.returncode == 0, result.stderr
    assert "skipping live smoke" in result.stderr


def test_skip_when_skill_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token set, backend up, but no skill id => skip."""
    # Best-effort: only meaningful when the local backend happens to be up.
    # Otherwise the test still passes because the backend-down skip fires
    # first. Either skip path is acceptable for CI safety.
    result = _run({"AIPLATFORM_ID_TOKEN": "fake-token-for-skip-path-test"})
    assert result.returncode == 0, result.stderr
    assert "skipping live smoke" in result.stderr

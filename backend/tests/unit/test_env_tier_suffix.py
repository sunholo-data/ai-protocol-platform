"""The deploy pipeline and the app must agree on which env a project id is.

`scripts/deploy-run-service.sh` derives ENV_TIER from the project id suffix and
keys the security posture on it: AUTH_REQUIRE_KNOWN_DOMAIN and
TENANT_FALLBACK_FAIL_CLOSED are on only for dev/test/prod, and an `unknown`
tier stays open so a fork's first deploy does not 403 everyone.

`backend/observability/trace_export.py::_env_from_project` accepted `-prod`
while the script accepted only `-production`, so a fork naming its production
project `<name>-prod` deployed with sign-in open to any domain, while its traces
said `prod`. This test runs the script's own `case` block, so the two cannot
drift apart again.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from observability.trace_export import _env_from_project

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_SCRIPT = REPO_ROOT / "scripts" / "deploy-run-service.sh"

_CASE_BLOCK = re.compile(r'^case "\$\{_PROJECT_ID\}" in\n.*?^esac$', re.MULTILINE | re.DOTALL)


def _script_tier(project: str) -> str:
    match = _CASE_BLOCK.search(SPEC_SCRIPT.read_text(encoding="utf-8"))
    assert match, "ENV_TIER case block not found in deploy-run-service.sh"
    out = subprocess.run(
        ["bash", "-c", f'_PROJECT_ID="$1"\n{match.group(0)}\necho "$ENV_TIER"', "_", project],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


@pytest.mark.parametrize(
    ("project", "expected"),
    [
        ("example-dev", "dev"),
        ("example-test", "test"),
        ("example-prod", "prod"),
        ("example-production", "prod"),
        ("example-staging", "unknown"),
        ("example", "unknown"),
    ],
)
def test_pipeline_tier_matches_app(project: str, expected: str) -> None:
    assert _script_tier(project) == expected
    assert _env_from_project(project) == expected

"""scripts/build-id-token.sh — the one way pipelines mint an ID token.

On Cloud Build's DEFAULT pool the metadata server answers
`/identity?audience=…` with HTTP 404 (while `/email` and `/token` work). The
pipelines used to call `/identity` inline with `curl -sf`, so a downstream fork
on the default pool got an EMPTY token and its seed step exited without a word.
The helper falls back to the IAM Credentials `generateIdToken` API.

These tests run the real script against a fake `curl` on PATH that behaves like
the default pool, so the fallback is exercised without any network.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "build-id-token.sh"
PIPELINES = [
    REPO_ROOT / "cloudbuild.yaml",
    REPO_ROOT / "cloudbuild.promote.yaml",
    REPO_ROOT / "backend" / "cloudbuild.yaml",
]

# A fake curl: the URL is the last http(s) argument. /identity fails like the
# default pool (exit 22, as `curl -f` does on 404); the rest answer.
FAKE_CURL = r"""#!/usr/bin/env bash
url=""
for a in "$@"; do case "$a" in http*) url="$a";; esac; done
case "$url" in
  *"/identity?"*) [ "${IDENTITY_WORKS:-0}" = 1 ] && { printf 'metadata-token'; exit 0; }; exit 22 ;;
  *"/default/email") printf 'builder@example-project.iam.gserviceaccount.com' ;;
  *"/default/token") printf '{"access_token": "ya29.fake", "expires_in": 3599}' ;;
  *":generateIdToken")
    if [ "${GENERATE_FAILS:-0}" = 1 ]; then
      printf '{"error": {"code": 403, "message": "Permission iam.serviceAccounts.getOpenIdToken denied"}}'
    else
      printf '{"token": "iam-credentials-token"}'
    fi ;;
  *) echo "unexpected url $url" >&2; exit 7 ;;
esac
"""


def _run(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    curl = bin_dir / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    return subprocess.run(
        ["bash", str(SCRIPT), "https://svc.example.run.app"],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", **env},
    )


def test_uses_the_metadata_token_when_identity_works(tmp_path: Path) -> None:
    out = _run(tmp_path, IDENTITY_WORKS="1")
    assert out.returncode == 0, out.stderr
    assert out.stdout == "metadata-token"


def test_falls_back_to_generate_id_token_on_the_default_pool(tmp_path: Path) -> None:
    out = _run(tmp_path)
    assert out.returncode == 0, out.stderr
    assert out.stdout == "iam-credentials-token"


def test_explains_a_failure_instead_of_printing_an_empty_token(tmp_path: Path) -> None:
    out = _run(tmp_path, GENERATE_FAILS="1")
    assert out.returncode != 0
    assert out.stdout == ""
    assert "serviceAccountTokenCreator" in out.stderr
    assert "builder@example-project.iam.gserviceaccount.com" in out.stderr


@pytest.mark.parametrize("pipeline", PIPELINES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_pipelines_mint_tokens_only_through_the_helper(pipeline: Path) -> None:
    text = pipeline.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "service-accounts/default/identity" not in code, (
        f"{pipeline.name} calls the metadata /identity endpoint inline; use "
        '`bash scripts/build-id-token.sh "$${URL}"`, which falls back when the '
        "default build pool answers /identity with 404."
    )

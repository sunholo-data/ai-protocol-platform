"""The frontend build reads FIREBASE_ENV (.env.local) into its environment.

`while read` skips a final line that has no trailing newline, and a secret
saved in the Secret Manager console has none. On 2026-11-09 a fork's brand
theme key, added as the secret's last line, never reached its dev build: no
error, the UI just shipped the template's look. Earlier the last line was an
empty value, so the loss went unnoticed. Both pipelines that build the
frontend must keep the last line.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _env_loop(pipeline: Path) -> str:
    """The `while … done < .env.local` block, unescaped from Cloud Build's `$$`."""
    text = pipeline.read_text()
    m = re.search(r"^\s*while IFS='=' read .*?done < \.env\.local$", text, re.S | re.M)
    assert m, f"{pipeline.name}: no .env.local read loop found"
    return m.group(0).replace("$$", "$")


@pytest.mark.parametrize("name", ["cloudbuild.yaml", "cloudbuild.promote.yaml"])
def test_last_line_without_newline_is_exported(tmp_path: Path, name: str) -> None:
    (tmp_path / ".env.local").write_text("# comment\nFIRST=1\nSPACED=Acme Energy & Co\nLAST=/brand/x/theme.css")
    script = _env_loop(REPO_ROOT / name) + '\nprintf "%s|%s|%s" "$FIRST" "$SPACED" "$LAST"'
    out = subprocess.run(["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True, check=True).stdout
    assert out == "1|Acme Energy & Co|/brand/x/theme.css"

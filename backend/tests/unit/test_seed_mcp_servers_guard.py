"""The seeder must not repoint a deployed env's customised toolsets.

Found on 2026-09-05 while fixing prod's missing `browser` row: running
`scripts/seed_mcp_servers.py --env prod` would ALSO have rewritten

    toolbox     .../mcp/<tenant-toolset>     ->  .../mcp/example
    toolbox-bq  .../mcp/<tenant-bq-toolset>  ->  .../mcp/example

silently breaking the two most load-bearing skills the deployment has. The
loopback HOST is the same in every environment; the PATH names the toolset and
is not. `seed_ext_apps_map` already had a preserve-don't-clobber guard for
exactly this reason — these two did not, on a docstring claim that the URL is
"the same in every environment".
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


@pytest.fixture
def seeder(monkeypatch):
    import seed_mcp_servers as mod

    fake = MagicMock()
    monkeypatch.setattr(mod, "fs", fake, raising=False)
    return mod, fake


def test_a_customised_toolbox_url_is_preserved(seeder):
    mod, fs = seeder
    fs.get_document.return_value = {"url": "http://127.0.0.1:5000/mcp/tenant-toolset"}

    mod.seed_toolbox(dry_run=False)

    fs.set_document.assert_not_called(), "a deployed toolset must never be repointed at the example"


def test_a_customised_bq_toolset_is_preserved(seeder):
    mod, fs = seeder
    fs.get_document.return_value = {"url": "http://127.0.0.1:5000/mcp/tenant-bq-toolset"}

    mod.seed_toolbox_bq(dry_run=False)

    fs.set_document.assert_not_called()


def test_an_empty_registry_still_gets_seeded(seeder):
    """Preserve must not mean 'never write' — a fresh env needs the row."""
    mod, fs = seeder
    fs.get_document.return_value = None

    mod.seed_toolbox(dry_run=False)

    fs.set_document.assert_called_once()
    assert fs.set_document.call_args.args[1] == "toolbox"


def test_browser_is_skipped_rather_than_guessed_when_no_url_is_given(seeder):
    """The browser URL is per-env; inventing one would be worse than nothing."""
    mod, fs = seeder
    fs.get_document.return_value = None

    mod.seed_browser(None, dry_run=False)

    fs.set_document.assert_not_called()


def test_browser_is_written_when_the_url_is_supplied(seeder):
    mod, fs = seeder
    fs.get_document.return_value = None

    mod.seed_browser("https://platform-browser-example-ew.a.run.app/mcp", dry_run=False)

    fs.set_document.assert_called_once()
    doc_id = fs.set_document.call_args.args[1]
    payload = fs.set_document.call_args.args[2]
    assert doc_id == "browser"
    assert payload["url"].endswith("/mcp")
    assert payload["auth"] == "google_id_token"
    assert payload["gate"] == "browser_allowlist", "the user allowlist gate is load-bearing"

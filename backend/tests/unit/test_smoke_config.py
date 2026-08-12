"""Regression tests for backend/scripts/_smoke_config.py (fork-ergonomics G23 Part A).

The point of this module is that the auth smoke scripts work in BOTH modes:

  * this deployment, where `backend/scripts/_env.py` exists and supplies named
    environments plus our throwaway principal; and
  * a public-template fork, where `_env.py` is excluded by the sanitizer and
    everything must come from flags / env vars / frontend/.env.local.

Mode 2 has no coverage anywhere else — it only manifests in a tree that has
been through `sanitize-for-template.sh`. These tests fake `_env.py`'s absence
so a regression is caught here rather than by a forker.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def _load(monkeypatch: pytest.MonkeyPatch, *, with_env: bool):
    """Import a fresh _smoke_config with `_env` importable or not."""
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    for mod in ("_smoke_config", "_env"):
        sys.modules.pop(mod, None)

    if not with_env:
        # Simulate the sanitized tree: `import _env` raises ImportError.
        real_import = importlib.__import__

        def fake_import(name, *args, **kwargs):
            if name == "_env":
                raise ImportError("No module named '_env'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "__import__", fake_import)
        monkeypatch.setattr("builtins.__import__", fake_import)

    module = importlib.import_module("_smoke_config")
    module = importlib.reload(module)

    # This developer machine has a real frontend/.env.local, and the resolver
    # legitimately reads it — which would silently satisfy assertions about
    # missing config. Point it at a path that cannot exist so each test
    # controls its own inputs. (Dotenv parsing is covered directly below.)
    monkeypatch.setattr(module, "_FRONTEND_ENV", Path("/nonexistent/.env.local"))
    return module


@pytest.fixture(autouse=True)
def _clear_config_env(monkeypatch: pytest.MonkeyPatch):
    """Ambient GCP/Firebase vars must not leak into resolution assertions."""
    for var in (
        "FIREBASE_API_KEY",
        "NEXT_PUBLIC_FIREBASE_API_KEY",
        "FIREBASE_PROJECT_ID",
        "NEXT_PUBLIC_FIREBASE_PROJECT_ID",
        "GOOGLE_CLOUD_PROJECT",
        "GCP_PROJECT",
        "SMOKE_BASE_URL",
        "BASE_URL",
        "FRONTEND_URL",
        "SMOKE_TEST_EMAIL",
        "SMOKE_GROUP_TAGS",
    ):
        monkeypatch.delenv(var, raising=False)


class TestForkMode:
    """`_env.py` absent — the public-template path."""

    def test_no_named_environments(self, monkeypatch):
        sc = _load(monkeypatch, with_env=False)
        assert sc.ENVIRONMENTS == {}
        assert sc.HAVE_NAMED_ENVS is False

    def test_resolves_from_next_public_vars(self, monkeypatch):
        sc = _load(monkeypatch, with_env=False)
        monkeypatch.setenv("NEXT_PUBLIC_FIREBASE_API_KEY", "fake-key")
        monkeypatch.setenv("NEXT_PUBLIC_FIREBASE_PROJECT_ID", "fork-proj")
        monkeypatch.setenv("SMOKE_BASE_URL", "https://fork.example.com")

        assert sc.resolve() == ("https://fork.example.com", "fake-key", "fork-proj")

    def test_explicit_args_win_over_env(self, monkeypatch):
        sc = _load(monkeypatch, with_env=False)
        monkeypatch.setenv("NEXT_PUBLIC_FIREBASE_API_KEY", "from-env")
        monkeypatch.setenv("NEXT_PUBLIC_FIREBASE_PROJECT_ID", "from-env")
        monkeypatch.setenv("SMOKE_BASE_URL", "https://from-env.example.com")

        assert sc.resolve(url="https://cli", api_key="cli-key", project_id="cli-proj") == (
            "https://cli",
            "cli-key",
            "cli-proj",
        )

    def test_missing_config_fails_loud_and_actionable(self, monkeypatch):
        """Never fall back to a wrong project silently — name what's missing."""
        sc = _load(monkeypatch, with_env=False)
        with pytest.raises(sc.SmokeConfigError) as exc:
            sc.resolve()

        message = str(exc.value)
        assert "SMOKE_BASE_URL" in message
        assert "NEXT_PUBLIC_FIREBASE_API_KEY" in message
        assert ".env.local" in message, "must point at the dotenv fallback"
        # No named environments exist in a fork, so don't advertise --env.
        assert "--env dev" not in message

    def test_unknown_named_env_is_rejected(self, monkeypatch):
        sc = _load(monkeypatch, with_env=False)
        with pytest.raises(sc.SmokeConfigError, match=r"_env\.py is absent"):
            sc.resolve(env="dev")

    def test_neutral_identity_defaults(self, monkeypatch):
        """A fork must not inherit this deployment's principal or group tags."""
        sc = _load(monkeypatch, with_env=False)
        email, domain, tags = sc.test_identity()

        assert email.endswith(".test"), "RFC 2606 reserved TLD keeps the principal undeliverable"
        assert "aitana" not in email.lower()
        assert not any("aitana" in t.lower() for t in tags)
        assert domain == email.split("@", 1)[1]

    def test_identity_overridable_by_env(self, monkeypatch):
        sc = _load(monkeypatch, with_env=False)
        monkeypatch.setenv("SMOKE_TEST_EMAIL", "smoke@myfork.test")
        monkeypatch.setenv("SMOKE_GROUP_TAGS", "a-admin, b-admin ,")

        email, domain, tags = sc.test_identity()
        assert (email, domain) == ("smoke@myfork.test", "myfork.test")
        assert tags == ["a-admin", "b-admin"], "blank entries trimmed"

    def test_malformed_identity_rejected(self, monkeypatch):
        sc = _load(monkeypatch, with_env=False)
        monkeypatch.setenv("SMOKE_TEST_EMAIL", "not-an-email")
        with pytest.raises(sc.SmokeConfigError, match="must be an email"):
            sc.test_identity()


@pytest.mark.skipif(
    not (SCRIPTS_DIR / "_env.py").is_file(),
    reason="backend/scripts/_env.py is deployment-private and excluded from the public template",
)
class TestDeploymentMode:
    """`_env.py` present — this repo. Behaviour must be unchanged.

    Self-skips in a sanitized tree rather than failing: the whole point of
    G23 Part A is that `_env.py` may legitimately be absent. This is the same
    pattern the customer-skill tests use (see the sanitize pipeline notes).
    """

    def test_named_environments_available(self, monkeypatch):
        sc = _load(monkeypatch, with_env=True)
        assert sc.HAVE_NAMED_ENVS is True
        assert "dev" in sc.ENVIRONMENTS

    def test_named_env_resolves_without_any_env_vars(self, monkeypatch):
        sc = _load(monkeypatch, with_env=True)
        base_url, api_key, project = sc.resolve(env="dev")
        assert base_url.startswith("https://")
        assert api_key and project

    def test_identity_comes_from_private_env_module(self, monkeypatch):
        """`make smoke-auth` asserts on this principal — it must not drift."""
        sc = _load(monkeypatch, with_env=True)
        email, domain, tags = sc.test_identity()

        from _env import SMOKE_IDENTITY

        assert email == SMOKE_IDENTITY["email"]
        assert tags == list(SMOKE_IDENTITY["group_tags"])
        assert domain == email.split("@", 1)[1]

    def test_unknown_env_lists_the_known_ones(self, monkeypatch):
        sc = _load(monkeypatch, with_env=True)
        with pytest.raises(sc.SmokeConfigError, match="dev"):
            sc.resolve(env="nope")


class TestDotenvParsing:
    def test_parses_quotes_comments_and_export(self, monkeypatch, tmp_path):
        sc = _load(monkeypatch, with_env=False)
        env_file = tmp_path / ".env.local"
        env_file.write_text(
            "\n".join(
                [
                    "# a comment",
                    "",
                    'NEXT_PUBLIC_FIREBASE_API_KEY="quoted-key"',
                    "export NEXT_PUBLIC_FIREBASE_PROJECT_ID='exported-proj'",
                    "MALFORMED_NO_EQUALS",
                ]
            ),
            encoding="utf-8",
        )

        parsed = sc._read_dotenv(env_file)
        assert parsed["NEXT_PUBLIC_FIREBASE_API_KEY"] == "quoted-key"
        assert parsed["NEXT_PUBLIC_FIREBASE_PROJECT_ID"] == "exported-proj"
        assert "MALFORMED_NO_EQUALS" not in parsed

    def test_missing_file_is_not_an_error(self, monkeypatch, tmp_path):
        sc = _load(monkeypatch, with_env=False)
        assert sc._read_dotenv(tmp_path / "nope.env") == {}

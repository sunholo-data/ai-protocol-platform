"""backend/extensions.py — the public/private seam is a no-op without the
private tier, and discovers routers and tools when a package is present."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI

import extensions


def test_deployment_presets_absent_in_public_tree(monkeypatch):
    monkeypatch.setattr(extensions, "private_packages", lambda: [])
    assert extensions.deployment_tool_permissions() is None
    assert extensions.deployment_local_skills(0, lambda name: None) == []


def test_deployment_presets_load_only_from_private_packages(monkeypatch):
    from types import SimpleNamespace

    preset = SimpleNamespace(COMPAT_API={})
    fixture = SimpleNamespace(
        skills=lambda now, load: [{"skillId": "example", "instructions": load("example"), "createdAt": now}]
    )
    monkeypatch.setattr(extensions, "private_packages", lambda: ["deployment"])
    monkeypatch.setattr(
        extensions,
        "_optional_module",
        lambda package, module: {"tool_permissions": preset, "local_fixture": fixture}[module],
    )
    assert extensions.deployment_tool_permissions() is preset
    assert extensions.deployment_local_skills(12, lambda name: "synthetic") == [
        {"skillId": "example", "instructions": "synthetic", "createdAt": 12}
    ]


@pytest.fixture()
def fake_private(tmp_path: Path, monkeypatch):
    """A throwaway `private` package on sys.path, shadowing any real one."""
    root = tmp_path / "private"
    (root / "kb").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "kb" / "__init__.py").write_text("")
    (root / "kb" / "routes.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter
            router = APIRouter()

            @router.get("/api/private-kb/ping")
            def ping():
                return {"ok": True}
            """
        )
    )
    (root / "kb" / "tools.py").write_text(
        textwrap.dedent(
            """
            TOOL_REGISTRY = {"kb_search": lambda cfg: "factory"}
            TOOL_CATALOG = [{"name": "kb_search", "label": "KB", "description": "d", "category": "Search"}]
            """
        )
    )
    (root / "broken").mkdir()
    (root / "broken" / "__init__.py").write_text("")
    (root / "broken" / "routes.py").write_text("raise RuntimeError('boom')\n")
    for name in [m for m in sys.modules if m == "private" or m.startswith("private.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield
    for name in [m for m in sys.modules if m == "private" or m.startswith("private.")]:
        sys.modules.pop(name, None)


def test_no_private_tier_is_a_no_op(monkeypatch):
    monkeypatch.setattr(extensions.importlib.util, "find_spec", lambda name: None)
    app = FastAPI()
    assert extensions.private_packages() == []
    assert extensions.include_private_routers(app) == []
    assert extensions.private_tools() == ({}, [])


def test_discovers_routers_and_tools_and_skips_broken(fake_private):
    from fastapi.testclient import TestClient

    assert extensions.private_packages() == ["broken", "kb"]
    app = FastAPI()
    assert extensions.include_private_routers(app) == ["kb"]
    assert TestClient(app).get("/api/private-kb/ping").json() == {"ok": True}
    registry, catalog = extensions.private_tools()
    assert list(registry) == ["kb_search"]
    assert catalog[0]["name"] == "kb_search"

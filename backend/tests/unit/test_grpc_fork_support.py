"""gRPC fork support is off before anything can load gRPC (2026-10-28).

With it on, a subprocess spawned while gRPC threads were busy sometimes handed
the parent pipe data that was not the child's: 7-35 of 200 calls to a local CLI
read as oversized, and 0 of 1,200 with it off. gRPC reads the variable when it
initialises, so the setting only works if it precedes every import that can
pull gRPC in. This guards the ordering, which a tidy-up of imports would break.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "fast_api_app.py"
SAFE_BEFORE = {"logging", "os", "sys"}


def _is_fork_setting(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and ast.unparse(node.value.func) == "os.environ.setdefault"
        and [ast.literal_eval(a) for a in node.value.args] == ["GRPC_ENABLE_FORK_SUPPORT", "false"]
    )


def _modules(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module or ""]
    return []


def test_fork_support_is_off_before_any_import_that_can_load_grpc():
    body = ast.parse(APP.read_text()).body
    position = next((i for i, node in enumerate(body) if _is_fork_setting(node)), None)
    assert position is not None, "fast_api_app must set GRPC_ENABLE_FORK_SUPPORT=false"
    earlier = [m for node in body[:position] for m in _modules(node)]
    assert set(earlier) <= SAFE_BEFORE, f"imported before the setting: {sorted(set(earlier) - SAFE_BEFORE)}"


def test_an_explicit_setting_still_wins():
    """setdefault, not assignment: an operator who needs fork support can say so."""
    body = ast.parse(APP.read_text()).body
    assert any(_is_fork_setting(node) for node in body)
    assert 'os.environ["GRPC_ENABLE_FORK_SUPPORT"]' not in APP.read_text()

"""`aiplatform resident` — the operator door to a resident agent.

Peers are stubbed at the transport, so these exercise the real request and
response shapes rather than a mock of our own code. The assertions that earn
their place are the REFUSALS: a half-finished task must not print as an answer,
and a missing token must explain itself rather than surface as a bare 401.
"""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from aiplatform.commands import resident as R

CARD = {
    "name": "resident-pi",
    "url": "https://peer.invalid/a2a",
    "capabilities": {
        "streaming": False,
        "extensions": [
            {
                "uri": "https://ailang.dev/a2a/ext/resident-registry/v1",
                "params": {"registeredModels": ["openrouter/z-ai/glm-5.3-flash"]},
            }
        ],
    },
}


def _task(state, text="hello back"):
    return {
        "kind": "task",
        "id": "t-1",
        "contextId": "c-1",
        "status": {"state": state},
        "artifacts": [{"parts": [{"kind": "text", "text": text}]}],
    }


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch):
    for var in ("AIPLATFORM_RESIDENT_URL", "AIPLATFORM_RESIDENT_TOKEN", "AIPLATFORM_RESIDENT_IMPERSONATE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def peer(monkeypatch):
    """Stub the agent. `states` drives successive tasks/get replies."""
    sent = []

    def install(states, card=CARD):
        seq = list(states)
        pos = {"i": 0}

        def _post(url, headers=None, json=None, timeout=None):
            sent.append(json)
            i = min(pos["i"], len(seq) - 1)
            pos["i"] += 1
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": _task(seq[i])})

        def _get(url, headers=None, timeout=None):
            # raise_for_status() needs a request attached, so build a real one.
            return httpx.Response(200, json=card, request=httpx.Request("GET", url))

        monkeypatch.setattr(R.httpx, "post", _post)
        monkeypatch.setattr(R.httpx, "get", _get)
        monkeypatch.setattr(R, "_token", lambda *a, **k: "tok")
        monkeypatch.setattr(R.time, "sleep", lambda *_: None)
        return sent

    return install


def test_card_reads_models_from_capabilities_extensions(peer):
    """A2A's AgentCard has no `metadata` field, so the registry lives in
    capabilities.extensions. Reading the wrong place reports no models."""
    peer(["completed"])
    out = CliRunner().invoke(R.resident, ["card", "--url", "https://peer.invalid"])
    assert out.exit_code == 0, out.output
    assert "openrouter/z-ai/glm-5.3-flash" in out.output


def test_ask_prints_the_answer(peer):
    peer(["completed"])
    out = CliRunner().invoke(R.resident, ["ask", "hi", "--url", "https://peer.invalid"])
    assert out.exit_code == 0, out.output
    assert "hello back" in out.output


def test_ask_reuses_the_context_id_it_is_given(peer):
    """The thread key. Without it every question starts a new conversation."""
    sent = peer(["completed"])
    CliRunner().invoke(R.resident, ["ask", "hi", "--url", "https://peer.invalid", "--context", "ctx-keep"])
    assert sent[0]["params"]["message"]["contextId"] == "ctx-keep"


def test_ask_mints_a_thread_when_none_is_given(peer):
    sent = peer(["completed"])
    CliRunner().invoke(R.resident, ["ask", "hi", "--url", "https://peer.invalid"])
    assert sent[0]["params"]["message"]["contextId"].startswith("cli-")


def test_ask_polls_until_terminal(peer):
    """`submitted` is not an answer — the agent works in the background."""
    sent = peer(["submitted", "working", "completed"])
    out = CliRunner().invoke(R.resident, ["ask", "hi", "--url", "https://peer.invalid"])
    assert out.exit_code == 0, out.output
    assert [c["method"] for c in sent] == ["message/send", "tasks/get", "tasks/get"]


def test_an_unfinished_task_exits_nonzero(peer):
    """It must not print a fragment to stdout as though it were the reply."""
    peer(["input-required"])
    out = CliRunner().invoke(R.resident, ["ask", "hi", "--url", "https://peer.invalid"])
    assert out.exit_code == 1
    assert "did not finish" in out.output


def test_no_url_says_what_to_set():
    out = CliRunner().invoke(R.resident, ["ask", "hi"])
    assert out.exit_code != 0
    assert "AIPLATFORM_RESIDENT_URL" in out.output


def test_a_refused_token_explains_the_allowlist(monkeypatch):
    """A bare 401 sends people to Cloud Run IAM, which is not what gates this."""
    monkeypatch.setattr(R, "_token", lambda *a, **k: "tok")
    monkeypatch.setattr(R.httpx, "post", lambda *a, **k: httpx.Response(401, text="unauthorized"))
    out = CliRunner().invoke(R.resident, ["ask", "hi", "--url", "https://peer.invalid"])
    assert out.exit_code != 0
    assert "allowlist" in out.output


def test_a_json_rpc_error_is_raised_not_printed_as_an_answer(monkeypatch):
    monkeypatch.setattr(R, "_token", lambda *a, **k: "tok")
    monkeypatch.setattr(
        R.httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "model not registered"}}
        ),
    )
    out = CliRunner().invoke(R.resident, ["ask", "hi", "--url", "https://peer.invalid"])
    assert out.exit_code != 0
    assert "model not registered" in out.output

#!/usr/bin/env python3
"""Simulate a peer agent doing A2A-only interaction with the deployed agent.

Walks the discovery + negotiation handshake an enterprise agent would do
when it lands on `/.well-known/agent.json`, then attempts a strict A2A
`message/send` invocation against the URL the card advertises. Reports
honestly what works and what doesn't — useful both as a verification
artefact and to surface the gap between "A2A discovery-compliant" and
"A2A invocation-compliant".

Stdlib only — `python3 scripts/simulate-a2a-peer.py` from anywhere.

Usage:
    AP_URL=https://<your-deployed-host> python3 scripts/simulate-a2a-peer.py
    python3 scripts/simulate-a2a-peer.py [AP_URL]      # positional alt

Default AP_URL is the platform's dev deploy.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_URL = "https://your-service-url.example"


# --- pretty print helpers ---------------------------------------------------

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def step(label: str) -> None:
    print(f"\n{CYAN}━━━ {label} ━━━{RESET}")


def out(arrow: str, text: str) -> None:
    colour = {
        "✓": GREEN,
        "⚠": YELLOW,
        "✗": RED,
        "→": "",
        "←": "",
        "•": "",
        "ℹ": CYAN,
    }.get(arrow, "")
    print(f"  {colour}{arrow}{RESET} {text}")


# --- HTTP helpers (stdlib only) ---------------------------------------------


def _flatten_headers(msg: object) -> dict[str, str]:
    """Combine multi-valued headers (e.g. duplicate `Vary`) into a single
    comma-joined string per name. `dict(HTTPMessage)` keeps only the last
    value, which makes a Vary like `[rsc..., X-A2A-Extensions]` appear as
    just one of them — exactly the false negative this helper avoids.
    """
    out: dict[str, list[str]] = {}
    for key, value in msg.items():  # type: ignore[attr-defined]
        out.setdefault(key.lower(), []).append(value)
    return {k: ", ".join(v) for k, v in out.items()}


def http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
    req = Request(url, headers=headers or {})
    try:
        with urlopen(req, timeout=15) as resp:
            return resp.status, _flatten_headers(resp.headers), resp.read().decode()
    except HTTPError as e:
        return e.code, _flatten_headers(e.headers), e.read().decode()


def http_post(url: str, body: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
    req = Request(
        url,
        data=body.encode(),
        method="POST",
        headers={**(headers or {}), "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status, _flatten_headers(resp.headers), resp.read().decode()
    except HTTPError as e:
        return e.code, _flatten_headers(e.headers), e.read().decode()


# --- simulation -------------------------------------------------------------


def simulate(ap_url: str) -> int:
    ap_url = ap_url.rstrip("/")

    # Step 1 — Discovery
    step("Step 1 · Discovery (peer fetches the agent card)")
    out("→", f"GET {ap_url}/.well-known/agent.json")
    out("→", "X-A2A-Extensions: a2a-v0.2, a2ui-v0.9, a2ui-inline-pattern")
    try:
        status, hdrs, body = http_get(
            f"{ap_url}/.well-known/agent.json",
            headers={"X-A2A-Extensions": "a2a-v0.2, a2ui-v0.9, a2ui-inline-pattern"},
        )
    except URLError as e:
        out("✗", f"unreachable: {e}")
        return 1
    out("←", f"HTTP {status}")
    out("←", f"X-A2A-Extensions: {hdrs.get('x-a2a-extensions', '(none)')}")
    out("←", f"Vary: {hdrs.get('vary', '(none)')}")
    if status != 200:
        out("✗", "discovery failed; abort")
        return 1
    card = json.loads(body)
    out(
        "✓",
        f"Discovered: {card['name']} v{card['version']} (A2A protocol {card['protocolVersion']})",
    )
    out("✓", f"Public URL for invocation: {card['url']}")

    # Step 2 — Capabilities
    step("Step 2 · Read capabilities")
    caps = card["capabilities"]
    out("•", f"streaming: {caps['streaming']}")
    out("•", f"pushNotifications: {caps['pushNotifications']}")
    out("•", f"stateTransitionHistory: {caps['stateTransitionHistory']}")
    out("•", f"extensions ({len(caps['extensions'])}):")
    for e in caps["extensions"]:
        out(" ", f"  - {e['uri']}")
        out(" ", f"      {e['description']}")

    # Step 3 — Skill selection (peer matches goal to skill descriptions)
    step("Step 3 · Pick a skill that matches the peer's goal")
    goal = "I need to write some code"
    out("•", f"Peer's goal: {goal!r}")
    keyword = "code"
    candidates = [s for s in card["skills"] if keyword in s["description"].lower()]
    out("→", f"Filter {len(card['skills'])} advertised skills by description ~ {keyword!r}")
    for s in candidates:
        out(" ", f"  - {s['name']}  (id={s['id']})")
    # Prefer named entry-points; fall back to the first matching skill;
    # fall back to first advertised skill if no description match (so the
    # probe still completes against a minimal-skills deployment).
    chosen = next(
        (s for s in candidates if s["name"].lower() in ("ap-orchestrator", "general assistant")),
        candidates[0] if candidates else (card["skills"][0] if card["skills"] else None),
    )
    if chosen is None:
        out("✗", "no skills advertised — abort")
        return 1
    out("✓", f"Chose: {chosen['name']}  ({chosen['description'][:80]}...)")

    # Step 4 — Attempt strict A2A invocation
    step("Step 4 · Attempt strict A2A `message/send` against the advertised URL")
    msg_id = str(uuid.uuid4())
    rpc = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [
                    {
                        "kind": "text",
                        "text": "Say hello back to me in one short sentence.",
                    }
                ],
                "messageId": msg_id,
            },
            "configuration": {"acceptedOutputModes": ["text"]},
        },
    }
    out("→", f"POST {card['url']}")
    out("→", "Content-Type: application/json")
    out("→", "method=message/send (A2A v0.2 JSON-RPC)")
    status, _, body = http_post(card["url"], json.dumps(rpc))
    if status == 200:
        out("✓", "HTTP 200 — strict A2A invocation works")
        # Body is a JSON-RPC envelope; show the result shape without
        # dumping the whole payload (sessions can be large).
        try:
            parsed = json.loads(body)
            if "result" in parsed:
                result = parsed["result"]
                kind = result.get("kind") or result.get("type") or "(no kind)"
                out("✓", f"result kind: {kind}")
                if "id" in result:
                    out("✓", f"task id: {result['id']}")
            elif "error" in parsed:
                out("⚠", f"JSON-RPC error: {parsed['error']}")
        except json.JSONDecodeError:
            out("⚠", f"non-JSON body: {body[:200]}")
    elif status == 401:
        out("⚠", "HTTP 401 — invocation requires Bearer auth (the bridge is mounted")
        out("⚠", "  but A2A_INVOCATION_REQUIRE_AUTH=true). Peers need an ID token.")
    elif status in (404, 405, 501):
        out(
            "⚠",
            f"HTTP {status} — the A2A invocation bridge is not deployed.",
        )
        out(
            "⚠",
            "  Set ENABLE_A2A_INVOCATION=true in cloudbuild.yaml and re-deploy.",
        )
    else:
        snippet = body[:200].replace("\n", " ")
        out("⚠", f"HTTP {status}: {snippet}")

    # Step 4b — Send a file (G46 — A2A document support)
    step("Step 4b · Send a FilePart (file inbound, Scenario A from G46)")
    file_mimes = card.get("defaultInputModes", [])
    file_capable = any(m != "text" for m in file_mimes)
    if not file_capable:
        out(
            "⚠",
            "card.defaultInputModes advertises text only; agent claims it can't take files.",
        )
        out("⚠", "  Set ENABLE_A2A_FILE_INPUT=true + redeploy to expose file MIMEs.")
        file_status = None
    else:
        out("•", f"card advertises {len(file_mimes)} input MIME(s); sending a small text/plain FilePart")
        file_msg_id = str(uuid.uuid4())
        sample_bytes = b"Sample document for the simulate-a2a-peer probe."
        import base64

        file_rpc = {
            "jsonrpc": "2.0",
            "id": file_msg_id,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": file_msg_id,
                    "parts": [
                        {"kind": "text", "text": "Summarise the attached file in one sentence."},
                        {
                            "kind": "file",
                            "file": {
                                "name": "simulate-a2a-peer-sample.txt",
                                "mimeType": "text/plain",
                                "bytes": base64.b64encode(sample_bytes).decode("ascii"),
                            },
                        },
                    ],
                },
                "configuration": {"acceptedOutputModes": ["text"]},
            },
        }
        out("→", f"POST {card['url']}  (file + text)")
        file_status, _, file_body = http_post(card["url"], json.dumps(file_rpc))
        if file_status == 200:
            out("✓", "HTTP 200 — FilePart accepted by the interceptor")
            try:
                parsed = json.loads(file_body)
                if "result" in parsed:
                    out("✓", f"task id: {parsed['result'].get('id', '(none)')}")
                elif "error" in parsed:
                    out("⚠", f"JSON-RPC error: {parsed['error']}")
            except json.JSONDecodeError:
                out("⚠", f"non-JSON body: {file_body[:200]}")
        elif file_status in (400,):
            out("✗", "HTTP 400 — likely FileExtractionInterceptor inert (Friction 29).")
            out("✗", "  → Confirm force_new_version=True on the deployed A2aAgentExecutor.")
        else:
            out("⚠", f"HTTP {file_status}: {file_body[:200]}")

    # Step 5 — What works / what doesn't
    step("What an A2A-only peer can do today")
    out("✓", "Discover the agent (unauthenticated GET on /.well-known/agent.json)")
    out("✓", "Read every public skill, its description, and ID")
    out("✓", "Negotiate UI / protocol extensions via X-A2A-Extensions")
    out("✓", "Learn the canonical public URL for further interaction")
    out("✓", "Be registered as a tool in a Gemini Enterprise workspace (proven)")
    if status == 200:
        out("✓", "Strict A2A `message/send` JSON-RPC invocation: WORKING")
    elif status == 401:
        out("✓", "Strict A2A `message/send` mounted; gated by Bearer auth")
    else:
        out("⚠", f"Strict A2A `message/send` JSON-RPC invocation: HTTP {status}")
    if file_status == 200:
        out("✓", "FilePart inbound (G46): WORKING")
    elif file_status is None:
        out("⚠", "FilePart inbound: NOT ADVERTISED on card (defaultInputModes=['text'])")
    else:
        out("⚠", f"FilePart inbound: HTTP {file_status}")
    out(
        "ℹ",
        "Bridge mounted via ADK A2aAgentExecutor + a2a-sdk A2AStarletteApplication;",
    )
    out(
        "ℹ",
        "  same Runner / session storage as the AG-UI surface at /api/skill/{id}/stream.",
    )

    # Step 6 — What Gemini Enterprise does with this card
    step("What Gemini Enterprise does with this card")
    out("•", "Validates the card against the A2A v0.2 JSON schema")
    out("•", "Stores it as a tool descriptor in the Agentspace app")
    out("•", "Routes other agents' tool calls to card.url via its internal A2A handler")
    out("•", "Surfaces the skill catalogue in the workspace UI for human discovery")
    return 0


if __name__ == "__main__":
    # Precedence: positional > AP_URL env > DEFAULT_URL
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AP_URL", DEFAULT_URL)
    raise SystemExit(simulate(url))

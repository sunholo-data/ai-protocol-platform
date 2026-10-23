#!/usr/bin/env python3
"""Verify THIS platform's A2A client against the REAL resident agent.

Why this exists alongside 23 unit tests: those stub the peer with
`httpx.MockTransport`, which proves our encoding matches our understanding of
A2A. It cannot prove our understanding matches the agent. Everything that has
actually broken in v6.40.0 broke in that gap — a flag that did not exist, a
stdin that was a pipe, an image that shipped without the package.

Complements `ailang-multivac/scripts/resident-instance.sh verify`, which proves
the AGENT works. This proves WE can talk to it. Deliberately separate: run them
apart and a failure says which half broke; run one and it does not.

    python scripts/verify_resident_live.py \
        --url https://resident-pi-ailang-...run.app \
        --impersonate resident@your-project-id.iam.gserviceaccount.com

The impersonation flag exists because ADC on a developer machine is a USER
credential, and `google.oauth2.id_token.fetch_id_token` cannot mint an ID token
from one — only a service account or the metadata server can. In Cloud Run the
runtime SA is ambient and no flag is needed.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _gcloud_id_token(audience: str, service_account: str) -> str:
    out = subprocess.run(
        [
            "gcloud",
            "auth",
            "print-identity-token",
            f"--impersonate-service-account={service_account}",
            f"--audiences={audience}",
            "--include-email",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="resident agent base URL")
    ap.add_argument("--impersonate", default="", help="service account to mint an ID token as")
    ap.add_argument("--model", default=None)
    ap.add_argument("--timeout", type=float, default=240.0)
    args = ap.parse_args()

    if args.impersonate:
        from tools.mcp import id_token_cache

        token = _gcloud_id_token(args.url, args.impersonate)
        id_token_cache._mint = lambda audience: token
        id_token_cache.clear_cache()
        print(f"  auth: impersonating {args.impersonate}")

    from protocols.a2a_client import A2APeerError, answer_of, converse, fetch_agent_card

    passed = failed = 0

    def ok(msg):
        nonlocal passed
        passed += 1
        print(f"  PASS  {msg}")

    def bad(msg):
        nonlocal failed
        failed += 1
        print(f"  FAIL  {msg}")

    print(f"verifying the platform's A2A client against {args.url}")

    # 1. Discovery through the SDK's resolver, not a hand-built URL. The
    #    resident serves both card spellings; this proves the one the SDK picks.
    try:
        card = await fetch_agent_card(args.url)
        ok(f"agent card resolved: {getattr(card, 'name', '?')}")
    except A2APeerError as exc:
        bad(f"agent card: {exc}")
        print(f"\n{passed} passed, {failed} failed")
        return 1

    # A2A's AgentCard has no `metadata` field and the SDK drops unknown keys,
    # so the registry is read from capabilities.extensions — the spec's own
    # extension point. Reading the wrong place is how this script first
    # reported "model under test: None" and then failed on the send.
    model = args.model
    registered = []
    for ext in getattr(card.capabilities, "extensions", None) or []:
        if "resident-registry" in getattr(ext, "uri", ""):
            registered = (getattr(ext, "params", None) or {}).get("registeredModels") or []
    if registered:
        ok(f"the card advertises its registry via capabilities.extensions: {registered}")
    else:
        bad("the card advertises no model registry — a client cannot discover what it may ask for")
    if not model and len(registered) == 1:
        model = registered[0]

    # Leaving the model unset must ALSO work when the agent registers exactly
    # one: that is the path ask_assistant takes by default.
    model = model or None
    ok(f"model under test: {model or '(unset — relying on the agent to default)'}")

    # 2. A real turn, through converse() — the same code path the ADK tool uses.
    context_id = f"platform-verify-{uuid.uuid4().hex[:12]}"
    secret = str(int(time.time()))[-5:]
    try:
        task = await converse(
            args.url,
            f"Remember this number: {secret}. Reply with just: OK",
            context_id=context_id,
            model=model,
            timeout_s=args.timeout,
        )
        state = (task.get("status") or {}).get("state")
        ok(f"first turn completed (state={state})") if state == "completed" else bad(f"first turn state={state}")
    except A2APeerError as exc:
        bad(f"first turn: {exc}")
        print(f"\n{passed} passed, {failed} failed")
        return 1

    # 3. THE ONE THAT MATTERS. Everything above passes against an agent with no
    #    memory, which is what this was until the pi pin landed.
    try:
        task2 = await converse(
            args.url,
            "What number did I ask you to remember? Reply with only the digits.",
            context_id=context_id,
            model=model,
            timeout_s=args.timeout,
        )
        recall = answer_of(task2)
        if secret in recall:
            ok(f"the platform's own contextId resumed the thread — recalled {secret}")
        else:
            bad(f"no recall of {secret}; the agent said: {recall[:120]!r}")
    except A2APeerError as exc:
        bad(f"second turn: {exc}")

    # 4. A refusal must arrive as a raised error, never as an empty success.
    try:
        await converse(
            args.url,
            "hi",
            context_id=context_id,
            model="openrouter/definitely-not-registered",
            timeout_s=30,
        )
        bad("an unregistered model was ACCEPTED")
    except A2APeerError:
        ok("an unregistered model raises A2APeerError, not a silent empty task")

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

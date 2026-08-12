#!/usr/bin/env python3
"""AG-UI SSE probe (Phase 0 M2 spike).

Sends an AG-UI RunAgentInput to a mounted /api/chat/spike endpoint and
streams back the SSE frames. Decodes each AG-UI event, logs the type +
salient fields, and writes a JSONL trace to last_run.jsonl.

Usage:
    # Local (make dev on port 1956):
    uv run python spikes/agui_harness/probe.py

    # Against deployed Cloud Run dev:
    uv run python spikes/agui_harness/probe.py --env dev

    # Custom URL:
    uv run python spikes/agui_harness/probe.py --url https://foo/api/chat/spike
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
TRACE_PATH = HERE / "last_run.jsonl"

ENVS = {
    "local": "http://localhost:1956/api/chat/spike",
    # Cloud Run multi-container deploy: frontend is the main container on :8080,
    # backend sidecar is exposed via /api proxy from the frontend. The spike
    # endpoint therefore sits behind the frontend origin.
    "dev": "https://frontend-your-project-id.europe-west1.run.app/api/proxy/api/chat/spike",
}


def build_run_input(prompt: str) -> dict:
    """Construct a minimal AG-UI RunAgentInput payload."""
    thread_id = f"spike-{uuid.uuid4().hex[:8]}"
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    return {
        "threadId": thread_id,
        "runId": run_id,
        "state": {},
        "messages": [
            {
                "id": f"msg-{uuid.uuid4().hex[:8]}",
                "role": "user",
                "content": prompt,
            }
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def parse_sse_stream(resp: httpx.Response):
    """Yield decoded JSON event dicts from an SSE response."""
    buf = ""
    for raw in resp.iter_lines():
        if raw == "":
            # Frame boundary
            if not buf:
                continue
            data_lines = [
                line[5:].lstrip() if line.startswith("data:") else None
                for line in buf.splitlines()
            ]
            payload = "\n".join(d for d in data_lines if d is not None)
            buf = ""
            if not payload:
                continue
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                yield {"_raw": payload, "_parse_error": True}
        else:
            buf += raw + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="Full SSE endpoint URL (overrides --env).")
    parser.add_argument("--env", choices=list(ENVS.keys()), default="local")
    parser.add_argument("--prompt", default="What time is it?")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    url = args.url or ENVS[args.env]
    payload = build_run_input(args.prompt)

    print(f"→ POST {url}")
    print(f"→ threadId={payload['threadId']} prompt={args.prompt!r}")

    events: list[dict] = []
    event_types: list[str] = []
    t0 = time.time()

    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=args.timeout) as client:
            with client.stream("POST", url, json=payload, headers=headers) as resp:
                print(f"← HTTP {resp.status_code} ({resp.headers.get('content-type')})")
                if resp.status_code >= 400:
                    body = resp.read().decode(errors="replace")
                    print(f"← body: {body[:2000]}")
                    return 1
                for evt in parse_sse_stream(resp):
                    events.append(evt)
                    etype = evt.get("type", "<unknown>")
                    event_types.append(etype)
                    salient = {
                        k: v
                        for k, v in evt.items()
                        if k in {"toolCallName", "toolName", "messageId", "delta", "content", "error"}
                    }
                    elapsed = time.time() - t0
                    print(f"  [{elapsed:6.2f}s] {etype}  {salient if salient else ''}")
    except httpx.HTTPError as e:
        print(f"× transport error: {type(e).__name__}: {e}")
        return 2

    # Write JSONL trace
    with TRACE_PATH.open("w", encoding="utf-8") as f:
        for evt in events:
            f.write(json.dumps(evt) + "\n")

    print(f"\n— {len(events)} events in {time.time() - t0:.2f}s — written to {TRACE_PATH}")
    print("— sequence: " + " → ".join(event_types))

    observed = sorted(set(event_types))
    print(f"— unique types: {observed}")

    # Simple pass signals for the spike
    has_run_started = "RUN_STARTED" in event_types
    has_run_finished = "RUN_FINISHED" in event_types
    has_tool_call = any(t.startswith("TOOL_CALL_") for t in event_types)
    has_text = any(t.startswith("TEXT_MESSAGE") for t in event_types)

    print("\n— verdicts —")
    print(f"  RUN_STARTED:           {'PASS' if has_run_started else 'FAIL'}")
    print(f"  RUN_FINISHED:          {'PASS' if has_run_finished else 'FAIL'}")
    print(f"  TOOL_CALL_* emitted:   {'PASS' if has_tool_call else 'FAIL'}")
    print(f"  TEXT_MESSAGE_* emitted:{'PASS' if has_text else 'FAIL'}")

    return 0 if (has_run_started and has_run_finished) else 3


if __name__ == "__main__":
    sys.exit(main())

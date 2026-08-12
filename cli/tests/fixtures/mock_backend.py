"""Tiny SSE mock for aiplatform CLI end-to-end self-tests.

Boots a thread-served HTTP server on 127.0.0.1:0 (OS-assigned port), accepts
any auth header, and replies to every POST with a canonical AG-UI event
sequence ending in a ``LATENCY_REPORT`` Custom event. The CLI's
``aiplatform skill probe`` consumes the report and prints a per-stage table.

Why a real socket vs respx
--------------------------
The existing pytest cases use ``respx`` to stub ``httpx.Response``, which
catches code-level bugs but not transport-level ones (SSE buffering, the
``httpx.stream`` lifecycle, real socket timing, stdin/stdout pipes when the
binary runs as a subprocess). This fixture exists to run the *actual
installed binary* against a *real local socket* so transport regressions
surface here, before they reach production traffic.

Two ways to run
---------------
* As a library, from a pytest test::

      from cli.tests.fixtures.mock_backend import MockBackend
      with MockBackend() as mb:
          # mb.url is e.g. "http://127.0.0.1:54321"
          subprocess.run(["aiplatform", "skill", "probe", "..."], env={**, "AIPLATFORM_API_URL": mb.url})

* As a standalone process, from a shell smoke::

      python3 -m cli.tests.fixtures.mock_backend --port 0 --print-port-to-fd 3
      # the script prints the resolved port to stderr (or a fd) and serves
      # forever until killed. The bash runner uses this mode.

Customising the response
------------------------
Pass ``events=...`` to the constructor to override the default canonical
sequence (e.g., for a "no LATENCY_REPORT" smoke that exercises the off-mode
exit path).
"""

from __future__ import annotations

import argparse
import json
import os
import socketserver
import sys
import threading
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler
from typing import Any


def canonical_events() -> list[dict[str, Any]]:
    """The default event sequence — happy path with a LATENCY_REPORT at the end."""
    return [
        {"type": "RUN_STARTED", "threadId": "t-probe", "runId": "r-1"},
        {
            "type": "CUSTOM",
            "name": "STAGE_PROGRESS",
            "value": {"stage": "before_model_done", "label": "Thinking…", "elapsed_ms": 142.0},
        },
        {"type": "TEXT_MESSAGE_START", "messageId": "m1", "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "Hi"},
        {"type": "TEXT_MESSAGE_END", "messageId": "m1"},
        {"type": "RUN_FINISHED", "threadId": "t-probe", "runId": "r-1"},
        {
            "type": "CUSTOM",
            "name": "LATENCY_REPORT",
            "value": {
                "skill_id": "mock-skill",
                "session_id": "t-probe",
                "user_id": "selftest",
                "model_used": "gemini-2.5-flash",
                "routing_choice": "fast",
                "tools_invoked_count": 0,
                "ttft_mode": "full",
                "request_received_ms": 0.0,
                "session_index_done_ms": 8.5,
                "before_agent_done_ms": 142.0,
                "before_model_done_ms": 148.7,
                "first_model_token_ms": 412.3,
                "first_agui_event_ms": 415.0,
                "first_sse_byte_ms": 416.0,
                "total_response_ms": 980.5,
                "event": "ttft",
            },
        },
    ]


def _make_handler(events: list[dict[str, Any]]) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to a given event sequence."""

    class _Handler(BaseHTTPRequestHandler):
        # `events` is captured by closure rather than via class attr so each
        # MockBackend instance can swap the sequence.

        def do_POST(self) -> None:  # noqa: N802 — http.server convention
            length = int(self.headers.get("content-length", "0") or 0)
            # Drain the body so the client doesn't see a half-read connection.
            if length:
                self.rfile.read(length)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.end_headers()
            for event in events:
                payload = json.dumps(event)
                try:
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # CLI client closed early — fine, nothing to do.
                    return

        def do_GET(self) -> None:  # noqa: N802
            # Lets bash health-checks (`curl /health`) succeed against the mock
            # without bypassing the POST flow used for probing.
            if self.path == "/health":
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok","mock":true}')
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args: Any, **_kwargs: Any) -> None:
            # Keep test output clean; the smoke wrapper does its own logging.
            pass

    return _Handler


class _ThreadedTCPServer(socketserver.ThreadingTCPServer):
    """Thread-per-request so the mock can serve concurrent probes during a smoke."""

    allow_reuse_address = True
    daemon_threads = True


class MockBackend:
    """Context-manager wrapper around the SSE mock for use from pytest.

    Usage::

        with MockBackend() as mb:
            os.environ["AIPLATFORM_API_URL"] = mb.url
            subprocess.run(["aiplatform", "skill", "probe", "demo", "--json"], check=True)
    """

    def __init__(self, events: Iterable[dict[str, Any]] | None = None) -> None:
        self._events = list(events) if events is not None else canonical_events()
        self._server: _ThreadedTCPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("MockBackend not started — use `with MockBackend()` or call .start()")
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        if self._server is not None:
            return
        handler = _make_handler(self._events)
        self._server = _ThreadedTCPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="aiplatform-mock-backend",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._server = None
        self._thread = None

    def __enter__(self) -> MockBackend:
        self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.stop()


# --- Standalone-process mode (used by the bash smoke) ---


def _serve_forever_blocking(port: int, events: list[dict[str, Any]]) -> None:
    """Run the mock in the foreground until the process is killed."""
    handler = _make_handler(events)
    with _ThreadedTCPServer(("127.0.0.1", port), handler) as server:
        resolved = server.server_address[1]
        # Print to stderr so a bash caller can capture the port even when the
        # CLI itself writes to stdout.
        print(f"PORT={resolved}", file=sys.stderr, flush=True)
        # Optional: also write to a file-descriptor (passed by the bash caller)
        # so a child process doesn't have to parse stderr.
        port_fd = os.environ.get("AIPLATFORM_MOCK_PORT_FD")
        if port_fd:
            try:
                fd = int(port_fd)
                os.write(fd, f"{resolved}\n".encode())
            except (OSError, ValueError):
                pass
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="aiplatform CLI mock backend (SSE).")
    parser.add_argument("--port", type=int, default=0, help="Port to bind (0 = OS-assigned).")
    parser.add_argument(
        "--no-latency-report",
        action="store_true",
        help="Drop the trailing LATENCY_REPORT event (simulates AITANA_TTFT_MODE=off).",
    )
    args = parser.parse_args()

    events = canonical_events()
    if args.no_latency_report:
        events = [e for e in events if e.get("name") != "LATENCY_REPORT"]

    _serve_forever_blocking(args.port, events)


if __name__ == "__main__":
    main()

"""CLI demo channel — the worked example for the channel-adapter howto.

`backend/channels/_demo_cli.py` exists for two reasons:

1.  It is the **worked example** in `docs/integrations/channels-adapter-howto.md`.
    The howto walks through the three abstract methods + two optional
    overrides by pointing at the implementation here. Doc + code co-evolve.
2.  It is a **debugging fixture** — `python -m channels._demo_cli` lets you
    chat with a skill from a terminal without provisioning Discord /
    Mailgun / Twilio creds. Useful when triaging a skill-side bug.

The file is underscore-prefixed (`_demo_cli.py`) so it is clearly
**internal / demo only**. It is intentionally NOT registered in
`fast_api_app.py` and must never be registered in production — there is
no webhook signature here, so `verify_webhook` returns True
unconditionally (the "user" running the process is trusted).

Keep it under ~100 LOC. If something needs more code than that to
express in the demo, the framework probably has friction worth fixing
in `base.py` rather than papering over in the demo.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from channels._agui_render import AGUIChannelRenderer, ChannelSink
from channels.base import BaseChannel, InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)


class CliDemoChannel(BaseChannel):
    """Stdin/stdout chat channel.

    Subclasses `BaseChannel` and implements only the three required
    abstract methods. The framework provides identity resolution,
    command parsing, attachment handling, skill dispatch, and audit-log
    metadata. The class is ~40 LOC because everything else is inherited.
    """

    name: ClassVar[str] = "cli"
    command_prefix: ClassVar[str] = "/"
    # Streaming on: this is the credential-free way to exercise the full
    # AG-UI render path (`channels._agui_render`) end to end — no Discord
    # bot, no Twilio, no Mailgun. If streaming breaks, it breaks here first.
    supports_streaming: ClassVar[bool] = True

    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """CLI has no transport-layer auth — the process owner is trusted.

        In a real adapter this would check an HMAC / Ed25519 / shared
        secret against the channel provider's signing scheme.
        """
        return True

    async def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Build an `InboundMessage` from `{user_id, text}` dict.

        Returning None signals a non-actionable event (the framework
        responds with `{"ok": true, "skipped": true}`). The CLI only
        sees actionable input, so the only None case is empty text.
        """
        text = (payload.get("text") or "").strip()
        if not text:
            return None
        return InboundMessage(
            channel_user_id=str(payload.get("user_id") or "cli-user"),
            channel_chat_id="stdout",
            text=text,
            metadata={"transport": "cli"},
        )

    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        """Write the reply to stdout, prefixed for readability.

        A real adapter calls the channel API (`requests.post(...)` or
        `client.send(...)`). The framework's chunking concern doesn't
        apply here: stdout has no length limit.
        """
        sys.stdout.write(f"\n[skill] {message.text}\n\n")
        sys.stdout.flush()

    async def send_streaming(
        self,
        chat_id: str,
        event_stream: AsyncIterator[dict[str, Any]],
    ) -> None:
        """Render the AG-UI stream to a terminal via the shared renderer.

        The worked example of an adapter that streams: it supplies only
        native primitives (`_TerminalSink`) and inherits every event
        semantic from `channels._agui_render`.
        """
        await AGUIChannelRenderer(_TerminalSink(), edit_interval_sec=0.0).run(event_stream)


class _TerminalSink(ChannelSink):
    """Terminal primitives — redraw a line rather than edit a message.

    Shows the other half of the `show_progress` contract: it returns None
    because a terminal HAS an out-of-band affordance (a status line on
    stderr), so progress must NOT be spliced into the reply text.
    """

    def __init__(self) -> None:
        self._last_len = 0

    async def begin(self) -> None:
        # Open the line only. The prefix is written by `update_text`, which
        # redraws from column 0 — writing it here too double-prints when
        # stdout is piped (no terminal to honour the carriage return).
        sys.stdout.write("\n")
        sys.stdout.flush()

    async def update_text(self, text: str) -> None:
        # Redraw in place: carriage return, overwrite, pad out the tail of
        # any previously longer line.
        pad = max(0, self._last_len - len(text))
        sys.stdout.write("\r[skill] " + text + " " * pad)
        sys.stdout.flush()
        self._last_len = len(text)

    async def show_progress(self, label: str) -> str | None:
        sys.stderr.write(f"\n  ... {label}\n")
        sys.stderr.flush()
        return None

    async def finish(self, text: str) -> None:
        await self.update_text(text)
        sys.stdout.write("\n\n")
        sys.stdout.flush()


# --- entry point -----------------------------------------------------------


async def run_cli() -> None:
    """Interactive loop: prompt for a user_id once, then chat turn-by-turn.

    Each turn re-enters the framework via `handle_webhook` with a fake
    body / headers tuple — exactly the path a real webhook delivery
    takes, so this exercise the full
        verify_webhook → parse_inbound → IdentityResolver → CommandParser →
        AttachmentPipeline → invoke_skill → send
    flow against the live skill processor.
    """
    channel = CliDemoChannel()
    sys.stdout.write("Aitana CLI demo channel. Type `exit` to quit.\n")
    sys.stdout.write("user_id (defaults to 'cli-user'): ")
    sys.stdout.flush()
    user_id = sys.stdin.readline().strip() or "cli-user"

    while True:
        sys.stdout.write("> ")
        sys.stdout.flush()
        line = sys.stdin.readline()
        if not line:
            break
        line = line.rstrip("\n")
        if line.strip().lower() in {"exit", "quit"}:
            break
        # Build a webhook-shaped payload so `handle_webhook` exercises
        # the same path Discord / Email / Telegram take. Headers + body
        # are unused (verify_webhook always returns True) but kept for
        # API parity with real adapters.
        await channel.handle_webhook(
            payload={"user_id": user_id, "text": line},
            headers={},
            body=b"",
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(run_cli())
    except KeyboardInterrupt:
        sys.stdout.write("\n")


__all__ = ["CliDemoChannel", "run_cli"]

"""Message chunking for channels with hard length limits.

Discord caps messages at 2000 characters; the same helper is reusable
for any channel with a similar guard (WhatsApp ≤4096, SMS ≤160 with
segmenting overhead, etc.). The contract:

    - Returns a list of strings, each <= max_length.
    - Empty / whitespace-only input returns [].
    - Word boundaries are preferred — never cut a word mid-token if a
      whitespace boundary exists within max_length of the current cursor.
    - A single token longer than max_length is hard-split (no boundary
      to honour). The user sees the word split across N messages, which
      is correct for code dumps / hashes / URLs that exceed the limit.

Stateless module-level function — channels never instantiate a chunker;
they call `chunk_message(text, max_length=2000)` from inside `send()`.
"""

from __future__ import annotations

import re

# Encoded destinations, including the <URL> form that keeps Markdown syntax
# out of the destination. Oversized links still require a channel-side limit.
_MARKDOWN_LINK = re.compile(r"\[[^\]\n]+\]\((?:<https?://[^<>\s]+>|https?://[^\s()]+)\)")


def chunk_message(text: str, *, max_length: int, preserve_links: bool = False) -> list[str]:
    """Split `text` into chunks of at most `max_length` characters.

    With `preserve_links`, keep HTTP(S) Markdown links that fit in one
    message together, including links whose labels contain spaces.
    See module docstring for the contract.
    """
    if max_length <= 0:
        raise ValueError(f"max_length must be positive, got {max_length}")

    if not text or not text.strip():
        return []

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_length:
        # Find the rightmost whitespace within [0, max_length]. If none,
        # hard-split at max_length (oversized single token).
        boundary = remaining.rfind(" ", 0, max_length + 1)
        if boundary <= 0:
            # No whitespace found, or only at index 0 — hard split.
            boundary = max_length
        if preserve_links:
            for link in _MARKDOWN_LINK.finditer(remaining):
                if link.start() < boundary < link.end() and len(link.group()) <= max_length:
                    # Move a whole link to the next message, or finish it here
                    # when it starts this message. Never split its label/URL.
                    boundary = link.start() or link.end()
                    break
        chunks.append(remaining[:boundary])
        remaining = remaining[boundary:]
        # Strip leading whitespace on the continuation so the user doesn't
        # see " continued" at the start of the next message.
        remaining = remaining.lstrip(" ")

    if remaining:
        chunks.append(remaining)
    return chunks


__all__ = ["chunk_message"]

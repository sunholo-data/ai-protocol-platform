"""Anchored comments — v6.34.0 DOC-WORKBENCH P4.

The point, in the customer's own words at the 2026-08-06 UAT:

    "The comment attribute in Word is a piece of text which points towards a
    selection of text. Not only the comment is important but also that it is
    able to read which piece of text is attached. If a comment says 'this is
    nonsense', it's important which part of the text is nonsense."

That is the whole feature. A comment without its anchor is noise, and worse than
noise here: rendered as a bare paragraph in the document body (which is what
happened before this module — `blocks_to_markdown`'s fallback branch printed the
comment text and dropped the author and the anchor), the model reads "Do
something." as if the document said it.

Verified live against `sample_docx_comments` on 2026-09-01 — anchoring HAS
shipped upstream, so this needs nothing new from `ailang-parse`:

    {'type': 'comment', 'text': 'Do something.', 'author': 'Jesse Rosenthal',
     'anchor_text': 'comment in a comment', 'anchor_kind': 'range',
     'anchored': True, 'anchor_block_index': 3, 'parent_id': '3'}

Two shapes worth knowing:

* Comments arrive **nested** as `children` of a `section` block whose `kind` is
  `"comment"`. A flat scan of the top-level blocks finds none — that is exactly
  how the first check of this feature concluded, wrongly, that comments did not
  work at all.
* Replies thread via `parent_id`, pointing at another comment's `id`.

**Anchor by TEXT, not by index.** `anchor_block_index` is a position, and the
document is regenerated on every revision, so the index moves. The quoted
`anchor_text` survives. Store both; match on the text; treat the index as a hint.
"""

from __future__ import annotations

from typing import Any

# Anything longer is quoted truncated — an anchor is a handle, not the content.
_MAX_ANCHOR_CHARS = 300


def extract_comments(blocks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Every comment in a parsed document, in document order.

    Walks nested `children`, so comments carried under a `kind: "comment"`
    section are found. Each entry keeps what the agent needs to act:
    `text` (what the reviewer said), `anchor_text` (what they said it ABOUT),
    `author`, `anchored`, and `parent_id` for replies.
    """
    found: list[dict[str, Any]] = []

    def walk(items: list[Any]) -> None:
        for block in items or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "comment":
                anchor = str(block.get("anchor_text") or "").strip()
                found.append(
                    {
                        "id": str(block.get("id") or ""),
                        "text": str(block.get("text") or "").strip(),
                        "author": str(block.get("author") or "").strip(),
                        "date": str(block.get("date") or ""),
                        "anchorText": anchor,
                        # `anchored` may be absent on older payloads; the anchor
                        # text itself is the real signal.
                        "anchored": bool(block.get("anchored")) or bool(anchor),
                        "anchorBlockIndex": block.get("anchor_block_index"),
                        "parentId": str(block.get("parent_id") or ""),
                        "resolved": bool(block.get("resolved")),
                    }
                )
            children = block.get("children")
            if isinstance(children, list):
                walk(children)

    walk(blocks or [])
    return found


def _truncate(text: str, limit: int = _MAX_ANCHOR_CHARS) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def comments_to_markdown(comments: list[dict[str, Any]]) -> str:
    """Render comments for the agent, each tied to the passage it points at.

    Returns "" when there are none, so the caller adds no empty section.

    Unresolved comments come first — those are the work. An UNANCHORED comment
    is still shown, labelled as such: dropping it would hide feedback, and
    presenting it as if it pointed somewhere would be a lie about which passage
    the reviewer meant.
    """
    if not comments:
        return ""

    by_id = {c["id"]: c for c in comments if c.get("id")}
    lines: list[str] = ["## Reviewer comments", ""]
    lines.append(
        "Each comment names the exact passage it refers to. When acting on one, "
        "change ONLY that passage and leave the rest of the document alone."
    )
    lines.append("")

    ordered = [c for c in comments if not c.get("resolved")] + [c for c in comments if c.get("resolved")]

    for c in ordered:
        who = c.get("author") or "Unknown"
        head = f"- **{who}**"
        if c.get("resolved"):
            head += " _(resolved)_"

        parent = by_id.get(c.get("parentId") or "")
        if parent is not None:
            parent_who = parent.get("author") or "Unknown"
            head += f" _(reply to {parent_who})_"

        lines.append(head + f": {c.get('text') or '(empty comment)'}")

        if c.get("anchorText"):
            lines.append(f'  - refers to: "{_truncate(c["anchorText"])}"')
        else:
            lines.append(
                "  - ⚠️ this comment has no anchor — it is not attached to a specific "
                "passage, so ask which part it refers to before changing anything."
            )

    return "\n".join(lines)


def find_anchor(blocks: list[dict[str, Any]] | None, anchor_text: str) -> int | None:
    """Index of the first top-level block containing `anchor_text`, or None.

    Text-based on purpose: a block index recorded at parse time is stale the
    moment the document is regenerated, which is precisely what happens between
    a comment being written and the revision that answers it.
    """
    needle = (anchor_text or "").strip().casefold()
    if not needle:
        return None
    for i, block in enumerate(blocks or []):
        if not isinstance(block, dict):
            continue
        if needle in str(block.get("text") or "").casefold():
            return i
    return None

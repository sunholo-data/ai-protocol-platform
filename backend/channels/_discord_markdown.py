"""Adapt model Markdown to what Discord actually renders.

Discord renders bold, italics, headings, lists, links and code blocks — but
NOT GitHub-style tables, which models love: a reply with a table arrives as
raw ``| a | b |`` rows. This pass rewrites each table into something Discord
shows well:

  * narrow tables -> a fenced code block with aligned columns (numbers
    right-aligned); emphasis markers are dropped, since code blocks show them
    literally;
  * wide tables (they wrap badly on a phone) -> one bullet per row:
    ``- **first cell** — Header: value · Header: value``.

It also collapses runs of blank lines, and never touches text inside an
existing code fence. Pure and idempotent, so it is safe on every streamed
edit as well as the final message.
"""

from __future__ import annotations

import re

MAX_CODE_TABLE_WIDTH = 60  # wider than this reads badly as a block on mobile

_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_NUMERIC = re.compile(r"^[-+]?[\d\s.,]+(?:\s*(?:%|h|€|\$|£|EUR|USD|kr))?$", re.IGNORECASE)
_EMPHASIS = re.compile(r"(\*\*|__|`)")


def _cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _is_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _is_separator(line: str) -> bool:
    cells = _cells(line)
    return bool(cells) and all(_SEPARATOR_CELL.match(c.replace(" ", "")) for c in cells)


def _plain(cell: str) -> str:
    return _EMPHASIS.sub("", cell).strip()


def _as_code_block(header: list[str], rows: list[list[str]]) -> str:
    table = [[_plain(c) for c in header], *[[_plain(c) for c in r] for r in rows]]
    ncols = max(len(r) for r in table)
    table = [r + [""] * (ncols - len(r)) for r in table]
    widths = [max(len(r[i]) for r in table) for i in range(ncols)]
    numeric = [
        all(not r[i] or _NUMERIC.match(r[i]) for r in table[1:]) and any(r[i] for r in table[1:]) for i in range(ncols)
    ]

    def fmt(row: list[str]) -> str:
        return "  ".join(
            c.rjust(w) if num else c.ljust(w) for c, w, num in zip(row, widths, numeric, strict=True)
        ).rstrip()

    lines = [fmt(table[0]), "  ".join("-" * w for w in widths), *(fmt(r) for r in table[1:])]
    return "```\n" + "\n".join(lines) + "\n```"


def _as_bullets(header: list[str], rows: list[list[str]]) -> str:
    out = []
    for row in rows:
        first = _plain(row[0]) if row else ""
        rest = []
        for i, cell in enumerate(row[1:], start=1):
            if not cell:
                continue
            label = _plain(header[i]) if i < len(header) else ""
            rest.append(f"{label}: {cell}" if label else cell)
        line = f"- **{first}**" if first else "-"
        if rest:
            line += (" — " if first else " ") + " · ".join(rest)
        out.append(line)
    return "\n".join(out)


def _render_table(lines: list[str]) -> str:
    header = _cells(lines[0])
    rows = [_cells(line) for line in lines[2:]]
    as_block = _as_code_block(header, rows)
    width = max(len(line) for line in as_block.splitlines()[1:-1])
    return as_block if width <= MAX_CODE_TABLE_WIDTH else _as_bullets(header, rows)


def to_discord_markdown(text: str) -> str:
    """Rewrite Markdown tables and blank-line runs for Discord. Code fences are kept verbatim."""
    if not text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    in_fence = False
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if not in_fence and _is_row(line) and i + 1 < len(lines) and _is_separator(lines[i + 1]):
            j = i + 2
            while j < len(lines) and _is_row(lines[j]):
                j += 1
            out.append(_render_table(lines[i:j]))
            i = j
            continue
        out.append(line)
        i += 1
    result = "\n".join(out)
    if "```" not in result:
        return re.sub(r"\n{3,}", "\n\n", result)
    # Collapse blank runs outside fences only.
    parts = result.split("```")
    for k in range(0, len(parts), 2):
        parts[k] = re.sub(r"\n{3,}", "\n\n", parts[k])
    return "```".join(parts)

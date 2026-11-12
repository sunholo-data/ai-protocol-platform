"""A parsed document's structure: its outline, and the blocks under a section.

AILANG Parse returns a block tree, not a page of text: headings with levels,
named containers (a spreadsheet's sheets, a deck's slides, an e-book's
chapters, an email's attachments) and ``page-break`` markers carrying the
printed page number (an EDGAR filing: 421 of them in a 421-page S-1). Rendering
all of that to one markdown string before anything reads it threw the structure
away, so the only way into a long document was the whole document.

This module turns the stored blocks into a section tree so the agent can read a
long document the way a person does: look at the outline (a few thousand tokens
for a filing whose full text is ~300k), then open only the sections it needs.
Section ids are the outline's numbering (``"3"``, ``"3.2"``, ``"3.2.1"``),
deterministic for a given parse; the user's edits change text, never structure.

No model call and no index: the input is already-authorised parsed blocks, and
every function here is pure.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from tools.documents.context import blocks_to_markdown

# Named containers that are part of a document's structure. Other section kinds
# (header, footer, textbox, notes, bookmark, column-break) are layout, not
# chapters: their content belongs to whatever section surrounds them.
CONTAINER_KINDS = frozenset({"sheet", "slide", "chapter", "attachment"})
PAGE_BREAK = "page-break"

# An outline is for choosing what to read; past this many lines it stops being
# cheaper than reading. The renderer lowers the depth until the outline fits.
MAX_OUTLINE_LINES = 400
DEFAULT_OUTLINE_DEPTH = 3
CHARS_PER_TOKEN = 4

_SECTION_ID = re.compile(r"^\d+(\.\d+)*$")


@dataclass
class Leaf:
    """One content block in reading order, with where it sits."""

    block: dict[str, Any]
    block_id: str
    page: Any  # printed page label from a marker, or the block's own page
    text: str  # the block as the agent reads it (blocks_to_markdown)
    node: Node | None = None  # innermost section containing it

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass(eq=False)
class Node:
    """One section: a heading and what follows it, or a named container."""

    title: str
    kind: str  # "heading", or a container kind ("sheet", "slide", …)
    level: int  # heading level; 0 for a container (always above its headings)
    start: int  # first leaf (the heading itself, for a heading section)
    end: int = -1  # one past the last leaf
    parent: Node | None = None
    children: list[Node] = field(default_factory=list)
    id: str = ""
    page_start: Any = None
    page_end: Any = None
    chars: int = 0

    @property
    def depth(self) -> int:
        return 1 if self.parent is None else self.parent.depth + 1

    @property
    def path(self) -> list[str]:
        return ([] if self.parent is None else self.parent.path) + [self.title]

    def subtree(self) -> Iterator[Node]:
        yield self
        for child in self.children:
            yield from child.subtree()


@dataclass
class Structure:
    leaves: list[Leaf]
    roots: list[Node]
    _word_index: list[tuple[str, Leaf]] | None = field(default=None, repr=False)

    @property
    def nodes(self) -> list[Node]:
        return [n for root in self.roots for n in root.subtree()]

    @property
    def has_pages(self) -> bool:
        return any(leaf.page is not None for leaf in self.leaves)

    @property
    def front_matter_chars(self) -> int:
        """Text before the first section (a cover page, a contents list)."""
        first = self.roots[0].start if self.roots else len(self.leaves)
        return sum(leaf.chars for leaf in self.leaves[:first])

    def find(self, section_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == section_id), None)

    def word_index(self) -> list[tuple[str, Leaf]]:
        """Each block's words as `` w1 w2 … `` for locate_passage — built once
        per structure, since one search locates several hits in one document."""
        if self._word_index is None:
            self._word_index = [(f" {' '.join(_words(leaf.text))} ", leaf) for leaf in self.leaves if leaf.text]
        return self._word_index


# --- Building -----------------------------------------------------------------


def _is_comment(block: dict[str, Any]) -> bool:
    return block.get("type") == "comment" or block.get("kind") == "comment"


def _block_page(block: dict[str, Any]) -> Any:
    """A page carried on the block itself (a PDF backend's per-block page)."""
    return block.get("page") or block.get("page_number") or block.get("pageNumber") or None


def _walk(blocks: list[dict[str, Any]]) -> Iterator[tuple[str, Any]]:
    """Reading-order events: ("leaf", (block, id, page)), ("page", label),
    ("open", (kind, name)), ("close", None).

    Block ids follow the scheme search results have always cited: the block's
    own ``block_id``/``id``, else its index, prefixed by its parents'. A page on
    a section applies to everything inside it.
    """

    def walk(items: list[Any], prefix: str, parent_page: Any = None) -> Iterator[tuple[str, Any]]:
        for index, block in enumerate(items):
            if not isinstance(block, dict) or _is_comment(block):
                continue
            block_id = str(block.get("block_id") or block.get("id") or f"{prefix}{index}")
            page = _block_page(block) or parent_page
            if block.get("type") != "section":
                yield "leaf", (block, block_id, page)
                continue
            kind = str(block.get("kind") or "")
            name = str(block.get("name") or "").strip()
            if kind == PAGE_BREAK:
                # A DOCX hard break has no name, and counting breaks is not
                # counting pages (soft breaks are invisible), so only a printed
                # page number is ever reported as a page.
                if name:
                    yield "page", name
                continue
            children = block.get("children") or []
            if kind in CONTAINER_KINDS and name:
                yield "open", (kind, name)
                yield from walk(children, f"{block_id}.", page)
                yield "close", None
            else:
                yield from walk(children, f"{block_id}.", page)

    yield from walk(blocks, "")


def build_structure(blocks: list[dict[str, Any]]) -> Structure:
    """The section tree of ``blocks`` (as stored, edits already applied)."""
    leaves: list[Leaf] = []
    roots: list[Node] = []
    stack: list[Node] = []
    page: str | None = None
    # A sheet too big for one stored chunk is split into consecutive sections
    # with the same name (block_store._split_oversized). Reading them back as two
    # sheets would put "Q1" in the outline twice; reopen the one just closed.
    just_closed: Node | None = None

    def close(node: Node) -> None:
        node.end = len(leaves)

    def attach(node: Node) -> None:
        node.parent = stack[-1] if stack else None
        (node.parent.children if node.parent else roots).append(node)
        stack.append(node)

    for event, value in _walk(blocks):
        if event == "page":
            page = value
            continue
        if event == "close":
            # Close everything opened inside the container, then the container.
            while stack:
                node = stack.pop()
                close(node)
                if node.kind != "heading":
                    just_closed = node
                    break
            continue
        if event == "open":
            kind, name = value
            reopen = just_closed if just_closed and (just_closed.kind, just_closed.title) == (kind, name) else None
            just_closed = None
            # A container ends any heading section still open at its own level.
            while stack and stack[-1].kind == "heading":
                close(stack.pop())
            if reopen is not None and reopen.end == len(leaves):
                stack.append(reopen)
            else:
                attach(Node(title=name, kind=kind, level=0, start=len(leaves)))
            continue

        block, block_id, own_page = value
        just_closed = None
        leaf = Leaf(block=block, block_id=block_id, page=own_page or page, text=blocks_to_markdown([block]))
        if block.get("type") == "heading" and (block.get("text") or "").strip():
            level = max(1, int(block.get("level") or 1))
            # A heading closes open headings at its level or deeper, never the
            # container it sits in.
            while stack and stack[-1].kind == "heading" and stack[-1].level >= level:
                close(stack.pop())
            title = " ".join(str(block["text"]).split())
            attach(Node(title=title, kind="heading", level=level, start=len(leaves)))
        leaves.append(leaf)

    while stack:
        close(stack.pop())

    structure = Structure(leaves=leaves, roots=roots)
    _finish(structure)
    return structure


def _finish(structure: Structure) -> None:
    """Number the sections and give each its page range and size."""

    def number(nodes: list[Node], prefix: str) -> None:
        for i, node in enumerate(nodes, 1):
            node.id = f"{prefix}{i}"
            number(node.children, f"{node.id}.")

    number(structure.roots, "")
    for node in structure.nodes:
        span = structure.leaves[node.start : node.end]
        node.chars = sum(leaf.chars for leaf in span)
        pages = [leaf.page for leaf in span if leaf.page is not None]
        node.page_start, node.page_end = (pages[0], pages[-1]) if pages else (None, None)
        for leaf in span:
            leaf.node = node  # deeper sections come later and overwrite: innermost wins


# --- Rendering ----------------------------------------------------------------


def token_estimate(chars: int) -> str:
    tokens = max(1, round(chars / CHARS_PER_TOKEN))
    return f"~{tokens / 1000:.1f}k tokens" if tokens >= 1000 else f"~{tokens} tokens"


def clip(title: str, limit: int) -> str:
    """A heading for display. Risk factors and clause headings are often whole
    sentences; the id, not the full title, is what the agent asks for."""
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "\u2026"


def heading_path(node: Node, limit: int = 80) -> str:
    return " \u203a ".join(clip(t, limit) for t in node.path)


def page_range(node: Node) -> str:
    if node.page_start is None:
        return ""
    if node.page_start == node.page_end:
        return f"p. {node.page_start}"
    return f"pp. {node.page_start}\u2013{node.page_end}"


def describe(node: Node) -> str:
    """``3.2 Risk Factors · pp. 21\u201358 · ~31.0k tokens`` (sheet/slide kind named)."""
    kind = f" ({node.kind})" if node.kind != "heading" else ""
    bits = [f"{node.id} {clip(node.title, 140)}{kind}", page_range(node), token_estimate(node.chars)]
    return " · ".join(b for b in bits if b)


def render_outline(
    structure: Structure, *, within: Node | None = None, max_depth: int = DEFAULT_OUTLINE_DEPTH
) -> tuple[list[str], int]:
    """Outline lines (indented markdown bullets) and the depth actually shown.

    Lowers the depth until the outline fits ``MAX_OUTLINE_LINES``; a section
    whose children are hidden says how many it has, so the agent knows to ask
    for that section's own outline.
    """
    tops = within.children if within is not None else structure.roots
    base = within.depth if within is not None else 0

    def lines_at(depth: int) -> list[str]:
        out: list[str] = []

        def emit(node: Node) -> None:
            rel = node.depth - base
            hidden = sum(1 for _ in node.subtree()) - 1 if rel == depth else 0
            more = f" · +{hidden} subsection{'s' if hidden != 1 else ''}" if hidden else ""
            out.append(f"{'  ' * (rel - 1)}- {describe(node)}{more}")
            if rel < depth:
                for child in node.children:
                    emit(child)

        for top in tops:
            emit(top)
        return out

    depth = max(1, max_depth)
    lines = lines_at(depth)
    while len(lines) > MAX_OUTLINE_LINES and depth > 1:
        depth -= 1
        lines = lines_at(depth)
    if len(lines) > MAX_OUTLINE_LINES:
        hidden = len(lines) - MAX_OUTLINE_LINES
        lines = [
            *lines[:MAX_OUTLINE_LINES],
            f"- \u2026 {hidden} more lines not shown; open a section by id, or its outline, to see the rest",
        ]
    return lines, depth


# --- Selecting sections ---------------------------------------------------------


@dataclass
class Selection:
    nodes: list[Node]
    # Set instead of nodes when the request does not name exactly one thing.
    problem: str = ""


def _norm(text: str) -> str:
    return " ".join(text.casefold().split()).strip(" .:;")


def select_sections(structure: Structure, section: str) -> Selection:
    """Resolve ``section`` to sections: outline ids (``"3.2"``, or ``"3.2, 5"``)
    or a heading title (exact match first, then a unique substring).

    Never guesses. Several headings matching a title is answered with the
    candidates and their ids — returning the first one is how "Overview" opened
    the wrong chapter's overview.
    """
    parts = [p.strip() for p in section.split(",")]
    if all(_SECTION_ID.match(p) for p in parts):
        found = [structure.find(p) for p in parts]
        missing = [p for p, n in zip(parts, found, strict=True) if n is None]
        if missing:
            return Selection([], f"There is no section {', '.join(missing)} in this document's outline.")
        return Selection([n for n in found if n is not None])

    wanted = _norm(section)
    nodes = structure.nodes
    matches = [n for n in nodes if _norm(n.title) == wanted] or [n for n in nodes if wanted in _norm(n.title)]
    if len(matches) == 1:
        return Selection(matches)
    if not matches:
        return Selection([], f"No section heading matches '{section}'.")
    listing = "\n".join(
        f"- {describe(n)} \u2014 in: {heading_path(n.parent) if n.parent else '(top level)'}" for n in matches[:25]
    )
    extra = f"\n- … and {len(matches) - 25} more" if len(matches) > 25 else ""
    return Selection(
        [],
        f"{len(matches)} sections match '{section}'. Ask again with the id of the one you need "
        f"(several ids can be comma-separated):\n{listing}{extra}",
    )


def section_blocks(structure: Structure, nodes: list[Node]) -> list[dict[str, Any]]:
    """The blocks of ``nodes`` in reading order, each block once (a section and
    one of its own subsections overlap)."""
    indices = sorted({i for node in nodes for i in range(node.start, node.end)})
    return [structure.leaves[i].block for i in indices]


# --- Locating a retrieved passage ----------------------------------------------

_WORD = re.compile(r"\w+")
PROBE_WORDS = 8
MIN_WHOLE_WORDS = 4
MAX_PROBES = 60


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def locate_passage(structure: Structure, passage: str) -> Leaf | None:
    """The block a retrieved passage (a semantic-search chunk) came from.

    A chunk's text is the document's own text, re-flowed: the index may prefix
    its ancestor headings, render a table differently, or cut mid-sentence. So
    match on words, not characters, two ways:

    1. runs of ``PROBE_WORDS`` words from the chunk (middle first, clear of any
       heading prefix; then the end, the start, and a stride across), each found
       in the blocks of exactly one section, VOTE: the section needs two votes
       (one for a chunk too short for two) and more than any other. A single
       run can straddle two paragraphs and happen to match a cross-reference
       elsewhere ("See the section titled …"); agreement cannot;
    2. failing that, a body block of at least ``MIN_WHOLE_WORDS`` words that sits
       whole inside the chunk (a short paragraph cannot contain a whole probe).

    Words found in several sections (repeated boilerplate) prove nothing and are
    skipped. No unique section is None — never a guess.
    """
    words = _words(passage)
    if not words:
        return None
    chunk = f" {' '.join(words)} "
    blocks = structure.word_index()
    n = min(PROBE_WORDS, len(words))
    last = len(words) - n
    starts = list(dict.fromkeys([last // 2, last, 0, *range(0, last + 1, n)]))[:MAX_PROBES]
    votes: dict[int, list[Leaf]] = {}
    for start in starts:
        probe = f" {' '.join(words[start : start + n])} "
        found = _one_section([leaf for text, leaf in blocks if probe in text])
        if found is not None:
            votes.setdefault(id(found.node), []).append(found)
            if len(votes) == 1 and len(found_votes := next(iter(votes.values()))) >= 3:
                return found_votes[0]  # three agree, none dissent: settled
    ranked = sorted(votes.values(), key=len, reverse=True)
    if ranked and len(ranked[0]) >= min(2, len(starts)) and (len(ranked) == 1 or len(ranked[0]) > len(ranked[1])):
        return ranked[0][0]  # the earliest probe's block: the middle of the chunk
    contained = [
        leaf
        for text, leaf in blocks
        if leaf.block.get("type") != "heading" and text.count(" ") > MIN_WHOLE_WORDS and text in chunk
    ]
    # In the order they appear in the chunk; the first whose words are unique wins.
    contained.sort(key=lambda leaf: chunk.find(f" {' '.join(_words(leaf.text))} "))
    for leaf in contained:
        text = f" {' '.join(_words(leaf.text))} "
        if _one_section([other for other_text, other in blocks if other_text == text]) is not None:
            return leaf
    return None


def _one_section(leaves: list[Leaf]) -> Leaf | None:
    """The match when every leaf is in the same section (a heading and its own
    paragraph often share words), preferring body text over the heading."""
    if not leaves or len({id(leaf.node) for leaf in leaves}) != 1:
        return None
    return next((leaf for leaf in leaves if leaf.block.get("type") != "heading"), leaves[0])

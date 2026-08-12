"""Workshop docs search — RAG-lite over the platform's docs/ tree.

Powers the `workshop-helper` skill. Walks
`docs/workshop/`, `docs/integrations/`, `docs/design/v6.X.Y/implemented/`,
and `docs/talks/ai-ui-protocol-stack.md` at first call, builds an
in-memory keyword index, and answers queries with the top-K matching
documents + a snippet around the first hit.

Deliberately small + dependency-free:
- No embeddings, no vector store. Plain case-insensitive substring +
  keyword-overlap scoring. The corpus is ~60 docs total, queries are
  cheap.
- Lazy-load on first call so module import + backend startup stay fast.
- The agent's job is to interpret the snippets, not the tool's job to
  generate them. The tool just surfaces the candidates.

The path-rewriter in `scripts/refresh-workshop-materials.sh` rewrites
intra-platform paths to GitHub URLs when publishing — this tool
returns the original on-disk paths (the agent runs in this repo).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Walk from `backend/tools/workshop_docs.py` → repo root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCS_DIR = _REPO_ROOT / "docs"

# Subdirectories + individual files to ingest. Anything else under docs/
# is platform-internal (deploy ops, contracts, v5 migration history) and
# not part of the helper's knowledge base.
_INCLUDE_DIRS = [
    "workshop",
    "integrations",
    "design/v6.0.0/implemented",
    "design/v6.1.0/implemented",
    "design/v6.2.0/implemented",
]
_INCLUDE_FILES = [
    "talks/ai-ui-protocol-stack.md",
]

# Cap individual doc size we read into the index — protects against
# pathological files. The talk doc is the only one near this size today.
_MAX_DOC_BYTES = 200_000

# Snippet width returned for the top-hit context window. The agent
# decides what to do with it.
_SNIPPET_RADIUS = 240


_INDEX: list[dict] | None = None


def _load_index() -> list[dict]:
    """Walk the doc corpus and build the in-memory index. Idempotent."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX

    docs: list[dict] = []
    seen: set[Path] = set()

    for subdir in _INCLUDE_DIRS:
        root = _DOCS_DIR / subdir
        if not root.exists():
            continue
        for md_path in sorted(root.glob("*.md")):
            if md_path in seen:
                continue
            seen.add(md_path)
            docs.append(_read_doc(md_path))

    for rel_file in _INCLUDE_FILES:
        md_path = _DOCS_DIR / rel_file
        if md_path.exists() and md_path not in seen:
            seen.add(md_path)
            docs.append(_read_doc(md_path))

    logger.info("workshop_docs: indexed %d documents from docs/", len(docs))
    _INDEX = docs
    return _INDEX


def _read_doc(path: Path) -> dict:
    """Read a markdown file into an index entry."""
    try:
        raw = path.read_bytes()[:_MAX_DOC_BYTES]
        text = raw.decode("utf-8", errors="replace")
    except OSError as exc:
        logger.warning("workshop_docs: failed to read %s: %s", path, exc)
        text = ""

    # Pull the title from the first `# Heading` line, fall back to filename.
    title = path.stem.replace("-", " ").replace("_", " ").title()
    for line in text.splitlines()[:20]:
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            break

    rel = path.relative_to(_REPO_ROOT).as_posix()
    return {
        "path": rel,
        "title": title,
        "text": text,
        "text_lower": text.lower(),
    }


_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


def _tokenise(query: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(query) if len(t) > 1]


def _score(doc: dict, tokens: list[str]) -> tuple[int, int]:
    """Score a doc against query tokens.

    Returns (hits_in_title, hits_in_body). Title hits weigh more than body
    hits — title matches imply the doc is *about* the topic.
    """
    if not tokens:
        return (0, 0)
    title_lower = doc["title"].lower()
    body_lower = doc["text_lower"]
    title_hits = sum(title_lower.count(t) for t in tokens)
    body_hits = sum(body_lower.count(t) for t in tokens)
    return (title_hits, body_hits)


def _snippet(doc: dict, tokens: list[str]) -> str:
    """Return a snippet centred on the first token hit, or the doc opening."""
    body = doc["text"]
    body_lower = doc["text_lower"]
    first_hit = -1
    for t in tokens:
        idx = body_lower.find(t)
        if idx != -1 and (first_hit == -1 or idx < first_hit):
            first_hit = idx
    if first_hit == -1:
        first_hit = 0

    start = max(0, first_hit - _SNIPPET_RADIUS)
    end = min(len(body), first_hit + _SNIPPET_RADIUS)
    snippet = body[start:end].strip()

    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{snippet}{suffix}"


def search_workshop_docs(query: str, max_results: int = 4) -> str:
    """Search the workshop docs corpus for content matching a query.

    Use this whenever the user asks about:
    - Workshop agenda, code tour, protocol gotchas, pre-work
    - How AG-UI, A2UI, MCP Apps, A2A, or ADK work in this platform
    - Sprint design decisions in v6.0.0, v6.1.0, or v6.2.0
    - Fork-adoption howtos for budget, artefact review, tenant attribution,
      anonymous-group auth, channels, multi-surface rendering

    Returns formatted top-matching documents with:
    - File path (clickable in the workshop repo)
    - Title (from the doc's `# Heading`)
    - Snippet around the first match

    Cite the path in your answer so the user can read the source. If no
    documents match, say so — DO NOT invent facts. Real ground-truth
    answers grounded in the repo are more valuable than confident
    hallucinations.

    Args:
        query: Free-text search terms. Multiple keywords are scored
            additively (more matches = higher rank). Title matches weigh
            more than body matches.
        max_results: Number of top hits to return. Default 4. Keep small
            (≤6) so the prompt stays focused.

    Returns:
        Markdown-formatted results, or a "no matches" message.
    """
    docs = _load_index()
    tokens = _tokenise(query)
    if not tokens:
        return "Please pass at least one search keyword."

    scored: list[tuple[tuple[int, int], dict]] = []
    for doc in docs:
        score = _score(doc, tokens)
        if score[0] > 0 or score[1] > 0:
            scored.append((score, doc))

    if not scored:
        return (
            f"No documents matched **{query}**. The workshop helper's "
            "knowledge base covers `docs/workshop/`, `docs/integrations/`, "
            "every shipped sprint design in `docs/design/v6.X.Y/implemented/`, "
            "and the canonical talk doc at "
            "`docs/talks/ai-ui-protocol-stack.md`."
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[: max(1, min(max_results, 6))]

    blocks: list[str] = [f"Found {len(top)} matching document(s):\n"]
    for (title_hits, body_hits), doc in top:
        blocks.append(f"### {doc['title']}")
        blocks.append(f"**File:** `{doc['path']}`")
        blocks.append(f"**Match strength:** title={title_hits}, body={body_hits}")
        blocks.append("")
        blocks.append(_snippet(doc, tokens))
        blocks.append("")
    return "\n".join(blocks)

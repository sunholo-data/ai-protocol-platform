"""Guardrail: every model call goes through a resilience layer (v6.14.0).

WHY THIS TEST EXISTS — READ BEFORE ADDING AN ALLOWLIST ENTRY.

A raw model call (a bare ``genai.Client().generate_content(...)`` or a hand-built
``Gemini(...)``/``LiteLlm(...)`` agent model) has NO retry and NO fallback. A
transient Vertex 429 RESOURCE_EXHAUSTED then becomes a silent, minutes-long
dead-end — this is exactly the 2026-07-17 obligation-analysis incident. The two
approved layers protect against it:

* **Agent / LlmAgent model seam** → build the model with
  ``adk.agent.resolve_model_chain(...)`` (returns a ``ResilientLlm`` chain:
  retry + Gemini region/model fallback + reliability events).
* **Raw google.genai structured-output seam** → call
  ``tools.resilient_genai.generate_content_resilient(...)``.

So this test scans the runtime source for the raw patterns and FAILS if a new one
appears outside the small, reasoned allowlist below. If your new code trips it:
route the call through one of the two layers above. Only add an allowlist entry
for a genuinely-exempt call (an infra probe, a cosmetic fail-soft path), and write
the reason — the reason is the whole point (it makes the exemption visible).
"""

from __future__ import annotations

import re
from pathlib import Path

# backend/ root (tests/unit/<this file> → parents[2]).
_BACKEND = Path(__file__).resolve().parents[2]

# Directories that are NOT runtime-serving code — excluded from the scan.
_EXCLUDED_DIRS = {"tests", "scripts", ".venv", "__pycache__", ".git"}

# Files permitted to contain a raw pattern, each with the reason it is exempt.
# The relative path (POSIX) → reason. KEEP THE REASON HONEST.
_ALLOWLIST: dict[str, str] = {
    "adk/agent.py": "the model resolver — constructs Gemini/LiteLlm/RegionalGemini and wraps them in ResilientLlm via resolve_model_chain",
    "adk/resilient_llm.py": "the ResilientLlm wrapper itself (its generate_content_async IS the retry/fallback loop)",
    "tools/resilient_genai.py": "the generate_content_resilient wrapper itself (its genai.Client + generate_content ARE the resilient call)",
    "db/title_generator.py": "cosmetic ≤6-word session title; best-effort, fails soft (returns None), off the critical path — an accepted exemption, not a gap",
}

# Raw model-call signatures. Each: (compiled regex, remediation hint).
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"genai\.Client\s*\("),
        "raw google.genai client — call tools.resilient_genai.generate_content_resilient instead",
    ),
    (
        # matches generate_content( and generate_content_async(, NOT
        # generate_content_resilient( (the approved wrapper).
        re.compile(r"\.generate_content(?:_async)?\s*\("),
        "raw generate_content — use generate_content_resilient (raw seam) or resolve_model_chain (agent seam)",
    ),
    (
        # A model constructor called directly (not imported, not annotated).
        re.compile(r"\b(?:RegionalGemini|Gemini|LiteLlm|Claude)\s*\("),
        "raw model constructor — build the model via adk.agent.resolve_model_chain (ResilientLlm)",
    ),
]


def _runtime_py_files() -> list[Path]:
    out: list[Path] = []
    for path in _BACKEND.rglob("*.py"):
        rel_parts = path.relative_to(_BACKEND).parts
        if any(part in _EXCLUDED_DIRS for part in rel_parts):
            continue
        out.append(path)
    return out


def test_no_raw_model_calls_outside_the_resilient_layers():
    violations: list[str] = []
    for path in _runtime_py_files():
        rel = path.relative_to(_BACKEND).as_posix()
        if rel in _ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            code = line.split("#", 1)[0]  # ignore comments to avoid false positives
            for pattern, hint in _PATTERNS:
                if pattern.search(code):
                    violations.append(f"{rel}:{lineno}: {line.strip()}\n    → {hint}")

    assert not violations, (
        "Raw model call(s) found that bypass the resilience layers "
        "(ResilientLlm / generate_content_resilient). Route them through a "
        "resilient layer, or — only if genuinely exempt — add the file to "
        "_ALLOWLIST in this test WITH a reason.\n\n" + "\n".join(violations)
    )


def test_allowlist_entries_still_exist_and_are_used():
    """An allowlist entry that no longer matches anything is stale — drop it, so
    the allowlist can't rot into a blanket exemption for files that changed."""
    stale: list[str] = []
    for rel, _reason in _ALLOWLIST.items():
        path = _BACKEND / rel
        if not path.exists():
            stale.append(f"{rel} (file no longer exists)")
            continue
        text = path.read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        if not any(pattern.search(code) for pattern, _ in _PATTERNS):
            stale.append(f"{rel} (no raw pattern present any more)")
    assert not stale, "Stale allowlist entries — remove them:\n" + "\n".join(stale)

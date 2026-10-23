#!/usr/bin/env python3
"""Relative-link checker for the docs tree.

Scans Markdown files for relative links (``[text](path)``) and verifies the
target exists on disk. Skips external (http/https/mailto), in-page anchors
(``#frag``), and ``file://`` absolute links (machine-specific scoping-site
pointers). Resolves ``#anchor`` and ``?query`` suffixes off the path before
checking. Exit 1 if any broken link is found.

Usage:
    python3 scripts/check-doc-links.py [root ...]   # default: docs
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "file://", "#", "tel:")


def _candidates(root: Path) -> list[Path]:
    return sorted(root.rglob("*.md"))


def _check_file(md: Path) -> list[tuple[int, str]]:
    broken: list[tuple[int, str]] = []
    for lineno, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
        for raw in LINK_RE.findall(line):
            target = raw.strip()
            if target.startswith(SKIP_PREFIXES):
                continue
            # strip #anchor / ?query
            path_part = re.split(r"[#?]", target, 1)[0]
            if not path_part:
                continue
            resolved = (md.parent / path_part).resolve()
            if not resolved.exists():
                broken.append((lineno, target))
    return broken


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:]] or [Path("docs")]
    total_broken = 0
    files_scanned = 0
    for root in roots:
        for md in _candidates(root):
            files_scanned += 1
            for lineno, target in _check_file(md):
                total_broken += 1
                print(f"BROKEN  {md}:{lineno}  ->  {target}")
    print(f"\nscanned {files_scanned} files; {total_broken} broken relative link(s)")
    return 1 if total_broken else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

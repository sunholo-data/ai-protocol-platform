#!/usr/bin/env python3
"""Resolve every relative markdown link in the docs surface.

Sprint TEMPLATE-INVERT, M2. Closes downstream fork feedback **#11**: of the 55
`docs/design/...` paths referenced from `CLAUDE.md`, `backend/`, `frontend/src`
and `.claude/skills/` in the published template, **only 20 resolved.**

Two causes, both structural rather than careless:

  * Promoting a doc to `implemented/` silently invalidates every link to it.
  * The sanitizer deletes docs that surviving files still link to (a whole
    `v6.14.0/` was referenced and absent).

Both matter more than they look. Those pointers exist precisely so a fork reads
the rationale instead of re-deriving it — that is the stated purpose of the
A2UI playbook in `backend/adk/CLAUDE.md`. A dead link sends the reader back to
re-deriving, which is the exact failure the doc was written to prevent.

Run against the SANITIZED tree as well as the source tree: a link that resolves
here but not there is precisely the #11 bug.

Usage:
    scripts/link-check.py [--root DIR] [--quiet]

Exit: 0 all links resolve · 1 dangling links found
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Markdown inline links. Skips images (leading !) — those are checked too, but
# via the same path resolution, so no separate pattern is needed.
LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")

# Where to look for links. Deliberately the same surface the fork measured.
SEARCH_GLOBS = ("docs/**/*.md", "*.md", ".claude/skills/**/*.md", "**/CLAUDE.md")

SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "tel:", "data:")


def is_external(target: str) -> bool:
    return target.startswith(SKIP_PREFIXES)


def collect_files(root: Path) -> list[Path]:
    seen: set[Path] = set()
    for pattern in SEARCH_GLOBS:
        for p in root.glob(pattern):
            if p.is_file() and ".git/" not in str(p) and "node_modules" not in str(p):
                seen.add(p)
    return sorted(seen)


def check(root: Path) -> list[tuple[Path, int, str]]:
    dangling: list[tuple[Path, int, str]] = []
    for path in collect_files(root):
        try:
            lines = path.read_text().splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(lines, 1):
            for target in LINK_RE.findall(line):
                target = target.split()[0].strip()  # drop optional "title"
                if is_external(target) or not target:
                    continue
                # Strip an anchor; we verify the FILE resolves, not the heading.
                file_part = target.split("#", 1)[0]
                if not file_part:
                    continue
                resolved = (path.parent / file_part).resolve()
                if not resolved.exists():
                    dangling.append((path.relative_to(root), lineno, target))
    return dangling


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = args.root
    if root is None:
        root = Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    root = root.resolve()

    dangling = check(root)

    if dangling:
        print(f"DANGLING LINKS: {len(dangling)}", file=sys.stderr)
        for path, lineno, target in dangling:
            print(f"  {path}:{lineno} -> {target}", file=sys.stderr)
        print(
            "\nA dangling link sends a reader back to re-deriving the thing the\n"
            "doc exists to explain. Fix the target or drop the link.",
            file=sys.stderr,
        )
        return 1

    if not args.quiet:
        print(f"All relative markdown links resolve under {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

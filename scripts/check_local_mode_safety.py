#!/usr/bin/env python3
"""check_local_mode_safety.py — fail CI if LOCAL_MODE leaks into deployed configs.

LOCAL_MODE=1 / NEXT_PUBLIC_LOCAL_MODE=1 must NEVER appear in cloudbuild.yaml,
terraform tfvars, or any other config that drives a real deployment. The
backend already refuses to start in that case (config/local_mode.py
assert_safe_local_mode), but a pre-deploy lint catches it before the build
runs and saves a wasted Cloud Build round-trip.

Scanned files (relative to repo root):
  - cloudbuild.yaml
  - backend/cloudbuild.yaml
  - infrastructure/**/cloudbuild.yaml
  - **/*.tf
  - **/*.tfvars
  - .github/workflows/*.yml

Exit codes:
  0 — clean (no LOCAL_MODE leaks)
  1 — found leak(s); each offending file printed to stdout
  2 — internal error
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Patterns we refuse to see in deployed configs. Match any context (yaml
# key, env-var assignment, shell export, terraform map) — LOCAL_MODE
# followed by an "=" or ":" then a truthy value, with optional quoting.
FORBIDDEN_PATTERNS = [
    re.compile(r"\bLOCAL_MODE\s*[:=]\s*['\"]?(?:1|true|yes|on)['\"]?", re.IGNORECASE),
    re.compile(r"\bNEXT_PUBLIC_LOCAL_MODE\s*[:=]\s*['\"]?(?:1|true|yes|on)['\"]?", re.IGNORECASE),
]

# Files that should NEVER set LOCAL_MODE truthy.
SCAN_PATHS = [
    "cloudbuild.yaml",
    "backend/cloudbuild.yaml",
]
SCAN_GLOBS = [
    "infrastructure/*/cloudbuild.yaml",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
]
SCAN_RECURSIVE_GLOBS = [
    "**/*.tf",
    "**/*.tfvars",
]


def repo_root() -> Path:
    import subprocess

    try:
        out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True)
        return Path(out.strip())
    except subprocess.CalledProcessError:
        return Path.cwd()


def file_contains_forbidden(path: Path) -> list[str]:
    """Return offending lines, or [] if clean."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    matches = []
    for pat in FORBIDDEN_PATTERNS:
        for m in pat.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            line = text.splitlines()[line_no - 1].strip()
            matches.append(f"{path}:{line_no}: {line}")
    return matches


def gather_files(root: Path) -> list[Path]:
    seen: set[Path] = set()
    for rel in SCAN_PATHS:
        p = root / rel
        if p.is_file():
            seen.add(p)
    for pattern in SCAN_GLOBS:
        for p in root.glob(pattern):
            if p.is_file():
                seen.add(p)
    for pattern in SCAN_RECURSIVE_GLOBS:
        for p in root.glob(pattern):
            # Skip node_modules, .venv, etc.
            if any(part in {"node_modules", ".venv", ".next", "__pycache__", ".git"} for part in p.parts):
                continue
            if p.is_file():
                seen.add(p)
    return sorted(seen)


def main() -> int:
    root = repo_root()
    offenders: list[str] = []
    for path in gather_files(root):
        offenders.extend(file_contains_forbidden(path))

    if offenders:
        print("FAIL: LOCAL_MODE truthy values found in deployed-config files:", file=sys.stderr)
        for line in offenders:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nLOCAL_MODE injects an auth stub + in-memory Firestore. It must "
            "never run in a deployed env. Unset LOCAL_MODE in the file above, "
            "or delete the file if it shouldn't exist.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

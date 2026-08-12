"""Idempotent: add `tool_configs.mcp.allow_context_writes` to public skills
that already have `tool_configs.mcp.servers`.

Sprint 1.25 — pairs with the SKILL.md template updates that ship this same
field on cold seeds. This script handles existing Firestore records that
were seeded BEFORE 1.25 (so the SKILL.md update alone wouldn't reach
them — `seed_skills.py` skips records that already exist).

Default policy: opt EVERY currently-activated server into context-writes.
That matches the workshop intent (we want the demo to work end-to-end on
the public skills); skills that want the feature off can prune the list
later via a future skills CRUD UI. Tightening to a per-server allow-list
on a case-by-case basis is a separate config decision.

Usage:
    # Dry run — print what WOULD change, no writes
    uv run python scripts/migrate_mcp_context_writes.py --dry-run

    # Apply
    uv run python scripts/migrate_mcp_context_writes.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._smoke_config import pin_project_for_env

pin_project_for_env("dev")

from db.firestore import get_client  # noqa: E402


def migrate(*, dry_run: bool) -> None:
    db = get_client()
    print(f"Project: {db.project}")
    docs = list(db.collection("skills").limit(200).get())
    print(f"Scanning {len(docs)} skill records...")

    updated = 0
    unchanged = 0
    skipped_no_mcp = 0

    for doc in docs:
        data = doc.to_dict()
        skill_md = data.get("skillMetadata") or {}
        tool_cfg = skill_md.get("toolConfigs") or {}
        mcp = tool_cfg.get("mcp") or {}
        servers = mcp.get("servers") or []
        if not servers:
            skipped_no_mcp += 1
            continue

        existing = mcp.get("allow_context_writes") or []
        if set(servers).issubset(set(existing)):
            unchanged += 1
            print(f"  ✓  {data.get('name'):30s} ({doc.id[:8]}…) already opted in")
            continue

        # Add every activated server to the allow_context_writes list
        new_allow = sorted(set(existing) | set(servers))
        mcp["allow_context_writes"] = new_allow
        tool_cfg["mcp"] = mcp
        skill_md["toolConfigs"] = tool_cfg

        if dry_run:
            print(f"  →  {data.get('name'):30s} ({doc.id[:8]}…) would set allow_context_writes={new_allow}")
        else:
            db.collection("skills").document(doc.id).update({"skillMetadata": skill_md})
            print(f"  ✓  {data.get('name'):30s} ({doc.id[:8]}…) set allow_context_writes={new_allow}")
        updated += 1

    print(f"\nDone. updated={updated} unchanged={unchanged} skipped_no_mcp={skipped_no_mcp} dry_run={dry_run}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

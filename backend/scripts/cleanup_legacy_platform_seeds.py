"""Delete legacy (non-platform) copies of platform-seeded skill names.

The platform seed step (admin/platform_seed.py) is keyed by
(name, ownerId==PLATFORM_OWNER_UID), so if a given environment was seeded
before PLATFORM-GLOBAL-SKILLS M1 landed — e.g., under a dev user's uid —
those legacy docs still live in Firestore alongside the new platform ones.
They share the same ``name`` but not ``ownerId``, which produces visually
duplicated rows in the marketplace.

This script finds any skill whose ``name`` matches one of the five
platform templates but whose ``ownerId`` is NOT the sentinel, and offers
to delete it. Dry-run by default; pass ``--yes`` to actually delete.

Usage:
    cd backend && uv run python scripts/cleanup_legacy_platform_seeds.py --env dev
    cd backend && uv run python scripts/cleanup_legacy_platform_seeds.py --env dev --yes

Requires ADC (``gcloud auth application-default login``) with Firestore
read/write on the target project.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._smoke_config import ENVIRONMENTS, project_for_env


def _template_names(templates_dir: Path) -> list[str]:
    return sorted(p.name for p in templates_dir.iterdir() if p.is_dir() and (p / "SKILL.md").exists())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # See seed_mcp_servers.py: empty choices would reject everything in a fork.
    parser.add_argument("--env", choices=sorted(ENVIRONMENTS.keys()) or None, default="dev")
    parser.add_argument("--yes", action="store_true", help="Actually delete (otherwise dry-run)")
    args = parser.parse_args()

    project_id = project_for_env(args.env)
    # Set BOTH — GCP_PROJECT shadows GOOGLE_CLOUD_PROJECT in the firestore
    # client (see gotcha_gcp_project_env_shadow memory).
    os.environ["GCP_PROJECT"] = project_id
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id

    from db import firestore as fs
    from skills.platform import PLATFORM_OWNER_UID
    from skills.skill_config import COLLECTION, delete_skill

    templates_dir = Path(__file__).resolve().parent.parent / "skills" / "templates"
    template_names = _template_names(templates_dir)
    print(f"env={args.env} project={project_id}")
    print(f"template names: {template_names}")
    print(f"platform sentinel: {PLATFORM_OWNER_UID}")
    print()

    client = fs.get_client()
    col = client.collection(COLLECTION)

    legacy: list[dict] = []
    platform: list[dict] = []
    for name in template_names:
        for snap in col.where("name", "==", name).stream():
            doc = snap.to_dict() or {}
            row = {
                "skillId": snap.id,
                "name": doc.get("name"),
                "ownerId": doc.get("ownerId"),
                "ownerEmail": doc.get("ownerEmail"),
            }
            if doc.get("ownerId") == PLATFORM_OWNER_UID:
                platform.append(row)
            else:
                legacy.append(row)

    print(f"Platform-owned ({len(platform)}):")
    for r in platform:
        print(f"  {r['name']:22}  {r['skillId']}  owner={r['ownerId']}")
    print()
    print(f"Legacy non-platform ({len(legacy)}):")
    for r in legacy:
        print(f"  {r['name']:22}  {r['skillId']}  owner={r['ownerId']} ({r['ownerEmail']})")
    print()

    if not legacy:
        print("Nothing to clean up.")
        return 0

    if not args.yes:
        print(f"DRY RUN — would delete {len(legacy)} skill(s). Re-run with --yes to execute.")
        return 0

    print(f"Deleting {len(legacy)} legacy skill(s)...")
    failed: list[str] = []
    for r in legacy:
        ok = delete_skill(r["skillId"])
        status = "OK  " if ok else "FAIL"
        print(f"  {status}  {r['skillId']}  ({r['name']})")
        if not ok:
            failed.append(r["skillId"])

    if failed:
        print(f"\n{len(failed)} delete(s) failed: {failed}")
        return 1
    print(f"\nDeleted {len(legacy)} legacy skill(s). Re-run script (dry) to confirm clean state.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

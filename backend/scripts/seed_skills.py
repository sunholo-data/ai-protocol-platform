"""Seed / re-seed platform skills into a target project's Firestore.

Thin CLI over the CANONICAL seeder ``admin.platform_seed.seed()`` — the exact
same logic the deploy's ``POST /api/admin/seed-platform-skills`` step runs. This
script used to carry its OWN divergent copy that never set ``slug`` and
hardcoded ``accessControl={type: public}``; that drift is what left skills
slug-less + public on a freshly-cut env (see docs/ops/env-cut-runbook.md Gap 3).
There is now ONE source of truth, so a manual re-seed and a deploy re-seed
produce identical results.

Usage:
    # Preview against an env (no writes):
    PLATFORM_SEED_PROJECT=your-project-id-test \
      uv run python scripts/seed_skills.py --dry-run

    # Apply (creates missing skills, refreshes existing to match templates —
    # backfilling slug + accessControl from each SKILL.md frontmatter):
    PLATFORM_SEED_PROJECT=your-project-id-test PLATFORM_OWNER_EMAIL=platform@yourcompany.com \
      uv run python scripts/seed_skills.py

Platform skills are always owned by ``PLATFORM_OWNER_UID`` (a fixed constant),
so there is no ``--owner-uid`` to get wrong — the old flag is accepted only to
reject a mismatched value loudly rather than silently seed under the wrong owner.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add backend root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set the GCP project BEFORE importing anything that builds a Firestore client —
# the client reads GOOGLE_CLOUD_PROJECT at construction time.
#
# Resolution order (item #2 from template-fork-ergonomics):
#   1. PLATFORM_SEED_PROJECT env var (fork override)
#   2. GOOGLE_CLOUD_PROJECT env var (ADC / Cloud Run default)
#   3. pin_project_for_env("dev") for Aitana devs on a local shell where
#      GCP_PROJECT may be shadowed (gotcha_gcp_project_env_shadow)
_seed_project = os.environ.get("PLATFORM_SEED_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
if _seed_project:
    os.environ["GOOGLE_CLOUD_PROJECT"] = _seed_project
else:
    from scripts._smoke_config import pin_project_for_env

    pin_project_for_env("dev")

from admin.platform_seed import seed  # noqa: E402
from skills.platform import PLATFORM_OWNER_UID  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed / re-seed platform skills (canonical seeder).")
    p.add_argument("--dry-run", action="store_true", help="Print what would change, write nothing.")
    p.add_argument(
        "--owner-email",
        default=os.environ.get("SEED_OWNER_EMAIL", ""),
        help="Platform owner email → PLATFORM_OWNER_EMAIL (recorded as ownerEmail on created skills).",
    )
    # Back-compat / footgun-guard: platform skills are always owned by
    # PLATFORM_OWNER_UID. Accept the old flag only to reject a wrong value.
    p.add_argument("--owner-uid", default="", help=argparse.SUPPRESS)
    # Deprecated no-op: the canonical seeder ALWAYS refreshes existing skills to
    # match templates (that's how slug/access get backfilled). Kept so old
    # invocations don't break.
    p.add_argument("--update-existing", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if args.owner_uid and args.owner_uid != PLATFORM_OWNER_UID:
        print(
            f"ERROR: --owner-uid={args.owner_uid!r} != PLATFORM_OWNER_UID={PLATFORM_OWNER_UID!r}. "
            "Platform skills must be platform-owned; refusing to seed under a different owner.",
            file=sys.stderr,
        )
        return 2
    if args.update_existing:
        print("note: --update-existing is now the default (the seeder always refreshes existing skills).")

    # The canonical seeder reads PLATFORM_OWNER_EMAIL from the env.
    if args.owner_email:
        os.environ["PLATFORM_OWNER_EMAIL"] = args.owner_email

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    print(f"{'DRY RUN — ' if args.dry_run else ''}seeding platform skills into project {project!r}")

    summary = seed(dry_run=args.dry_run)
    d = summary.as_dict()
    verb = "would " if args.dry_run else ""
    print(
        f"  {verb}created={d['created']} {verb}refreshed={d['refreshed']} "
        f"skipped={d['skipped']} {verb}purged={d['purged']} failed={d['failed']}"
    )
    if d["failed"]:
        print(f"  FAILED templates: {d['failed']}", file=sys.stderr)
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Backfill ``chat_sessions/{id}.collaborators`` for shared machine threads.

A machine principal's ``supervisors`` are stamped onto each NEW thread it
starts. Threads it started before that (or before a supervisor was added) carry
no ``collaborators``, so they don't appear under the supervisor's "Shared with
me" and the supervisor can't reply in them. This adds the owner's CURRENT
supervisors to every thread that owner has.

Only ever adds (ArrayUnion): removing a supervisor from the principal stops new
threads being shared but does not unshare old ones — pass ``--replace`` to make
existing rows match the current list exactly.

Safety properties:
  * **Dry-run by default.** Writes only with ``--apply``.
  * **Idempotent.** Rows that already list every supervisor are skipped.
  * **Owner-scoped.** Only rows whose ``ownerUid`` is the given owner.

Usage:
    uv run python scripts/backfill_session_collaborators.py --owner machine:agent            # dry run
    uv run python scripts/backfill_session_collaborators.py --owner machine:agent --apply
    uv run python scripts/backfill_session_collaborators.py --owner machine:agent \\
        --emails ada@example.com --apply        # explicit list instead of the principal's
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backfill-collaborators")

_COLLECTION = "chat_sessions"


def _supervisors_for(owner_uid: str) -> list[str]:
    from auth.firebase_auth import resolve_user_by_uid

    owner = resolve_user_by_uid(owner_uid)
    if owner is None:
        log.error("owner %s is not a known principal — pass --emails explicitly", owner_uid)
        sys.exit(2)
    return list(owner.supervisors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", required=True, help="ownerUid of the threads, e.g. machine:agent")
    parser.add_argument("--emails", nargs="*", help="collaborator emails (default: the owner's supervisors)")
    parser.add_argument("--replace", action="store_true", help="set exactly this list instead of adding")
    parser.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    args = parser.parse_args()

    emails = sorted({e.strip().lower() for e in (args.emails or _supervisors_for(args.owner)) if e.strip()})
    if not emails and not args.replace:
        log.error("no collaborators to add (the owner has no supervisors and --emails was not given)")
        return 2

    from google.cloud import firestore

    from db.firestore import get_client

    col = get_client().collection(_COLLECTION)
    rows = col.where(filter=firestore.FieldFilter("ownerUid", "==", args.owner)).stream()
    seen = changed = 0
    for snap in rows:
        seen += 1
        current = sorted({str(c).lower() for c in (snap.to_dict() or {}).get("collaborators") or []})
        target = emails if args.replace else sorted(set(current) | set(emails))
        if current == target:
            continue
        changed += 1
        log.info("  %s: %s -> %s", snap.id, current or "[]", target)
        if args.apply:
            col.document(snap.id).update({"collaborators": target})

    verb = "updated" if args.apply else "would update (dry run; pass --apply)"
    log.info("%d of %d threads owned by %s %s", changed, seen, args.owner, verb)
    return 0


if __name__ == "__main__":
    sys.exit(main())

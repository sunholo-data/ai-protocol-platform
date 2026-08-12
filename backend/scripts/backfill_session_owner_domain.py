"""Backfill ``chat_sessions/{id}.ownerDomain`` (v6.16.0 / ADMIN-SCOPE M4).

Tenant-scoped admin analytics filters sessions by ``ownerDomain``, but rows
written before v6.16.0 only carry ``ownerUid``. Those rows **fail closed** — a
tenant admin never sees them — so until this runs, a tenant admin's session list
is silently incomplete rather than wrong. This fills them in.

Resolution is uid → Firebase Auth record → email → domain, with a per-uid cache
(sessions cluster heavily by user, so the cache does most of the work).

Safety properties:
  * **Dry-run by default.** Writes only with ``--apply``.
  * **Idempotent.** Rows that already have a non-blank ``ownerDomain`` are
    skipped, so re-running costs reads and changes nothing.
  * **Resumable.** Each row is written independently; interrupting it mid-run
    leaves a consistent partial state that the next run continues from.
  * **Fail-soft per row.** A deleted user or an Auth blip marks that row
    unresolved and moves on — one bad row must not abort the batch. Unresolved
    rows stay blank, which is the safe state.

Usage:
    uv run python scripts/backfill_session_owner_domain.py            # dry run
    uv run python scripts/backfill_session_owner_domain.py --apply
    uv run python scripts/backfill_session_owner_domain.py --apply --limit 500
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backfill-owner-domain")

_COLLECTION = "chat_sessions"


def _fb_auth():
    import firebase_admin
    from firebase_admin import auth as fb_auth

    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app()
    return fb_auth


def _domain_for_uid(fb, uid: str, cache: dict[str, str]) -> str:
    """uid → email domain, cached. Blank when unresolvable (fail closed)."""
    if uid in cache:
        return cache[uid]
    domain = ""
    try:
        rec = fb.get_user(uid)
        email = (getattr(rec, "email", "") or "").strip().lower()
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    except Exception as exc:  # deleted user, permission blip — never abort
        log.warning("  uid=%s unresolved (%s)", uid, type(exc).__name__)
    cache[uid] = domain
    return domain


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Actually write (default is a dry run).")
    ap.add_argument("--limit", type=int, default=0, help="Process at most N rows needing a backfill.")
    args = ap.parse_args()

    from db.firestore import query_documents, update_document

    fb = _fb_auth()
    cache: dict[str, str] = {}
    scanned = skipped = resolved = unresolved = written = 0

    log.info("Scanning %s ... (%s)", _COLLECTION, "APPLY" if args.apply else "DRY RUN")
    for doc in query_documents(_COLLECTION):
        scanned += 1
        session_id = str(doc.get("__id", ""))
        if str(doc.get("ownerDomain", "") or "").strip():
            skipped += 1
            continue
        uid = str(doc.get("ownerUid", "") or "")
        if not uid:
            unresolved += 1
            continue
        domain = _domain_for_uid(fb, uid, cache)
        if not domain:
            unresolved += 1
            continue
        resolved += 1
        if args.apply:
            try:
                update_document(_COLLECTION, session_id, {"ownerDomain": domain})
                written += 1
            except Exception as exc:  # keep going; the row stays blank = safe
                log.warning("  session=%s write FAILED (%s)", session_id, type(exc).__name__)
        else:
            log.info("  would set session=%s ownerDomain=%s", session_id, domain)
        if args.limit and resolved >= args.limit:
            log.info("Reached --limit %d; stopping.", args.limit)
            break

    log.info(
        "\nscanned=%d already_set=%d resolved=%d written=%d unresolved=%d",
        scanned,
        skipped,
        resolved,
        written,
        unresolved,
    )
    if unresolved:
        # Never silent: an operator must know coverage is partial, and that
        # those sessions remain invisible to tenant admins by design.
        log.info(
            "%d row(s) could not be attributed (no uid, deleted user, or lookup failure).\n"
            "They keep a blank ownerDomain and stay hidden from tenant-scoped views.",
            unresolved,
        )
    if not args.apply and resolved:
        log.info("\nDry run — re-run with --apply to write these %d row(s).", resolved)
    return 0


if __name__ == "__main__":
    sys.exit(main())

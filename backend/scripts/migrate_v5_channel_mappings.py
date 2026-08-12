"""Migrate v5 `channel_mappings.PHONE_TO_EMAIL` to v6 `channel_identities`.

v5 stored a hand-curated module-level dict mapping phone numbers to user
email addresses, used by both the WhatsApp adapter (direct lookup) and
the Telegram adapter (after contact-sharing). v6 normalises this into
the `channel_identities` Firestore collection that the
`IdentityResolver` consults.

This is a one-shot port. After the WhatsApp + Telegram adapters are
live in production, new mappings get auto-created on first inbound via
`BaseChannel.on_unknown_user`. This script only seeds the historical
data that pre-dates v6.

Usage:
    # Dry run — prints the plan, makes no writes.
    uv run python scripts/migrate_v5_channel_mappings.py --dry-run

    # Live run.
    uv run python scripts/migrate_v5_channel_mappings.py

    # Point at a custom v5 checkout / file path.
    uv run python scripts/migrate_v5_channel_mappings.py \\
        --source /path/to/channel_mappings.py

The mapping `PHONE_TO_EMAIL` is loaded by AST parsing the source file
(not by `import`), so the v5 file's Sunholo dependencies don't have to
be resolvable in the v6 venv. The file is small (24 lines, one dict
literal) and the parse is robust.

Idempotency: every write targets a deterministic doc id
(`whatsapp_{phone}` / `telegram_{phone}`). Existing documents are
skipped (logged as `[skip] already exists`), so re-running is safe.
"""

from __future__ import annotations

import argparse
import ast
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("migrate_v5_channel_mappings")

DEFAULT_SOURCE = Path("<your-v5-source>/backend/channel_mappings.py")
# v6's identity resolver derives the firebase_uid from `channel-{channel}_{user_id}`.
# Mirror that here so the v5 import lands at the same UID an auto-create would have.
_DERIVED_UID_PREFIX = "channel-"


def load_phone_to_email(source_path: Path) -> dict[str, str]:
    """Parse `PHONE_TO_EMAIL` from a v5 `channel_mappings.py` file.

    Uses AST rather than `import` so the v5 file's Sunholo / langchain
    imports don't need to resolve in the v6 venv. Raises ValueError if
    no `PHONE_TO_EMAIL` literal is found.
    """
    if not source_path.exists():
        raise FileNotFoundError(f"v5 source not found at {source_path}")
    tree = ast.parse(source_path.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "PHONE_TO_EMAIL":
                literal = ast.literal_eval(node.value)
                if not isinstance(literal, dict):
                    raise ValueError("PHONE_TO_EMAIL is not a dict literal")
                # Normalise: phone keys + email values must be strings.
                out: dict[str, str] = {}
                for phone, email in literal.items():
                    if not isinstance(phone, str) or not isinstance(email, str):
                        logger.warning("Skipping non-string mapping: %r → %r", phone, email)
                        continue
                    out[phone] = email
                return out
    raise ValueError(f"PHONE_TO_EMAIL not defined in {source_path}")


def plan_migrations(phone_to_email: dict[str, str]) -> list[dict[str, str]]:
    """Build the list of `channel_identities` records to write.

    Each v5 phone↔email entry maps to TWO records — one for WhatsApp,
    one for Telegram — because v5 used the same phone for both. The
    Telegram record is correct after contact-sharing (which is how the
    user's number gets known); pre-share Telegram users would not have
    been in this table either.

    Returns a list of dicts ready for `set_document`. The `_doc_id`
    field is metadata for the runner; the rest is the record body.
    """
    plan: list[dict[str, str]] = []
    for phone, email in phone_to_email.items():
        # WhatsApp: channel_user_id is the phone WITH +.
        plan.append(
            {
                "_doc_id": f"whatsapp_{phone}",
                "channel": "whatsapp",
                "channel_user_id": phone,
                "email": email,
            }
        )
        # Telegram: same phone — would have been populated post contact-share.
        plan.append(
            {
                "_doc_id": f"telegram_{phone}",
                "channel": "telegram",
                "channel_user_id": phone,
                "email": email,
            }
        )
    return plan


def apply_plan(plan: list[dict[str, str]], dry_run: bool) -> dict[str, int]:
    """Apply (or rehearse) the migration plan against Firestore.

    Returns counts: {written, skipped, errors}.

    Idempotency: each doc is checked first via `get_document`; existing
    records are skipped. Re-runs are safe.
    """
    counts = {"written": 0, "skipped": 0, "errors": 0}

    if dry_run:
        # Skip the Firestore import entirely in dry-run so the script
        # works without GCP credentials. Print the plan and bail.
        for record in plan:
            doc_id = record["_doc_id"]
            print(f"[dry-run] would write channel_identities/{doc_id}")
            print(
                f"          channel={record['channel']} "
                f"channel_user_id={record['channel_user_id']} "
                f"email={record['email']}"
            )
            counts["written"] += 1
        return counts

    # Live run — import Firestore only here so dry-run never touches GCP.
    from db.firestore import get_document, set_document

    for record in plan:
        doc_id = record["_doc_id"]
        try:
            existing = get_document("channel_identities", doc_id)
        except Exception:
            logger.exception("Failed to read channel_identities/%s", doc_id)
            counts["errors"] += 1
            continue

        if existing:
            logger.info("[skip] channel_identities/%s already exists", doc_id)
            counts["skipped"] += 1
            continue

        now = datetime.now(UTC)
        email = record["email"]
        domain = email.split("@", 1)[1] if "@" in email else ""
        body = {
            "channel": record["channel"],
            "channel_user_id": record["channel_user_id"],
            # Derive the firebase_uid the same way `IdentityResolver.auto_create`
            # would have when the user first messaged us in v6 — so the v5
            # import lands at the same UID an auto-create would have.
            "firebase_uid": f"{_DERIVED_UID_PREFIX}{doc_id}",
            "email": email,
            "domain": domain,
            "group_tags": [],
            "created_at": now,
            "last_seen_at": now,
            "imported_from_v5": True,
        }
        try:
            set_document("channel_identities", doc_id, body)
        except Exception:
            logger.exception("Failed to write channel_identities/%s", doc_id)
            counts["errors"] += 1
            continue
        logger.info("[write] channel_identities/%s -> %s", doc_id, body["firebase_uid"])
        counts["written"] += 1

    return counts


def main(argv: list[str] | None = None) -> int:
    """Entrypoint. Returns shell exit code (0 = success)."""
    parser = argparse.ArgumentParser(description="Migrate v5 channel_mappings to v6 channel_identities.")
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"Path to v5 channel_mappings.py (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without writing to Firestore (no GCP creds required).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        phone_to_email = load_phone_to_email(args.source)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not phone_to_email:
        print("No mappings found in v5 source — nothing to migrate.")
        return 0

    plan = plan_migrations(phone_to_email)
    print(f"Loaded {len(phone_to_email)} v5 mappings → {len(plan)} v6 records to write.")
    counts = apply_plan(plan, dry_run=args.dry_run)

    print("--- summary ---")
    print(f"written: {counts['written']}")
    print(f"skipped: {counts['skipped']}")
    print(f"errors:  {counts['errors']}")

    return 0 if counts["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

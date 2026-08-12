#!/usr/bin/env python3
"""Bootstrap Vertex AI RAG corpora for existing users.

For each user in the `user_profiles` Firestore collection that does not yet have
a `ragCorpusName` field, this script creates a per-user RAG corpus and writes the
resource name back to Firestore.

Idempotent: users who already have `ragCorpusName` set are skipped.

Usage:
    export GOOGLE_CLOUD_PROJECT=your-project-id
    export GOOGLE_CLOUD_LOCATION=europe-west1
    uv run python backend/scripts/bootstrap_rag_corpus.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


async def _run(dry_run: bool, limit: int | None) -> None:
    import vertexai.rag as rag

    from config.gcp import resolve_gcp_project
    from db import firestore as fs
    from rag.corpus import _DISPLAY_NAME_PREFIX, _ensure_vertexai

    project = resolve_gcp_project()
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    _ensure_vertexai()

    _log(f"Project: {project}, Location: {location}")
    _log(f"Dry-run: {dry_run}")

    profiles_ref = fs._db().collection("user_profiles")
    docs = list(profiles_ref.stream())

    if limit:
        docs = docs[:limit]

    _log(f"Found {len(docs)} user profiles")

    created = 0
    skipped = 0
    errors = 0

    for doc in docs:
        data = doc.to_dict() or {}
        user_id = doc.id
        existing = data.get("ragCorpusName")

        if existing:
            _log(f"  SKIP  {user_id[:8]}… — corpus already set: {existing}")
            skipped += 1
            continue

        display_name = f"{_DISPLAY_NAME_PREFIX}{user_id}"
        _log(f"  CREATE {user_id[:8]}… — {display_name}", end="")

        if dry_run:
            _log(" [dry-run]")
            created += 1
            continue

        try:
            corpus = await asyncio.to_thread(
                rag.create_corpus,
                display_name=display_name,
            )
            corpus_name = corpus.name
            profiles_ref.document(user_id).update({"ragCorpusName": corpus_name})
            _log(f" → {corpus_name}")
            created += 1
        except Exception as exc:
            _log(f" ERROR: {exc}")
            errors += 1

    _log(f"\nDone — created: {created}, skipped: {skipped}, errors: {errors}")
    if errors:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without creating corpora.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N users (for testing).")
    args = parser.parse_args()

    asyncio.run(_run(dry_run=args.dry_run, limit=args.limit))


if __name__ == "__main__":
    main()

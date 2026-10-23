#!/usr/bin/env python3
"""Read and write `resident_agents` records — the PLATFORM half of provisioning.

The AILANG estate creates the service account, bucket and instance
(`ailang-multivac/scripts/resident-provision.sh`); this writes the record that
says the user has one and where it is. Two estates, two acts, by design (P2) —
so the join between them is a hash both sides compute from the user alone, and
getting that key wrong is the failure mode this script exists to prevent.

WHY A SCRIPT AND NOT A REPL

The record is keyed by ``user_hash(uid)``, and there is no admin route that
CREATES one (deliberately — see `admin/residents_routes.py`). Doing it by hand
means retyping the hash rule at the exact moment it must agree with two other
systems, which is how a record lands under a key nothing reads: `upsert` then
succeeds, the record looks right in a console, and `ask_assistant` still says
you have no assistant. Taking an EMAIL and resolving it here (CLAUDE.md #9,
friendly -> id) removes the chance to be wrong.

    # who has one
    uv run python scripts/resident_record.py list --env dev

    # what the estate provisioned, recorded
    uv run python scripts/resident_record.py set --env dev \
        --email someone@example.com --state ready \
        --instance resident-0fca45abec2c \
        --url https://resident.example.com \
        --bucket example-dev-home-0fca45abec2c

    # eligible, estate has not built it yet
    uv run python scripts/resident_record.py set --env dev \
        --email agent-verify@example.com --state requested

`--dry-run` prints the resolved key and the fields and writes nothing.

Requires ADC with Firestore + Firebase admin on the target project.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The env NAMES this script accepts. The values are the public template's
# placeholders and are NOT the project map — `pin_project_for_env` resolves the
# real project through `_env.ENVIRONMENTS`, so a deployment has exactly one
# env->project map instead of two that drift. Mirrors seed_tool_permissions.py.
#
# They drifted here: the private map's `prod` entry was a truncation of the real
# project id, so `--env prod` had never once addressed the real project. That is
# the third copy of that same typo found in this repo (see
# scripts/one-seed-brand-assets.sh), which is the argument for not keeping a
# second map at all rather than for spelling this one correctly.
_ENV_PROJECTS = {
    "dev": "your-project-id",
    "test": "your-project-id-test",
    "prod": "your-project-id-prod",
}


def _uid_for(email: str) -> str:
    import firebase_admin
    from firebase_admin import auth

    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app()
    return auth.get_user_by_email(email).uid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("list", "show", "set"))
    ap.add_argument("--env", default="dev", choices=sorted(_ENV_PROJECTS))
    ap.add_argument("--email", help="the user, by address (resolved to a uid here)")
    ap.add_argument("--state", help="requested | provisioning | ready | failed | revoked")
    ap.add_argument("--instance")
    ap.add_argument("--url")
    ap.add_argument("--bucket")
    ap.add_argument("--project", help="AILANG estate project hosting the instance")
    ap.add_argument("--region")
    ap.add_argument("--service-account")
    ap.add_argument("--budget-usd", type=float)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Resolve + pin through the one canonical map. `pin_project_for_env` sets
    # BOTH GCP_PROJECT and GOOGLE_CLOUD_PROJECT, because a shell-level
    # GCP_PROJECT silently shadows an inline one under `uv run`
    # (gotcha_gcp_project_env_shadow).
    from scripts._smoke_config import pin_project_for_env

    pin_project_for_env(args.env)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]

    from db import resident_agents as records

    if args.action == "list":
        rows = records.list_all()
        if not rows:
            print(f"no resident_agents records in {project}")
            return 0
        for r in rows:
            print(f"{r.user_hash}  {r.state:<12} {r.domain or '-':<24} {r.url or '-'}")
        return 0

    if not args.email:
        ap.error("--email is required for show/set")

    uid = _uid_for(args.email)
    key = records.user_hash(uid)

    if args.action == "show":
        rec = records.get(uid)
        print(f"{args.email}  uid={uid}  key={key}")
        print(rec.model_dump_json(indent=2, by_alias=True) if rec else "  (no record)")
        return 0 if rec else 1

    if not args.state:
        ap.error("--state is required for set")
    if args.state not in {
        records.STATE_REQUESTED,
        records.STATE_PROVISIONING,
        records.STATE_READY,
        records.STATE_FAILED,
        records.STATE_REVOKED,
    }:
        ap.error(f"unknown state: {args.state}")
    # `ready` is what makes `ask_assistant` route a real question somewhere, so
    # a ready record with no address is refused rather than written: it would
    # fall through to "not available", which reads as a broken instance.
    if args.state == records.STATE_READY and not args.url:
        ap.error("--url is required with --state ready")

    fields = {
        "state": args.state,
        # From the ADDRESS, because the record's key cannot carry it: a tenant
        # admin is scoped by domain, and a record without one is withheld from
        # them (deny-by-default in `admin/residents_routes.list_residents`).
        "domain": args.email.rsplit("@", 1)[-1].lower() if "@" in args.email else None,
        "instance": args.instance,
        "url": args.url,
        "bucket": args.bucket,
        "project": args.project,
        "region": args.region,
        "serviceAccount": args.service_account,
        "budgetUsd": args.budget_usd,
    }
    fields = {k: v for k, v in fields.items() if v is not None}

    print(f"{args.email}  uid={uid}  key={key}  -> {project}/resident_agents/{key}")
    for k, v in fields.items():
        print(f"  {k} = {v}")
    if args.dry_run:
        print("(dry run, nothing written)")
        return 0

    rec = records.upsert(uid, **fields)
    print(f"written: state={rec.state} url={rec.url or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

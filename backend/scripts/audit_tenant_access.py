"""Audit users whose tenant access is silently degraded (PRECUT-HARDENING M3).

A user can authenticate successfully and still land in a workspace with nothing
in it. That reads to them as "the product is broken", and it reads to us as a
normal login — nothing in the auth path is wrong, so nothing logs. This finds
those accounts before a customer does.

Found by: a ONE tester holds a second, personal ``gmail.com`` account alongside
her work address. ``gmail.com`` has no ``clients/{domain}`` record and therefore
no derived group tags, so signing in with it yields a working session and an
empty workspace.

Three classes are reported, and only the first two are defects:

  UNMAPPED   — the user's domain has no ``clients/{domain}`` record at all.
               On a deployment with ``TENANT_FALLBACK_FAIL_CLOSED=1`` this
               fails visibly (403 on the bucket). With the flag OFF the user
               silently shares the deployment-wide ``DOCUMENTS_BUCKET`` — the
               state this script exists to surface. Check the flag per env; it
               is NOT the same everywhere.

  NO_TAGS    — the domain IS a configured tenant, but resolves to zero derived
               group tags. Tag-gated skills and documents will be invisible.
               This is the sharp one: the domain looks configured, so nothing
               looks wrong.

  OK         — either a configured tenant with tags, or an operator domain with
               no tags, which is the normal internal case and NOT a finding.
               Reported only under ``--verbose`` so the signal stays readable.

Safety: **read-only**. It never writes, disables or deletes an account —
deciding what to do with a flagged account is a human call, and disabling the
wrong one locks a customer out.

Usage:
    uv run python scripts/audit_tenant_access.py                     # current project
    uv run python scripts/audit_tenant_access.py --project example-platform-test
    uv run python scripts/audit_tenant_access.py --project example-platform-production --verbose

Exit codes: 0 = no findings, 1 = at least one UNMAPPED or NO_TAGS account.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from google.cloud import firestore


def _domain_of(email: str | None) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if email and "@" in email else ""


def classify(email: str | None, clients: dict[str, dict]) -> tuple[str, str]:
    """Classify one account against the configured tenants. Pure — see tests.

    ``clients`` maps domain → the ``clients/{domain}`` document. A domain that
    is absent is unmapped; a domain present but carrying no ``derived_group_tags``
    is the sharp case, because it LOOKS configured.
    """
    domain = _domain_of(email)
    if not domain:
        return "UNMAPPED", "no parseable email domain"

    client = clients.get(domain)
    if client is None:
        return "UNMAPPED", f"no clients/{domain} record"

    # Mirror auth's own resolution: absent and empty are the same outcome.
    derived = client.get("derived_group_tags") or []
    if not derived:
        return "NO_TAGS", f"clients/{domain} has no derived_group_tags"
    return "OK", f"tags={sorted(derived)}"


def audit(project: str, verbose: bool = False) -> int:
    db = firestore.Client(project=project)

    # clients/{domain} is the authority on "is this a configured tenant".
    clients: dict[str, dict] = {}
    for snap in db.collection("clients").stream():
        clients[snap.id.strip().lower()] = snap.to_dict() or {}

    findings: dict[str, list[tuple[str, str]]] = defaultdict(list)
    total = 0

    for snap in db.collection("users").stream():
        total += 1
        d = snap.to_dict() or {}
        email = d.get("email") or ""
        label, why = classify(email, clients)
        if label == "OK" and not verbose:
            continue
        findings[label].append((email or snap.id, why))

    print(f"\n=== tenant access audit — {project} ===")
    print(f"users scanned: {total}   configured tenants: {len(clients)}\n")

    defects = 0
    for label in ("UNMAPPED", "NO_TAGS", "OK"):
        rows = findings.get(label) or []
        if not rows:
            continue
        if label != "OK":
            defects += len(rows)
        print(f"{label} ({len(rows)}):")
        for email, why in sorted(rows):
            print(f"  - {email:<40} {why}")
        print()

    if not defects:
        print("No findings — every account resolves to a configured tenant with tags.")
    return 1 if defects else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=None, help="GCP project id (default: ADC default project)")
    ap.add_argument("--verbose", action="store_true", help="also list accounts that resolve correctly")
    args = ap.parse_args()

    project = args.project
    if not project:
        import os

        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if not project:
        print("No project: pass --project or set GOOGLE_CLOUD_PROJECT.", file=sys.stderr)
        return 2

    return audit(project, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())

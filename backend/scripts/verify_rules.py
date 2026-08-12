"""Automated Firestore rules verifier — sprint-close and regression gate.

Exercises every branch of `firestore.rules` we care about against a live GCP
project. Both positive (allow) and negative (deny) paths are checked.

Strategy:
    1. Admin SDK (rule bypass) creates two `.test`-TLD users + five fixture
       skills, one per `accessControl.type`: private, public, domain, specific,
       tagged. The non-owner user gets a `groupTags` claim matching the
       tagged fixture.
    2. Rules-deployed probe: anon GET on the private fixture. If it returns
       200, rules are not deployed/propagated — abort with exit 2 (setup
       error) and a clear "run `firebase deploy --only firestore:rules`"
       message. Distinguishes infra-not-wired from rule-drift regressions.
    3. Identity Toolkit REST mints ID tokens for both users.
    4. Firestore REST exercises 10 rule paths. Each check asserts the HTTP
       response matches the expected allow/deny outcome.
    5. `finally:` cleans up every fixture via Admin SDK, even if assertions
       fail mid-run. Safe to re-run, safe to interrupt.

Rules covered (see [firestore.rules](../../firestore.rules)):

    [skills]
      read   — canAccessSkill (public, domain, specific, tagged, owner)
      create — isAuthenticated
      update — isSkillOwner || isAdmin  (positive owner path via check 10)
      delete — isSkillOwner || isAdmin
    [tool_permissions]
      write  — isAdmin  (denial only; admin positive path needs owner@yourcompany.com)

Not covered (known gaps):
    - admin-positive path on tool_permissions (no scripted admin sign-in).
    - messages subcollection.
    - skill_templates, users, tags collections.
    - rules propagation latency beyond the single probe.

Usage:
    uv run python scripts/verify_rules.py --env dev

Exit codes:
    0   all assertions passed
    1   one or more rule checks failed (rules are wrong or drifted)
    2   setup failure (no ADC, sign-in disabled, rules-deployed probe failed, ...)
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
import uuid

import firebase_admin
from firebase_admin import auth
from firebase_admin import firestore as admin_fs

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from _smoke_config import ENVIRONMENTS, SmokeConfigError, resolve, test_identity  # noqa: E402

# `.test` TLD is RFC 2606 reserved — no delivery, no collision, and crucially
# does NOT match the deployment's own email-domain rule in canAccessSkill(), so
# these users are pure non-domain-member identities by default. The domain comes
# from the resolved smoke identity so a fork tests against its own namespace.
_SMOKE_DOMAIN = test_identity()[1]
OWNER_EMAIL = f"rules-test-owner@{_SMOKE_DOMAIN}"
NONOWNER_EMAIL = f"rules-test-nonowner@{_SMOKE_DOMAIN}"

# Test-only tag used for the `tagged` access-control positive check. Unique
# enough that no real production skill would use it, so we don't accidentally
# grant the non-owner test user access to something real in dev.
TAGGED_CLAIM = "rules-test-tag"

# Orphan owner — literal string that can't match any real uid. Used for
# non-private fixtures so only the access-type branch under test can grant
# access (the owner branch is trivially false).
ORPHAN_OWNER = "rules-test-orphan-owner-uid"

FIRESTORE_REST = "https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents"


def _fail(msg: str, code: int = 2) -> None:
    print(f"FAIL  {msg}", file=sys.stderr)
    sys.exit(code)


def _ensure_user(email: str, password: str, claims: dict | None = None) -> str:
    """Idempotent create-or-update of a rules-test user. Returns uid.

    `claims` is set verbatim (None → {} — explicitly clears stale claims from
    a previous run so the `tagged` branch isn't triggered by accident).
    """
    try:
        user = auth.get_user_by_email(email)
        auth.update_user(user.uid, password=password)
    except auth.UserNotFoundError:
        user = auth.create_user(email=email, password=password)
    auth.set_custom_user_claims(user.uid, claims or {})
    return user.uid


def _sign_in(api_key: str, email: str, password: str) -> str:
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Referer": "http://localhost/"},
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        if "PASSWORD_LOGIN_DISABLED" in body:
            _fail(
                "password sign-in is disabled on this Firebase project. "
                "Enable it (see docs/ops/dev-accounts.md) or re-run the Terraform apply."
            )
        _fail(f"signInWithPassword ({email}) {exc.code}: {body[:200]}")
    return resp["idToken"]


def _sign_in_anonymous(api_key: str) -> tuple[str, str] | None:
    """Mint an anonymous Firebase ID token via Identity Toolkit signUp.

    Returns ``(id_token, uid)`` or ``None`` if anonymous auth is disabled on
    the project. The latter is non-fatal — the M4 workshop shared-tier checks
    are skipped with a warning rather than failing the whole sweep.
    """
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={api_key}"
    payload = {"returnSecureToken": True}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Referer": "http://localhost/"},
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        return resp["idToken"], resp["localId"]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        if "ADMIN_ONLY_OPERATION" in body or "OPERATION_NOT_ALLOWED" in body:
            return None  # anon-auth disabled — caller can skip
        _fail(f"signUp (anonymous) {exc.code}: {body[:200]}")
        return None  # unreachable; _fail exits


def _firestore_request(
    project: str,
    path: str,
    *,
    method: str = "GET",
    id_token: str | None = None,
    body: dict | None = None,
) -> int:
    url = FIRESTORE_REST.format(project=project) + "/" + path.lstrip("/")
    headers = {"Content-Type": "application/json"}
    if id_token is not None:
        headers["Authorization"] = f"Bearer {id_token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _create_skill_fixture(name: str, owner_uid: str, access_control: dict) -> str:
    """Admin-SDK write of a skill doc with the given access control. Returns id."""
    client = admin_fs.client()
    fid = f"rules-test-{name}-{uuid.uuid4().hex[:8]}"
    client.collection("skills").document(fid).set(
        {
            "ownerId": owner_uid,
            "name": f"rules-verify {name} fixture",
            "accessControl": access_control,
        }
    )
    return fid


def _cleanup_fixtures(fixtures: dict[str, str]) -> None:
    client = admin_fs.client()
    for name, fid in fixtures.items():
        try:
            client.collection("skills").document(fid).delete()
        except Exception as exc:  # pragma: no cover - best effort
            print(f"WARN  admin cleanup of {name}={fid} failed: {exc}", file=sys.stderr)


class Reporter:
    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, int, str]] = []

    def check(self, label: str, status: int, expect: str) -> None:
        """`expect` ∈ {'allowed', 'denied'}."""
        if expect == "allowed":
            ok = status == 200
        elif expect == "denied":
            # 401 UNAUTHENTICATED (missing/invalid creds) or 403
            # PERMISSION_DENIED (rules blocked) both count as denial.
            # 404 on a fixture we *just created* would mean rules allowed
            # the read but the doc vanished — treat as a rule failure.
            ok = status in (401, 403)
        else:  # pragma: no cover
            raise ValueError(expect)
        self.rows.append((ok, label, status, expect))
        marker = "PASS" if ok else "FAIL"
        print(f"{marker}  {label:<52} http={status:<3}  (expected {expect})")

    def exit_code(self) -> int:
        return 0 if all(ok for ok, *_ in self.rows) else 1


def run(env: str, project_override: str | None, api_key_override: str | None) -> int:
    # A rules check needs only project + key; the base URL is resolved too but
    # unused here (Firestore is reached via the admin SDK, not the frontend).
    try:
        _, api_key, project = resolve(
            env=env or None,
            url="unused://rules-check",
            api_key=api_key_override,
            project_id=project_override,
        )
    except SmokeConfigError as exc:
        _fail(str(exc))

    print(f"Target project : {project}")
    print(f"Target API key : {api_key[:6]}…{api_key[-4:]}")
    print()

    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={"projectId": project})

    # ---- Setup: users + fixtures -----------------------------------------
    owner_pw = secrets.token_urlsafe(24)
    nonowner_pw = secrets.token_urlsafe(24)
    # Owner has no claims; non-owner has the test tag so we can positively
    # verify the `tagged` access branch with just two users.
    owner_uid = _ensure_user(OWNER_EMAIL, owner_pw, claims={})
    nonowner_uid = _ensure_user(NONOWNER_EMAIL, nonowner_pw, claims={"groupTags": [TAGGED_CLAIM]})
    owner_token = _sign_in(api_key, OWNER_EMAIL, owner_pw)
    nonowner_token = _sign_in(api_key, NONOWNER_EMAIL, nonowner_pw)

    fixtures: dict[str, str] = {}
    try:
        # Private fixture is owned by the OWNER user so owner/non-owner
        # read/delete checks have real allow/deny outcomes.
        fixtures["private"] = _create_skill_fixture("private", owner_uid=owner_uid, access_control={"type": "private"})
        # Non-private fixtures use the orphan owner so only the access-type
        # branch under test can grant access.
        fixtures["public"] = _create_skill_fixture("public", owner_uid=ORPHAN_OWNER, access_control={"type": "public"})
        fixtures["domain"] = _create_skill_fixture(
            "domain",
            owner_uid=ORPHAN_OWNER,
            access_control={"type": "domain", "domain": "yourcompany.test"},
        )
        fixtures["specific"] = _create_skill_fixture(
            "specific",
            owner_uid=ORPHAN_OWNER,
            access_control={"type": "specific", "emails": [NONOWNER_EMAIL]},
        )
        fixtures["tagged"] = _create_skill_fixture(
            "tagged",
            owner_uid=ORPHAN_OWNER,
            access_control={"type": "tagged", "tags": [TAGGED_CLAIM]},
        )

        print("Setup OK")
        print(f"  owner         : {OWNER_EMAIL} uid={owner_uid}")
        print(f"  non-owner     : {NONOWNER_EMAIL} uid={nonowner_uid} tags={[TAGGED_CLAIM]}")
        for name, fid in fixtures.items():
            print(f"  fixture/{name:<8}: skills/{fid}")
        print()

        # ---- Rules-deployed probe ----------------------------------------
        # If anon can read the private fixture, rules are not deployed or
        # haven't propagated. Bail with exit 2 (setup error) and a clear
        # remediation message — this is infra broken, not rule drift.
        probe_status = _firestore_request(project, f"skills/{fixtures['private']}", method="GET")
        if probe_status == 200:
            _cleanup_fixtures(fixtures)
            _fail(
                "Rules-deployed probe: anon GET private skill returned 200. "
                "Firestore rules are NOT active on this project. Deploy them "
                "with `firebase deploy --only firestore:rules --project "
                f"{project}` and retry.",
                code=2,
            )
        if probe_status not in (401, 403):
            _cleanup_fixtures(fixtures)
            _fail(
                f"Rules-deployed probe: anon GET private returned unexpected {probe_status} "
                f"(expected 401 or 403). Check network connectivity and project ID.",
                code=2,
            )
        print(f"Rules probe OK (anon GET private → {probe_status})")
        print()

        r = Reporter()
        private_path = f"skills/{fixtures['private']}"
        public_path = f"skills/{fixtures['public']}"
        domain_path = f"skills/{fixtures['domain']}"
        specific_path = f"skills/{fixtures['specific']}"
        tagged_path = f"skills/{fixtures['tagged']}"
        tp_path = f"tool_permissions/rules-test-{uuid.uuid4().hex[:10]}"

        # --- Denials (negative branches) ---
        r.check(
            " 1. anon GET private skill",
            probe_status,  # same call as the probe; recorded for completeness
            "denied",
        )
        r.check(
            " 2. non-owner GET private skill",
            _firestore_request(project, private_path, method="GET", id_token=nonowner_token),
            "denied",
        )
        r.check(
            " 3. anon PATCH private skill",
            _firestore_request(
                project,
                private_path,
                method="PATCH",
                body={"fields": {"name": {"stringValue": "hacked"}}},
            ),
            "denied",
        )
        r.check(
            " 4. non-owner DELETE private skill",
            _firestore_request(project, private_path, method="DELETE", id_token=nonowner_token),
            "denied",
        )
        r.check(
            " 5. non-admin PATCH tool_permissions",
            _firestore_request(
                project,
                tp_path,
                method="PATCH",
                id_token=owner_token,
                body={"fields": {"note": {"stringValue": "should-be-denied"}}},
            ),
            "denied",
        )

        # --- Allows (positive branches of canAccessSkill) ---
        r.check(
            " 6. owner GET private skill",
            _firestore_request(project, private_path, method="GET", id_token=owner_token),
            "allowed",
        )
        r.check(
            " 7. anon GET public skill",
            _firestore_request(project, public_path, method="GET"),
            "allowed",
        )
        r.check(
            " 8. non-owner GET domain-match skill",
            _firestore_request(project, domain_path, method="GET", id_token=nonowner_token),
            "allowed",
        )
        r.check(
            " 9. non-owner GET specific-list skill",
            _firestore_request(project, specific_path, method="GET", id_token=nonowner_token),
            "allowed",
        )
        r.check(
            "10. non-owner GET tagged skill (via groupTags claim)",
            _firestore_request(project, tagged_path, method="GET", id_token=nonowner_token),
            "allowed",
        )

        # --- Owner write (covers update/delete branch positive) ---
        r.check(
            "11. owner DELETE private skill",
            _firestore_request(project, private_path, method="DELETE", id_token=owner_token),
            "allowed",
        )
        # Private fixture already deleted by check 11 — drop from cleanup map
        # so the admin-SDK sweep doesn't log a spurious failure.
        fixtures.pop("private", None)

        # --- Anonymous auth (workshop shared-tier) ---
        # Skipped non-fatally if anonymous auth is disabled on the project.
        # When enabled (M4 of sprint LOCAL-MODE-AND-FORK), workshop attendees
        # sign in anonymously and must be scoped to their own uid — they can
        # read public skills but not domain/specific/tagged/private ones,
        # and they cannot create skills (which would pollute the marketplace).
        anon_result = _sign_in_anonymous(api_key)
        if anon_result is None:
            print()
            print("SKIP  anon-auth checks — anonymous sign-in disabled on project")
            print("      (enable it in Firebase Console > Authentication > Sign-in method)")
        else:
            anon_token, anon_uid = anon_result
            print()
            print(f"  anon          : uid={anon_uid} (workshop shared-tier)")
            print()
            r.check(
                "12. anon-auth GET public skill",
                _firestore_request(project, public_path, method="GET", id_token=anon_token),
                "allowed",
            )
            r.check(
                "13. anon-auth GET domain skill (no email)",
                _firestore_request(project, domain_path, method="GET", id_token=anon_token),
                "denied",
            )
            r.check(
                "14. anon-auth GET specific skill (no email)",
                _firestore_request(project, specific_path, method="GET", id_token=anon_token),
                "denied",
            )
            r.check(
                "15. anon-auth GET tagged skill (no claims)",
                _firestore_request(project, tagged_path, method="GET", id_token=anon_token),
                "denied",
            )
            r.check(
                "16. anon-auth CREATE skill (isIdentified required)",
                _firestore_request(
                    project,
                    f"skills/anon-test-skill-{uuid.uuid4().hex[:8]}",
                    method="PATCH",  # PATCH-with-not-exist == create in Firestore REST
                    id_token=anon_token,
                    body={
                        "fields": {
                            "ownerId": {"stringValue": anon_uid},
                            "name": {"stringValue": "anon-should-fail"},
                            "accessControl": {"mapValue": {"fields": {"type": {"stringValue": "public"}}}},
                        }
                    },
                ),
                "denied",
            )

        print()
        total = len(r.rows)
        passed = sum(1 for ok, *_ in r.rows if ok)
        print(f"Summary: {passed}/{total} checks passed.")
        return r.exit_code()
    finally:
        _cleanup_fixtures(fixtures)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # No named environments in a template fork (_env.py is deployment-private):
    # fall back to flags / env vars / frontend/.env.local instead of --env.
    if ENVIRONMENTS:
        parser.add_argument("--env", choices=sorted(ENVIRONMENTS), default="dev")
    else:
        parser.add_argument("--env", default="", help=argparse.SUPPRESS)
    parser.add_argument("--project", dest="project", help="Override project ID.")
    parser.add_argument("--api-key", dest="api_key", help="Override Firebase Web API key.")
    args = parser.parse_args()
    sys.exit(run(args.env, args.project, args.api_key))


if __name__ == "__main__":
    main()

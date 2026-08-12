"""Deployed /api/auth/whoami smoke test — authenticated end-to-end.

Verifies that a Firebase custom claim (`groupTags`) set via the admin SDK
round-trips all the way through:
    admin SDK set_custom_user_claims
      -> Firebase mints fresh ID token via signInWithPassword REST
      -> frontend proxy forwards `Authorization: Bearer`
      -> backend `Depends(get_current_user)` verifies the JWT
      -> /api/auth/whoami echoes the claim back in the JSON body.

The test uses a dedicated Firebase user whose password we rotate on every
run — no persistent secret to manage. The user is created on first run,
reused on subsequent runs. Email/password sign-in must be enabled on the
target Firebase project (see [docs/ops/dev-accounts.md](../../docs/ops/dev-accounts.md)).

Usage:
    # Against deployed dev:
    uv run python scripts/whoami_smoke.py --env dev

    # Against a local backend (run `make dev` first):
    uv run python scripts/whoami_smoke.py --url http://127.0.0.1:1956

Exit codes:
    0   all assertions passed
    1   assertion failure (uid/email/groupTags mismatch or HTTP non-200)
    2   setup failure (can't reach Firebase admin, password sign-in off, ...)

Requires ADC with Firebase admin access on the target project
(`gcloud auth application-default login`).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.request

import firebase_admin
from firebase_admin import auth

# Allow `from _env import ENVIRONMENTS` both when run directly
# (`python scripts/whoami_smoke.py` puts scripts/ on sys.path) and when
# imported by pytest as `scripts.whoami_smoke` (backend/ is on pythonpath
# via pyproject; we still need scripts/ for the sibling `_env` import).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from _smoke_config import ENVIRONMENTS, SmokeConfigError, resolve, test_identity  # noqa: E402

# Dedicated smoke-test user. `.test` TLD is RFC 2606 reserved — no real
# deliverability, no collision with real users. Resolved rather than hardcoded
# so a template fork gets its own principal (see _smoke_config.test_identity).
TEST_EMAIL, TEST_DOMAIN, TEST_GROUP_TAGS = test_identity()
TEST_DISPLAY_NAME = "whoami smoke test"


def _fail(msg: str, code: int = 2) -> None:
    print(f"FAIL {msg}", file=sys.stderr)
    sys.exit(code)


def _ensure_user(password: str) -> str:
    """Idempotent: create or update the smoke-test user, return its uid."""
    try:
        user = auth.get_user_by_email(TEST_EMAIL)
        auth.update_user(user.uid, password=password)
    except auth.UserNotFoundError:
        user = auth.create_user(
            email=TEST_EMAIL,
            password=password,
            display_name=TEST_DISPLAY_NAME,
        )
    auth.set_custom_user_claims(user.uid, {"groupTags": TEST_GROUP_TAGS})
    return user.uid


def _sign_in(api_key: str, password: str) -> str:
    """Exchange email+password for a Firebase ID token via Identity Toolkit REST."""
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    payload = {"email": TEST_EMAIL, "password": password, "returnSecureToken": True}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        # Referer required: the api_security module restricts the Firebase
        # browser key to an allowlist; localhost is the smoke-tooling entry
        # (same as verify_rules.py — test 403'd without it, 2026-07-10).
        headers={"Content-Type": "application/json", "Referer": "http://localhost/"},
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        if "PASSWORD_LOGIN_DISABLED" in body:
            _fail(
                "password sign-in is disabled on this Firebase project. "
                "Enable it (see docs/ops/dev-accounts.md) or rerun the Terraform apply.",
                code=2,
            )
        _fail(f"signInWithPassword {exc.code}: {body[:200]}", code=2)
    return resp["idToken"]


def _whoami(base_url: str, id_token: str) -> tuple[int, dict]:
    url = base_url.rstrip("/") + "/api/proxy/api/auth/whoami"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {id_token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {"_body": exc.read().decode(errors="replace")[:200]}


def run(env: str | None, url: str | None, api_key: str | None, project_id: str | None) -> int:
    try:
        base_url, key, project = resolve(env=env, url=url, api_key=api_key, project_id=project_id)
    except SmokeConfigError as exc:
        _fail(str(exc))

    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={"projectId": project})

    password = secrets.token_urlsafe(24)
    uid = _ensure_user(password)
    id_token = _sign_in(key, password)
    status, body = _whoami(base_url, id_token)

    if status != 200:
        _fail(f"whoami returned {status}: {body}", code=1)
    expected = {
        "uid": uid,
        "email": TEST_EMAIL,
        "domain": TEST_DOMAIN,
        "groupTags": sorted(TEST_GROUP_TAGS),
    }
    if body != expected:
        _fail(f"whoami body mismatch.\n  expected={expected}\n  got={body}", code=1)

    print(f"OK   {base_url}/api/proxy/api/auth/whoami -> 200")
    print(f"     uid={uid} groupTags={body['groupTags']}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # In a template fork `_env.py` is absent, so there are no named environments
    # and `choices=[]` would reject every value. Only offer --env when we have
    # some; otherwise config comes from flags / env vars / frontend/.env.local.
    if ENVIRONMENTS:
        parser.add_argument("--env", choices=sorted(ENVIRONMENTS), help="Named environment (dev).")
    else:
        parser.add_argument("--env", help=argparse.SUPPRESS)
    parser.add_argument("--url", help="Frontend base URL (overrides env).")
    parser.add_argument("--api-key", dest="api_key", help="Firebase Web API key (overrides env).")
    parser.add_argument("--project-id", dest="project_id", help="Firebase project ID (overrides env).")
    args = parser.parse_args()
    sys.exit(run(args.env, args.url, args.api_key, args.project_id))


if __name__ == "__main__":
    main()

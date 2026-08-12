"""Integration test: /api/auth/whoami round-trip against a live Firebase project.

Exercises the full chain — admin SDK → custom claim → Firebase sign-in →
frontend proxy → backend JWT verification → whoami response. Complements the
unit tests in `tests/api_tests/test_auth_whoami.py` which mock the JWT check.

Gated on the `integration` marker and the `WHOAMI_SMOKE_ENV` env var so this
does not run in the default `make test-fast` path (which has no ADC).
Required to pass before we mark the AUTH-PERMISSIONS sprint closed and
before any future change to `auth/firebase_auth.py` ships to dev.

Run locally (dev):
    WHOAMI_SMOKE_ENV=dev uv run pytest tests/integration/test_whoami_deployed.py -v

Requires `gcloud auth application-default login` with Firebase admin access
on the target project, and email/password sign-in enabled on that project.
"""

from __future__ import annotations

import os

import pytest

from scripts.whoami_smoke import ENVIRONMENTS, run


@pytest.mark.integration
def test_whoami_round_trip_against_deployed_env() -> None:
    env = os.getenv("WHOAMI_SMOKE_ENV")
    if not env:
        pytest.skip("WHOAMI_SMOKE_ENV not set — skipping deployed whoami round-trip")
    if not ENVIRONMENTS:
        # Template fork: backend/scripts/_env.py is deployment-private, so there
        # are no named environments. Drive the smoke by URL instead
        # (`uv run python scripts/whoami_smoke.py --url ... --api-key ...`).
        pytest.skip("no named environments (backend/scripts/_env.py absent) — use --url/--api-key instead")
    assert env in ENVIRONMENTS, f"unknown env '{env}'. Known: {sorted(ENVIRONMENTS)}"
    rc = run(env=env, url=None, api_key=None, project_id=None)
    assert rc == 0, f"whoami_smoke.run({env!r}) exited {rc}"

"""Gate for tests/integration/: skip cleanly unless live GCP is armed.

Integration tests here require real GCP credentials AND the root
conftest's session-wide ``google.auth.default()`` stub to be disarmed
(both controlled by ``RUN_LIVE_GCP=1``). Without this gate, any pytest
invocation whose marker filter included them (e.g. ``-m "not slow"``)
crashed inside google-auth with ``Mock object has no attribute 'token'``
— a confusing red that looked like a code regression at every sprint
baseline (MODEL-RELIABILITY M0).

Run live deliberately with:

    RUN_LIVE_GCP=1 uv run pytest tests/integration/ -m integration
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _require_live_gcp(request):
    if os.environ.get("RUN_LIVE_GCP") == "1":
        return
    pytest.skip("live GCP disabled (set RUN_LIVE_GCP=1 to run integration tests)")

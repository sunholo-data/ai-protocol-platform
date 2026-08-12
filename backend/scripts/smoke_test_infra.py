#!/usr/bin/env python3
"""Live integration smoke test for Aitana v6 GCP infrastructure.

Complements verify_infra.py (config-level checks) by actually connecting to
each backing service and performing a real round-trip. Use this after a
terraform apply or secret rotation to confirm the stack is healthy
end-to-end. Idempotent: every test cleans up what it creates.

Checks (each can be run in isolation via --only):
  - secret       read AGENT_ENGINE_ID from Secret Manager
  - agent-engine create + get + delete a session on Vertex AI Agent Engine
  - firestore    write + read + delete a doc in the smoke_test/ collection
  - gcs          upload + download + delete a small blob in the logs bucket
  - bigquery     insert + query + delete a row in platform_telemetry
  - gemini       minimal generate_content call on Gemini Flash

Usage:
    # Full run against dev
    GOOGLE_CLOUD_PROJECT=your-project-id \\
    GOOGLE_CLOUD_LOCATION=europe-west1 \\
    uv run python scripts/smoke_test_infra.py

    # Single subtest (useful for debugging a specific service)
    GOOGLE_CLOUD_PROJECT=your-project-id \\
    uv run python scripts/smoke_test_infra.py --only agent-engine

    # Against test/prod (after those envs are applied)
    GOOGLE_CLOUD_PROJECT=your-project-id-test uv run python scripts/smoke_test_infra.py

Exit code 0 = all selected checks passed; non-zero = one or more failed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
import uuid
from collections.abc import Callable

# --- Terminal formatting -----------------------------------------------------

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"  {_GREEN}PASS{_RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {_RED}FAIL{_RESET} {msg}")


def _info(msg: str) -> None:
    print(f"  {_DIM}····{_RESET} {msg}")


def _skip(msg: str) -> None:
    print(f"  {_YELLOW}SKIP{_RESET} {msg}")


# --- Helpers -----------------------------------------------------------------


def _require_env(*names: str) -> dict[str, str]:
    """Return a dict of the named env vars; raise if any missing."""
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(f"missing required env var(s): {', '.join(missing)}")
    return {n: os.environ[n] for n in names}


def _secret_name(project: str, name: str = "AGENT_ENGINE_ID") -> str:
    return f"projects/{project}/secrets/{name}/versions/latest"


# --- Subtests ----------------------------------------------------------------
# Each subtest is (name, fn). fn() raises on failure and returns a short
# human-readable result string on success. They're callable independently so
# a targeted --only run doesn't pay for setup it doesn't need.


def test_secret() -> str:
    """Read AGENT_ENGINE_ID from Secret Manager — confirms SA has secretAccessor.

    If AGENT_ENGINE_ID is already set in the environment, this subtest trusts it
    and is effectively a bypass. That's intentional: local devs often can't read
    the secret directly (the role is granted to sa-platform, not to users) and
    impersonation would fail without `roles/iam.serviceAccountTokenCreator` on
    the SA — see docs/design/v6.0.0/infra-terraform-lessons.md §12.
    """
    env = _require_env("GOOGLE_CLOUD_PROJECT")

    # Fast path: trust env var if already set (injected by Cloud Run or CI).
    pre_set = os.environ.get("AGENT_ENGINE_ID")
    if pre_set and pre_set != "dummy_value":
        return f"AGENT_ENGINE_ID from env ...{pre_set[-40:]} (secret read skipped)"

    from google.api_core import exceptions as gax
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    try:
        resp = client.access_secret_version(name=_secret_name(env["GOOGLE_CLOUD_PROJECT"]))
    except gax.PermissionDenied as e:
        hint = (
            "Caller lacks secretmanager.versions.access. The sa-platform SA has this at project "
            "level, so Cloud Run will succeed. For local runs either (a) impersonate: "
            f"`gcloud auth application-default login --impersonate-service-account="
            f"sa-platform@{env['GOOGLE_CLOUD_PROJECT']}.iam.gserviceaccount.com` "
            "(requires roles/iam.serviceAccountTokenCreator on the SA — not granted by default), "
            "or (b) set AGENT_ENGINE_ID directly from `gcloud secrets versions access latest "
            f"--secret=AGENT_ENGINE_ID --project={env['GOOGLE_CLOUD_PROJECT']}` after granting "
            "yourself roles/secretmanager.secretAccessor."
        )
        raise RuntimeError(hint) from e

    payload = resp.payload.data.decode()
    if not payload or payload == "dummy_value":
        raise RuntimeError(f"AGENT_ENGINE_ID secret still has placeholder value: {payload!r}")
    if "reasoningEngines/" not in payload:
        raise RuntimeError(f"AGENT_ENGINE_ID does not look like a resource name: {payload!r}")
    # Stash for downstream subtests that would otherwise need to re-read.
    os.environ["AGENT_ENGINE_ID"] = payload
    return f"AGENT_ENGINE_ID = ...{payload[-40:]}"


def test_agent_engine() -> str:
    """Create + get + delete a session on Vertex AI Agent Engine."""
    env = _require_env("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION")
    # Lazy secret read if we weren't run after test_secret.
    agent_engine_id = os.environ.get("AGENT_ENGINE_ID")
    if not agent_engine_id:
        test_secret()  # populates AGENT_ENGINE_ID
        agent_engine_id = os.environ["AGENT_ENGINE_ID"]

    from google.adk.sessions import VertexAiSessionService

    svc = VertexAiSessionService(
        project=env["GOOGLE_CLOUD_PROJECT"],
        location=env["GOOGLE_CLOUD_LOCATION"],
        agent_engine_id=agent_engine_id,
    )
    user_id = f"smoke-{uuid.uuid4().hex[:8]}"
    app_name = "aitana_platform"

    # create
    import asyncio

    session = asyncio.run(svc.create_session(app_name=app_name, user_id=user_id))
    sid = session.id
    # get
    got = asyncio.run(svc.get_session(app_name=app_name, user_id=user_id, session_id=sid))
    if got is None or got.id != sid:
        raise RuntimeError(f"created session {sid} not retrievable")
    # delete (cleanup)
    asyncio.run(svc.delete_session(app_name=app_name, user_id=user_id, session_id=sid))
    return f"session {sid[:16]}… create/get/delete OK"


def test_firestore() -> str:
    """Write + read + delete a doc in smoke_test/ — confirms datastore.user."""
    env = _require_env("GOOGLE_CLOUD_PROJECT")
    from google.cloud import firestore

    client = firestore.Client(project=env["GOOGLE_CLOUD_PROJECT"])
    doc_id = f"smoke-{uuid.uuid4().hex[:8]}"
    ref = client.collection("smoke_test").document(doc_id)
    payload = {"ts": time.time(), "probe": "smoke_test_infra.py"}
    ref.set(payload)
    snap = ref.get()
    if not snap.exists or snap.to_dict().get("probe") != "smoke_test_infra.py":
        raise RuntimeError("round-trip mismatch")
    ref.delete()
    return f"smoke_test/{doc_id} round-trip OK"


def test_gcs() -> str:
    """Upload + download + delete a blob in the v6 logs bucket."""
    env = _require_env("GOOGLE_CLOUD_PROJECT")
    # Bucket name follows `${project_id}-${workspace}-${name}` convention.
    # Dev: your-project-id-dev-platform-logs
    # Env override wins — otherwise infer workspace from project suffix.
    bucket_name = os.environ.get("LOGS_BUCKET_NAME")
    if not bucket_name:
        proj = env["GOOGLE_CLOUD_PROJECT"]
        if proj.endswith("-dev"):
            workspace = "dev"
        elif proj.endswith("-test"):
            workspace = "test"
        elif proj.endswith("-production"):
            workspace = "prod"
        else:
            raise RuntimeError(f"cannot infer workspace from project {proj!r}; set LOGS_BUCKET_NAME")
        bucket_name = f"{proj}-{workspace}-platform-logs"

    from google.cloud import storage

    client = storage.Client(project=env["GOOGLE_CLOUD_PROJECT"])
    bucket = client.bucket(bucket_name)
    if not bucket.exists():
        raise RuntimeError(f"bucket gs://{bucket_name} does not exist")

    blob_name = f"smoke_test/{uuid.uuid4().hex}.txt"
    blob = bucket.blob(blob_name)
    probe = f"smoke probe {time.time()}"
    blob.upload_from_string(probe)
    got = blob.download_as_text()
    if got != probe:
        raise RuntimeError("GCS round-trip mismatch")
    blob.delete()
    return f"gs://{bucket_name}/{blob_name} round-trip OK"


def test_bigquery() -> str:
    """Insert + query + delete a row in platform_telemetry.smoke_probe."""
    env = _require_env("GOOGLE_CLOUD_PROJECT")
    from google.cloud import bigquery

    client = bigquery.Client(project=env["GOOGLE_CLOUD_PROJECT"])
    dataset_id = os.environ.get("TELEMETRY_DATASET", "platform_telemetry")
    table_id = "smoke_probe"
    full_table = f"{env['GOOGLE_CLOUD_PROJECT']}.{dataset_id}.{table_id}"

    # Create transient table (no-op if exists)
    schema = [bigquery.SchemaField("probe_id", "STRING"), bigquery.SchemaField("ts", "TIMESTAMP")]
    table = bigquery.Table(full_table, schema=schema)
    client.create_table(table, exists_ok=True)

    probe_id = f"smoke-{uuid.uuid4().hex[:8]}"
    # Use a query-insert so we don't hit streaming buffer (which blocks deletion 30m).
    insert_sql = f"INSERT INTO `{full_table}` (probe_id, ts) VALUES (@probe_id, CURRENT_TIMESTAMP())"
    job = client.query(
        insert_sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("probe_id", "STRING", probe_id)]
        ),
    )
    job.result()

    # Query back
    rows = list(
        client.query(
            f"SELECT probe_id FROM `{full_table}` WHERE probe_id = @probe_id",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("probe_id", "STRING", probe_id)]
            ),
        ).result()
    )
    if not rows or rows[0].probe_id != probe_id:
        raise RuntimeError("BQ round-trip mismatch")

    # Cleanup: drop the transient table entirely (cheaper than DELETE).
    client.delete_table(full_table, not_found_ok=True)
    return f"{dataset_id}.{table_id} insert/query/drop OK (probe={probe_id})"


def test_gemini() -> str:
    """Minimal generate_content against Gemini Flash via Vertex AI."""
    env = _require_env("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION")
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
    from google import genai

    client = genai.Client(vertexai=True, project=env["GOOGLE_CLOUD_PROJECT"], location="global")
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="Respond with exactly one word: 'ok'.",
    )
    text = (resp.text or "").strip().lower()
    if not text:
        raise RuntimeError("empty response")
    return f"gemini-2.5-flash responded ({len(text)} chars)"


# --- Runner ------------------------------------------------------------------

SUBTESTS: dict[str, Callable[[], str]] = {
    "secret": test_secret,
    "agent-engine": test_agent_engine,
    "firestore": test_firestore,
    "gcs": test_gcs,
    "bigquery": test_bigquery,
    "gemini": test_gemini,
}


def _run_one(name: str, fn: Callable[[], str]) -> bool:
    """Run a single subtest; return True on pass."""
    print(f"\n[{name}]")
    t0 = time.time()
    try:
        result = fn()
        _ok(f"{result} ({time.time() - t0:.2f}s)")
        return True
    except Exception as e:  # we want any failure to be reported, not raised
        _fail(f"{type(e).__name__}: {e} ({time.time() - t0:.2f}s)")
        if os.environ.get("SMOKE_VERBOSE"):
            traceback.print_exc()
        return False


def _warn_if_api_key_set() -> None:
    """google-genai prefers GOOGLE_API_KEY over ADC — and Agent Engine's session
    API rejects API-key auth with `401 CREDENTIALS_MISSING`. If the dev has an
    API key lying around in their shell, surface that before the confusing 401.
    """
    if os.environ.get("GOOGLE_API_KEY"):
        print(
            f"  {_YELLOW}WARN{_RESET} GOOGLE_API_KEY is set in env — google-genai will "
            "prefer it over ADC, and Agent Engine session APIs reject API-key auth "
            "(401 CREDENTIALS_MISSING). Unset it for this run:"
        )
        print(f"  {_DIM}    env -u GOOGLE_API_KEY uv run python scripts/smoke_test_infra.py ...{_RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--only",
        choices=list(SUBTESTS.keys()),
        action="append",
        help="Run only this subtest (repeatable). Default: run all.",
    )
    parser.add_argument(
        "--skip",
        choices=list(SUBTESTS.keys()),
        action="append",
        default=[],
        help="Skip this subtest (repeatable).",
    )
    args = parser.parse_args()

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "<unset>")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "<unset>")
    print(f"Smoke test: project={project} location={location}")
    if args.only:
        print(f"Running only: {', '.join(args.only)}")
    _info("Tip: set SMOKE_VERBOSE=1 for full tracebacks on failure")
    _warn_if_api_key_set()

    selected = args.only if args.only else list(SUBTESTS.keys())
    selected = [s for s in selected if s not in args.skip]

    results: dict[str, bool] = {}
    for name in selected:
        results[name] = _run_one(name, SUBTESTS[name])

    passed = sum(1 for ok in results.values() if ok)
    failed = len(results) - passed
    print(f"\n=== {passed} passed, {failed} failed of {len(results)} ===")
    for name, ok in results.items():
        marker = f"{_GREEN}✓{_RESET}" if ok else f"{_RED}✗{_RESET}"
        print(f"  {marker} {name}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

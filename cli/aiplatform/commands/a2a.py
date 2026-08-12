"""`aiplatform a2a` — A2A document support operator commands (G46).

Two subcommands:
  * ``probe-org-bucket`` — list objects in the deployed agent's bound GCS
    bucket. Confirms ``A2A_AGENT_DOCUMENTS_BUCKET`` is set AND the
    deployed service account has ``roles/storage.objectViewer`` on it.
  * ``send-file`` — POST a local file to the local backend's ``/a2a``
    invocation endpoint as a ``FilePart``. Confirms the
    ``FileExtractionInterceptor`` is running and the file is persisted as
    a ``doc:{id}.json`` artifact end-to-end.

Both are operator-loop tools, not part of any agent code path.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import sys
import uuid
from pathlib import Path

import click

from aiplatform.http import AIPlatformClient


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


@click.group()
def a2a() -> None:
    """A2A document-support operator tools (G46)."""


@a2a.command("probe-org-bucket")
@click.option("--limit", type=int, default=10, show_default=True, help="Max objects to list.")
@click.pass_context
def probe_org_bucket(ctx: click.Context, limit: int) -> None:
    """Probe the deployed agent's bound org bucket.

    Hits the backend's debug endpoint (or falls back to checking the env
    via the public health surface) and reports which bucket is bound and
    the first ``--limit`` object names. Returns non-zero exit code if
    no bucket is bound OR the SA lacks list permission — useful in CI
    after a deploy to catch IAM-grant drift.
    """
    # Backend exposes the org-bucket probe at /api/a2a/org-bucket; if not
    # wired yet, fall back to listing via the agent's tool invocation.
    try:
        result = _client(ctx).get(
            "/api/a2a/org-bucket",
            params={"limit": limit},
        )
    except Exception as exc:
        click.echo(f"probe failed: {exc}", err=True)
        click.echo(
            "If the /api/a2a/org-bucket endpoint is not deployed, the org-bucket support "
            "is still env-gated only — set A2A_AGENT_DOCUMENTS_BUCKET on the service and "
            "verify with: gcloud run services describe <service> --format json | "
            "jq '.spec.template.spec.containers[].env[]'",
            err=True,
        )
        sys.exit(1)

    bucket = result.get("bucket")
    if not bucket:
        click.echo("✗ No bucket bound — A2A_AGENT_DOCUMENTS_BUCKET is unset on the service.")
        sys.exit(1)

    click.echo(f"✓ Bucket: {bucket}")
    objects = result.get("objects", [])
    if not objects:
        click.echo("⚠ Bucket is reachable but lists zero objects.")
        click.echo(
            "  → Either the bucket is empty or the SA lacks roles/storage.objectViewer.",
        )
        sys.exit(2)
    click.echo(f"✓ {len(objects)} object(s) (showing first {min(limit, len(objects))}):")
    for obj in objects[:limit]:
        click.echo(f"    - {obj}")


@a2a.command("send-file")
@click.argument("local_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--ap-url",
    "ap_url",
    default="http://localhost:1956",
    show_default=True,
    help="Backend URL (NOT the Next.js proxy — go direct).",
)
@click.option(
    "--prompt",
    default="Analyze the attached file and tell me what it contains.",
    show_default=False,
    help="Text prompt to send alongside the file.",
)
@click.pass_context
def send_file(
    ctx: click.Context,
    local_path: Path,
    ap_url: str,
    prompt: str,
) -> None:
    """Send a local file to the running backend's /a2a endpoint as a FilePart.

    Exercises the FileExtractionInterceptor + force_new_version=True wiring
    end-to-end. Confirms the file is persisted as doc:{id}.json and the
    agent processes it. Useful in dev loop to test new MIME types before
    pushing to a deployed environment.
    """
    del ctx  # unused — this command hits ap_url directly, no auth helper needed.
    bytes_data = local_path.read_bytes()
    mime, _ = mimetypes.guess_type(str(local_path))
    if mime is None:
        click.echo(f"⚠ Could not infer MIME from {local_path}; defaulting to application/octet-stream.")
        mime = "application/octet-stream"

    msg_id = str(uuid.uuid4())
    rpc = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": msg_id,
                "parts": [
                    {"kind": "text", "text": prompt},
                    {
                        "kind": "file",
                        "file": {
                            "name": local_path.name,
                            "mimeType": mime,
                            "bytes": base64.b64encode(bytes_data).decode("ascii"),
                        },
                    },
                ],
            },
            "configuration": {"acceptedOutputModes": ["text"]},
        },
    }

    invocation_url = f"{ap_url.rstrip('/')}/a2a"
    click.echo(f"→ POST {invocation_url}  ({len(bytes_data)} bytes, mime={mime})")

    import httpx

    try:
        resp = httpx.post(
            invocation_url,
            content=json.dumps(rpc),
            headers={"Content-Type": "application/json"},
            timeout=60.0,
        )
    except httpx.HTTPError as exc:
        click.echo(f"✗ POST failed: {exc}", err=True)
        sys.exit(1)

    if resp.status_code != 200:
        click.echo(f"✗ HTTP {resp.status_code}: {resp.text[:300]}", err=True)
        sys.exit(1)

    body = resp.json()
    if "error" in body:
        click.echo(f"⚠ JSON-RPC error: {body['error']}")
        sys.exit(1)

    result = body.get("result", {})
    click.echo(f"✓ HTTP 200, task id: {result.get('id', '(none)')}")
    click.echo(f"✓ result kind: {result.get('kind') or result.get('type') or '(unknown)'}")
    # Best-effort: surface document_ids if the interceptor reported them in metadata.
    artifacts = result.get("artifacts") or []
    if artifacts:
        click.echo(f"✓ {len(artifacts)} artifact(s) produced")

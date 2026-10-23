"""Real-model synthetic image trial; in-memory session, NOT deployed auth proof.

Run from backend with uv run --no-sync scripts/verify_visual_evidence.py --help.
Only repository synthetic fixtures and previously captured PNGs are used. Model
recipients must be explicitly approved; this never writes deployment config.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adk.agui import APP_NAME
from auth import User
from tools import visual_evidence as ve


async def run(args) -> dict:
    root = Path(__file__).resolve().parents[2]
    inputs = []
    for fixture, image in [("clipped-label", "clipped"), ("correct-label", "corrected")]:
        source = (root / "tests/fixtures/visual-feedback" / f"{fixture}.svg").read_text().strip()
        data = (args.evidence / f"{image}.png").read_bytes()
        revision = hashlib.sha256(source.encode()).hexdigest()
        inputs.append((source, data, revision))
    os.environ.update(
        VISUAL_EVIDENCE_ENABLED="true",
        VISUAL_EVIDENCE_ALLOWED_UIDS="synthetic-verifier",
        VISUAL_EVIDENCE_SYNTHETIC_SOURCE_DIGESTS=",".join(item[2] for item in inputs),
        VISUAL_EVIDENCE_SYNTHETIC_CAPTURE_PAIRS=",".join(
            f"{revision}:{hashlib.sha256(data).hexdigest()}" for _, data, revision in inputs
        ),
        VISUAL_EVIDENCE_MODEL=args.model,
        VISUAL_EVIDENCE_APPROVED_MODEL_REGIONS=args.approved_model_regions,
        GOOGLE_CLOUD_PROJECT=args.project,
        GCP_PROJECT=args.project,
        GOOGLE_CLOUD_LOCATION=args.location,
        GOOGLE_GENAI_USE_VERTEXAI="true",
    )
    # Fail closed on recipient configuration before loading the synthetic session.
    ve.approved_model()
    service = InMemorySessionService()
    session = await service.create_session(app_name=APP_NAME, user_id="synthetic-verifier")
    original_service = ve.get_session_service
    ve.get_session_service = lambda: service
    results = []
    try:
        for source, data, revision in inputs:
            await service.append_event(
                session=session,
                event=Event(
                    author="assistant",
                    content=types.Content(role="model", parts=[types.Part.from_text(text=f"```svg\n{source}\n```")]),
                ),
            )
            results.append(
                await ve.inspect_asset(
                    ve.InspectRequest(
                        session_id=session.id,
                        source_revision=revision,
                        png_base64=base64.b64encode(data).decode(),
                        criteria="Is the label on the right fully visible? Describe exactly what its pixels show.",
                    ),
                    User(uid="synthetic-verifier"),
                )
            )
    finally:
        ve.get_session_service = original_service
    return {
        "status": "assessed",
        "scope": "Real approved model, synthetic browser captures, in-memory source session",
        "limitations": "Not deployed authentication, automatic revision, or a universal visual correctness evaluation.",
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--location", default="europe-west1")
    parser.add_argument("--approved-model-regions", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), indent=2))
    except Exception as exc:
        # Never echo SDK request bodies or credentials in a failed trial.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "detail": getattr(exc, "detail", "Real-model trial unavailable"),
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

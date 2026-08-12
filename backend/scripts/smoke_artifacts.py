"""ADK artifact service round-trip smoke test.

Usage:
    ADK_ARTIFACT_BUCKET=your-project-id-artifacts uv run python scripts/smoke_artifacts.py
"""

import asyncio
import json
import os

from google.adk.artifacts import GcsArtifactService
from google.genai.types import Blob, Part


async def main() -> None:
    bucket = os.environ.get("ADK_ARTIFACT_BUCKET", "your-project-id-artifacts")
    print(f"Testing artifact round-trip on bucket: {bucket}")

    svc = GcsArtifactService(bucket_name=bucket)
    payload = json.dumps([{"type": "paragraph", "text": "smoke test"}]).encode()
    part = Part(inline_data=Blob(data=payload, mime_type="application/json"))

    ver = await svc.save_artifact(
        app_name="aitana_platform",
        user_id="smoke-user",
        session_id="smoke-session",
        filename="smoke.json",
        artifact=part,
    )
    print(f"Written version {ver}")

    read = await svc.load_artifact(
        app_name="aitana_platform",
        user_id="smoke-user",
        session_id="smoke-session",
        filename="smoke.json",
    )
    assert read is not None, "artifact not found after write"
    decoded = json.loads(read.inline_data.data)
    assert decoded[0]["text"] == "smoke test", f"unexpected content: {decoded}"
    print("Artifact round-trip OK")


if __name__ == "__main__":
    asyncio.run(main())

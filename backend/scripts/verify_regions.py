"""MODEL-RELIABILITY M3 — probe every {model, location} pair in the chains.

Per-region model availability drifts (verified 2026-07-08:
``gemini-flash-lite-latest`` 404s in EU regions while the pinned 2.5 id
serves fine), so cross-region rungs in models.yaml are only trustworthy
if probed. This sends a 1-token generate to each Gemini chain entry in
its pinned region (plus each Gemini primary in the default region) and
fails loudly on any 404/permission error — wired as ``make verify-regions``.

Usage:  uv run python scripts/verify_regions.py
Env:    GOOGLE_CLOUD_PROJECT (defaults to your-project-id for laptops)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DEFAULT_REGION = "europe-west1"


def main() -> int:
    from google.genai import Client, types

    from config.models import load_models_config

    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or "your-project-id"
    cfg = load_models_config()

    # Collect unique (api_name, location) pairs from primaries + chain rungs.
    pairs: dict[tuple[str, str], str] = {}
    by_id = {m.id: m for m in cfg.models}
    for entry in cfg.models:
        if entry.provider != "google":
            continue
        # An entry with its own pinned `location` (the `-eu` entries added
        # 2026-08-13 for Gemini 3.x models whose only EU option is the "eu"
        # jurisdictional multi-region endpoint) is probed there. Otherwise
        # global-residency models serve ONLY at location="global" (runtime
        # routes them there via RegionalGemini — see adk.agent.resolve_model).
        if entry.location:
            primary_loc = entry.location
        elif entry.residency == "global":
            primary_loc = "global"
        else:
            primary_loc = DEFAULT_REGION
        if entry.fallbacks:
            pairs[(entry.api_name, primary_loc)] = f"{entry.id} (primary)"
        for link in entry.fallbacks:
            target = by_id.get(link.id)
            if target is None or target.provider != "google":
                continue
            location = link.location or DEFAULT_REGION
            pairs[(target.api_name, location)] = f"{entry.id} -> {link.id}@{location}"

    print(f"Probing {len(pairs)} {{model, region}} pairs on project {project}…")
    failures: list[str] = []
    for (api_name, location), label in sorted(pairs.items()):
        client = Client(vertexai=True, project=project, location=location)
        try:
            client.models.generate_content(
                model=api_name,
                contents="ping",
                config=types.GenerateContentConfig(max_output_tokens=1),
            )
            print(f"  OK   {api_name:32s} @ {location:15s} ({label})")
        except Exception as exc:
            print(f"  FAIL {api_name:32s} @ {location:15s} ({label}): {str(exc)[:140]}")
            failures.append(f"{api_name}@{location}")

    if failures:
        print(f"\nFAILED pairs: {failures}")
        print("Fix models.yaml chains (or regional availability) before shipping — trap: silent region drift.")
        return 1
    print("\nAll chain {model, region} pairs verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Seed Firestore mcp_servers/ collection with MCP server configs.

Idempotent: re-running with the same args is safe (uses set with merge=False so
the document is replaced, not appended). Reads optional URL overrides from
flags so the same script seeds local-dev (localhost:3001) and deployed
(Cloud Run sidecar URL) without code changes.

Usage:
    # Local dev (default env: dev — points at localhost:3001/mcp)
    uv run python scripts/seed_mcp_servers.py

    # A DEPLOYED env (issue #14: test had never been seeded because this
    # script could only target dev — every env needs its own run, with the
    # env's own Cloud Run URL for ext-apps-map):
    uv run python scripts/seed_mcp_servers.py --env test \\
        --public-url https://mcp-ext-apps-map-<hash>-ew.a.run.app/mcp

    # Dry run
    uv run python scripts/seed_mcp_servers.py --env test --dry-run

The seeded server is then activated per-skill by adding its id to the
SkillConfig's tool_configs.mcp.servers list (handled by seed_skills.py
or the skill admin UI; not this script's concern). The set of ids this
script can seed MUST stay in sync with
admin.mcp_registry_check.KNOWN_SEEDABLE_SERVER_IDS — the post-seed
deploy gate and the PR-time unit test both pin against that set.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._smoke_config import ENVIRONMENTS, pin_project_for_env, project_for_env

# Firestore module — bound in main() AFTER --env pins the project, because
# db.firestore reads GCP_PROJECT at import (gotcha_gcp_project_env_shadow).
fs: Any = None

COLLECTION = "mcp_servers"
# G42 (template-mcp-strict-resolution.md): default to the IPv4 loopback,
# NOT `localhost`. Node's DNS can resolve `localhost` to ::1 (IPv6)
# while the MCP server may bind 0.0.0.0 (IPv4-only), producing a silent
# fetch-failed at agent-run time that looks like "MCP server returned
# no tools." Using 127.0.0.1 explicitly avoids the trap. Forks running
# against a non-loopback target MUST pass --public-url explicitly so
# the override is auditable in the script's stdout.
DEFAULT_LOCAL_URL = "http://127.0.0.1:3001/mcp"

EXT_APPS_MAP_CONFIG = {
    "name": "Geo / 3D Globe (ext-apps map-server)",
    "transport": "http",
    "headers": {},
    "source_repo": "https://github.com/modelcontextprotocol/ext-apps",
    "source_path": "examples/map-server",
    "source_commit": "0008d3b7",  # ext-apps 1.7.1; pinned in M1 fixture capture
    "operated_by": "aitana",
    "tags": ["geo", "visualization", "mcp-app"],
}

# MCP Toolbox (v6.14.0) — Google's database gateway, run as a SIDECAR container
# inside platform-frontend rather than as its own Cloud Run service.
#
# The URL is loopback in BOTH local dev and deployed, because a sidecar shares
# the instance's network namespace. That is the whole point: no per-env URL to
# drift (env_config_parity_not_in_code), no Cloud Run IAM hop, no ID token to
# mint or refresh, no cold start in the TTFT path. `headers` stays empty and
# there is NO auth block — the registry needs no new code to reach it.
#
# 127.0.0.1 (not `localhost`) for the same G42 reason as above: `localhost` can
# resolve to ::1 while the server binds IPv4, and the failure is a silent
# "MCP server returned no tools" at agent-run time.
#
# Toolbox's default port is 5000. The path is TOOLSET-SCOPED — /mcp/<toolset>
# serves only that toolset's tools, which is how a fork exposes a different
# per-client toolset without touching the registry.
TOOLBOX_URL = "http://127.0.0.1:5000/mcp/example"
TOOLBOX_CONFIG = {
    "name": "Toolbox — ONE MarketData PPA prices",
    "transport": "http",
    "headers": {},
    "source_repo": "https://github.com/googleapis/mcp-toolbox",
    "source_path": "infrastructure/mcp-toolbox/tools.yaml",
    "source_commit": "v1.7.0",  # upstream image tag pinned in the Dockerfile
    "operated_by": "aitana",
    "tags": ["database", "bigquery", "ppa", "sidecar"],
}

# v6.23.0 ONE-BQ — the SAME sidecar, a DIFFERENT toolset path. Toolbox serves
# each toolset at /mcp/<toolset>, which is the whole reason a second capability
# needs no second container, no second port and no infra change: only a second
# registry row pointing one path over.
#
# Kept as a separate mcp_servers/ document rather than folded into toolbox
# because the registry document is the ACCESS unit — a skill declares server ids,
# so two documents let one-ppa-expert keep exactly its two curated MarketData tools
# while one-bigquery gets the generic executors, with no overlap. Merging them
# would hand every MarketData consumer arbitrary SQL by accident.
TOOLBOX_BQ_URL = "http://127.0.0.1:5000/mcp/one-bigquery"
TOOLBOX_ONE_BQ_CONFIG = {
    "name": "Toolbox — ONE BigQuery (scoped ad-hoc query)",
    "transport": "http",
    "headers": {},
    "source_repo": "https://github.com/googleapis/mcp-toolbox",
    "source_path": "infrastructure/mcp-toolbox/tools.yaml",
    "source_commit": "v1.7.0",
    "operated_by": "aitana",
    "tags": ["database", "bigquery", "adhoc", "sidecar"],
}


# Maps Grounding Lite (v6.23.0 MAPS-GROUNDING) — Google's geospatial grounding
# service, reached as a plain remote MCP server.
#
# WHY THIS AND NOT ADK's `google_maps_grounding`: the native Vertex tool is
# contractually unavailable to us. Its terms read "Service not available for
# customers with billing addresses in the European Economic Area (EEA)", and
# Aitana Labs bills from the EEA. Grounding Lite is the path Google documents
# for EEA customers. Do NOT "simplify" this back to the built-in tool.
#
# Two structural bonuses over the native tool, both of which we'd otherwise pay
# for: it is an ordinary MCP toolset, so (a) it coexists with FunctionTools and
# needs no AgentTool sub-agent wrapper (the native built-in is a model-level tool
# that cannot share a request with function tools — see tools/search_agent.py),
# and (b) it works on Claude and OpenAI skill agents, not just Gemini.
#
# The API key rides as ${MAPS_GROUNDING_API_KEY} — a NAME, resolved from the
# environment at toolset-build time. Never write the key itself here; see
# "Secret-bearing headers" in tools/mcp/registry.py.
MAPS_GROUNDING_URL = "https://mapstools.googleapis.com/mcp"
MAPS_GROUNDING_CONFIG = {
    "name": "Google Maps Grounding Lite",
    "url": MAPS_GROUNDING_URL,
    "transport": "http",  # streamable HTTP
    "headers": {"X-Goog-Api-Key": "${MAPS_GROUNDING_API_KEY}"},
    "source_repo": "https://developers.google.com/maps/ai/grounding-lite",
    "operated_by": "google",
    "tags": ["geo", "maps", "weather", "routes", "grounding"],
}


def seed_maps_grounding(*, dry_run: bool = False) -> None:
    """Seed the Maps Grounding Lite entry.

    Takes no URL override: the endpoint is Google's, identical in every
    environment. What DOES differ per env is the ``MAPS_GROUNDING_API_KEY``
    secret behind the header reference — that is mounted by Cloud Run, not
    seeded here, so this row promotes cleanly across envs.
    """
    if dry_run:
        print(f"[dry-run] would write mcp_servers/maps-grounding-lite: url={MAPS_GROUNDING_URL}")
        return
    fs.set_document(COLLECTION, "maps-grounding-lite", MAPS_GROUNDING_CONFIG)
    print(f"Seeded mcp_servers/maps-grounding-lite: url={MAPS_GROUNDING_URL}")


def seed_ext_apps_map(url: str | None, *, dry_run: bool = False) -> None:
    """Seed ext-apps-map. ``url`` None means 'no explicit URL given'.

    FOOTGUN GUARD: this script targets the Firestore of whichever env ``--env``
    selected (default dev). A bare invocation used to write the localhost
    DEFAULT over ext-apps-map's DEPLOYED Cloud Run URL, silently breaking it
    (the deployed backend can't reach localhost:3001). So: only write
    ext-apps-map's URL when one is EXPLICITLY given (``--url``/``--public-url``).
    With no explicit URL we PRESERVE whatever is already there — a bare run that
    just wants to (re)seed the loopback Toolbox entry can no longer clobber it.
    """
    if url is None:
        existing = fs.get_document(COLLECTION, "ext-apps-map")
        if existing and existing.get("url"):
            print(f"ext-apps-map: preserving existing url={existing['url']} (no --url/--public-url given)")
            return
        url = DEFAULT_LOCAL_URL  # first-time local seed, nothing to clobber
    config = {**EXT_APPS_MAP_CONFIG, "url": url}
    if dry_run:
        print(f"[dry-run] would write mcp_servers/ext-apps-map: url={url}")
        return
    fs.set_document(COLLECTION, "ext-apps-map", config)
    print(f"Seeded mcp_servers/ext-apps-map: url={url}")


def seed_toolbox(url: str = TOOLBOX_URL, *, dry_run: bool = False) -> None:
    """Seed the MCP Toolbox sidecar entry.

    Takes no ``--public-url``: the sidecar is loopback-only by construction, so
    a non-loopback URL would mean someone had exposed the database gateway to
    the network — which the design forbids. Same URL in every environment.
    """
    config = {**TOOLBOX_CONFIG, "url": url}
    if dry_run:
        print(f"[dry-run] would write mcp_servers/toolbox: url={url}")
        return
    fs.set_document(COLLECTION, "toolbox", config)
    print(f"Seeded mcp_servers/toolbox: url={url}")


def seed_toolbox_bq(url: str = TOOLBOX_BQ_URL, *, dry_run: bool = False) -> None:
    """Seed the scoped ad-hoc BigQuery toolset (v6.23.0 ONE-BQ).

    Same sidecar as ``seed_toolbox``, same loopback-only rationale — the only
    difference is the toolset path. Takes no ``--public-url`` for the same reason:
    a non-loopback URL would mean the database gateway had been exposed to the
    network, which the design forbids.
    """
    config = {**TOOLBOX_ONE_BQ_CONFIG, "url": url}
    if dry_run:
        print(f"[dry-run] would write mcp_servers/toolbox-bq: url={url}")
        return
    fs.set_document(COLLECTION, "toolbox-bq", config)
    print(f"Seeded mcp_servers/toolbox-bq: url={url}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env",
        # No named environments in a template fork (backend/scripts/_env.py is
        # deployment-private). `choices=[]` would reject every value, so only
        # constrain when we actually have a set; the project then comes from
        # GOOGLE_CLOUD_PROJECT via project_for_env().
        choices=sorted(ENVIRONMENTS) or None,
        default="dev",
        help=(
            "Target environment (default: dev). Issue #14: every env's "
            "mcp_servers/ registry needs its OWN seed run — Firestore state "
            "never promotes with code, and a missing registry doc hard-500s "
            "every skill that declares the server (G42)."
        ),
    )
    url_group = parser.add_mutually_exclusive_group()
    url_group.add_argument(
        "--url",
        default=None,
        help=(
            f"MCP server URL (default: {DEFAULT_LOCAL_URL}). "
            "DEPRECATED for non-loopback URLs — use --public-url so the "
            "override is explicit in the script invocation."
        ),
    )
    url_group.add_argument(
        "--public-url",
        default=None,
        help=(
            "Explicit non-loopback URL (e.g. the deployed Cloud Run URL). "
            "Required for any URL that isn't 127.0.0.1/localhost so a "
            "stray `--url <public>` invocation can't silently re-target "
            "the seed at a wrong environment."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written, don't touch Firestore",
    )
    args = parser.parse_args()

    # Pin the project BEFORE importing db.firestore (it reads GCP_PROJECT at
    # import — gotcha_gcp_project_env_shadow). Exits loudly on a mismatch.
    pin_project_for_env(args.env)
    global fs
    from db import firestore as fs

    # G42: resolve which URL to seed with. None = 'no explicit URL' → preserve an
    # existing ext-apps-map URL rather than downgrade it to the localhost default.
    url = args.public_url or args.url
    print(f"seed_mcp_servers: env = {args.env} ({project_for_env(args.env)})")
    print(f"seed_mcp_servers: ext-apps-map url = {url or '(preserve existing / local default)'}")
    seed_ext_apps_map(url, dry_run=args.dry_run)
    # The Toolbox sidecar's URL is loopback in every env (see TOOLBOX_CONFIG),
    # so it is deliberately NOT affected by --url / --public-url — those exist to
    # re-target ext-apps-map at a deployed service.
    seed_toolbox(dry_run=args.dry_run)
    seed_toolbox_bq(dry_run=args.dry_run)
    # Google-operated endpoint, same URL in every env — like the Toolbox rows it
    # is unaffected by --url / --public-url.
    seed_maps_grounding(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

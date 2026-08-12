"""MCP-registry consistency check — the issue #14 seed-drift safeguard.

Why this exists: G42 strict resolution (adk/tools.resolve_mcp_tools) hard-500s
an ENTIRE skill at agent-build time when its SKILL.md declares an MCP server
the env's Firestore ``mcp_servers/`` registry can't satisfy. That is the right
fail-loud behaviour at runtime — but the misconfiguration itself is SEED DRIFT
(per-env Firestore state never promotes with code), and it has shipped silently
more than once: test's registry was missing ``ext-apps-map`` entirely, so the
first ONE-team request to web-researcher died with a 500 (2026-07-21, issue
#14).

This module closes the gap at the two moments the drift can be caught early:

* **Deploy time** — ``admin.platform_seed.seed()`` runs :func:`verify_mcp_registry`
  after every seed and reports ``mcp_missing`` / ``mcp_warnings`` in the
  SeedSummary. The Cloud Build seed step FAILS THE BUILD on a non-empty
  ``mcp_missing`` — a deploy that would ship hard-500 skills never goes green.
* **PR time** — ``tests/unit/test_mcp_registry_check.py`` asserts every server
  id declared in any template is one this repo's seed tooling actually knows
  how to seed (:data:`KNOWN_SEEDABLE_SERVER_IDS`), catching typos and
  seed-support-less declarations before they merge.

Ad-hoc / pre-promotion use: ``uv run python scripts/verify_mcp_registry.py
--env test`` (wraps this module; part of the promotion audit).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

COLLECTION = "mcp_servers"

# Every server id the repo's seed tooling can put into an env's registry.
# MUST stay in sync with the catalog in scripts/seed_mcp_servers.py — the
# unit test pins template declarations to this set, and the seed script
# references it as the source of truth for "what can I seed".
KNOWN_SEEDABLE_SERVER_IDS = {"ext-apps-map", "toolbox", "toolbox-bq", "maps-grounding-lite"}

# Servers whose URL is loopback IN EVERY ENV by design (in-service sidecars
# sharing the instance's network namespace). A loopback URL on any OTHER
# server in a deployed env is drift: the deployed backend can't reach it, and
# the failure mode is a silent "MCP server returned no tools" at run time.
LOOPBACK_BY_DESIGN = {"toolbox", "toolbox-bq"}


def declared_servers_by_skill(templates_root: Path) -> dict[str, list[str]]:
    """Map skill template name -> MCP server ids its SKILL.md declares.

    Malformed templates are skipped here — ``seed()`` already surfaces them
    in ``SeedSummary.failed``; double-reporting would just add noise.
    """
    from admin.platform_seed import _parse_template  # local import: avoid cycle

    declared: dict[str, list[str]] = {}
    if not templates_root.is_dir():
        return declared
    for child in sorted(templates_root.iterdir()):
        skill_md = child / "SKILL.md"
        if not (child.is_dir() and skill_md.exists()):
            continue
        try:
            parsed = _parse_template(skill_md)
        except Exception:
            continue
        tool_configs = (parsed.get("metadata") or {}).get("toolConfigs") or {}
        servers = (tool_configs.get("mcp") or {}).get("servers") or []
        if servers:
            declared[parsed["name"]] = [str(s) for s in servers]
    return declared


def verify_mcp_registry(templates_root: Path, *, deployed: bool | None = None) -> dict[str, Any]:
    """Check every template-declared MCP server against the env's registry.

    Args:
        templates_root: The skills/templates directory to scan.
        deployed: Whether to apply the loopback-URL-on-deployed-env warning.
            ``None`` auto-detects via the Cloud Run ``K_SERVICE`` env var
            (correct for the in-service post-seed call); pass ``True`` when
            auditing a deployed env from a laptop.

    Returns:
        ``{"ok": bool, "mcp_missing": [..], "mcp_warnings": [..],
        "declared": {skill: [ids]}}``. ``mcp_missing`` entries are
        ``"<skill> -> <server_id>"`` strings for registry docs that are absent
        or url-less — each one is a skill that would hard-500 (G42).
        ``mcp_warnings`` are non-fatal latent problems (loopback drift).
    """
    from db import firestore as fs  # local import: keeps module import side-effect free

    if deployed is None:
        deployed = bool(os.environ.get("K_SERVICE"))

    declared = declared_servers_by_skill(templates_root)
    missing: set[str] = set()
    warnings: set[str] = set()
    cache: dict[str, dict[str, Any] | None] = {}

    for skill, server_ids in declared.items():
        for sid in server_ids:
            if sid not in cache:
                try:
                    cache[sid] = fs.get_document(COLLECTION, sid)
                except Exception as e:  # registry unreadable ≠ registry empty
                    logger.warning("mcp_registry_check: read failed for %s: %s", sid, e)
                    warnings.add(f"{sid}: registry read failed ({e}) — cannot verify")
                    cache[sid] = {}
                    continue
            doc = cache[sid]
            if doc == {}:  # read-failed marker from above
                continue
            if not doc or not doc.get("url"):
                missing.add(f"{skill} -> {sid}")
                continue
            url = str(doc["url"])
            if deployed and sid not in LOOPBACK_BY_DESIGN and ("127.0.0.1" in url or "localhost" in url):
                warnings.add(
                    f"{skill} -> {sid}: url={url} is loopback but this env is deployed — "
                    "the tool will silently resolve to no tools (re-point with "
                    "scripts/seed_mcp_servers.py --env <env> --public-url <cloud-run-url>)"
                )

    return {
        "ok": not missing,
        "mcp_missing": sorted(missing),
        "mcp_warnings": sorted(warnings),
        "declared": declared,
    }

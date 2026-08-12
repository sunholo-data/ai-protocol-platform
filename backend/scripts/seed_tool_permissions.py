"""Seed Firestore with tool-permission documents (dev / test / prod).

The ``tool_permissions`` collection is the *tool-invocation* access plane
(enforced per tool call in ``adk/callbacks.py`` via
``auth.permissions.can_use_tool``). Doc id is a user email, an email domain,
or ``*`` (wildcard); shape ``{type, tools[], denied[]}`` — see
``auth/permissions.py``. Lookup is user → domain → wildcard → deny, and the
FIRST matching doc wins: **a domain doc SHADOWS the wildcard entirely** (the
wildcard is never consulted once a domain doc matches). So a domain grant must
be a *superset* of everything that domain's users touch — including whatever the
wildcard used to provide.

Presets / flags:

* ``--one`` — seed the **acme-energy.example** (ONE) domain doc. Grants ``["*"]``
  by default; ``--one --explicit`` uses the ``ONE_TOOLS`` allowlist instead.
  ONE is a trusted customer tenant; the REAL access boundary is each skill's
  ``access_control: tagged [ONE]`` gate (which decides what a ONE user can even
  load), so the tool-permission doc is defense-in-depth.

  **Per-env policy (decided 2026-07-17):**
    - **dev / test → ``["*"]``** (``--one``). Matches the ``yourcompany.com``
      precedent; keeps dev iteration frictionless (and test's wildcard is open
      anyway).
    - **prod → explicit ``ONE_TOOLS`` allowlist** (``--one --explicit``).
      Least-privilege on the customer-facing env.

  The explicit list is a **silent-403 risk** if it drifts: a ONE user's front
  door (``one-assistant``) delegates to shared skills (``web-researcher``,
  ``claude-assistant`` → ``deep-reasoner``, ``gpt-assistant`` →
  ``openai-reasoner``), and the enforcer keys on the USER not the skill, so the
  list must track the whole delegation graph PLUS framework tools that are
  auto-wired but still gated (``load_artifacts`` / ``retrieve_artifact``, absent
  from every skill YAML) PLUS AgentTool names (``web_search_agent`` /
  ``enterprise_search_agent``) PLUS MCP tool names (``toolbox_*``). That risk is
  contained by ``tests/unit/test_seed_tool_permissions.py``, which fails CI if a
  ONE-reachable skill declares a tool ``ONE_TOOLS`` omits — so the allowlist
  stays a maintained superset. **When that test flags a new tool, re-seed prod.**

* ``--domain <d>`` / ``--email <e>`` / ``--wildcard`` — the original knobs.
* ``--tools`` — override the granted tool list (default ``["*"]`` for
  user/domain, ``DEFAULT_TOOLS`` for wildcard).

Usage::

    # Fix ONE under-permissioning on dev/test (["*"], the common case):
    uv run python scripts/seed_tool_permissions.py --env dev --one
    uv run python scripts/seed_tool_permissions.py --env test --one

    # Prod: explicit least-privilege allowlist (dry-run first):
    uv run python scripts/seed_tool_permissions.py --env prod --one --explicit --dry-run

    # Baseline domain + wildcard (original behaviour):
    uv run python scripts/seed_tool_permissions.py --env dev --domain yourcompany.com --wildcard

Per-env note: tool-permission docs are Firestore state that a code merge does
NOT carry (see docs/ops/env-config-parity.md). Run this per env you seed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add backend root to path so ``db`` / ``scripts`` import cleanly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

COLLECTION = "tool_permissions"

# env -> GCP project. dev/test are live for v6; production is a future env cut
# (v6 is dev/test-only until proven — see docs/ops/env-config-parity.md).
_ENV_PROJECTS = {
    "dev": "your-project-id",
    "test": "your-project-id-test",
    "prod": "your-project-id-prod",
}

# Provenance marker written onto every seeded doc (mirrors the platform_seed
# convention already present on the test wildcard). Extra fields are ignored by
# the admin ToolPermissionDoc reader, so this is safe to carry.
CREATED_BY = "seed_tool_permissions"

# Baseline tools granted by the wildcard doc. The wildcard is the fallback for
# EVERY authenticated user with no more-specific doc — including untrusted /
# unknown domains — so keep it tight.
DEFAULT_TOOLS = [
    "google_search",
    "web_search_agent",  # web-researcher's AI-search tool; needed for delegated research handoffs
    "code_execution",
]

# --- ONE (acme-energy.example) grant --------------------------------------------
ONE_DOMAIN = "acme-energy.example"

# The explicit union of every tool a ONE user can reach — the PROD grant
# (``--one --explicit``); dev/test use ["*"] (see the module docstring). Kept a
# maintained superset by the CI drift guard in
# tests/unit/test_seed_tool_permissions.py. Sourced from:
#   - the 4 ONE skills (one-assistant, one-ppa-expert, one-doc-compare,
#     one-obligation-analysis);
#   - the shared skills one-assistant delegates to (web-researcher,
#     claude-assistant, gpt-assistant, deep-reasoner, openai-reasoner);
#   - framework tools auto-wired by the agent factory but still gated;
#   - the AgentTool + v6.14.0 MCP Toolbox tool names.
ONE_TOOLS = [
    # wildcard baseline — a domain doc SHADOWS the wildcard, so re-grant these
    "google_search",
    "code_execution",
    "web_search_agent",
    "enterprise_search_agent",  # AgentTool sibling of web_search_agent
    # framework tools auto-wired by adk/agent.py (not in any skill YAML) but gated
    "load_artifacts",
    "retrieve_artifact",
    # ONE PPA / document tools
    "ai_search",
    "list_documents",
    "list_bucket_documents",
    "get_document_content",
    "extract_ppa_clauses",
    "compare_ppa_contracts",
    "map_ppa_obligations",
    "entsoe_day_ahead_prices",
    # web-researcher / mid + deep reasoner delegates
    "url_processing",
    # v6.14.0 MCP Toolbox sidecar (toolbox)
    "popular_baby_names",
    "names_by_decade_source",
    # v6.23.0 ONE-BQ — scoped ad-hoc BigQuery (toolbox-bq). Two tools because
    # ONE's datasets span two BigQuery regions and a Toolbox source declares
    # exactly one: `market` = market_prices + entsoe (europe-west4), `analysis` =
    # analysis + deal_tracker + storage_models (europe-west1). Schema discovery is
    # INFORMATION_SCHEMA through these same two tools — Toolbox's dedicated
    # list-tables/get-table-info tools resolve the dataset against the BILLING
    # project, so they cannot see a cross-project dataset at all.
    "bq_market_query",
    "bq_analysis_query",
]


def build_docs(
    *,
    domain: str | None,
    email: str | None,
    tools: list[str] | None,
    wildcard: bool,
    one: bool,
    one_explicit: bool,
) -> list[tuple[str, dict]]:
    """Build the ``(doc_id, data)`` list to write. Pure — no Firestore side effects."""
    docs: list[tuple[str, dict]] = []

    if one:
        docs.append(
            (
                ONE_DOMAIN,
                {
                    "type": "domain",
                    "tools": list(ONE_TOOLS) if one_explicit else ["*"],
                    "denied": [],
                    "created_by": CREATED_BY,
                },
            )
        )

    if domain:
        docs.append(
            (
                domain,
                {
                    "type": "domain",
                    "tools": tools or ["*"],
                    "denied": [],
                    "created_by": CREATED_BY,
                },
            )
        )

    if wildcard:
        docs.append(
            (
                "*",
                {
                    "type": "wildcard",
                    "tools": tools or list(DEFAULT_TOOLS),
                    "denied": [],
                    "created_by": CREATED_BY,
                },
            )
        )

    if email:
        docs.append(
            (
                email,
                {
                    "type": "user",
                    "tools": tools or ["*"],
                    "denied": [],
                    "created_by": CREATED_BY,
                },
            )
        )

    return docs


def _pin(env: str) -> None:
    """Pin GCP_PROJECT/GOOGLE_CLOUD_PROJECT to ``env`` BEFORE db.firestore imports.

    Uses ``scripts._env.pin_project_for_env`` for the live envs (dev/test). prod
    is not in ``_env.ENVIRONMENTS`` yet (v6 prod is a future cut), so it is pinned
    inline with the same "don't silently rewrite a mismatching GCP_PROJECT" guard.
    """
    if env in ("dev", "test"):
        from scripts._smoke_config import pin_project_for_env

        pin_project_for_env(env)
        return

    if env == "prod":
        expected = _ENV_PROJECTS["prod"]
        current = os.environ.get("GCP_PROJECT")
        if current and current != expected:
            print(
                f"ERROR: GCP_PROJECT is set to {current!r} but --env prod targets {expected!r}.\n"
                f"  Unset GCP_PROJECT, or run with GCP_PROJECT={expected} explicitly.",
                file=sys.stderr,
            )
            sys.exit(2)
        os.environ["GCP_PROJECT"] = expected
        os.environ["GOOGLE_CLOUD_PROJECT"] = expected
        print(
            "WARNING: v6 prod may not be stood up yet — confirm the env is cut before "
            "seeding (see docs/ops/env-config-parity.md prod-readiness checklist).",
            file=sys.stderr,
        )
        return

    raise ValueError(f"Unknown env {env!r}; expected one of {sorted(_ENV_PROJECTS)}")


def seed(env: str, docs: list[tuple[str, dict]], dry_run: bool) -> None:
    """Write ``docs`` to the target env's Firestore (or print them on --dry-run)."""
    _pin(env)  # env-var only; safe on dry-run and validates the env/project

    if dry_run:
        for doc_id, data in docs:
            print(f"  DRY   [{env}] {COLLECTION}/{doc_id} → {data}")
        print("Done (dry-run — no Firestore writes).")
        return

    from db import firestore as fs  # imported AFTER _pin so it reads the right project

    for doc_id, data in docs:
        fs.set_document(COLLECTION, doc_id, data, merge=False)
        print(f"  SEED  [{env}] {COLLECTION}/{doc_id} → {data}")

    print("Done.")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed tool_permissions in Firestore.")
    p.add_argument(
        "--env",
        choices=sorted(_ENV_PROJECTS),
        default="dev",
        help="Target environment (default: dev). Maps to your-project-id-<env>.",
    )
    p.add_argument("--one", action="store_true", help="Seed the acme-energy.example (ONE) domain doc")
    p.add_argument(
        "--explicit",
        action="store_true",
        help="With --one: grant the explicit ONE_TOOLS allowlist instead of ['*'] (fragile — see docstring)",
    )
    p.add_argument("--domain", default=None, help="Domain to grant tools to (e.g. yourcompany.com)")
    p.add_argument("--email", default=None, help="Specific user email to grant tools to")
    p.add_argument(
        "--tools",
        nargs="*",
        default=None,
        help="Tool names to grant (default: all '*' for user/domain, baseline set for wildcard)",
    )
    p.add_argument("--wildcard", action="store_true", help="Also seed a wildcard (*) doc")
    p.add_argument("--dry-run", action="store_true", help="Print what would be seeded, write nothing")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if not (args.one or args.domain or args.email or args.wildcard):
        print("ERROR: at least one of --one, --domain, --email, or --wildcard is required.")
        sys.exit(2)

    docs = build_docs(
        domain=args.domain,
        email=args.email,
        tools=args.tools,
        wildcard=args.wildcard,
        one=args.one,
        one_explicit=args.explicit,
    )

    if args.dry_run:
        print("DRY RUN — no Firestore writes")
    seed(env=args.env, docs=docs, dry_run=args.dry_run)

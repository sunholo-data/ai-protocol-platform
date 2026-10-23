"""Seed explicit user, domain or baseline wildcard tool permissions.

A matching domain grant shadows the wildcard completely. Supply --tools for
least-privilege grants and use --dry-run before writing deployment state.
Private deployments may provide their own presets through extensions.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

COLLECTION = "tool_permissions"
CREATED_BY = "seed_tool_permissions"
_ENV_PROJECTS = {"dev": "your-project-id", "test": "your-project-id-test", "prod": "your-project-id-prod"}
DEFAULT_TOOLS = ["google_search", "web_search_agent", "code_execution"]
FRAMEWORK_GATED_TOOLS = [
    "load_artifacts",
    "retrieve_artifact",
    "load_memory",
    "preload_memory",
    "remember",
    "forget",
    "list_projects",
    "create_project",
    "add_to_project",
    "remove_from_project",
    "grep_project_documents",
    "search_project_documents",
    "search_documents",
    "send_a2ui_json_to_client",
    "request_confirmation",
]


def build_docs(*, domain=None, email=None, tools=None, wildcard=False):
    """Build permission documents without writing Firestore."""
    docs = []
    for doc_id, kind in ((domain, "domain"), ("*" if wildcard else None, "wildcard"), (email, "user")):
        if doc_id:
            granted = tools if tools is not None else (list(DEFAULT_TOOLS) if kind == "wildcard" else ["*"])
            docs.append((doc_id, {"type": kind, "tools": list(granted), "denied": [], "created_by": CREATED_BY}))
    return docs


def _pin(env: str) -> None:
    """Pin GCP_PROJECT/GOOGLE_CLOUD_PROJECT to ``env`` BEFORE db.firestore imports.

    Delegates to ``scripts._smoke_config.pin_project_for_env`` for EVERY env
    this deployment names, so there is exactly one env->project map
    (``_env.ENVIRONMENTS``) rather than two that can drift apart.

    prod used to be special-cased here on the premise that it "is a future env
    cut" and therefore absent from ``_env.ENVIRONMENTS``. That premise expired
    when prod was cut: the inline branch went on pinning the module-level
    ``_ENV_PROJECTS["prod"]``, which in this repo is the TEMPLATE PLACEHOLDER,
    so ``--env prod`` targeted a project that does not exist and seeding prod
    was impossible. Found during the test -> prod readiness audit.

    ``_ENV_PROJECTS`` survives as the ``--choices`` source and as the fallback
    for a template fork, where ``_env.py`` is deployment-private and absent —
    there ``pin_project_for_env`` resolves from the ambient project instead.
    """
    from scripts._smoke_config import ENVIRONMENTS, pin_project_for_env

    if env not in _ENV_PROJECTS:
        raise ValueError(f"Unknown env {env!r}; expected one of {sorted(_ENV_PROJECTS)}")

    if ENVIRONMENTS and env not in ENVIRONMENTS:
        # A named-environment deployment that simply hasn't added this env yet.
        # Fail loudly rather than fall through to a placeholder project.
        print(
            f"ERROR: env {env!r} is not in scripts/_env.py ENVIRONMENTS "
            f"(known: {', '.join(sorted(ENVIRONMENTS))}). Add it before seeding.",
            file=sys.stderr,
        )
        sys.exit(2)

    pin_project_for_env(env)


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=sorted(_ENV_PROJECTS), default="dev")
    parser.add_argument("--domain")
    parser.add_argument("--email")
    parser.add_argument("--wildcard", action="store_true")
    parser.add_argument("--tools", nargs="*")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not (args.domain or args.email or args.wildcard):
        parser.error("select --domain, --email, or --wildcard")
    seed(
        args.env,
        build_docs(domain=args.domain, email=args.email, tools=args.tools, wildcard=args.wildcard),
        args.dry_run,
    )


from extensions import deployment_tool_permissions  # noqa: E402

_preset = deployment_tool_permissions()
if _preset is not None:
    globals().update(_preset.COMPAT_API)

if __name__ == "__main__":
    main()

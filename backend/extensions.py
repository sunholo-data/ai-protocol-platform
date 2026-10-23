"""Optional private extensions — the seam between public and private code.

platform-source has a private tier: any path under a ``private/`` directory
(``**/private/**``) is excluded from the public template, alongside
``deploy/**``. Private forks get it on merge-down; the public template never
sees it. See docs/design/template/template-private-tier.md.

Public code must never import ``private.*`` directly — in the public template
the package does not exist and the import would crash at startup. It asks this
module instead, and every function here is a no-op when ``backend/private`` is
absent. ``private/scripts/publish_public.py check`` (CI) fails on any direct
import that bypasses this seam.

Conventions for a private extension package ``backend/private/<name>/``:

* ``routes.py`` exposing ``router`` (a FastAPI ``APIRouter``) — mounted by
  :func:`include_private_routers`.
* ``tools.py`` exposing ``TOOL_REGISTRY`` (name → factory, same shape as
  ``adk.tools.TOOL_REGISTRY``) and optionally ``TOOL_CATALOG`` (same shape as
  ``adk.tools.TOOL_CATALOG``) — merged by :func:`private_tools`.
* ``validators.py`` exposing ``validate_skill(skill, user)`` — called by
  :func:`validate_skill` before a skill is created or updated; it raises
  (typically ``HTTPException``) to refuse the save.
* ``events.py`` exposing ``on_document_event(event, payload)`` — called by
  :func:`document_event` after a library document or folder changes (see
  there for the events). Observational: it must not block or raise.
* ``project_search.py`` exposing ``make_toolsets()`` (ADK toolsets every agent
  gets — :func:`private_toolsets`) and ``search_status(uid, project_id)`` (a
  project's search readiness — :func:`project_search_status`).

A private module that fails to import is logged loudly and skipped: one broken
extension must not take down the public surface. Validators are the exception:
they guard access, so one that fails to import refuses the save rather than
letting it through unchecked.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

_PACKAGE = "private"


def private_packages() -> list[str]:
    """Names of the extension packages under ``backend/private``; [] if absent."""
    try:
        spec = importlib.util.find_spec(_PACKAGE)
    except (ImportError, ValueError):
        return []
    if spec is None or not spec.submodule_search_locations:
        return []
    return sorted(m.name for m in pkgutil.iter_modules(list(spec.submodule_search_locations)) if m.ispkg)


def _optional_module(package: str, module: str) -> Any | None:
    name = f"{_PACKAGE}.{package}.{module}"
    if importlib.util.find_spec(f"{_PACKAGE}.{package}") is None:
        return None
    try:
        if importlib.util.find_spec(name) is None:
            return None
        return importlib.import_module(name)
    except Exception:
        log.exception("private extension %s failed to import; skipping it", name)
        return None


def include_private_routers(app: Any) -> list[str]:
    """Mount every private package's ``routes.router``. Returns the package names mounted."""
    mounted: list[str] = []
    for pkg in private_packages():
        mod = _optional_module(pkg, "routes")
        router = getattr(mod, "router", None) if mod is not None else None
        if router is None:
            continue
        app.include_router(router)
        mounted.append(pkg)
    if mounted:
        log.info("private extensions: mounted routers from %s", ", ".join(mounted))
    return mounted


class ExtensionUnavailable(RuntimeError):
    """A private validator exists but could not be loaded — fail closed."""


def validate_skill(skill: dict[str, Any], user: Any) -> None:
    """Run every private package's ``validators.validate_skill(skill, user)``.

    ``skill`` is the skill as it will be stored (camelCase keys: ``skillMetadata``,
    ``accessControl``, ``ownerId``). A validator refuses by raising; this
    re-raises. A validator module that is present but fails to import raises
    :class:`ExtensionUnavailable` instead of being skipped.
    """
    for pkg in private_packages():
        name = f"{_PACKAGE}.{pkg}.validators"
        try:
            if importlib.util.find_spec(name) is None:
                continue
            mod = importlib.import_module(name)
        except Exception as exc:
            log.exception("private validator %s failed to import; refusing the save", name)
            raise ExtensionUnavailable(f"{name} failed to import") from exc
        check = getattr(mod, "validate_skill", None)
        if check is not None:
            check(skill, user)


DOCUMENT_EVENTS = frozenset({"upserted", "moved", "deleted", "folder_deleted", "projects_changed", "project_opened"})


def document_event(event: str, payload: dict[str, Any]) -> None:
    """Tell private packages a library document or folder changed.

    Events and payloads (``uid`` always present):

    * ``upserted`` — a document was (re)parsed: ``doc_id``, ``folder_id``.
    * ``moved`` — ``doc_id``, ``from_folder_id``, ``folder_id``.
    * ``deleted`` — ``doc_id``, ``folder_id``.
    * ``folder_deleted`` — ``folder_id``.
    * ``projects_changed`` — a document's project tags changed (tagged,
      untagged, or its project deleted): ``doc_id``. Read the document for its
      current ``projectIds`` and ``folderId`` (projects.md §6.3; the knowledge
      index re-indexes it with ``project:<id>`` tags in P2).
    * ``project_opened`` — a project's page was opened, or a chat turn started
      inside it: ``project_id``. A chance to catch up work for that project
      (the knowledge index backfills its documents, projects.md §11 D4).

    NEVER raises: a document operation must not fail because an extension did.
    Handlers record their own failures somewhere the user can see them.
    """
    if event not in DOCUMENT_EVENTS:
        raise ValueError(f"unknown document event {event!r}")
    for pkg in private_packages():
        mod = _optional_module(pkg, "events")
        handler = getattr(mod, "on_document_event", None) if mod is not None else None
        if handler is None:
            continue
        try:
            handler(event, payload)
        except Exception:
            log.exception("private document-event handler %s.events failed on %s", pkg, event)


def private_tools() -> tuple[dict[str, Callable[[dict], Any]], list[dict[str, str]]]:
    """Merged ``(TOOL_REGISTRY, TOOL_CATALOG)`` contributions of every private package."""
    registry: dict[str, Callable[[dict], Any]] = {}
    catalog: list[dict[str, str]] = []
    for pkg in private_packages():
        mod = _optional_module(pkg, "tools")
        if mod is None:
            continue
        for name, factory in (getattr(mod, "TOOL_REGISTRY", None) or {}).items():
            if name in registry:
                log.error("private tool %r from %s shadows another private tool; skipping", name, pkg)
                continue
            registry[name] = factory
        catalog.extend(getattr(mod, "TOOL_CATALOG", None) or [])
    return registry, catalog


def private_toolsets() -> list[Any]:
    """ADK toolsets every agent gets from private packages (``project_search.
    make_toolsets()``) — e.g. project-scoped document search, which offers its
    tool only inside a project chat. ``[]`` in the public template; a package
    whose factory fails is logged and skipped."""
    out: list[Any] = []
    for pkg in private_packages():
        mod = _optional_module(pkg, "project_search")
        factory = getattr(mod, "make_toolsets", None) if mod is not None else None
        if factory is None:
            continue
        try:
            out.extend(factory() or [])
        except Exception:
            log.exception("private toolsets from %s.project_search failed; skipping them", pkg)
    return out


def project_search_status(uid: str, project_id: str) -> dict[str, Any] | None:
    """A project's search readiness — ``{indexed, failed, total, status}`` — from
    the first private package that provides it, or ``None`` (the public template,
    or a failure, which is logged). The project page shows it; never raises."""
    for pkg in private_packages():
        mod = _optional_module(pkg, "project_search")
        fn = getattr(mod, "search_status", None) if mod is not None else None
        if fn is None:
            continue
        try:
            return dict(fn(uid, project_id))
        except Exception:
            log.exception("project search status from %s.project_search failed", pkg)
            return None
    return None


def access_tags(name: str) -> list[str] | None:
    """Tags a private package supplies for ``access_control: {type: tagged, tags_from: <name>}``
    in a template skill (e.g. ``user_guide`` → the deployment's guide tags).
    ``None`` when no package knows ``name``; the seeder then skips that skill."""
    for pkg in private_packages():
        mod = _optional_module(pkg, "seed")
        fn = getattr(mod, "access_tags", None) if mod is not None else None
        if fn is None:
            continue
        tags = fn(name)
        if tags is not None:
            return list(tags)
    return None


def after_seed(*, dry_run: bool = False) -> dict[str, Any]:
    """Run each private package's ``seed.after_seed`` once the platform skills are
    seeded (every deploy and promotion). Returns their reports, merged, for the
    seed summary. NEVER raises: a hook's failure is reported, not fatal."""
    out: dict[str, Any] = {}
    for pkg in private_packages():
        mod = _optional_module(pkg, "seed")
        fn = getattr(mod, "after_seed", None) if mod is not None else None
        if fn is None:
            continue
        try:
            out.update(fn(dry_run=dry_run) or {})
        except Exception as exc:
            log.exception("private seed hook %s.seed failed", pkg)
            out[pkg] = {"state": "error", "reason": f"{type(exc).__name__}: {exc}"}
    return out


def deployment_tool_permissions() -> Any | None:
    """Optional deployment permission presets; absent in the public template."""
    for package in private_packages():
        module = _optional_module(package, "tool_permissions")
        if module is not None:
            return module
    return None


def deployment_local_skills(now: float, load_instructions: Callable) -> list[dict]:
    """Additional LOCAL_MODE fixtures from private deployment packages."""
    result: list[dict] = []
    for package in private_packages():
        module = _optional_module(package, "local_fixture")
        if module is not None:
            result.extend(module.skills(now, load_instructions))
    return result


def outside_project_tools() -> set[str]:
    """Private tools without project-document isolation are denied in project chats."""
    names: set[str] = set()
    for package in private_packages():
        module = _optional_module(package, "project_scope")
        if module is not None:
            names.update(module.OUTSIDE_DOCUMENT_TOOLS)
    return names

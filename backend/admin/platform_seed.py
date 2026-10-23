"""Seed the five default platform-owned skills into Firestore.

Called by POST /api/admin/seed-platform-skills, which is hit once per
deploy by the Cloud Build seed step. Idempotent: any template whose
`name` already exists as a platform-owned skill is skipped, so repeat
runs are safe (and the expected steady state).

Template layout (one directory per skill):
    backend/skills/templates/<name>/SKILL.md    # YAML frontmatter + markdown body

The frontmatter supplies name/description/metadata; the body is the
agent instruction. Platform-owned skills are always created with
owner_id=PLATFORM_OWNER_UID and accessControl={type: public}.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from config.local_mode import is_local_mode
from db import firestore as fs
from skills import skill_config
from skills.platform import PLATFORM_OWNER_UID
from skills.slugify import slugify, unique_slug

logger = logging.getLogger(__name__)

# Email recorded as the owner of platform-seeded skills.
# Resolved lazily by _resolve_owner_email() so module import never raises —
# the validation fires at seed() call time where the error message is actionable.
PLATFORM_OWNER_EMAIL = os.environ.get("PLATFORM_OWNER_EMAIL", "platform@yourcompany.com")
DEFAULT_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "skills" / "templates"

# G17 (template-fork-ergonomics.md): demo-skill names that ship with the
# template but are NOT seeded for forks by default. The flag below
# (``_INCLUDE_DEMO_SKILLS``) gates whether these directories' SKILL.md
# files are imported into Firestore. The platform repo's own deploys
# default to including them (the workshop demos + dev fixtures depend
# on this); a public-template fork flips ``_INCLUDE_DEMO_SKILLS=false``
# at sanitize time so a freshly-deployed fork starts with zero seeded
# skills instead of seven inherited workshop demos the fork author
# never asked for.
DEMO_SKILL_NAMES: frozenset[str] = frozenset(
    {
        "code-assistant",
        "data-extractor",
        "document-analyst",
        "general-assistant",
        # Needs a Vertex AI Search datastore that only the operator can create,
        # so it must not seed by default — an unconfigured datastore_id fails at
        # call time with an opaque 400 instead of an actionable message.
        "knowledge-search",
        # Same reason as knowledge-search: needs operator-only config a fork
        # cannot have. Maps Grounding Lite requires a Maps Platform API key in
        # the env's Secret Manager plus an mcp_servers/maps-grounding-lite row;
        # without both, G42 strict resolution hard-500s the skill. Seeding it
        # into a fresh fork would ship a skill that is broken on first click.
        "maps-assistant",
        "web-researcher",
        "workspace-demo",
        "workspace-demo-interactive",
    }
)


# Provider → the env var whose presence means the provider is enabled here.
# Mirrors adk.agent._PROVIDER_KEY_ENVS: cloudbuild mounts the key only when
# _ENABLE_ANTHROPIC / _ENABLE_OPENAI is "true", so key presence IS the flag.
_PROVIDER_KEY_ENVS: dict[str, str] = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


def _pinned_provider(metadata: dict[str, Any]) -> str | None:
    """Provider of the skill's pinned ``metadata.model`` (None if unpinned).

    Registry ids and tier names resolve via the model registry; raw api names
    fall back to prefix inference, the same way the runtime does.
    """
    ref = metadata.get("model")
    if not ref:
        return None
    from config.models import api_name_for, entry_for

    entry = entry_for(str(ref))
    if entry is not None:
        return entry.provider
    api = api_name_for(str(ref))
    if api.startswith("claude-"):
        return "anthropic"
    if api.startswith(("gpt-", "o3")):
        return "openai"
    return "google"


def _disabled_provider(metadata: dict[str, Any]) -> str | None:
    """The pinned provider if its key isn't mounted in this env, else None.

    A skill pinned to a disabled provider would silently fall back to Gemini
    while its name and description still claim Claude/GPT —
    so the seeder skips it rather than ship a skill that misdescribes itself.
    """
    provider = _pinned_provider(metadata)
    key_env = _PROVIDER_KEY_ENVS.get(provider or "")
    if key_env and not os.environ.get(key_env):
        return provider
    return None


def _include_demo_skills() -> bool:
    """Return True iff the inherited demo bundle should be seeded.

    Default ``true`` preserves backwards compatibility for the platform
    repo's own dev/test/prod deploys (workshop relies on the demos).
    The sanitize pipeline flips this to ``false`` for the public template
    so forks get a clean slate.
    """
    raw = os.environ.get("_INCLUDE_DEMO_SKILLS", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _json_env(name: str) -> Any:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON: {exc}") from exc


def _seed_default_access() -> dict:
    """Access for a template skill whose SKILL.md declares none.

    ``PLATFORM_SEED_DEFAULT_ACCESS`` (JSON accessControl) — e.g.
    ``{"type": "private"}`` for a deployment where nothing should be visible
    until an operator grants it. Unset keeps the legacy ``public``.
    """
    return _json_env("PLATFORM_SEED_DEFAULT_ACCESS") or {"type": "public"}


def _seed_access_overrides() -> dict[str, dict]:
    """Per-skill access set by the deployment, keyed by skill ``name``.

    ``PLATFORM_SEED_ACCESS_OVERRIDES`` (JSON ``{name: accessControl}``) wins over
    the SKILL.md frontmatter on create AND on every refresh, so a fork can decide
    who sees a template skill without editing the template file.
    """
    overrides = _json_env("PLATFORM_SEED_ACCESS_OVERRIDES") or {}
    if not isinstance(overrides, dict) or not all(isinstance(v, dict) for v in overrides.values()):
        raise ValueError("PLATFORM_SEED_ACCESS_OVERRIDES must map skill names to accessControl objects")
    return overrides


def _seed_avatar_overrides() -> dict[str, str]:
    """Per-skill avatar set by the deployment, keyed by skill ``name``.

    ``PLATFORM_SEED_AVATAR_OVERRIDES`` (JSON ``{name: "/path/under/public.webp"}``)
    wins over the SKILL.md ``avatar`` on create AND refresh, so a fork can brand
    the template's skills without editing them (pairs with the theme hook).
    """
    overrides = _json_env("PLATFORM_SEED_AVATAR_OVERRIDES") or {}
    if not isinstance(overrides, dict) or not all(isinstance(v, str) and v for v in overrides.values()):
        raise ValueError("PLATFORM_SEED_AVATAR_OVERRIDES must map skill names to non-empty avatar paths")
    return overrides


def _declared_avatar(parsed: dict) -> str:
    """The deployment's avatar for this skill, else its frontmatter's, else ""."""
    return _seed_avatar_overrides().get(parsed["name"]) or parsed.get("avatar") or ""


class AccessUnresolved(ValueError):
    """A template's ``tags_from`` resolved to no tags on this deployment."""


def _resolve_tags_from(access: dict | None) -> dict | None:
    """``{type: tagged, tags_from: <name>}`` → ``{type: tagged, tags: [...]}`` with
    the tags a private package supplies (``extensions.access_tags``), or the
    template's explicit fallback tags. A skill whose tags can't be resolved is
    NOT seeded, rather than seeded public or tag-less."""
    if not access or "tags_from" not in access:
        return access
    import extensions

    name = str(access.get("tags_from") or "")
    tags = extensions.access_tags(name) or access.get("tags")
    if not tags:
        raise AccessUnresolved(f"access tags_from {name!r} resolved to no tags on this deployment")
    return {**{k: v for k, v in access.items() if k != "tags_from"}, "type": "tagged", "tags": tags}


def _declared_access(parsed: dict) -> dict | None:
    """The deployment override for this skill, else its frontmatter, else None."""
    return _seed_access_overrides().get(parsed["name"]) or parsed.get("access_control")


@dataclass
class SeedSummary:
    created: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    tool_permissions_wildcard_seeded: bool = False
    # G16 (template-fork-ergonomics.md): the refresh+purge phases.
    refreshed: int = 0  # existing skills whose template fields were updated
    purged: int = 0  # skills owned by a previous-owner UID that were deleted
    preserved: int = 0  # v6.9.0 9.2: durable (managed_by=firestore) skills left untouched
    # Issue #14 safeguard: post-seed MCP-registry consistency. Non-empty
    # mcp_missing means a template declares an MCP server this env's
    # mcp_servers/ registry can't satisfy — the skill would hard-500 at agent
    # build (G42). The Cloud Build seed step FAILS THE BUILD on it.
    mcp_missing: list[str] = field(default_factory=list)
    mcp_warnings: list[str] = field(default_factory=list)
    # Skills pinned to a provider whose key isn't mounted. Never
    # created; an existing template-managed row is deleted.
    skipped_disabled_provider: list[str] = field(default_factory=list)
    # Templates this fork has opted out of (templates/.fork-skip) — removed
    # from the marketplace and excluded from the MCP-registry gate.
    skipped_by_fork: list[str] = field(default_factory=list)
    # Why a template was skipped when it wasn't a demo, e.g. its `tags_from`
    # resolved to nothing on this deployment (NEVER SILENT).
    notes: list[str] = field(default_factory=list)
    # Reports from private packages' after-seed hooks (extensions.after_seed),
    # e.g. {"user_guide": {"state": "queued", ...}}.
    extensions: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "skipped": self.skipped,
            "failed": self.failed,
            "tool_permissions_wildcard_seeded": self.tool_permissions_wildcard_seeded,
            "refreshed": self.refreshed,
            "purged": self.purged,
            "preserved": self.preserved,
            "mcp_missing": self.mcp_missing,
            "mcp_warnings": self.mcp_warnings,
            "skipped_disabled_provider": self.skipped_disabled_provider,
            "skipped_by_fork": self.skipped_by_fork,
            "notes": self.notes,
            "extensions": self.extensions,
        }


def _parse_template(skill_md: Path) -> dict[str, Any]:
    """Parse a SKILL.md file into a dict with `name`, `description`, `instructions`, `metadata`.

    Raises ValueError on malformed frontmatter.
    """
    text = skill_md.read_text()
    if not text.startswith("---"):
        raise ValueError(f"missing frontmatter in {skill_md}")

    # Split on the closing --- of the frontmatter. [0] is "", [1] is the
    # frontmatter YAML, [2]+ is the body.
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"missing frontmatter close fence in {skill_md}")

    try:
        front = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML frontmatter in {skill_md}: {e}") from e

    if "name" not in front:
        raise ValueError(f"frontmatter missing 'name' in {skill_md}")

    # v6.4.0 4.5 SKILL-ONBOARDING: extract welcome + access_control + initial_message
    # + tags from frontmatter so SKILL.md becomes the source of truth for these
    # fields (previously hardcoded to access_control={type: public} in the create
    # path and stripped on refresh). The seeder's create + refresh now both pass
    # whatever the frontmatter declared, falling back to sensible defaults.
    # Accept both snake_case (yaml convention) and camelCase (Pydantic alias).
    return {
        "name": front["name"],
        "description": (front.get("description") or "").strip(),
        "instructions": parts[2].strip(),
        "metadata": front.get("metadata") or {},
        "welcome": front.get("welcome"),
        # v6.4.0 SHELL-MODES: page-level shell shape from frontmatter.
        "shell": front.get("shell"),
        "access_control": front.get("access_control") or front.get("accessControl"),
        "initial_message": front.get("initial_message") or front.get("initialMessage") or "",
        "display_name": front.get("display_name") or front.get("displayName") or "",
        "tags": front.get("tags") or [],
        "avatar": front.get("avatar") or "",
    }


def _existing_platform_skill_names() -> set[str]:
    configs = skill_config.list_skills(owner_id=PLATFORM_OWNER_UID, limit=200)
    return {c.name for c in configs}


def _existing_platform_skill_by_name() -> dict[str, Any]:
    """Map skill name → SkillConfig for current-owner platform skills.

    G16 helper — the refresh phase needs the full SkillConfig (not just
    the name) so it can call ``update_skill(skill_id, …)``.
    """
    configs = skill_config.list_skills(owner_id=PLATFORM_OWNER_UID, limit=200)
    return {c.name: c for c in configs}


def _previous_owner_uids() -> list[str]:
    """Parse ``PLATFORM_PREVIOUS_OWNER_UIDS`` (comma-separated) into a list.

    G16 (template-fork-ergonomics.md): a fork that rotates
    ``PLATFORM_OWNER_UID`` keeps the OLD owner's skill rows in Firestore
    until manually purged. Setting ``PLATFORM_PREVIOUS_OWNER_UIDS`` to
    the comma-separated list of historic UIDs lets the seeder clean them
    up on the next deploy.

    Returns ``[]`` when unset/empty so the purge phase is a no-op for
    forks that never rotated.
    """
    raw = os.environ.get("PLATFORM_PREVIOUS_OWNER_UIDS", "").strip()
    if not raw:
        return []
    return [uid.strip() for uid in raw.split(",") if uid.strip()]


def _purge_stale_owner_skills(previous_uids: list[str], dry_run: bool = False) -> int:
    """Delete platform-skill rows owned by any of ``previous_uids``.

    G16: returns the number of skills purged (or, in dry_run, *would* purge) so
    the seeder can surface it in SeedSummary. Iterates each previous UID
    separately to avoid composite-index requirements during a first deploy (no
    ``where("ownerUid", "in", …)`` clause).
    """
    if not previous_uids:
        return 0
    purged = 0
    for uid in previous_uids:
        configs = skill_config.list_skills(owner_id=uid, limit=200)
        for cfg in configs:
            # Only template rows are re-created under the new owner, so only
            # they are safe to delete. A durably-managed (in-product-edited)
            # skill has no template to come back from — keep it and say so.
            if getattr(cfg, "managed_by", None) == "firestore":
                logger.warning(
                    "platform_seed: NOT purging durable skill %r owned by previous owner %s — migrate it by hand",
                    cfg.name,
                    uid,
                )
                continue
            if dry_run:
                logger.info("platform_seed: [dry-run] would purge stale skill %r (previous_owner=%s)", cfg.name, uid)
                purged += 1
                continue
            if skill_config.delete_skill(cfg.skill_id):
                logger.info(
                    "platform_seed: purged stale skill %r (previous_owner=%s)",
                    cfg.name,
                    uid,
                )
                purged += 1
    return purged


def _ensure_tool_permissions_wildcard(dry_run: bool = False) -> bool:
    """Idempotent: write a wildcard allow-all rule if none exists.

    Returns True if the doc was created (or, in dry_run, *would* be created),
    False if it already existed. Mirrors the wildcard that local_fixture.py
    seeds for LOCAL_MODE so dev and prod stay consistent (item #20 from the
    CPH Uni upstream feedback).
    """
    existing = fs.get_document("tool_permissions", "*")
    if existing is not None:
        return False
    if dry_run:
        logger.info("platform_seed: [dry-run] would seed tool_permissions wildcard allow-all rule")
        return True
    fs.set_document(
        "tool_permissions",
        "*",
        {
            "type": "wildcard",
            "tools": ["*"],
            "denied": [],
            "created_by": "platform_seed",
        },
    )
    logger.info("platform_seed: seeded tool_permissions wildcard allow-all rule")
    return True


def _resolve_owner_email() -> str:
    """Return the platform owner email, with fail-loud validation.

    Forks MUST set PLATFORM_OWNER_EMAIL. The module-level default keeps
    the Aitana fallback so tests can import without env vars, but the
    first real seed() call in a non-LOCAL_MODE environment will surface a
    clear error instead of silently shipping skills owned by Aitana.
    """
    email = os.environ.get("PLATFORM_OWNER_EMAIL", "")
    if email:
        return email
    if is_local_mode():
        return "platform@localhost"
    raise RuntimeError(
        "PLATFORM_OWNER_EMAIL env var is required in non-LOCAL_MODE. "
        "Set it to the platform admin email for this deployment "
        "(e.g. platform@yourdomain.com). "
        "Forks: add it to your Cloud Build substitutions as _PLATFORM_OWNER_EMAIL."
    )


FORK_SKIP_FILE = ".fork-skip"


def _fork_skipped_skills(root: Path) -> frozenset[str]:
    """Template names this fork never offers, one per line in
    ``<templates>/.fork-skip`` (``#`` comments allowed).

    A fork can't delete an upstream template directory without conflicting on
    every sync (and upstream constants/tests name them), so it lists them
    instead — e.g. skills whose MCP services this deployment doesn't run.
    """
    path = root / FORK_SKIP_FILE
    if not path.exists():
        return frozenset()
    names = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
    return frozenset(n for n in names if n)


def seed(templates_root: Path | None = None, dry_run: bool = False) -> SeedSummary:
    """Seed platform skills from disk templates. Idempotent by `name`.

    Returns a SeedSummary counting created/skipped/failed entries. A
    malformed template surfaces in `failed` rather than aborting the run
    — the Cloud Build step runs non-fatally and we prefer to partially
    seed over blocking a deploy.

    ``dry_run=True`` performs every read + validation and populates the summary
    with what WOULD change, but issues no Firestore writes (no create/update/
    delete/set). Used by ``scripts/seed_skills.py --dry-run`` to preview a
    re-seed before touching a shared env (e.g. prod).

    Demo-skill gating (G17 — template-fork-ergonomics.md):
        ``_INCLUDE_DEMO_SKILLS`` env var controls whether the inherited
        7-skill workshop demo bundle is seeded. Default is "true" for
        backwards compatibility with the platform repo's own
        dev/test/prod deploys. Public-template forks ship with the flag
        set to "false" so a clean fork starts with zero seeded skills —
        flip to "true" only if you want the demos.
    """
    owner_email = _resolve_owner_email()
    root = templates_root or DEFAULT_TEMPLATES_ROOT
    summary = SeedSummary()
    summary.tool_permissions_wildcard_seeded = _ensure_tool_permissions_wildcard(dry_run=dry_run)

    # G16 Phase 1 — purge skills owned by any previous-owner UID.
    # Runs BEFORE the main loop so the existing-by-name dict reflects
    # current-owner skills only.
    summary.purged = _purge_stale_owner_skills(_previous_owner_uids(), dry_run=dry_run)

    existing_by_name = _existing_platform_skill_by_name()
    include_demos = _include_demo_skills()
    fork_skipped = _fork_skipped_skills(root)

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.exists():
            continue
        if not include_demos and child.name in DEMO_SKILL_NAMES:
            logger.info(
                "platform_seed: skipping demo skill %r (_INCLUDE_DEMO_SKILLS != 'true')",
                child.name,
            )
            summary.skipped += 1
            continue

        try:
            parsed = _parse_template(skill_md)
        except Exception as e:
            logger.warning("platform_seed: failed to parse %s: %s", skill_md, e)
            summary.failed.append(child.name)
            continue
        try:
            parsed["access_control"] = _resolve_tags_from(parsed.get("access_control"))
        except AccessUnresolved as e:
            logger.warning("platform_seed: skipping %s: %s", parsed["name"], e)
            summary.skipped += 1
            summary.notes.append(f"{parsed['name']}: skipped — {e}")
            continue

        disabled = _disabled_provider(parsed["metadata"])
        by_fork = child.name in fork_skipped or parsed["name"] in fork_skipped
        if disabled or by_fork:
            if by_fork:
                logger.info("platform_seed: skipping %r — listed in %s", parsed["name"], FORK_SKIP_FILE)
                summary.skipped_by_fork.append(parsed["name"])
            else:
                logger.info(
                    "platform_seed: skipping %r — pinned to %s, which is not enabled in this env",
                    parsed["name"],
                    disabled,
                )
                summary.skipped_disabled_provider.append(parsed["name"])
            stale = existing_by_name.get(parsed["name"])
            # Remove a previously-seeded row so it leaves the marketplace, but
            # never a durably-managed (in-product-edited) skill.
            if stale is not None and getattr(stale, "managed_by", None) != "firestore":
                if dry_run:
                    logger.info("platform_seed: [dry-run] would delete %r", parsed["name"])
                elif skill_config.delete_skill(stale.skill_id):
                    logger.info(
                        "platform_seed: deleted %r (%s)",
                        parsed["name"],
                        "fork opted out" if by_fork else "provider disabled",
                    )
            continue

        # G16 Phase 2 — refresh template fields on existing skills.
        # The pre-G16 seeder skipped any name match, so a SKILL.md edit
        # never reached Firestore until someone deleted the skill row.
        # Now: when the skill exists, push displayName/description/
        # instructions/metadata updates so Firestore tracks disk.
        if parsed["name"] in existing_by_name:
            existing_cfg = existing_by_name[parsed["name"]]
            # v6.9.0 9.2: never clobber a durably-managed (in-product-created or
            # -edited) skill on redeploy. Only template-provenance skills track
            # disk; a `managed_by == "firestore"` skill is authoritative in
            # Firestore. Legacy None is treated as template (clobberable).
            if getattr(existing_cfg, "managed_by", None) == "firestore":
                summary.preserved += 1
                continue
            # v6.4.0 4.5: push welcome + accessControl + initial_message +
            # display_name + tags through to Firestore on refresh too,
            # so editing SKILL.md actually changes the live skill.
            refresh_payload: dict[str, Any] = {
                "description": parsed["description"],
                "instructions": parsed["instructions"],
                "skillMetadata": parsed["metadata"],
                # Mark disk-tracked skills so the drift badge + future re-seeds
                # can tell template-managed from durable.
                "managedBy": "template",
            }
            if parsed.get("welcome") is not None:
                refresh_payload["welcome"] = parsed["welcome"]
            if parsed.get("shell") is not None:
                refresh_payload["shell"] = parsed["shell"]
            declared_access = _declared_access(parsed)
            if declared_access is not None:
                refresh_payload["accessControl"] = declared_access
            if parsed.get("initial_message"):
                refresh_payload["initialMessage"] = parsed["initial_message"]
            if parsed.get("display_name"):
                refresh_payload["displayName"] = parsed["display_name"]
            if parsed.get("tags"):
                refresh_payload["tags"] = parsed["tags"]
            if _declared_avatar(parsed):
                refresh_payload["avatar"] = _declared_avatar(parsed)
            # Backfill slug for skills created before slug-at-create existed. The
            # refresh path historically never set it, so a pre-slug skill stayed
            # slug-less forever → skillHref emits its bare-id `/chat/{id}` URL,
            # which the frontend slug-resolver used to reject as "not found".
            # Only set when missing so an existing slug is never churned.
            if not getattr(existing_cfg, "slug", None):
                refresh_payload["slug"] = unique_slug(PLATFORM_OWNER_UID, slugify(parsed["name"]))
            try:
                if not dry_run:
                    skill_config.update_skill(existing_cfg.skill_id, refresh_payload)
                summary.refreshed += 1
            except Exception as e:
                logger.warning(
                    "platform_seed: failed to refresh %s: %s",
                    parsed["name"],
                    e,
                )
                summary.failed.append(parsed["name"])
            continue

        try:
            # Generate slug at creation time so the friendly URL
            # /chat/@aitana-platform/{slug} works without a follow-up
            # backfill. unique_slug guards against collisions if a
            # template name slugifies to the same value as another
            # platform skill (defensive — current templates don't).
            slug = unique_slug(PLATFORM_OWNER_UID, slugify(parsed["name"]))
            # v6.4.0 4.5: read access control from frontmatter if set, default
            # to public when omitted (preserves the legacy seeder behaviour
            # for skills without explicit access control). Also propagate
            # welcome, initialMessage, displayName, tags from frontmatter.
            create_kwargs: dict[str, Any] = {
                "name": parsed["name"],
                "description": parsed["description"],
                "instructions": parsed["instructions"],
                "owner_id": PLATFORM_OWNER_UID,
                "owner_email": owner_email,
                "accessControl": _declared_access(parsed) or _seed_default_access(),
                "skillMetadata": parsed["metadata"],
                "slug": slug,
                # v6.9.0 9.2: seeded-from-disk provenance (vs durable "firestore").
                "managedBy": "template",
            }
            if parsed.get("welcome") is not None:
                create_kwargs["welcome"] = parsed["welcome"]
            if parsed.get("shell") is not None:
                create_kwargs["shell"] = parsed["shell"]
            if parsed.get("initial_message"):
                create_kwargs["initialMessage"] = parsed["initial_message"]
            if parsed.get("display_name"):
                create_kwargs["displayName"] = parsed["display_name"]
            if parsed.get("tags"):
                create_kwargs["tags"] = parsed["tags"]
            if _declared_avatar(parsed):
                create_kwargs["avatar"] = _declared_avatar(parsed)
            if not dry_run:
                skill_config.create_skill(**create_kwargs)
            summary.created += 1
        except Exception as e:
            logger.warning("platform_seed: failed to create %s: %s", parsed["name"], e)
            summary.failed.append(parsed["name"])

    # Issue #14 safeguard — post-seed MCP-registry consistency. Runs on every
    # deploy of every env, so registry drift (per-env Firestore never promotes
    # with code) is caught the moment it would ship, not when a customer hits
    # a G42 hard-500. Verification failures surface via mcp_missing — the
    # Cloud Build seed step fails the build on them. A crash in the checker
    # itself must never fail the seed: it degrades to a loud warning.
    try:
        from admin.mcp_registry_check import verify_mcp_registry

        check = verify_mcp_registry(root)
        # A skill the fork doesn't seed can't hard-500 — don't gate on it.
        summary.mcp_missing = [
            m for m in check["mcp_missing"] if m.split(" -> ", 1)[0] not in set(summary.skipped_by_fork)
        ]
        summary.mcp_warnings = check["mcp_warnings"]
        if summary.mcp_missing:
            logger.error(
                "platform_seed: mcp_servers registry cannot satisfy declared servers "
                "(these skills would hard-500 at agent build): %s",
                summary.mcp_missing,
            )
        for warning in summary.mcp_warnings:
            logger.warning("platform_seed: mcp registry: %s", warning)
    except Exception as e:
        logger.warning("platform_seed: mcp registry check crashed: %s", e)
        summary.mcp_warnings = [f"mcp-registry check crashed (verification skipped): {e}"]

    # Private packages' post-seed work (e.g. the user-guide KB sync). Runs on
    # every deploy and promotion, in the image being deployed. Never fatal.
    import extensions

    summary.extensions = extensions.after_seed(dry_run=dry_run)
    return summary

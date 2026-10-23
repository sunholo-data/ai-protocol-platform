"""``my_context`` — the signed-in user's own view of this deployment.

The user guide says how the app works in general; this says how it is set up
for THIS user, so "why can't I…?" and "which skill should I use for…?" get an
answer about their account rather than a list of possibilities: their roles,
the skills they can open, their folders and which are searchable, and which
features are on for them.

Caller-only by construction: every lookup is keyed to the uid of the verified
caller from the ADK invocation, and the tool takes no argument naming anyone
else. Friendly names and links only — never a uid, doc id or gs:// path
(CLAUDE.md #9). Design: docs/design/private/user-guide-and-platform-guide-skill.md.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

_MAX_SKILLS = 40


def _caller(tool_context: Any) -> Any:
    from auth.firebase_auth import resolve_user_by_uid
    from tools.documents.context import resolve_caller_uid

    uid = resolve_caller_uid(tool_context)
    return resolve_user_by_uid(uid) if uid else None


def _roles(user: Any) -> list[str]:
    from auth import admin_roles

    tags = getattr(user, "group_tags", None) or ()
    roles = []
    if admin_roles.is_platform_admin(tags):
        roles.append("platform admin (can manage every workspace and built-in skill)")
    domains = sorted(admin_roles.tenant_admin_domains(tags))
    if domains:
        roles.append(f"workspace admin for {', '.join(domains)}")
    if admin_roles.is_skill_admin(tags) and not roles:
        roles.append("skill admin (can edit shared skills)")
    return roles or ["member (no admin roles)"]


def _skills(user: Any) -> list[dict[str, str]]:
    from auth.access_context import build_access_context
    from db.clients import resolve_enabled_skills
    from skills import skill_config
    from skills.platform import PLATFORM_OWNER_UID
    from skills.visibility import visible_configs

    access = build_access_context(user)
    visible = visible_configs(skill_config.list_skills(limit=200), access, resolve_enabled_skills(user))
    out = []
    for c in visible[:_MAX_SKILLS]:
        owner = (
            "built in"
            if c.owner_id == PLATFORM_OWNER_UID
            else ("yours" if c.owner_id == user.uid else "shared with you")
        )
        row = {"name": c.display_name or c.name, "about": (c.description or "").strip()[:200], "owner": owner}
        if c.slug:
            row["open"] = f"/chat/@{c.owner_id}/{c.slug}"  # the same link the app builds (skillHref)
        out.append(row)
    return out


def _folders(user: Any) -> list[dict[str, Any]]:
    from db import folders as folders_db

    out = []
    for f in folders_db.list_folders(user.uid):
        out.append(
            {
                "name": f.get("name"),
                "documents": f.get("docCount", 0),
                "searchable": bool(f.get("knowledgeBaseId")),
                **({"managed": "documents the assistant created for you"} if f.get("system") else {}),
            }
        )
    return out


def _features(user: Any) -> dict[str, str]:
    import extensions
    from db.clients import UnmappedTenantError, resolve_documents_bucket

    try:
        resolve_documents_bucket(user)
        uploads = "on"
    except UnmappedTenantError:
        uploads = "off — your organisation hasn't been set up for document upload; an administrator can enable it"
    except Exception:  # an unreadable mapping must not break the answer
        uploads = "unknown"
    kbs = "on" if "knowledge" in extensions.private_packages() else "off on this deployment"
    return {
        "document upload": uploads,
        "searchable folders (Make searchable, Add to a skill)": kbs,
    }


async def my_context(tool_context: ToolContext = None) -> dict[str, Any]:
    """How this app is set up for the person asking: their roles, the skills they
    can open, their folders (and which are searchable), and which features are on.

    Call it when the answer depends on THEIR account — "why can't I see…?",
    "which skill should I use for…?", "is my folder searchable yet?", "am I an
    admin?". Combine it with the user guide: the guide says how something
    works; this says whether it is available to them and what they already have.

    Returns:
        roles, workspace (their email domain), skills (name, about, owner, open
        link), folders (name, documents, searchable), features (on/off with why).
    """
    user = await asyncio.to_thread(_caller, tool_context)
    if user is None:
        return {"status": "error", "message": "Could not verify who is asking, so nothing was looked up."}

    async def part(fn: Any) -> Any:
        try:
            return await asyncio.to_thread(fn, user)
        except Exception:
            logger.exception("my_context: %s failed", getattr(fn, "__name__", "a part"))
            return "unavailable right now"

    roles, skills, folders, features = await asyncio.gather(
        part(_roles), part(_skills), part(_folders), part(_features)
    )
    return {
        "status": "ok",
        "workspace": getattr(user, "domain", "") or "",
        "roles": roles,
        "skills": skills,
        "folders": folders,
        "features": features,
    }

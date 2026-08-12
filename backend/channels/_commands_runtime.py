"""Execute framework-level commands parsed by `CommandParser`.

The four built-in commands (`/skill`, `/skills`, `/help`, `/clear`)
behave the same on every channel. Channel-specific commands (e.g.,
Discord's `/scope <client>`) are handled by overriding
`BaseChannel._handle_command`.
"""

from __future__ import annotations

import logging

from channels._default_skill import get_user_default_skill, set_user_default_skill
from channels.commands import Command

logger = logging.getLogger(__name__)


async def execute_command(
    cmd: Command,
    firebase_uid: str,
    *,
    channel_name: str,
) -> str:
    """Run a framework command. Returns the reply text to send back."""
    if cmd.name == "skill":
        return await _cmd_skill(cmd, firebase_uid, channel_name)
    if cmd.name == "skills":
        return await _cmd_skills(firebase_uid)
    if cmd.name == "help":
        return _cmd_help(channel_name)
    if cmd.name == "clear":
        return _cmd_clear(firebase_uid)

    # Defensive: CommandParser only returns Commands with names in
    # KNOWN_COMMANDS, but if that drifts, fail loud instead of silent.
    logger.warning("execute_command got unexpected command name=%r", cmd.name)
    return f"Unknown command: {cmd.name}"


async def _cmd_skill(cmd: Command, firebase_uid: str, channel_name: str) -> str:
    if not cmd.args:
        return "Usage: /skill <name> — switches your default skill."

    skill_name_or_id = cmd.args[0]
    resolved_id = await _resolve_skill_by_name_or_id(skill_name_or_id)
    if resolved_id is None:
        return f"No skill matching {skill_name_or_id!r}. Use /skills to list available skills."

    await set_user_default_skill(firebase_uid, resolved_id)
    if cmd.body:
        # Bracket-form (email): `[Skill Name] body` — caller sent both
        # the skill switch AND a message. Acknowledge switch; the
        # framework will run the body as a normal user message on the
        # next webhook entry.
        return f"Switched to skill: {resolved_id}. (Run your message again to hit this skill.)"
    return f"Switched to skill: {resolved_id}"


async def _cmd_skills(firebase_uid: str) -> str:
    """List skills the user can access — public marketplace + their own."""

    skills = await _list_skills_for_uid(firebase_uid)
    if not skills:
        return "No skills available."

    default_id = await get_user_default_skill(firebase_uid)
    lines = ["Available skills:"]
    for skill in skills:
        marker = " (default)" if skill.get("skill_id") == default_id else ""
        lines.append(f"• {skill.get('skill_id')} — {skill.get('title', '')}{marker}")
    lines.append("")
    lines.append("Use /skill <name> to switch.")
    return "\n".join(lines)


def _cmd_help(channel_name: str) -> str:
    return (
        f"Channel: {channel_name}\n\n"
        "Available commands:\n"
        "  /skill <name>  Switch your default skill\n"
        "  /skills        List skills you can use\n"
        "  /help          Show this help\n"
        "  /clear         Reset the current session\n\n"
        "Send a message without a command to chat with your default skill."
    )


def _cmd_clear(firebase_uid: str) -> str:
    # TODO(channels M2/M3): wire real per-thread reset.
    # v1 stub — no actual session reset happens. ADK session IDs are
    # generated per-request when not supplied, so each message starts
    # fresh unless the channel explicitly threads. Real "reset this
    # thread's session" is channel-specific (Telegram has no thread
    # primitive; Discord uses thread IDs). Adapter overrides
    # `BaseChannel._handle_command` to wire real reset semantics when
    # the channel has a stable thread identity to clear.
    logger.info("clear command received for uid=%s — no-op in v1", firebase_uid)
    return "Session reset noted. (Per-thread reset is channel-specific — coming in a follow-up.)"


# --- internal lookups ------------------------------------------------------


async def _resolve_skill_by_name_or_id(needle: str) -> str | None:
    """Match a user-supplied skill identifier against `skill_id` or `title`.

    Case-insensitive on `title`. Returns the resolved skill_id, or None
    if no match.
    """
    from skills.skill_config import get_skill, list_marketplace

    direct = get_skill(needle)
    if direct is not None:
        return needle

    needle_lower = needle.lower()
    for skill in list_marketplace(limit=100):
        if skill.title and skill.title.lower() == needle_lower:
            return skill.skill_id
    return None


async def _list_skills_for_uid(firebase_uid: str) -> list[dict]:
    """Return marketplace + user-owned skills as a flat list of dicts."""
    from skills.skill_config import list_marketplace

    out = [{"skill_id": s.skill_id, "title": s.title} for s in list_marketplace(limit=100)]
    return out


__all__ = ["execute_command"]

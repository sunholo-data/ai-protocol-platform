"""Channel-agnostic command parser.

Channels declare a `command_prefix` ("/" for Telegram / Discord / WhatsApp,
"[" for email's `[SkillName] body` subject pattern). The parser returns a
structured `Command(name, args, body)` for the BaseChannel framework to
dispatch, or None if the text is not a command.

Supported commands (channel-agnostic):

    /skill <name>     switch the user's default skill
    /skills           list available skills
    /help             show channel help text
    /clear            reset the current session

Email's `[Skill Name]` form maps to `Command(name="skill", args=["Skill Name"])`
with `body` set to the text after the closing bracket. Same downstream handler.
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from typing import ClassVar

logger = logging.getLogger(__name__)


# Known commands. Unknown commands fall through (parser returns None) so
# channels can layer adapter-specific commands without modifying the
# framework. The framework only acts on this allowlist.
KNOWN_COMMANDS: frozenset[str] = frozenset({"skill", "skills", "help", "clear"})


@dataclass(frozen=True)
class Command:
    """A parsed framework command.

    Attributes:
        name: lowercased command name (e.g., "skill")
        args: positional arguments (e.g., ["General Assistant"])
        body: remaining text after the command — used for `[Skill] body`
            email pattern where the body becomes the user message
    """

    name: str
    args: tuple[str, ...]
    body: str = ""


class CommandParser:
    """Channel-agnostic command parser.

    Stateless; all entry points are classmethods. The parser is permissive
    about whitespace and quoting (it uses `shlex.split` so `/skill "Doc
    Analyst"` works) but strict about prefix matching.
    """

    SLASH_PREFIX: ClassVar[str] = "/"
    BRACKET_PREFIX: ClassVar[str] = "["

    @classmethod
    def parse(cls, text: str, prefix: str = SLASH_PREFIX) -> Command | None:
        """Try to parse `text` as a command using `prefix`.

        Returns None if:
            - text is empty or whitespace
            - text does not start with prefix
            - parsed command name is not in KNOWN_COMMANDS

        Falling through to None means BaseChannel proceeds with normal
        skill dispatch — channels can extend by overriding `_handle_command`.
        """
        if not text:
            return None
        text = text.lstrip()
        if not text.startswith(prefix):
            return None

        if prefix == cls.BRACKET_PREFIX:
            return cls._parse_bracket(text)
        return cls._parse_slash(text, prefix)

    @classmethod
    def _parse_slash(cls, text: str, prefix: str) -> Command | None:
        """Parse `/command arg1 arg2 ...` form."""
        stripped = text[len(prefix) :].strip()
        if not stripped:
            return None

        # shlex preserves quoted spans: /skill "Doc Analyst" → ["skill", "Doc Analyst"]
        try:
            tokens = shlex.split(stripped)
        except ValueError as exc:
            # shlex raises on unclosed quotes — treat as no-match rather than crashing.
            logger.debug("CommandParser: shlex failure on %r: %s", text, exc)
            return None
        if not tokens:
            return None

        name = tokens[0].lower()
        if name not in KNOWN_COMMANDS:
            return None
        return Command(name=name, args=tuple(tokens[1:]))

    @classmethod
    def _parse_bracket(cls, text: str) -> Command | None:
        """Parse `[Skill Name] remaining body text` form (email subject).

        The bracketed name is treated as the `skill` command argument; the
        text after the closing bracket becomes `body` (the actual message).
        Missing closing bracket or unknown command falls through.
        """
        close = text.find("]")
        if close == -1:
            return None
        skill_name = text[1:close].strip()
        if not skill_name:
            return None
        body = text[close + 1 :].strip()
        return Command(name="skill", args=(skill_name,), body=body)


__all__ = ["KNOWN_COMMANDS", "Command", "CommandParser"]

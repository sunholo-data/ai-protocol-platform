"""Unit tests for `channels.commands.CommandParser`.

The parser is pure: no I/O, no async, no Firestore. Tests cover the
matrix of prefix types ("/" vs "[") and the boundary cases (empty
input, unknown command, malformed quotes, missing close bracket).
"""

from __future__ import annotations

from channels.commands import Command, CommandParser


class TestSlashParse:
    """Slash-prefix parsing (Telegram / Discord / WhatsApp)."""

    def test_slash_skill_with_simple_arg(self) -> None:
        cmd = CommandParser.parse("/skill general-assistant")
        assert cmd == Command(name="skill", args=("general-assistant",))

    def test_slash_skill_with_quoted_arg(self) -> None:
        cmd = CommandParser.parse('/skill "Doc Analyst"')
        assert cmd == Command(name="skill", args=("Doc Analyst",))

    def test_slash_skills_no_args(self) -> None:
        cmd = CommandParser.parse("/skills")
        assert cmd == Command(name="skills", args=())

    def test_slash_help(self) -> None:
        cmd = CommandParser.parse("/help")
        assert cmd == Command(name="help", args=())

    def test_slash_clear(self) -> None:
        cmd = CommandParser.parse("/clear")
        assert cmd == Command(name="clear", args=())

    def test_unknown_command_returns_none(self) -> None:
        # An unknown `/foo` falls through so channels can add adapter-specific
        # commands without modifying the framework.
        assert CommandParser.parse("/foo bar") is None

    def test_empty_input_returns_none(self) -> None:
        assert CommandParser.parse("") is None
        assert CommandParser.parse("   ") is None

    def test_no_prefix_returns_none(self) -> None:
        # Free-form chat text is not a command.
        assert CommandParser.parse("hello there") is None

    def test_prefix_only_returns_none(self) -> None:
        assert CommandParser.parse("/") is None
        assert CommandParser.parse("/   ") is None

    def test_uppercase_command_is_normalised(self) -> None:
        cmd = CommandParser.parse("/SKILL foo")
        assert cmd is not None
        assert cmd.name == "skill"

    def test_leading_whitespace_is_tolerated(self) -> None:
        cmd = CommandParser.parse("   /skill foo")
        assert cmd == Command(name="skill", args=("foo",))

    def test_unclosed_quote_returns_none(self) -> None:
        # shlex raises ValueError on unbalanced quotes; parser treats as no-match.
        assert CommandParser.parse('/skill "unclosed') is None


class TestBracketParse:
    """Bracket-prefix parsing (email subjects)."""

    def test_bracket_basic(self) -> None:
        cmd = CommandParser.parse("[General Assistant] hello", prefix=CommandParser.BRACKET_PREFIX)
        assert cmd is not None
        assert cmd.name == "skill"
        assert cmd.args == ("General Assistant",)
        assert cmd.body == "hello"

    def test_bracket_with_punctuation_body(self) -> None:
        cmd = CommandParser.parse(
            "[Doc Analyst] please summarise this!",
            prefix=CommandParser.BRACKET_PREFIX,
        )
        assert cmd is not None
        assert cmd.args == ("Doc Analyst",)
        assert cmd.body == "please summarise this!"

    def test_bracket_no_body(self) -> None:
        cmd = CommandParser.parse("[General Assistant]", prefix=CommandParser.BRACKET_PREFIX)
        assert cmd is not None
        assert cmd.body == ""

    def test_bracket_missing_close_returns_none(self) -> None:
        assert CommandParser.parse("[unclosed", prefix=CommandParser.BRACKET_PREFIX) is None

    def test_bracket_empty_name_returns_none(self) -> None:
        assert CommandParser.parse("[] body", prefix=CommandParser.BRACKET_PREFIX) is None

    def test_bracket_no_prefix_returns_none(self) -> None:
        # Text that doesn't start with `[` is not a bracket command.
        assert CommandParser.parse("Re: prev message", prefix=CommandParser.BRACKET_PREFIX) is None


class TestPrefixMismatch:
    """Confirm prefix is strict — slash text doesn't match the bracket parser."""

    def test_slash_text_under_bracket_prefix(self) -> None:
        assert CommandParser.parse("/skill foo", prefix=CommandParser.BRACKET_PREFIX) is None

    def test_bracket_text_under_slash_prefix(self) -> None:
        assert CommandParser.parse("[Skill] hi") is None

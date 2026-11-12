"""A generated document carries a signed-in link and records the skill that made it."""

from __future__ import annotations

from types import SimpleNamespace


def test_generated_document_link_is_the_signed_in_app_page(monkeypatch):
    from tools.documents.generate import _document_link

    monkeypatch.setenv("APP_BASE_URL", "https://app.example/")
    assert _document_link("abc") == "https://app.example/documents/abc"
    monkeypatch.delenv("APP_BASE_URL")
    assert _document_link("abc") == "/documents/abc"  # the web app can use the path
    assert _document_link("") == ""


def test_generated_document_records_the_skill_running_now():
    """Provenance skillId was empty on EVERY generated document: nothing ever
    wrote state["skill_id"]. The link's workbench forward needs it."""
    from adk.callbacks import ACTIVE_SKILL_KEY, make_before_agent
    from tools.documents.generate import _skill_id

    state: dict = {}
    make_before_agent("dw-1")(SimpleNamespace(state=state))
    assert state[ACTIVE_SKILL_KEY] == "dw-1"
    assert _skill_id(SimpleNamespace(state=state)) == "dw-1"
    # After a handoff the delegate's before_agent overwrites the door's.
    make_before_agent("door")(SimpleNamespace(state=state))
    assert _skill_id(SimpleNamespace(state=state)) == "door"
    assert _skill_id(None) == ""

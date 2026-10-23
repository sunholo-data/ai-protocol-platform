"""Document read access (PRECUT-HARDENING M11).

Paired with M10. That fix stopped `list_documents` handing every user every
document; this one stops the CONTENT being readable by id regardless of owner.
Together they close the exposure — listing was the discovery half, this is the
read half, and a known id was enough on its own.

The design constraint that makes this non-trivial: the prewarmed shared library
is owned by the ``PLATFORM_OWNER_UID`` sentinel *precisely so every tenant can
read it*. A naive "owner must match caller" check would break the PPA library on
the day it shipped. So the rule is: **your own document, or the platform's.**

``caller_uid`` is a REQUIRED parameter, not an optional one. An optional security
argument fails open the first time a call site forgets it — and this codebase
already has the worked example, where `list_documents` read a state key nothing
ever wrote and silently returned everything.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from skills.platform import PLATFORM_OWNER_UID
from tools.documents.context import UNCHECKED_INTERNAL, DocumentAccessError, build_document_context


def _doc(owner: str) -> dict:
    return {
        "originalFilename": "Contract.pdf",
        "parseStatus": "parsed",
        "userId": owner,
        "blocks": [{"type": "paragraph", "text": "hello", "id": "b1"}],
    }


class TestTheGate:
    def test_owner_can_read_their_own_document(self):
        with patch("tools.documents.context.get_document", return_value=_doc("uid-a")):
            content, _ = build_document_context("d1", caller_uid="uid-a")
        assert "Contract.pdf" in content

    def test_another_user_cannot_read_it(self):
        with patch("tools.documents.context.get_document", return_value=_doc("uid-a")):
            with pytest.raises(DocumentAccessError):
                build_document_context("d1", caller_uid="uid-b")

    def test_the_platform_shared_library_stays_readable_by_everyone(self):
        """The prewarmed PPA library is sentinel-owned so every tenant can read it."""
        with patch("tools.documents.context.get_document", return_value=_doc(PLATFORM_OWNER_UID)):
            content, _ = build_document_context("d1", caller_uid="uid-anyone")
        assert "Contract.pdf" in content

    def test_a_blank_caller_is_denied_not_waved_through(self):
        """Fail closed: an unidentified caller is the case that must NOT read."""
        with patch("tools.documents.context.get_document", return_value=_doc("uid-a")):
            for blank in ("", None):
                with pytest.raises(DocumentAccessError):
                    build_document_context("d1", caller_uid=blank)

    def test_a_document_with_no_owner_recorded_is_denied(self):
        """Missing userId must not read as 'unowned, therefore public'."""
        doc = _doc("uid-a")
        del doc["userId"]
        with patch("tools.documents.context.get_document", return_value=doc):
            with pytest.raises(DocumentAccessError):
                build_document_context("d1", caller_uid="uid-a")


class TestTheEscapeHatch:
    def test_internal_callers_can_opt_out_explicitly(self):
        """An explicit, greppable sentinel — never a default."""
        with patch("tools.documents.context.get_document", return_value=_doc("uid-a")):
            content, _ = build_document_context("d1", caller_uid=UNCHECKED_INTERNAL)
        assert "Contract.pdf" in content

    def test_caller_uid_is_required(self):
        """Omitting it must be a TypeError, not a silent unchecked read.

        This is the property that makes the gate hold as the codebase grows: a
        new call site cannot forget the argument, it can only opt out loudly.
        """
        import inspect

        sig = inspect.signature(build_document_context)
        assert sig.parameters["caller_uid"].default is inspect.Parameter.empty

"""Classification logic for the tenant-access audit (PRECUT-HARDENING M3).

The audit itself is read-only and is verified by running it against a real
project. What is worth pinning is the classification, because its sharp case is
a domain that LOOKS configured — a ``clients/{domain}`` record exists but carries
no derived tags — which resolves to a working login onto an empty workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from audit_tenant_access import classify

_CLIENTS = {
    "tenant-with-tags.example": {"derived_group_tags": ["TENANT_A"]},
    "configured-but-empty.com": {"derived_group_tags": []},
    "configured-no-field.com": {"bucket": "gs://x"},
}


def test_configured_tenant_with_tags_is_ok():
    assert classify("user@tenant-with-tags.example", _CLIENTS)[0] == "OK"


def test_unknown_domain_is_unmapped():
    label, why = classify("someone@gmail.com", _CLIENTS)
    assert label == "UNMAPPED"
    assert "gmail.com" in why


def test_configured_domain_with_empty_tag_list_is_flagged():
    """The sharp case: configured, so nothing looks wrong, but zero tags."""
    assert classify("a@configured-but-empty.com", _CLIENTS)[0] == "NO_TAGS"


def test_configured_domain_missing_the_field_is_flagged_the_same_way():
    """Absent and empty must resolve identically — auth treats them the same."""
    assert classify("a@configured-no-field.com", _CLIENTS)[0] == "NO_TAGS"


def test_case_and_whitespace_are_normalised():
    assert classify("  User@Tenant-With-Tags.EXAMPLE  ", _CLIENTS)[0] == "OK"


def test_unparseable_email_is_unmapped_not_a_crash():
    for bad in (None, "", "no-at-sign"):
        assert classify(bad, _CLIENTS)[0] == "UNMAPPED"

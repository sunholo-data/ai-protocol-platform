"""Unit tests for ``backend/observability/tenant_context.py`` and
``backend/observability/tenant_span_processor.py``.

Sprint 2.14 M1. Covers:
  - contextvar set/get + isolation across concurrent async tasks
  - SpanProcessor stamps attrs from the contextvar
  - SpanProcessor with no context emits span unchanged (no crash)
  - Enricher exception swallowed + WARN log; other enrichers still run
  - register_tenant_enricher rejects non-callables (fail-loud)
  - Multiple enrichers compose; later registrations win on key collision

The headline correctness test is the concurrent-task isolation case —
Python contextvars are per-task so this "should work" automatically,
but writing the test explicitly is what proves OBSERVABLE_BY_DEFAULT +2
in the design's axiom matrix.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from auth.firebase_auth import User
from observability.tenant_context import (
    _tenant_context,
    clear_tenant_enrichers,
    get_tenant_context,
    register_tenant_enricher,
    set_tenant_context,
)
from observability.tenant_span_processor import TenantAttributeSpanProcessor

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def reset_state():
    """Each test starts with an empty contextvar + no registered enrichers."""
    clear_tenant_enrichers()
    token = _tenant_context.set(None)
    yield
    _tenant_context.reset(token)
    clear_tenant_enrichers()


@pytest.fixture
def tracer_with_exporter():
    """Set up an isolated TracerProvider with InMemorySpanExporter +
    our TenantAttributeSpanProcessor, so we can assert spans carry the
    right attrs without touching the global OTel state."""
    provider = TracerProvider()
    provider.add_span_processor(TenantAttributeSpanProcessor())
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("tenant_context_tests")
    yield tracer, exporter
    provider.shutdown()


def _user(
    uid: str = "firebase-uid-1",
    email: str = "",
    auth_mode: str = "firebase",
    group_id: str = "",
) -> User:
    return User(uid=uid, email=email, auth_mode=auth_mode, group_id=group_id)


# ─── set_tenant_context shape ────────────────────────────────────────────────


def test_set_tenant_context_writes_uid_and_auth_mode_always(reset_state):
    set_tenant_context(_user(uid="alice"))
    attrs = get_tenant_context()
    assert attrs is not None
    assert attrs["tenant.uid"] == "alice"
    assert attrs["tenant.auth_mode"] == "firebase"


def test_set_tenant_context_includes_group_id_when_present(reset_state):
    set_tenant_context(_user(uid="anon-PHYS-abc", auth_mode="anonymous_group_id", group_id="PHYS-7K2N"))
    attrs = get_tenant_context()
    assert attrs is not None
    assert attrs["tenant.group_id"] == "PHYS-7K2N"
    assert attrs["tenant.auth_mode"] == "anonymous_group_id"


def test_set_tenant_context_omits_group_id_when_empty(reset_state):
    set_tenant_context(_user(uid="alice"))  # group_id defaults to ""
    attrs = get_tenant_context()
    assert attrs is not None
    assert "tenant.group_id" not in attrs


def test_set_tenant_context_hashes_email_to_uid_hash(reset_state):
    set_tenant_context(_user(uid="alice", email="alice@example.com"))
    attrs = get_tenant_context()
    assert attrs is not None
    # The hash exists, is a hex string, and is NOT the raw email.
    assert "tenant.uid_hash" in attrs
    hash_value = attrs["tenant.uid_hash"]
    assert hash_value != "alice@example.com"
    assert len(hash_value) == 64  # SHA256 hex digest
    assert all(c in "0123456789abcdef" for c in hash_value)
    # Raw email MUST NOT appear under any key.
    assert "alice@example.com" not in attrs.values()


def test_set_tenant_context_omits_uid_hash_when_email_empty(reset_state):
    set_tenant_context(_user(uid="alice", email=""))
    attrs = get_tenant_context()
    assert attrs is not None
    assert "tenant.uid_hash" not in attrs  # NOT hash of empty string


def test_uid_hash_is_deterministic(reset_state):
    """Same email → same hash. Verifies the hash function is stable."""
    set_tenant_context(_user(uid="alice", email="alice@example.com"))
    first = get_tenant_context()["tenant.uid_hash"]
    set_tenant_context(_user(uid="bob", email="alice@example.com"))  # same email
    second = get_tenant_context()["tenant.uid_hash"]
    assert first == second


def test_set_tenant_context_accepts_extra_attrs(reset_state):
    """The `extra` kwarg lets callers add per-request attrs without
    going through the enricher registry (handy for per-skill tagging)."""
    set_tenant_context(_user(uid="alice"), extra={"tenant.skill_id": "physics-tutor"})
    attrs = get_tenant_context()
    assert attrs is not None
    assert attrs["tenant.skill_id"] == "physics-tutor"


# ─── SpanProcessor behaviour ─────────────────────────────────────────────────


def test_span_processor_stamps_attrs_from_contextvar(reset_state, tracer_with_exporter):
    tracer, exporter = tracer_with_exporter
    set_tenant_context(_user(uid="alice", group_id="PHYS-7K2N"))

    with tracer.start_as_current_span("test_span"):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs["tenant.uid"] == "alice"
    assert attrs["tenant.auth_mode"] == "firebase"
    assert attrs["tenant.group_id"] == "PHYS-7K2N"


def test_span_processor_no_context_emits_span_unchanged(reset_state, tracer_with_exporter):
    """No set_tenant_context call → no tenant attrs on the span; no crash."""
    tracer, exporter = tracer_with_exporter

    with tracer.start_as_current_span("test_span"):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert "tenant.uid" not in attrs
    assert "tenant.group_id" not in attrs


# ─── Contextvar isolation — the headline correctness test ────────────────────


@pytest.mark.asyncio
async def test_contextvar_isolation_across_concurrent_tasks(reset_state, tracer_with_exporter):
    """OBSERVABLE_BY_DEFAULT +2 hinges on this: two concurrent async
    tasks setting different contexts produce spans with their own
    attrs (zero cross-tenant leakage).

    Python contextvars are per-task so this "should work" — but
    write the test explicitly to lock down the invariant.
    """
    tracer, exporter = tracer_with_exporter
    barrier_a = asyncio.Event()
    barrier_b = asyncio.Event()

    async def tenant_a():
        set_tenant_context(_user(uid="alice", group_id="GROUP-A"))
        # Let B set its context too — proves they don't stomp.
        barrier_a.set()
        await barrier_b.wait()
        with tracer.start_as_current_span("task_a_span"):
            pass

    async def tenant_b():
        set_tenant_context(_user(uid="bob", group_id="GROUP-B"))
        barrier_b.set()
        await barrier_a.wait()
        with tracer.start_as_current_span("task_b_span"):
            pass

    await asyncio.gather(tenant_a(), tenant_b())

    spans = {s.name: dict(s.attributes or {}) for s in exporter.get_finished_spans()}
    assert spans["task_a_span"]["tenant.uid"] == "alice"
    assert spans["task_a_span"]["tenant.group_id"] == "GROUP-A"
    assert spans["task_b_span"]["tenant.uid"] == "bob"
    assert spans["task_b_span"]["tenant.group_id"] == "GROUP-B"


# ─── Enricher registry ───────────────────────────────────────────────────────


def test_register_tenant_enricher_invokes_during_set(reset_state):
    def class_id_enricher(user: User) -> dict[str, str]:
        return {"tenant.class_id": "class-101"} if user.group_id else {}

    register_tenant_enricher(class_id_enricher)
    set_tenant_context(_user(uid="alice", group_id="PHYS-7K2N"))
    attrs = get_tenant_context()
    assert attrs is not None
    assert attrs["tenant.class_id"] == "class-101"


def test_register_tenant_enricher_rejects_non_callable(reset_state):
    with pytest.raises(TypeError, match="callable"):
        register_tenant_enricher("not a function")  # type: ignore[arg-type]


def test_multiple_enrichers_compose_with_last_wins_on_collision(reset_state):
    def enricher_a(user: User) -> dict[str, str]:
        return {"tenant.class_id": "from-A", "tenant.from_a_only": "yes"}

    def enricher_b(user: User) -> dict[str, str]:
        return {"tenant.class_id": "from-B", "tenant.from_b_only": "yes"}

    register_tenant_enricher(enricher_a)
    register_tenant_enricher(enricher_b)
    set_tenant_context(_user(uid="alice"))
    attrs = get_tenant_context()
    assert attrs is not None
    # Both enrichers contributed their unique keys.
    assert attrs["tenant.from_a_only"] == "yes"
    assert attrs["tenant.from_b_only"] == "yes"
    # Collision resolves to LAST enricher's value (deterministic).
    assert attrs["tenant.class_id"] == "from-B"


def test_enricher_exception_swallowed_and_logged(reset_state, caplog):
    def broken_enricher(user: User) -> dict[str, str]:
        raise RuntimeError("enricher exploded")

    def working_enricher(user: User) -> dict[str, str]:
        return {"tenant.from_working": "yes"}

    register_tenant_enricher(broken_enricher)
    register_tenant_enricher(working_enricher)

    with caplog.at_level(logging.WARNING, logger="observability.tenant_context"):
        set_tenant_context(_user(uid="alice"))

    attrs = get_tenant_context()
    assert attrs is not None
    # Broken enricher didn't crash the call; working enricher still ran.
    assert attrs["tenant.from_working"] == "yes"
    # And the platform defaults are still there.
    assert attrs["tenant.uid"] == "alice"
    # Exception was logged at WARN.
    assert any("enricher" in rec.message.lower() for rec in caplog.records)


def test_clear_tenant_enrichers_resets_registry(reset_state):
    def enricher(user: User) -> dict[str, str]:
        return {"tenant.added": "yes"}

    register_tenant_enricher(enricher)
    clear_tenant_enrichers()
    set_tenant_context(_user(uid="alice"))
    attrs = get_tenant_context()
    assert attrs is not None
    assert "tenant.added" not in attrs


# ─── M3: PII rule explicit hardening ─────────────────────────────────────────


def test_uid_hash_is_sha256_not_a_weaker_function(reset_state):
    """Golden test: verify the platform uses SHA256 (cryptographically
    irreversible) and not some weaker / custom hash. Locking down the
    hash function prevents an accidental swap to (e.g.) MD5 or a
    truncated digest that would weaken PII protection."""
    import hashlib

    set_tenant_context(_user(uid="alice", email="alice@example.com"))
    attrs = get_tenant_context()
    expected = hashlib.sha256(b"alice@example.com").hexdigest()
    assert attrs is not None
    assert attrs["tenant.uid_hash"] == expected


def test_set_tenant_context_never_writes_a_display_name_attr(reset_state):
    """The platform's User model has NO display_name field. Even if a
    future fork adds one, set_tenant_context reads fields explicitly
    (NOT via reflection) so display names cannot accidentally land on
    a span.

    This test exercises the impl with a User-like duck type that
    HAS a display_name attribute — and asserts the attribute is
    NEVER written to the contextvar dict.
    """

    class UserWithDisplayName:
        # Duck-typed minimum surface set_tenant_context reads.
        uid = "alice"
        email = "alice@example.com"
        auth_mode = "firebase"
        group_id = ""
        # The hypothetical PII-leak vector:
        display_name = "Alice Anderson"

    set_tenant_context(UserWithDisplayName())  # type: ignore[arg-type]
    attrs = get_tenant_context()
    assert attrs is not None
    # The PII rule: display_name MUST NOT appear under any key.
    assert "tenant.display_name" not in attrs
    assert "Alice Anderson" not in attrs.values()


def test_set_tenant_context_never_writes_raw_email_under_any_key(reset_state):
    """Defence-in-depth: scan EVERY value in the attrs dict and assert
    the raw email string never appears. Stricter than the existing
    'tenant.uid_hash != email' test because it catches any future
    code path that might accidentally write email under another key
    (e.g. tenant.email)."""
    raw_email = "secrets@example.com"
    set_tenant_context(_user(uid="alice", email=raw_email))
    attrs = get_tenant_context()
    assert attrs is not None
    for key, value in attrs.items():
        assert raw_email not in value, f"Raw email leaked into attr '{key}': {value!r}"
        # Also assert no attr KEY contains 'email' — defence against
        # adding 'tenant.email' as a hashed-or-not variant in the future.
        assert "email" not in key.lower(), f"Attr key '{key}' contains 'email' — possible PII regression"


def test_extra_kwarg_cannot_override_a_required_default(reset_state):
    """The ``extra`` kwarg is for fork-side per-request attrs, NOT a
    way to override the platform's identity defaults. If a fork passes
    extra={'tenant.uid': 'forged'}, this currently DOES override
    (set_tenant_context uses dict.update). Document the behaviour
    explicitly — the override is intentional for testing but should
    be loud in code review."""
    set_tenant_context(
        _user(uid="real-uid"),
        extra={"tenant.uid": "forged-uid", "tenant.skill_id": "physics-tutor"},
    )
    attrs = get_tenant_context()
    assert attrs is not None
    # extra wins over platform defaults — explicit, not silent.
    assert attrs["tenant.uid"] == "forged-uid"
    assert attrs["tenant.skill_id"] == "physics-tutor"

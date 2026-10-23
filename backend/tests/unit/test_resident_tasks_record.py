"""`resident_tasks` — the correlation record behind the push webhook (M5a.1).

The webhook body is an A2A task and nothing else, so this record is the only
thing that can answer "whose conversation does this answer belong in". It is
also the ONLY credential store for the receiver: there is no OIDC gate on that
route (the instance sends the push config's bearer, not a Google ID token —
HANDOVER-2026-09-04 §9.3), so the token rules below are the security of the
feature, not hygiene around it.

The assertions that earn their place are the negative ones. A verifier tested
only on the right token passes while accepting everything.
"""

from __future__ import annotations

import inspect

import pytest


@pytest.fixture
def store(monkeypatch):
    """Real code path against the in-memory Firestore — same implementation
    LOCAL_MODE runs on, so a passing test here is not a mock agreeing with
    itself."""
    from db import firestore as fs
    from db.firestore_inmemory import InMemoryFirestoreClient

    monkeypatch.setattr(fs, "_client", InMemoryFirestoreClient())
    from db import resident_tasks

    return resident_tasks


def _create(store, task_id="task-1"):
    return store.create(
        task_id,
        user_id="uid-1",
        session_id="sess-1",
        app_name="aitana_platform",
    )


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------


def test_the_stored_hash_is_actually_the_hash_of_the_token(store):
    """`token not in repr(raw)` alone passes for `hash = token[::-1]`."""
    import hashlib

    _rec, token = _create(store)
    from db import firestore as fs

    raw = fs.get_client().collection(store.COLLECTION).document("task-1").get().to_dict()
    assert raw["tokenHash"] == hashlib.sha256(token.encode()).hexdigest()


def test_create_returns_the_token_once_and_stores_only_its_hash(store):
    """A Firestore dump must not yield a working webhook credential — the same
    rule `resident_agents.keyHash` follows (design P7)."""
    rec, token = _create(store)
    assert token, "the plaintext token is returned to the caller exactly once"

    from db import firestore as fs

    raw = fs.get_client().collection(store.COLLECTION).document("task-1").get().to_dict()
    assert token not in repr(raw), "the plaintext token must never be persisted"
    assert raw["tokenHash"] != token
    assert rec.token_hash == raw["tokenHash"]


def test_two_records_never_share_a_token(store):
    _, a = _create(store, "task-a")
    _, b = _create(store, "task-b")
    assert a != b


def test_verify_token_accepts_the_right_token(store):
    _, token = _create(store)
    rec = store.verify_token("task-1", token)
    assert rec is not None
    assert rec.session_id == "sess-1"
    assert rec.user_id == "uid-1"


def test_verify_token_rejects_a_wrong_token_of_equal_length(store):
    """Equal length on purpose: a comparison that only checks length, or that
    short-circuits on the first byte, passes a naive wrong-token test."""
    _, token = _create(store)
    wrong = ("x" * len(token))[: len(token)]
    assert len(wrong) == len(token)
    assert store.verify_token("task-1", wrong) is None


def test_verify_token_rejects_when_no_record_exists(store):
    """An unknown task id and a wrong token are the same answer to the caller —
    the route turns both into one 403."""
    assert store.verify_token("never-created", "anything") is None


def test_verify_token_rejects_an_empty_presented_token(store):
    _create(store)
    assert store.verify_token("task-1", "") is None


def test_verify_token_compares_in_constant_time(store):
    """Asserted by reading the source: a timing property cannot be observed
    reliably in a unit test, but its absence can be seen in the code."""
    src = inspect.getsource(store.verify_token)
    assert "compare_digest" in src, "token comparison must use hmac.compare_digest"


# ---------------------------------------------------------------------------
# Delivery, which happens more than once
# ---------------------------------------------------------------------------


def test_mark_delivered_is_idempotent(store):
    """The instance notifies on EVERY setState, so a duplicate `completed` is
    the normal case rather than the exception."""
    _create(store)
    assert store.mark_delivered("task-1") is True, "the first call performs the delivery"
    assert store.mark_delivered("task-1") is False, "the second reports already-delivered"


def test_mark_delivered_on_an_unknown_task_is_false_not_an_exception(store):
    assert store.mark_delivered("never-created") is False


def test_delivered_at_is_stamped_once_and_not_moved(store):
    _create(store)
    store.mark_delivered("task-1")
    first = store.get("task-1").delivered_at
    store.mark_delivered("task-1")
    assert store.get("task-1").delivered_at == first


# ---------------------------------------------------------------------------
# State, and what the record is for
# ---------------------------------------------------------------------------


def test_record_is_readable_immediately_after_create(store):
    """`ask_assistant` writes this BEFORE the A2A send, so a webhook that beats
    the send's own response still resolves. A create that only became visible
    later would put a 403 on the first legitimate push."""
    _create(store)
    assert store.get("task-1") is not None


def test_record_state_does_not_clobber_the_correlation(store):
    _create(store)
    store.record_state("task-1", "input-required")
    rec = store.get("task-1")
    assert rec.state == "input-required"
    assert rec.session_id == "sess-1", "the reason the record exists must survive a state update"
    assert rec.token_hash, "the credential must survive a state update"


def test_record_state_on_an_unknown_task_is_a_no_op(store):
    store.record_state("never-created", "working")
    assert store.get("never-created") is None


def test_a_task_id_with_a_slash_is_refused(store):
    """Firestore document ids cannot contain '/', and a task id is chosen by
    the peer. Refusing loudly beats a path-shaped write."""
    with pytest.raises(ValueError):
        store.create("a/b", user_id="u", session_id="s", app_name="app")


def test_release_delivery_gives_the_claim_back(store):
    """A claim spent on a write that then failed would stand in for a message
    nobody got — the exact silent loss the claim exists to prevent."""
    _create(store)
    assert store.mark_delivered("task-1") is True
    store.release_delivery("task-1", error="session write failed")
    assert store.get("task-1").delivered_at is None
    assert store.mark_delivered("task-1") is True, "a released claim can be taken again"


def test_release_delivery_on_an_unknown_task_is_a_no_op(store):
    store.release_delivery("never-created", error="whatever")


# ── added after the M5a.2 adversarial review ────────────────────────────────


def test_a_token_is_good_for_ITS_task_and_no_other(store):
    """The gap the first test suite left: `unknown task` used a MISSING record,
    which is the wrong branch. A verify_token that looked the hash up across
    the collection, or ignored task_id entirely, passed every test."""
    _create(store, "task-a")
    _, token_b = _create(store, "task-b")
    assert store.verify_token("task-b", token_b) is not None
    assert store.verify_token("task-a", token_b) is None


def test_an_expired_token_is_refused(store):
    """A record whose send then failed would otherwise leave a live webhook
    credential in Firestore for ever."""
    _rec, token = store.create("task-old", user_id="u", session_id="s", app_name="app", ttl_hours=-1)
    assert store.verify_token("task-old", token) is None


def test_ids_firestore_itself_would_reject_are_refused_before_any_io(store):
    """`.`, `..` and `__x__` raise InvalidArgument on the real client — which is
    NOT a ValueError, so it escaped the gate as a 500 while the same input
    returned 403 under LOCAL_MODE."""
    for bad in (".", "..", "__x__", "_leading", "a" * 200, "a b", ""):
        with pytest.raises(ValueError):
            store.get(bad)


def test_a_terminal_state_is_not_overwritten_by_a_late_working(store):
    """The peer's notifies are fire-and-forget and can arrive out of order."""
    _create(store)
    store.record_state("task-1", "completed")
    store.record_state("task-1", "working")
    assert store.get("task-1").state == "completed"


def test_the_answer_is_recorded_in_the_same_write_as_the_claim(store):
    """A process killed a millisecond after claiming must leave a record that
    still knows what was supposed to be said — nothing re-delivers."""
    _create(store)
    assert store.mark_delivered("task-1", answer="the answer") is True
    assert store.get("task-1").answer == "the answer"


def test_an_oversized_answer_is_capped(store):
    _create(store)
    store.mark_delivered("task-1", answer="x" * (store.MAX_ANSWER_CHARS + 5000))
    assert len(store.get("task-1").answer) == store.MAX_ANSWER_CHARS


def test_releasing_says_why_and_keeps_the_answer(store):
    """An undelivered answer must be recoverable, not merely regrettable."""
    _create(store)
    store.mark_delivered("task-1", answer="the answer")
    store.release_delivery("task-1", error="session no longer exists")
    rec = store.get("task-1")
    assert rec.delivered_at is None
    assert rec.delivery_error == "session no longer exists"
    assert rec.answer == "the answer", "the text must survive the failure that lost it"

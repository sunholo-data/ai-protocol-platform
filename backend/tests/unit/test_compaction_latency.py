"""Routine compaction stays out of the user's turn (COMPACTION-LATENCY M1).

MEASURED, 2026-08-06, 18 real turns against a live backend:

    TTFT   14,682 -> 28,326 ms   (+13.6s)
    tail    3,141 -> 31,330 ms   (+28.2s)
    worst turn: 90s total — and EVERY compacting turn compacted TWICE.

Twice because ADK runs two compaction paths and both fired on the same turn.
The pre-request one lands in TTFT: the user waits, looking at nothing, while we
summarise a conversation already on their screen. The post-invocation one runs
at the end of a turn — already the right moment, since that is when the user
starts reading.

The fix rests entirely on the two paths reading DIFFERENT config objects, so
that is pinned here as an ADK contract: if a future google-adk merges them,
`make adk-conformance` fails loudly rather than silently restoring 13.6s of
TTFT that nobody would think to re-measure.
"""

from __future__ import annotations

import pytest
from google.adk.apps.app import EventsCompactionConfig

from adk.session import emergency_compaction_config, get_compaction_config

pytestmark = pytest.mark.adk_contract


class TestEmergencyIsAnAbsoluteLine:
    """The assertion the first cut of these tests was missing.

    v1 set emergency = routine * 3 and every test passed, because they only
    asserted "higher than routine". A RELATIVE threshold rises with the routine
    one, so a large conversation crosses both and the pre-request path fires
    anyway. Measured live: turn 15 compacted once with TTFT at baseline (fixed),
    turn 16 compacted twice with TTFT +14s (defeated).

    So assert what emergency has to MEAN — a fraction of the model's real
    context window — not merely that it is bigger than the routine value.
    """

    def test_threshold_is_derived_from_the_model_window(self):
        from adk.session import _EMERGENCY_WINDOW_FRACTION, context_window_for

        base = get_compaction_config("gemini-2.5-flash")
        demoted = emergency_compaction_config(base, "gemini-3.6-flash")
        expected = int(context_window_for("gemini-3.6-flash") * _EMERGENCY_WINDOW_FRACTION)
        assert demoted.token_threshold == expected

    def test_it_does_not_track_the_routine_threshold(self):
        """The regression guard. Halve the routine threshold and emergency must
        NOT halve with it — that relationship is what failed in measurement."""
        from google.adk.apps.app import EventsCompactionConfig

        low = EventsCompactionConfig(
            compaction_interval=40, overlap_size=5, token_threshold=3_000, event_retention_size=60
        )
        high = EventsCompactionConfig(
            compaction_interval=40, overlap_size=5, token_threshold=250_000, event_retention_size=60
        )
        a = emergency_compaction_config(low, "gemini-3.6-flash").token_threshold
        b = emergency_compaction_config(high, "gemini-3.6-flash").token_threshold
        assert a == b, (
            "emergency threshold moves with the routine one — a big conversation "
            "will cross both and the pre-request path fires anyway"
        )

    def test_a_forced_low_threshold_still_gets_a_high_emergency_line(self):
        """The exact configuration the live run used (COMPACTION_TOKEN_THRESHOLD=3000).

        Under v1 this produced an emergency of 9,000, which a 16-turn
        conversation blew straight through.
        """
        from google.adk.apps.app import EventsCompactionConfig

        forced = EventsCompactionConfig(
            compaction_interval=40, overlap_size=5, token_threshold=3_000, event_retention_size=60
        )
        demoted = emergency_compaction_config(forced, "gemini-3.6-flash")
        assert demoted.token_threshold > 100_000, (
            f"emergency threshold is only {demoted.token_threshold}; a long "
            "conversation will cross it and pay the TTFT cost again"
        )

    def test_unknown_model_falls_back_to_the_smallest_window(self):
        from adk.session import _FALLBACK_CONTEXT_WINDOW, context_window_for

        assert context_window_for("some-fork-model") == _FALLBACK_CONTEXT_WINDOW

    def test_never_lowers_the_threshold(self):
        """On a small-window model the derived line can land BELOW routine.
        Lowering it would make the pre-request path fire more eagerly — the
        opposite of the point."""
        from google.adk.apps.app import EventsCompactionConfig

        huge = EventsCompactionConfig(
            compaction_interval=40, overlap_size=5, token_threshold=900_000, event_retention_size=60
        )
        demoted = emergency_compaction_config(huge, "claude-sonnet-5")
        assert demoted.token_threshold >= huge.token_threshold


class TestEmergencyDemotion:
    def test_threshold_is_raised(self):
        base = get_compaction_config("gemini-2.5-flash")
        demoted = emergency_compaction_config(base, "gemini-3.6-flash")
        assert demoted.token_threshold > base.token_threshold, (
            "pre-request compaction would still fire at the routine threshold, "
            "putting a summarisation call back in front of the first token"
        )

    def test_the_input_is_not_mutated(self):
        """These configs are shared, and ADK itself mutates them in place.

        A demotion that mutated its input would raise the App's threshold too,
        disabling the post-invocation path — leaving the conversation to grow
        unbounded, which is the bug the previous sprint just fixed.
        """
        base = get_compaction_config("gemini-2.5-flash")
        original = base.token_threshold
        emergency_compaction_config(base, "gemini-3.6-flash")
        assert base.token_threshold == original

    def test_retention_and_backstop_are_untouched(self):
        base = get_compaction_config("gemini-2.5-flash")
        demoted = emergency_compaction_config(base, "gemini-3.6-flash")
        assert demoted.event_retention_size == base.event_retention_size
        assert demoted.compaction_interval == base.compaction_interval
        assert demoted.overlap_size == base.overlap_size

    def test_the_safety_net_is_raised_not_removed(self):
        """Demoted, never disabled.

        The pre-request path is what stops a turn exceeding the model's context
        window. A failed turn is worse than a slow one, so it must still fire
        for a genuinely at-risk turn.
        """
        base = get_compaction_config("gemini-2.5-flash")
        demoted = emergency_compaction_config(base, "gemini-3.6-flash")
        assert demoted.token_threshold is not None
        assert demoted.event_retention_size is not None

    def test_config_without_a_token_trigger_is_returned_unchanged(self):
        cfg = EventsCompactionConfig(compaction_interval=10, overlap_size=3)
        assert emergency_compaction_config(cfg, "gemini-3.6-flash") is cfg


class TestTheCallbackActuallyAppliesIt:
    """A correct helper nobody calls is exactly how compaction shipped inert.

    `test_session_factories.py` asserted `get_compaction_config` returned the
    right values for months, while `app.py` passed a hardcoded model and no
    session ever received them. So assert the wiring, not just the function.
    """

    def _ctx(self, config, model_api_name: str = "gemini-3.6-flash"):
        """A callback context shaped like the real one.

        The agent carries a model whose `.model` is an API NAME, because that is
        what a live agent holds — and resolving api names is exactly what the
        first implementation got wrong.
        """

        class _Model:
            model = model_api_name

        class _Agent:
            model = _Model()

        class _ICtx:
            events_compaction_config = config
            agent = _Agent()

        class _CbCtx:
            _invocation_context = _ICtx()
            state: dict = {}  # noqa: RUF012 — test stub, one instance per call

        return _CbCtx()

    def test_demotion_is_applied_to_the_invocation_context(self):
        from adk.callbacks import _demote_pre_request_compaction

        base = get_compaction_config("gemini-2.5-flash")
        ctx = self._ctx(base)
        _demote_pre_request_compaction(ctx)
        assert ctx._invocation_context.events_compaction_config.token_threshold > base.token_threshold

    def test_a_missing_invocation_context_is_survivable(self):
        """Fail-open: this is a latency optimisation. A turn that runs at the
        routine threshold is slower, not broken — never raise from here."""
        from adk.callbacks import _demote_pre_request_compaction

        class _Bare:
            state: dict = {}  # noqa: RUF012 — test stub

        _demote_pre_request_compaction(_Bare())  # must not raise

    def test_a_missing_config_is_survivable(self):
        from adk.callbacks import _demote_pre_request_compaction

        _demote_pre_request_compaction(self._ctx(None))  # must not raise

    def test_the_real_before_agent_callback_wires_it(self):
        """Through the production factory, not the private helper."""
        from adk.callbacks import make_before_agent

        base = get_compaction_config("gemini-2.5-flash")
        ctx = self._ctx(base)
        make_before_agent("test-skill")(ctx)
        assert ctx._invocation_context.events_compaction_config.token_threshold > base.token_threshold, (
            "make_before_agent does not apply the demotion — routine compaction "
            "is still landing in TTFT on every real turn"
        )


class TestTheTwoPathsReadDifferentConfigs:
    """The ADK contract the whole fix depends on.

    Pre-request compaction reads `invocation_context.events_compaction_config`;
    post-invocation reads `app.events_compaction_config`. Demoting one without
    the other only works while that stays true.
    """

    def test_invocation_context_carries_its_own_compaction_config(self):
        from google.adk.agents.invocation_context import InvocationContext

        assert "events_compaction_config" in InvocationContext.model_fields, (
            "InvocationContext no longer carries its own events_compaction_config — "
            "the per-invocation demotion has nowhere to write, and routine "
            "compaction is back in TTFT."
        )

    def test_the_pre_request_processor_reads_the_invocation_context(self):
        """Reads ADK's source rather than trusting the field's existence.

        The field could survive while the processor stops reading it — same
        silent regression, so assert the actual read.
        """
        import inspect

        from google.adk.flows.llm_flows import compaction as flow_compaction

        src = inspect.getsource(flow_compaction.CompactionRequestProcessor.run_async)
        assert "invocation_context.events_compaction_config" in src, (
            "the pre-request processor no longer reads the per-invocation config; "
            "the M1 demotion is a no-op and TTFT has silently regressed"
        )

    def test_the_post_invocation_path_reads_the_app(self):
        """The other half: post-invocation must NOT read the demoted copy, or
        demoting it would disable routine compaction entirely."""
        import inspect

        from google.adk.apps import compaction as app_compaction

        src = inspect.getsource(app_compaction._run_compaction_for_sliding_window)
        assert "app.events_compaction_config" in src, (
            "post-invocation compaction no longer reads the App config — the "
            "demotion may now be suppressing ALL compaction, not just the "
            "pre-request path"
        )

"""Compaction summarizer — what survives when history is condensed.

Compaction is LOSSY AND IRREVERSIBLE: the summary this produces REPLACES the
raw turns in every subsequent model request, and nothing downstream can reach
past it. Whatever this drops is gone for the rest of the conversation. So the
summarizer is a correctness surface, not a formatting detail.

Two problems with ADK's stock `LlmEventSummarizer` made an explicit one
necessary rather than nice-to-have:

1. **It drops tool results entirely.** `_format_events_for_prompt` keeps only
   `part.text`, skipping `function_call` / `function_response`. For this
   platform the substance IS the tool output — extracted clauses, contract
   comparisons, obligation timelines, BigQuery results. Left as-is, a compacted
   PPA conversation summarises the chat *around* the analysis and silently
   discards the analysis.

2. **The default binds itself to a shared config object.** ADK's
   `_ensure_compaction_summarizer` does `config.summarizer = LlmEventSummarizer(
   llm=agent.canonical_model)` — an in-place mutation. Our configs are shared
   (module-level in `adk/session.py`, and `from_app` shallow-copies the App per
   request), so the FIRST skill to compact would pin its own model as the
   summarizer for every skill afterwards: a `lite` front door compacting first
   would leave Claude and `pro` skills summarising on flash-lite for the life of
   the container. Setting `summarizer` explicitly makes
   `_ensure_compaction_summarizer` return early, which closes that hole.

The prompt also matters. ADK's default asks for a summary that is "concise" and
captures "the essence" — reasonable for chit-chat, wrong for contract review,
where the specifics ARE the content. A strike price, a party name, or a clause
reference paraphrased away is indistinguishable from one that was never said.
"""

from __future__ import annotations

import logging

from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.events.event import Event

logger = logging.getLogger(__name__)

# Preserve specifics over brevity. Concision is what a summariser optimises for
# by default, and it is precisely wrong here: the user established these facts
# over many turns and will refer back to them by name.
#
# REVISED 2026-08-06 after reading real summaries for the first time. The first
# version said "carry tool results across" and "preserve every identifier", and
# both were too blunt — measured over a 5-compaction session, EVERY summary
# reproduced a 20-document directory listing complete with 20 raw
# `[ref: <uuid>]` handles, in perpetuity. A `list_documents` result is not a
# finding; it is environment state the tool re-fetches on demand, and copying it
# forward crowds out the actual work while baking opaque ids into context the
# model re-reads every turn (against CLAUDE.md #9, which warns that AIs
# hallucinate and transform exactly these).
#
# Hence the findings-vs-environment distinction below, and the explicit ban on
# opaque ids. See docs/projects/compaction/README.md §3.1.
FIDELITY_PROMPT_TEMPLATE = (
    "You are compacting the earlier part of a working conversation so it can "
    "continue within a context limit. Your summary REPLACES those turns — the "
    "assistant will never see the originals again, so anything you omit is "
    "permanently lost.\n\n"
    "Preserve, verbatim and in full:\n"
    "- Every number, price, percentage, quantity, currency amount, date and deadline\n"
    "- Every clause, section or article reference, and what it was said to mean\n"
    "- Every party, counterparty, person and business identifier the USER or the "
    "documents use (contract refs, project names, site names)\n"
    "- Every conclusion reached, decision made, and correction the user issued\n"
    "- Every open question, unresolved disagreement and outstanding task\n"
    "- Any explicit instruction the user gave about how to work "
    "(tone, format, language, what to avoid)\n\n"
    "FINDINGS vs ENVIRONMENT — this distinction matters more than any other:\n"
    "- A FINDING is what a tool discovered or computed: extracted clauses, "
    "comparison results, query rows, calculated figures, retrieved passages. "
    "Carry findings across in full — they are the work, and they cannot be "
    "recovered once these turns are gone.\n"
    "- ENVIRONMENT STATE is what merely exists: which documents are available, "
    "which tools were listed, directory or inventory output, connection status. "
    "Do NOT reproduce it. The assistant can re-query it at any time, and copying "
    "it forward crowds out the actual work. At most say what was being worked on "
    "('the two 2023 Spain PPAs'), never reproduce the listing.\n\n"
    "NEVER carry across opaque system identifiers — UUIDs, `[ref: …]` handles, "
    "`gs://` paths, internal document or session ids. They are machine addressing, "
    "they are meaningless to a reader, and repeating them invites the assistant to "
    "quote or transform them incorrectly. Refer to things by their human name "
    "(the filename, the party, the project).\n\n"
    "Do NOT compress by generalising. 'Discussed pricing' is a failure; "
    "'strike price agreed at EUR 48.62/MWh, indexed annually to CPI' is correct. "
    "Prefer a long summary that keeps the facts over a short one that reads well — "
    "but length must come from FINDINGS, never from restating the environment.\n"
    "Attribute statements to the user or the assistant where it matters.\n\n"
    "Conversation to compact:\n\n{conversation_history}"
)

# Tool payloads can be enormous (a full clause extraction, a BigQuery page).
# Sending them whole into the summariser risks blowing the very context limit
# compaction exists to relieve, so each is capped. Generous, because truncating
# a finding is the failure mode we are trying to avoid — this is a backstop
# against pathological payloads, not a compression strategy.
_MAX_TOOL_PAYLOAD_CHARS = 4000


def _truncate(text: str, limit: int = _MAX_TOOL_PAYLOAD_CHARS) -> str:
    """Cap a payload, and SAY SO when capped.

    A silent truncation would let the summariser treat a partial result as the
    whole finding and state it with unearned confidence (CLAUDE.md #8 applied to
    the model's own input).
    """
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… [truncated, {len(text) - limit} more chars]"


# CUSTOM AG-UI event name for "history was summarised". Rides the same pending
# queue as MODEL_RETRY / MODEL_FALLBACK, drained by `stream_agui_events`.
COMPACTION_EVENT_NAME = "HISTORY_COMPACTED"

# Fired BEFORE summarisation begins. `HISTORY_COMPACTED` reports a completed
# compaction and therefore lands ~35s later, alongside RUN_FINISHED; this one
# tells the client the ANSWER is finished and only housekeeping remains, so the
# composer can re-enable instead of spinning through it.
COMPACTION_STARTED_EVENT_NAME = "COMPACTION_STARTED"


class FidelityEventSummarizer(LlmEventSummarizer):
    """`LlmEventSummarizer` that can see tool calls and tool results, and says
    when it has run.

    Only `_format_events_for_prompt` and `maybe_summarize_events` are
    overridden — the trigger logic, event construction and timestamps stay
    ADK's. We are widening what the summariser is shown and announcing the
    result, not reimplementing compaction (Axiom #6).
    """

    def _format_events_for_prompt(self, events: list[Event]) -> str:
        lines: list[str] = []
        for event in events:
            if not (event.content and event.content.parts):
                continue
            author = event.author or "unknown"
            for part in event.content.parts:
                # Thoughts are the model's scratchpad, not conversation, and
                # they are already excluded from the request contents. Keeping
                # them would spend summary budget on reasoning the user never
                # saw and that no later turn can refer back to.
                if getattr(part, "thought", None):
                    continue
                if part.text:
                    lines.append(f"{author}: {part.text}")
                    continue
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    args = _truncate(str(getattr(fc, "args", "") or ""))
                    lines.append(f"{author} called tool {getattr(fc, 'name', '?')}({args})")
                    continue
                fr = getattr(part, "function_response", None)
                if fr is not None:
                    payload = _truncate(str(getattr(fr, "response", "") or ""))
                    lines.append(f"tool {getattr(fr, 'name', '?')} returned: {payload}")
        return "\n".join(lines)

    async def maybe_summarize_events(self, *, events):
        """Summarise, then announce it (CLAUDE.md #8 — NEVER SILENT).

        Compaction silently rewrites what the assistant can remember: the user
        keeps seeing a full transcript while the model sees a summary, so a
        degraded answer looks identical to a good one. That invisibility is
        what made the 2026-08-06 issue undiagnosable and forced this sprint to
        reverse-engineer the mechanism from raw session events.

        METADATA ONLY on the wire. Summaries derive from customer conversation
        content, and the stream boundary already withholds tool payloads from
        lower-trust group sessions
        (`stream_invariants.redact_privileged_results`) — putting summary text
        in a CUSTOM event would route straight around that gate.

        Fail-open throughout: compaction runs inside the request flow, so
        raising here would fail the user's turn, trading a silent degradation
        for a hard error. Losing the notice is the lesser harm.
        """
        # COMPACTION-LATENCY M2 — announce BEFORE the model call, not after.
        #
        # This is the whole point of the event. `HISTORY_COMPACTED` below fires
        # once summarisation RETURNS, which measurement showed is ~35s later and
        # roughly when RUN_FINISHED arrives — far too late to tell the UI
        # anything useful. Meanwhile the frontend holds `isLoading` until the run
        # finalises, so the composer sits disabled and the typing indicator spins
        # for that whole stretch, with the answer already fully rendered on
        # screen. Measured: ~37s median, up to 47s, of dead UI time.
        #
        # Emitting here says precisely "the answer is done; we are tidying up",
        # which is both the truth and the signal the client needs.
        if events:
            try:
                from observability.timing import get_current_tracker

                get_current_tracker().emit_reliability_event(
                    COMPACTION_STARTED_EVENT_NAME,
                    {"events_to_compact": len(events)},
                )
            except Exception as exc:
                logger.warning("compaction start notice not emitted (suppressed): %s", exc)

        compaction_event = await super().maybe_summarize_events(events=events)
        if compaction_event is None:
            # Nothing was compacted. Announcing a no-op would train people to
            # ignore the marker.
            return None

        # 1e — a live compaction just landed, which is the one moment a
        # second pass becomes worth scheduling (it re-derives exactly the span
        # this summary claims, from the raw events, once the session is idle).
        # The tracker is the request context: session/user ids for the task
        # payload. Fail-soft like everything else in this method — flag-off,
        # NULL tracker and queue errors all reduce to "no task", never a
        # failed turn.
        try:
            from internal_tasks.enqueue import schedule_second_pass
            from observability.timing import get_current_tracker

            comp_for_task = getattr(getattr(compaction_event, "actions", None), "compaction", None)
            end_ts = getattr(comp_for_task, "end_timestamp", None) if comp_for_task else None
            if end_ts is not None:
                tracker = get_current_tracker()
                schedule_second_pass(
                    session_id=tracker.session_id,
                    user_id=tracker.user_id,
                    compaction_end_ts=end_ts,
                )
        except Exception as exc:
            logger.warning("second-pass enqueue skipped (suppressed): %s", exc)

        try:
            from observability.timing import get_current_tracker

            summary_chars = 0
            comp = getattr(getattr(compaction_event, "actions", None), "compaction", None)
            content = getattr(comp, "compacted_content", None) if comp else None
            for part in getattr(content, "parts", None) or []:
                summary_chars += len(getattr(part, "text", "") or "")

            get_current_tracker().emit_reliability_event(
                COMPACTION_EVENT_NAME,
                {
                    "events_compacted": len(events),
                    # Length, not content: enough to spot a suspiciously tiny
                    # summary without putting the summary on the wire.
                    "summary_chars": summary_chars,
                    "start_timestamp": getattr(comp, "start_timestamp", None) if comp else None,
                    "end_timestamp": getattr(comp, "end_timestamp", None) if comp else None,
                },
            )
        except Exception as exc:
            logger.warning("compaction notice not emitted (suppressed): %s", exc)

        return compaction_event


def build_compaction_summarizer() -> LlmEventSummarizer | None:
    """The summarizer every compaction config carries.

    Pinned to one model rather than inheriting the compacting skill's, so a
    conversation's history is condensed the same way whichever skill happens to
    be answering when the threshold trips. Routed through `resolve_model_chain`
    for retry + fallback like every other model call (backend/CLAUDE.md) — a
    failed summarisation would otherwise abandon the compaction silently.

    Returns None if the model can't be resolved (an unmounted provider on a
    fork, a residency violation). ADK then falls back to its own default
    summarizer, which is worse but not broken — losing tool fidelity beats
    failing every turn once the threshold is crossed.
    """
    try:
        from adk.agent import resolve_model_chain
        from adk.compaction_settings import summarizer_model_ref

        # `pro`, not the compacting skill's tier: summarising a long technical
        # conversation without losing specifics is a harder task than most of
        # the turns being summarised, and it runs rarely enough that the cost is
        # negligible. (backend/adk/CLAUDE.md #7 — tier is a correctness
        # property, not a cost knob.)
        # TUNING-CONSOLE (1b): an admin may override the tier; the resolver
        # rejects raw api names so a bad value can't silently take a fallback.
        llm = resolve_model_chain(summarizer_model_ref("pro"))
    except Exception as exc:
        logger.warning(
            "compaction summarizer unavailable (%s); ADK will use its default, which drops tool results from summaries",
            exc,
        )
        return None
    # `prompt_template` MUST be passed. Without it the base class silently falls
    # back to ADK's `_DEFAULT_PROMPT_TEMPLATE` ("concise… the essence"), so the
    # subclass would fix tool visibility while quietly keeping the prompt that
    # paraphrases away the specifics. That exact bug survived a green test suite
    # here, because the test asserted on the CONSTANT rather than on what the
    # built summarizer uses — see test_built_summarizer_actually_uses_our_prompt.
    # TUNING-CONSOLE (1b): the prompt is the most consequential lever — what the
    # summariser is TOLD to preserve decides what survives. `summarizer_prompt`
    # falls back to the shipped template if the stored one lacks the placeholder
    # (which would otherwise raise inside a user's turn).
    from adk.compaction_settings import summarizer_prompt

    return FidelityEventSummarizer(llm=llm, prompt_template=summarizer_prompt(FIDELITY_PROMPT_TEMPLATE))

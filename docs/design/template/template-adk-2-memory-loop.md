# ADK 2 upgrade · 11 — Memory loop (scoping, pre-compaction flush, consolidation)

**Status**: Proposed
**Phase**: 2, after 03b. Consolidation also depends on scheduled runs (v6.47 `scheduled-skill-runs`) or a Cloud Scheduler job.
**Estimated**: 2–3 days
**Parent**: [template-adk-2-upgrade.md](template-adk-2-upgrade.md)
**Pattern source**: Horizon's self-improvement loop (`horizon/memory/auto_capture.py`, `docs/memory.md`): write-back, prefetch, a pre-compaction flush and a nightly "dream-review" consolidation.
**Last Updated**: 2026-10-16

## Where we are

- **Recall:** `load_memory_tool` + `preload_memory_tool` on skills with memory on (`adk/agent.py`).
- **Write:** `adk/memory_autosave.py` streams each turn's **text only** to Memory Bank `ingest_events`. Tool calls and results are dropped deliberately, because a result can be a whole confidential document. Vertex generates memories after `MEMORY_AUTOSAVE_IDLE_MINUTES` of idle time.
- **Missing:**
  - no topic scoping
  - nothing saves durable facts before compaction summarizes them away
  - no cross-session consolidation, so memories accumulate as fragments

## Design

1. **Scope generation with `allowed_topics`** (Memory Bank, 2.8; `vertex_ai_memory_bank_service.py`). Each skill declares the topics it may remember (e.g. user preferences, project facts). Memory Bank then won't mint memories from arbitrary content. This is a second line of defence behind the text-only rule. Pass `memory_id` for stable updates rather than duplicates.
2. **Pre-compaction flush.** Before live compaction summarizes a span, ingest that span's text events (still text-only) so durable facts land before the lossy summary. Hook: our `compaction_summarizer.py` subclass, or a callback ahead of it. This is the one Horizon idea worth copying from its compaction layer.
3. **Scheduled consolidation**, per user and per skill scope. A scheduled run lists the user's memories (Vertex `load_profiles`, 2.4), asks a model to merge duplicates and resolve contradictions, and writes back. Run it as a v6.47 scheduled skill run, so it shows up as a persisted, inspectable run, not a hidden cron job (Horizon's lesson: drive the same handler, or the UI is blank).
4. **Tenant boundary.** Memory scope is `(app, user)`. It is never shared across users or clients, and consolidation never reads another user's memories. Shared-bucket visibility rules for documents do **not** extend to memory.

## Tests

- Unit: `memory_autosave` still drops tool parts, and the flush path does too (pin with a fixture containing a document-sized tool result).
- Eval (Horizon-style "memory recall" rubric): state a preference in session A, start session B, and the preference is used. 10/10 on dev.
- Consolidation dry-run mode prints the proposed merge without writing. Review it on one internal account before enabling.

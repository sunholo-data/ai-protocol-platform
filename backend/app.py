"""
Aitana Platform v6 — Root agent definition.

This is the ADK agent entry point. The ADK framework discovers this file
and creates the agent + app from it.

Individual skills create sub-agents; this root agent delegates to them.

Workshop W2a — ADK: The Foundation
  The entire agent is declared here: a name, a model, an instruction, and a
  tool list. No orchestration loop, no retry logic, no token counting. ADK
  handles all of that. Sub-agents are populated at runtime from Firestore
  skill configs via the factory in adk/agent.py (W2b).
"""

import os

from google.adk.agents import Agent
from google.adk.apps import App

from adk.agent import resolve_model_chain
from adk.artifact_tools import retrieve_artifact
from adk.session import get_compaction_config
from config.gcp import PLACEHOLDER_PROJECT, resolve_gcp_project
from config.models import default_model

# Fallback project keeps module import working on CI runners (no env vars, no
# ADC) — the resolver returns None there. v6.19.0 (AIPLA #42): the default used
# to be Aitana's own dev project, which pointed every fork at OUR project unless
# they knew to override it. There is no brand default now; a placeholder that is
# obviously not a real project is safer than a real one that belongs to someone
# else, and the startup guard (config.gcp.check_startup_project) refuses to boot
# on it outside LOCAL_MODE anyway.
_FALLBACK_PROJECT = os.environ.get("PLATFORM_DEFAULT_PROJECT", PLACEHOLDER_PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", resolve_gcp_project() or _FALLBACK_PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")


# --- Root agent ---
# The root agent delegates to skill-specific sub-agents.
# In v6, each skill becomes a sub-agent created from its Firestore config.

root_agent = Agent(
    name="aitana",
    # Reliability: route the A2A / dev-UI root through the shared model chain
    # (retry + Gemini region fallback via ResilientLlm) — NOT a bare Gemini with
    # SDK-level retry_options, which multiplies attempts against the resilient
    # layer's budget (see adk/resilient_llm.py). v6.14.0 reliability sweep.
    # `lite` (not a pinned id) so this tracks the registry automatically —
    # 2026-08-13: was a hardcoded gemini-2-5-flash, which EOLs 2026-10-16.
    model=resolve_model_chain("lite"),
    instruction=(
        "You are Aitana, a helpful AI assistant. "
        "You can help with document analysis, search, data extraction, and more. "
        "Use your available tools to assist the user."
    ),
    tools=[retrieve_artifact],  # Tools added dynamically from skill config
    sub_agents=[],  # Sub-agents added dynamically from skill config
)


# G46 M3: org-scoped bucket tools — conditionally attached to the root agent
# when A2A_AGENT_DOCUMENTS_BUCKET is set. Gives peer agents (and the
# orchestrator) the ability to discover + load documents from this deploy's
# bound GCS workspace. Both tools degrade gracefully (return [] / ok=False)
# when the env var is unset OR the SA lacks roles/storage.objectViewer, so
# wiring them unconditionally would also be safe — we gate on env so the
# agent's tool list doesn't grow for deploys that don't use the feature
# (keeps Gemini's tool-call decisions tighter).
if os.environ.get("A2A_AGENT_DOCUMENTS_BUCKET"):
    from tools.org_documents import list_org_documents, read_org_document

    root_agent.tools.extend([list_org_documents, read_org_document])


app = App(
    root_agent=root_agent,
    name="aitana_platform",
    # Compaction config follows THIS DEPLOYMENT'S DEFAULT MODEL.
    #
    # It used to read `gemini_api_name_for("gemini-2-5-flash")` — a hardcoded
    # lookup. Because `EventsCompactionConfig` lives on `App` and `App` is built
    # once at import, that meant every session on this deploy got the config
    # computed for a 1M-token Gemini window, whatever model it actually ran. A
    # Claude skill on a ~200K window got the 1M settings. `get_compaction_config`
    # was correct and had never once been applied — the bug was here, at its one
    # call site. (2026-08-06 ONE UAT; see
    # docs/design/v6.23.0/conversation-context-fidelity.md.)
    #
    # `default_model()` is deliberately NOT `gemini_api_name_for(...)`: the
    # deploy default may legitimately be a Claude or OpenAI tier, and this call
    # site does not need Gemini (that accessor exists for Vertex-only structured
    # output, which compaction is not). Asserting Gemini here would fail the
    # import on an entirely valid deployment.
    #
    # This makes the App config track the COMMON case. A skill pinned to a
    # different family still runs under it — the App is global. Narrowing that
    # per-session is the remaining half; ADK 1.31.1 exposes the seam via
    # `invocation_context.events_compaction_config`, which the pre-request
    # token-threshold processor reads (the post-invocation sliding window still
    # reads the App). Tracked as Phase 2 in the design doc.
    events_compaction_config=get_compaction_config(default_model()),
)

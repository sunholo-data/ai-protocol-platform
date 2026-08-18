"""Code execution sub-agent for non-Gemini skill agents.

Gemini agents receive BuiltInCodeExecutor directly on their LlmAgent instance.
Claude and OpenAI agents cannot use BuiltInCodeExecutor (Gemini-only), so they
delegate code execution to this Gemini-backed sub-agent via AgentTool.
"""

from __future__ import annotations

import os

from google.adk.agents import LlmAgent
from google.adk.code_executors import BuiltInCodeExecutor

# A registry id/tier (not a raw api name) so resolve_model_chain can attach the
# fallback chain and pass the eu-strict residency check (v6.14.0 reliability sweep).
# `lite` tracks the registry automatically — 2026-08-13: was a hardcoded
# gemini-2-5-flash, which EOLs 2026-10-16.
_CODE_AGENT_MODEL = os.environ.get("CODE_AGENT_MODEL", "lite")


def create_code_agent() -> LlmAgent:
    """Return a Gemini LlmAgent with BuiltInCodeExecutor for code execution tasks.

    Used as the backing agent for AgentTool when the parent skill agent runs on
    Claude or OpenAI (which cannot use BuiltInCodeExecutor natively).
    """
    # Function-local import avoids an import cycle (adk.agent lazily imports this
    # module). resolve_model_chain gives the code sub-agent retry + region fallback.
    from adk.agent import resolve_model_chain

    return LlmAgent(
        name="code_agent",
        model=resolve_model_chain(_CODE_AGENT_MODEL),
        instruction=(
            "You are a code execution assistant. "
            "Execute the code the user provides and return the output. "
            "If the code produces an error, include the full error message in your response."
        ),
        code_executor=BuiltInCodeExecutor(),
    )

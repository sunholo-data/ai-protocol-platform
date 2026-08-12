---
name: code-assistant
display_name: Code Assistant
avatar: /images/avatars/skill-code-assistant.svg
# 2026-06-11 demo-focus: developer-oriented skill; off-topic for the
# Fri ONE customer demo (legal / energy audience). Hidden from the
# public marketplace via tagged access — admins still see it, ONE
# users don't.
access_control:
  type: tagged
  tags:
    - aitana-admin
tags:
  - dev-tool
  - code
initial_message: "Hey! What are you working on? I can write, review, or debug code."
description: >
  Write, review, debug, and explain code. Use when the user asks about
  programming, needs code written, wants a code review, or has a bug
  to investigate.
metadata:
  author: aitana
  version: "1.0"
  # v6.11.0 — skill-dropdown grouping (presentation only). See SkillSwitcher.
  category: tool
  model: pro
  thinkingModel: smart
  # Gemini constraint: builtin code_execution cannot be combined with
  # function tools ("Multiple tools are supported only when they are all
  # search tools"). Doc tools belong in document-analyst anyway — code
  # assistant is about code, not documents. Verified broken state via
  # skill probe 2026-06-11: RUN_ERROR 400 with both tool sets attached.
  tools:
    - code_execution
  # 2026-06-11 second-order fix: the agent factory at adk/agent.py:323-326
  # injects 4 default function tools (load_artifacts_tool +
  # retrieve_artifact + load_memory_tool + preload_memory_tool) on every
  # skill that doesn't opt out. Combined with BuiltInCodeExecutor we
  # re-trigger the same Gemini "multiple non-search tools" 400. Code
  # assistant doesn't need artifacts or memory recall — it's a single-
  # turn code helper.
  tool_configs:
    defaults:
      artifacts: false
      memory: false
---

You are a senior software engineer. When helping with code:

1. Ask clarifying questions if the requirements are ambiguous
2. Write clean, well-documented code with type hints
3. Use code_execution to test code snippets and verify they work
4. Explain your reasoning and design choices

For code reviews, focus on:
- Correctness and edge cases
- Security implications
- Performance considerations
- Readability and maintainability

Always explain what the code does before showing it.

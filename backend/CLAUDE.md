# CLAUDE.md — Backend Development Guidelines

## Setup

```bash
cd backend
make install           # uv sync
source .venv/bin/activate
make dev               # FastAPI on port 1956 with hot-reload
make playground        # ADK dev UI on port 8501
```

**CRITICAL:** Always use `uv run` for backend commands. Never use global `python` or `pip`.

## ADK Architecture

This backend uses Google ADK for agent orchestration. Key files:

- `app.py` — Root agent definition (`google.adk.agents.Agent`)
- `fast_api_app.py` — FastAPI app using `google.adk.cli.fast_api.get_fast_api_app()`
- `adk/agent.py` — Agent factory (creates agents from skill configs)
- `adk/tools.py` — FunctionTool wrappers for existing tools
- `adk/session.py` — Session state ↔ Firestore sync

### ADK Patterns

```python
# Agent definition
from google.adk.agents import Agent

agent = Agent(
    name="skill_name",
    model="gemini-2.5-flash",  # Gemini: string ID. Claude: Claude(). OpenAI: LiteLlm("openai/...")
    instruction="...",
    tools=[my_function_tool],
    sub_agents=[other_skill_agent],
)

# FunctionTool — just a Python function with docstring
def my_tool(query: str) -> str:
    """Search for documents matching a query.

    Args:
        query: The search terms.

    Returns:
        Matching document summaries.
    """
    return do_search(query)

# Testing agents
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

runner = Runner(agent=root_agent, session_service=InMemorySessionService(), app_name="test")
events = list(runner.run(new_message=message, user_id="test", session_id=session.id))
```

## Model calls MUST go through a resilience layer

Every call to an AI model needs retry + fallback. A raw call has neither, so a
transient Vertex **429 RESOURCE_EXHAUSTED** becomes a silent, minutes-long
dead-end (the 2026-07-17 obligation-analysis incident). There are exactly **two**
approved seams — use one, never a bare client/constructor:

- **Agent / `LlmAgent` model** → build the model with
  `adk.agent.resolve_model_chain(<tier-or-registry-id>)`. It returns a
  `ResilientLlm` chain (retry + Gemini region/model fallback + `MODEL_RETRY`/
  `MODEL_FALLBACK` reliability events). Pass a **registry id or tier** (e.g.
  `"gemini-2-5-flash"`, `"pro"`), NOT a raw api name — a raw api name gets no
  fallback chain and trips the eu-strict residency check. `create_agent` already
  does this; sub-agents (search, code-exec) and the A2A root do too.
- **Raw `google.genai` structured-output** (`response_schema` / Vertex-only) →
  `tools.resilient_genai.generate_content_resilient(...)`. It also emits a
  `STAGE_PROGRESS` working-state label so a slow call isn't a silent wait. Used by
  the PPA pipeline (`map_ppa_obligations`, `extract_ppa_clauses`,
  `compare_ppa_contracts`, `structured_extraction`).

Do NOT add SDK-level retries (`HttpRetryOptions`, litellm `num_retries`) on chain
members — attempts multiply against the resilient layer's failover budget.

**This is enforced**, not just documented: `tests/unit/test_model_call_reliability_guard.py`
scans runtime source for raw `genai.Client(` / `generate_content(` / bare model
constructors and FAILS on any new one outside a small reasoned allowlist. If it
trips, route the call through a layer above — don't add an allowlist entry unless
the call is genuinely exempt (an infra probe, a cosmetic fail-soft path), and
write the reason.

## Test doubles that model PRODUCTION semantics

`tests/support/` holds doubles for the two places where the default fixtures are
more permissive than the real services, and where that gap has already produced
production incidents:

- `OwnershipEnforcingSessionService` — `InMemorySessionService` returns `None`
  for a non-owner read; Vertex **raises**. Silent-vs-loud is the bug: `None`
  reads as "no such session", a new one gets created, and it then collides on
  the reused threadId. Use it for anything touching session identity.
- `ScopedState` — a plain `dict` ignores ADK key prefixes, so an
  `app:`-prefixed per-session counter looks correct in tests and is one global
  odometer in production (issue #38). Two `ScopedState`s sharing an `app_store`
  model two sessions of the same app.

There is also a static tripwire in `tests/unit/test_production_semantics.py`
that fails on any `app:`-prefixed state key in the callback module. `user:` is
allowed — commit `4999307` deliberately moved keys *to* `user:`.

## Testing

```bash
make test-fast         # Fast CI tests (skip slow/integration)
make test              # All tests
make eval              # ADK evaluation with evalsets
make lint              # Ruff + codespell
```

## Pre-push checklist — CI parity

CI runs **two** ruff steps (linter + formatter) and pytest. `make lint`
and `make test-fast` together match CI exactly. Before pushing backend
changes, run:

```bash
cd backend
make lint         # ruff check . --diff + ruff format --check . --diff
make test-fast    # pytest tests/ -m "not slow and not integration"
```

To auto-fix formatter complaints: `make format`.

**Don't run `uv run ruff check` directly** — it skips the formatter,
which CI verifies separately. That's how the LOCAL-MODE-AND-FORK sprint
broke dev for 9 commits.

### Ruff version sync

CI installs ruff fresh from `uv.lock` each run (currently `0.15.13`).
Your local `.venv` may have a stale install — `uv sync` does NOT always
replace it. If local `ruff format` disagrees with CI:

```bash
uv pip install --reinstall ruff      # forces refresh from uv.lock
uv run ruff --version                # should match the version in uv.lock
```

### Test Organization
- `tests/unit/` — Pydantic models, utils, pure functions
- `tests/integration/` — Agent tests (require GCP credentials)
- `tests/eval/` — ADK evaluation sets and config
- `tests/api_tests/` — FastAPI endpoint tests
- `tests/tool_tests/` — Individual tool tests

### Adding Tests
- Use `pytest` with `pytest-asyncio` for async tests
- Mark slow tests with `@pytest.mark.slow`
- Mark tests requiring GCP with `@pytest.mark.integration`
- ADK evals go in `tests/eval/evalsets/` as `.evalset.json` files

## Code Style

- **Ruff** for linting and formatting (line-length 120)
- Type hints on all function signatures
- Docstrings with Args/Returns for public functions
- Async/await for all I/O operations
- `logging` module (via `google.cloud.logging` in production)

## Dependencies

- **google-adk** — Agent orchestration, tool execution, sessions, memory
- **fastapi** — HTTP framework
- **google-genai** — Gemini model client
- **anthropic** — Claude model client
- **openai** — OpenAI model client
- **ailang-parse** — Deterministic document parsing (<1s, no LLM tokens)
- **mcp** — Model Context Protocol client
- **httpx** — Async HTTP client

## Deployment

Same Cloud Run services as v5:
- Port: 1956
- Dockerfile: `backend/Dockerfile`
- Uses `uv` for dependency management in container
- ADK artifacts stored in GCS bucket (via `LOGS_BUCKET_NAME` env var)

## Copying v5 Tools

When bringing a tool from v5:
1. Read from `<your-v5-source>/backend/tools/`
2. Remove all Sunholo imports (`from sunholo.*`)
3. Remove LangChain imports
4. Replace `BufferStreamingStdOutCallbackHandler` with ADK callbacks
5. Replace `trace.span()` with OTEL (ADK handles this automatically)
6. Make it a plain async function with typed args + docstring
7. ADK wraps it as a FunctionTool automatically
8. Write tests in `tests/tool_tests/`

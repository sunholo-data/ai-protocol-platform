# Models API — Design Doc

**Status:** Draft  
**Sprint:** MODELS-API  
**Estimated effort:** 0.5 days (~120 LOC)  
**Dependencies:** SESSION-MEMORY (complete), RESOURCE-ACCESS (complete)

---

## Problem

The v6 platform supports three model providers (Gemini, Claude, OpenAI) each with multiple tiers (default / smart / fast). The frontend skill-configuration UI needs to know which models are available without hardcoding model IDs in TypeScript. Model availability changes regularly (new releases, deprecated IDs) and the backend is the right place to own the list.

The canonical source of truth is `backend/config/models.yaml`, introduced in the SESSION-MEMORY sprint and aligned with `~/.ailang/models.yml`.

---

## Solution

**`GET /api/models`** — a lightweight, unauthenticated endpoint that returns the structured model list from `backend/config/models.yaml`. The frontend uses this to populate provider/model dropdowns in skill settings.

No database. No per-user state. The response is deterministic and can be cached aggressively.

---

## Data Model

### YAML schema (`backend/config/models.yaml`)

```yaml
models:
  <key>:
    api_name: str          # Exact ID passed to ADK/LiteLLM
    provider: google | anthropic | openai
    tier: default | smart | fast
    context_window: int    # Tokens
    max_output_tokens: int
    description: str

defaults:
  google: <model-key>
  anthropic: <model-key>
  openai: <model-key>

platform_default: <model-key>

compaction:
  large_context:
    models: [prefix, ...]
    interval: int
    overlap: int
  small_context:
    models: [prefix, ...]
    interval: int
    overlap: int
  default:
    interval: int
    overlap: int
```

### API response

```json
GET /api/models

{
  "models": [
    {
      "id": "gemini-3-flash",
      "api_name": "gemini-3-flash-preview",
      "provider": "google",
      "tier": "default",
      "context_window": 1000000,
      "max_output_tokens": 65536,
      "description": "Gemini 3 Flash — fast, 1M context, outperforms 2.5 Pro"
    },
    ...
  ],
  "defaults": {
    "google": "gemini-3-flash",
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-5-1-instant"
  },
  "platform_default": "gemini-3-flash"
}
```

The `compaction` block is internal and not exposed in the API response (it has no frontend use).

---

## Implementation

### Backend (`~80 LOC`)

**`backend/config/models.py`** — Pydantic models + YAML loader:

```python
class ModelEntry(BaseModel):
    id: str                    # dict key from YAML
    api_name: str
    provider: Literal["google", "anthropic", "openai"]
    tier: Literal["default", "smart", "fast"]
    context_window: int
    max_output_tokens: int
    description: str

class ModelsConfig(BaseModel):
    models: list[ModelEntry]
    defaults: dict[str, str]   # provider → model id
    platform_default: str

def load_models_config() -> ModelsConfig: ...
```

YAML loaded once at module import (or lazily on first request). No hot-reload needed — a redeploy is expected when models change.

**`backend/protocols/models_route.py`** — FastAPI router:

```python
router = APIRouter(prefix="/api", tags=["models"])

@router.get("/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse: ...
```

Mounted in `fast_api_app.py` alongside existing routers. **No auth required** — model list is not sensitive.

### Frontend (`~40 LOC`)

`GET /api/proxy/api/models` fetched once on skill-settings page load. Populates a `<Select>` component grouped by provider. Selected value is stored as the `model_id` field on the skill Firestore document.

---

## Updating the Model List

When a new model releases (tracked in `~/.ailang/models.yml`):

1. Add entry to `backend/config/models.yaml`
2. Update `get_compaction_config()` prefix dict in `backend/adk/session.py` if the new model's context window tier differs
3. Run `cd backend && make test-fast` — model tests live in `test_session_factories.py`
4. Deploy — frontend picks up new models immediately from the API

No frontend code changes required for model additions.

---

## Testing

- `tests/unit/test_models_config.py` — YAML loads without error; all required fields present; platform_default exists in models list; defaults reference valid model IDs
- `tests/api_tests/test_models_route.py` — `GET /api/models` returns 200; response contains all three providers; `platform_default` matches YAML; no auth required (200 without token)

---

## Out of Scope

- Model capability probing (live latency / availability checks)
- Per-user model allow-lists (future: controlled by skill permissions)
- Pricing display (internal cost tracking only, not exposed to users)

---

## Implementation Report

**Status:** Implemented  
**Completed:** 2026-04-22  
**Actual effort:** 0.1 days (~147 LOC vs 120 estimated)  
**Evaluator score:** 93/100 PASS

**What was built:**
- `backend/config/models.py` — `ModelEntry` + `ModelsConfig` Pydantic models with `model_validator` enforcing that `platform_default` and all `defaults` reference real model IDs. `load_models_config()` with `lru_cache`, `RuntimeError` on bad YAML.
- `backend/protocols/models_route.py` — `GET /api/models`, no auth, `compaction` block excluded from response.
- `frontend/src/components/skill/ModelSelector.tsx` — fetches `/api/proxy/api/models`, groups by provider via `<optgroup>`, defaults to `platform_default` when `value=null`, calls `onChange` with `api_name`.
- `frontend/src/app/skill/[skillId]/settings/page.tsx` — minimal settings page wiring `ModelSelector`.
- 27 new tests: 20 backend (12 unit + 8 API), 7 frontend.

**Deviations from spec:**
- Settings page route used `skill/[skillId]/settings` (matching existing `[skillId]` convention) rather than `skills/[id]/settings` as specced — no functional difference.
- `@testing-library/user-event` added as a dev dependency (not anticipated in spec).

**Evaluator fixes applied:**
1. YAML error handling (`FileNotFoundError` + `yaml.YAMLError` → `RuntimeError` with clear message)
2. State variable renamed `modelId` → `modelApiName` in settings page for semantic clarity

**Open concerns:**
- No retry button in `ModelSelector` on fetch error — deferred until settings UI matures.

# ==============================================================================
# Aitana Platform v6 — Backend Makefile
# ==============================================================================

# Installation
install:
	@command -v uv >/dev/null 2>&1 || { echo "uv is not installed. Installing..."; curl -LsSf https://astral.sh/uv/install.sh | sh; }
	uv sync

# ==============================================================================
# Development
# ==============================================================================

# ADK playground (agent dev UI)
playground:
	uv run adk web . --port 8501 --reload_agents

# Local dev server with hot-reload
# IMPORTANT: Always use `make dev`, never `uv run uvicorn` directly.
# This target loads .env (including GCP_PROJECT=your-project-id) so the
# shell-level v5 GCP_PROJECT=multivac-internal-dev cannot shadow it.
dev:
	@PROJECT=$$(grep -v '^#' .env | grep 'GOOGLE_CLOUD_PROJECT=' | cut -d= -f2); \
	echo "==> Starting backend — GCP project: $${PROJECT:-UNKNOWN (check .env)}"; \
	if [ -z "$$PROJECT" ]; then echo "ERROR: GOOGLE_CLOUD_PROJECT not set in .env — aborting" && exit 1; fi
	env $$(grep -v '^#' .env | xargs) uv run uvicorn fast_api_app:app --host localhost --port 1956 --reload

# ==============================================================================
# Testing
# ==============================================================================

# Run unit and integration tests
test:
	uv sync --dev
	uv run pytest tests/ -v --tb=short

# Fast CI tests (skip slow and integration)
test-fast:
	uv sync --dev
	uv run pytest tests/ -m "not slow and not integration" -v --tb=short

# ADK-contract conformance gate (v6.17.0 adk-contract-conformance).
# Runs ONLY the hermetic "real ADK flow" guards (marker: adk_contract) that catch
# custom<->ADK boundary regressions — the class of bug jsdom/isolated unit tests
# miss. This is the gate for an ADK version bump: it MUST stay green when the
# `google-adk` pin in pyproject.toml changes.
# See docs/design/v6.17.0/adk-contract-checklist.md.
adk-conformance:
	uv run pytest tests/ -m adk_contract -v --tb=short

# Headless preview of a result→A2UI mapping (tool-results-as-a2ui / 7.3).
# Runs a registered mapping against a typed tool-result JSON and prints the
# schema-validated A2UI v0.9 messages — no browser, no running backend.
#   make a2ui-render MAPPING=ppa_comparison RESULT=/tmp/comparison.json
#   make a2ui-render LIST=1
a2ui-render:
	uv run python -m adk.a2ui_render_preview $(if $(LIST),--list,--mapping $(MAPPING) --result $(RESULT))

# Authenticated /api/auth/whoami round-trip against deployed dev. Proves a
# custom claim set via the admin SDK lands in the verified ID token. Requires
# ADC + email/password sign-in enabled on the Firebase project.
smoke-artifacts:
	uv run python scripts/smoke_artifacts.py

smoke-auth:
	WHOAMI_SMOKE_ENV=dev uv run pytest tests/integration/test_whoami_deployed.py -v --tb=short

# Automated Firestore rules verifier. Creates two `.test`-TLD users + a private
# skill fixture via Admin SDK, then makes Firestore REST calls as each identity
# to assert each rule branch (allow/deny) behaves as expected. Safe to re-run;
# fixture is cleaned up in all exit paths.
#
# CRITICAL: must override BOTH GCP_PROJECT and GOOGLE_CLOUD_PROJECT because
# Mark's shell has older multivac-internal-dev values set — inline env alone
# won't win (see memory/gotcha_gcp_project_env_shadow.md).
verify-rules:
	GCP_PROJECT=your-project-id GOOGLE_CLOUD_PROJECT=your-project-id \
		uv run python scripts/verify_rules.py --env dev

# ==============================================================================
# Evaluation
# ==============================================================================

# Run agent evaluation using ADK eval
eval:
	uv sync --dev --extra eval
	uv run adk eval . $${EVALSET:-tests/eval/evalsets/basic.evalset.json} \
		$(if $(EVAL_CONFIG),--config_file_path=$(EVAL_CONFIG),$(if $(wildcard tests/eval/eval_config.json),--config_file_path=tests/eval/eval_config.json,))

# ==============================================================================
# Code Quality
# ==============================================================================

# `lint` matches CI's `.github/workflows/ci.yml` step exactly: plain
# `ruff check .` (NOT `--diff`) exits 1 on ANY error, fixable or not.
# Prior versions of this target used `--diff` which silently hid 7
# unfixable lint errors that broke CI for sprint 2.11. If you're
# tempted to add `--diff` back to make local cleaner, don't — let
# local match CI, fix the errors, or add `# noqa: <rule>` with a
# justification comment.
lint:
	uv sync --dev --extra lint
	uv run ruff check .
	uv run ruff format . --check

format:
	uv sync --dev --extra lint
	uv run ruff check . --fix
	uv run ruff format .

# ==============================================================================
# Deployment
# ==============================================================================

# Deploy to Cloud Run (uses gcloud source deploy)
deploy:
	PROJECT_ID=$$(gcloud config get-value project) && \
	gcloud beta run deploy backend-api \
		--source . \
		--memory "4Gi" \
		--project $$PROJECT_ID \
		--region "europe-west1" \
		--no-allow-unauthenticated \
		--no-cpu-throttling \
		--labels "created-by=adk" \
		--port 1956

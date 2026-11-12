#!/usr/bin/env bash
# The CI checks, for Cloud Build (cloudbuild.ci.yaml) and local runs alike.
# No deployment credentials or model keys required. Mirrors
# .github/workflows/ci.yml, which forks may keep using instead.
set -euo pipefail
cd "$(dirname "$0")/../.."
# The real AG-UI event stream the backend emits, checked against the frontend's
# installed client: written by `backend`, read by `frontend`.
WIRE="$PWD/.ci-cache/agui-wire.jsonl"
case "${1:-all}" in
  backend)
    cd backend
    uv sync --frozen --dev --extra lint
    uv run ruff check .
    uv run ruff format --check .
    # Match the GitHub CI gate: only the seeder may mint platform-owned skills.
    hits=$(grep -RnE 'owner_id\s*=\s*PLATFORM_OWNER_UID|owner_id\s*=\s*"aitana-platform"' --include='*.py' --exclude-dir='.venv' --exclude-dir='__pycache__' --exclude-dir='tests' . | grep -v 'admin/platform_seed.py' | grep -v 'skills/platform.py' || true)
    if [[ -n "$hits" ]]; then
      echo "Platform owner_id may only be set in admin/platform_seed.py: $hits" >&2
      exit 1
    fi
    GOOGLE_CLOUD_PROJECT=test-project ADK_ARTIFACT_BUCKET= uv run pytest tests/ --ignore=tests/integration -m 'not slow' --tb=short -q
    # Runtime dependencies only, in their own environment (the dev venv above
    # stays as it is for local runs).
    mkdir -p "$(dirname "$WIRE")"
    UV_PROJECT_ENVIRONMENT="$PWD/../.ci-cache/agui-venv" uv run --frozen --no-dev python scripts/capture_agui_wire.py > "$WIRE"
    ;;
  cli)
    cd cli
    uv sync --frozen --dev
    uv run ruff check .
    uv run ruff format --check .
    uv run pytest tests/ --tb=short -q
    ;;
  frontend)
    cd frontend
    npm ci
    if [[ -f "$WIRE" ]]; then
      node scripts/check-agui-wire.mjs "$WIRE"
    elif [[ "${CI:-}" == true ]]; then
      echo "No AG-UI wire capture at $WIRE: the backend check must run first." >&2
      exit 1
    else
      echo "Skipping the AG-UI wire check: run '$0 backend' first to capture the stream."
    fi
    npm run quality:check:fast
    CI=true npm run test:run
    npm run build
    ;;
  guide)
    node --test scripts/user-guide/*.test.mjs scripts/i18n/*.test.mjs
    node scripts/i18n/bundle.mjs --check
    node scripts/i18n/literals.mjs   # hard-coded UI strings may only go down
    node scripts/user-guide/check.mjs
    ;;
  security)
    node --test scripts/tests/security-floors.test.mjs scripts/tests/npm-audit-backport.test.mjs
    python3 -m unittest discover -s scripts/tests -p 'test_reconcile_dependabot_alerts.py'
    bash scripts/security-check.sh
    ;;
  safety)
    python3 scripts/check_local_mode_safety.py
    # Footgun guards ported from the AIPLA fork (platform-source#6, 2026-09-08)
    # and wired here on 2026-11-05: until then the scripts existed in this repo
    # but nothing ran them. Each one exists because its bug shipped once.
    bash scripts/check-auth-dispatcher.sh        # route imports the Firebase-only get_current_user
    bash scripts/audit-trust-cards.sh            # element pushes state to the agent with no visible card
    python3 -c 'import yaml' 2>/dev/null || python3 -m pip install -q pyyaml
    python3 scripts/check-cloudbuild-substitutions.py  # single-$ shell var dies at Cloud Build SUBMIT
    bash scripts/check-app-routes-tracked.sh     # a .gitignore rule silently swallows a route
    bash scripts/check-client-api-mounted.sh     # an API-client export nobody can reach
    # The publish gate (the public template is sound without private/) belongs
    # to canonical platform-source. Deployment forks intentionally contain
    # customer data under docs/customers/, so `auto` skips it there.
    publish="${CI_PUBLISH_CHECK:-auto}"
    if [[ "$publish" == auto ]]; then
      publish=0
      if [[ -f private/scripts/publish_public.py && ! -d docs/customers ]]; then publish=1; fi
    fi
    if [[ "$publish" == 1 ]]; then
      python3 private/scripts/publish_public.py check
    fi
    # The existing dangling-link ceiling is enforced by backend tests.
    python3 scripts/link-check.py || true
    ;;
  all)
    for check in backend cli frontend guide safety security; do "$0" "$check"; done
    ;;
  *) echo "Usage: $0 [backend|cli|frontend|guide|safety|security|all]" >&2; exit 2 ;;
esac

#!/usr/bin/env bash
#
# Shared loader for this deployment's identity. Source it, don't run it:
#
#     source "$(dirname "$0")/_deploy_env.sh"
#
# Sprint TEMPLATE-INVERT M4b. Before this, dev.sh / promote-env.sh /
# smoke-*.sh each carried their own copy of the per-env project map — this
# deployment's identity duplicated six times, shipped to every fork, so a fork
# running `make dev` pointed ADC at OUR project.
#
# Values live in `deploy.env` (gitignored, generated from the tracked
# deploy.env.example by `make materialize-config`). Env vars already exported
# win, so CI and one-off overrides keep working.

_DEPLOY_ENV_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
_DEPLOY_ENV_FILE="${_DEPLOY_ENV_ROOT}/deploy.env"

if [ -f "$_DEPLOY_ENV_FILE" ]; then
  # `set -a` exports everything the file defines; existing values are then
  # re-applied below so an explicit override always wins.
  _saved="$(export -p)"
  set -a
  # shellcheck disable=SC1090
  . "$_DEPLOY_ENV_FILE"
  set +a
  eval "$_saved" 2>/dev/null || true
  unset _saved
else
  echo "NOTE: no deploy.env — run 'make materialize-config' and fill it in." >&2
  echo "      Falling back to deploy.env.example placeholders." >&2
  if [ -f "${_DEPLOY_ENV_ROOT}/deploy.env.example" ]; then
    set -a
    # shellcheck disable=SC1090
    . "${_DEPLOY_ENV_ROOT}/deploy.env.example"
    set +a
  fi
fi

# project_for_env <dev|test|prod> — echoes the project id, empty if unknown.
project_for_env() {
  case "$1" in
    dev)  echo "${DEPLOY_PROJECT_DEV:-}" ;;
    test) echo "${DEPLOY_PROJECT_TEST:-}" ;;
    prod|production) echo "${DEPLOY_PROJECT_PROD:-}" ;;
    *)    echo "" ;;
  esac
}

# host_for_env <dev|test|prod> — echoes the deployed service URL, empty if unknown.
host_for_env() {
  case "$1" in
    dev)  echo "${DEPLOY_HOST_DEV:-}" ;;
    test) echo "${DEPLOY_HOST_TEST:-}" ;;
    prod|production) echo "${DEPLOY_HOST_PROD:-}" ;;
    *)    echo "" ;;
  esac
}

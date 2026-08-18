#!/usr/bin/env bash
#
# materialize-config.sh — create the local, real-valued config files from their
# tracked `.example` templates.
#
# Sprint TEMPLATE-INVERT, M3. Design:
# docs/design/template/template-repo-topology.md
#
# WHY THIS EXISTS
#
# Two files used to be handled by a THIRD sanitize mechanism, distinct from
# "delete" and "scrub": the publish step deleted the real file and renamed the
# `.example` over it.
#
#     infrastructure/mcp-toolbox/tools.yaml   <- tools.example.yaml
#     docs/ops/deployed-urls.md               <- deployed-urls.example.md
#
# That works for a one-shot copy and cannot survive the upstream/downstream
# inversion: it leaves the SAME tracked path holding generic content upstream
# and real content downstream, which is precisely the permanent-merge-conflict
# case the whole design exists to eliminate.
#
# So the tracked file is now the `.example`, the real file is generated and
# gitignored, and each tier fills it with its own values.
#
# BUILD-ORDER CONSTRAINT
#
# infrastructure/mcp-toolbox/Dockerfile does `COPY tools.yaml`, and the deployed
# sidecar passes `--config=/app/tools.yaml`. Both need the real filename, so
# this must run BEFORE any image build. It is idempotent and never overwrites an
# existing real file, so it is safe to call from a build step, a Makefile
# target, or by hand.
#
# Usage:
#   scripts/materialize-config.sh            # fill in anything missing
#   scripts/materialize-config.sh --force    # overwrite from .example
#   scripts/materialize-config.sh --check    # exit 1 if anything is missing

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
MODE="fill"

case "${1:-}" in
  --force) MODE="force" ;;
  --check) MODE="check" ;;
  "") ;;
  *) echo "Unknown argument: $1" >&2; exit 2 ;;
esac

# example -> real
PAIRS=(
  "infrastructure/mcp-toolbox/tools.example.yaml:infrastructure/mcp-toolbox/tools.yaml"
  "docs/ops/deployed-urls.example.md:docs/ops/deployed-urls.md"
  "deploy.env.example:deploy.env"
  "cli/aiplatform/config.example.yaml:cli/aiplatform/config.yaml"
)

missing=0
# This deployment's REAL Toolbox config, if it has one. It lives under
# docs/customers/ — a directory the sanitizer deletes wholesale, so it reaches
# neither the public template nor platform-source, and the parity gap stays 0
# (a deletion, never a rewrite).
#
# Why not Secret Manager, which would be tidier: the Cloud Build SA has no
# project-level bindings in the runtime projects, and granting it access must go
# through the terraform folder cascade in a repo that auto-applies on push. That
# is a cross-repo change, not a build fix. Tracked as a follow-up.
#
# Why this matters: TEMPLATE-INVERT M3 made tools.yaml gitignored and generated
# from the example, which is right for a fork — but it meant Cloud Build's clone
# no longer contained the real config, so the deployed toolbox served the generic
# `example` toolset and the customer's skills silently lost their tools. The
# MCP-registry gate caught it.
REAL_TOOLBOX="docs/customers/one/mcp-toolbox-tools.yaml"
if [ -f "${REPO_ROOT}/${REAL_TOOLBOX}" ]; then
  PAIRS[0]="${REAL_TOOLBOX}:infrastructure/mcp-toolbox/tools.yaml"
  echo "  using this deployment's real Toolbox config (${REAL_TOOLBOX})"
fi

for pair in "${PAIRS[@]}"; do
  example="${REPO_ROOT}/${pair%%:*}"
  real="${REPO_ROOT}/${pair##*:}"

  if [ ! -f "$example" ]; then
    echo "ERROR: template missing: ${pair%%:*}" >&2
    exit 2
  fi

  if [ -f "$real" ] && [ "$MODE" != "force" ]; then
    [ "$MODE" = "check" ] || echo "  ok       ${pair##*:} (already present, left alone)"
    continue
  fi

  if [ "$MODE" = "check" ]; then
    echo "  MISSING  ${pair##*:}" >&2
    missing=1
    continue
  fi

  # --force overwrites a file that may hold this deployment's real values (the
  # curated toolset, live service URLs). Back it up first — these files are
  # gitignored, so a clobber is NOT recoverable from git.
  if [ -f "$real" ]; then
    cp "$real" "${real}.bak"
    echo "  backup   ${pair##*:}.bak (previous contents preserved)"
  fi

  cp "$example" "$real"
  echo "  created  ${pair##*:} from ${pair%%:*}"
done

if [ "$MODE" = "check" ] && [ "$missing" = 1 ]; then
  echo >&2
  echo "Run: make materialize-config" >&2
  exit 1
fi

if [ "$MODE" != "check" ]; then
  echo
  echo "Real config files are GITIGNORED — fill them with this deployment's"
  echo "values. The tracked .example files are what ship."
fi

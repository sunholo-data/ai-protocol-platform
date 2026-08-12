#!/usr/bin/env bash
#
# install-toolbox.sh — fetch the MCP Toolbox binary for local dev.
#
# Deployed, Toolbox runs as a sidecar container built from
# infrastructure/mcp-toolbox/Dockerfile. Locally there is no container, so
# `make dev` needs the raw binary. This fetches it into .bin/ (gitignored).
#
# The version MUST match the Dockerfile's pinned image tag, or local dev tests a
# different Toolbox than production runs. That parity is asserted by
# backend/tests/tool_tests/test_toolbox_config_safety.py.
#
# Usage: scripts/install-toolbox.sh [--force]

set -euo pipefail

# Keep in sync with infrastructure/mcp-toolbox/Dockerfile (FROM ...:<tag>).
# The parity test will fail the build if these drift.
TOOLBOX_VERSION="1.7.0"

REPO_ROOT="$(git rev-parse --show-toplevel)"
BIN_DIR="$REPO_ROOT/.bin"
TARGET="$BIN_DIR/toolbox"
FORCE="${1:-}"

if [ -x "$TARGET" ] && [ "$FORCE" != "--force" ]; then
  echo "toolbox already installed: $("$TARGET" --version 2>/dev/null || echo "$TARGET")"
  echo "(re-run with --force to reinstall)"
  exit 0
fi

case "$(uname -s)" in
  Darwin) OS="darwin" ;;
  Linux)  OS="linux" ;;
  *) echo "ERROR: unsupported OS $(uname -s) — see https://mcp-toolbox.dev/" >&2; exit 1 ;;
esac

case "$(uname -m)" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64)  ARCH="amd64" ;;
  *) echo "ERROR: unsupported arch $(uname -m)" >&2; exit 1 ;;
esac

# NOTE: the bucket is `mcp-toolbox-for-databases`, NOT `genai-toolbox`. The
# project was renamed and most search results / older docs still point at the
# old bucket and at googleapis.github.io/genai-toolbox (which now 404s). Live
# docs: https://mcp-toolbox.dev/
URL="https://storage.googleapis.com/mcp-toolbox-for-databases/v${TOOLBOX_VERSION}/${OS}/${ARCH}/toolbox"

mkdir -p "$BIN_DIR"
echo "Downloading toolbox v${TOOLBOX_VERSION} (${OS}/${ARCH})…"
if ! curl -fsSL -o "$TARGET" "$URL"; then
  echo "ERROR: download failed: $URL" >&2
  exit 1
fi
chmod +x "$TARGET"

echo "Installed: $("$TARGET" --version)"
echo "  -> $TARGET"
echo "`make dev` will now start the Toolbox sidecar on 127.0.0.1:5000."

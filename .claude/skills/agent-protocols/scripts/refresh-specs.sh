#!/usr/bin/env bash
# Refresh vendored protocol specs from authoritative sources.
# Run quarterly or when a spec version bumps.
# Usage: .claude/skills/agent-protocols/scripts/refresh-specs.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REF_DIR="${SCRIPT_DIR}/../references"
mkdir -p "${REF_DIR}"

fetch() {
  local url="$1" out="$2"
  echo "  Fetching $out from $url"
  curl -fsSL "$url" -o "${REF_DIR}/${out}"
}

echo "Refreshing agent-protocols specs into ${REF_DIR}/"

# AG-UI
fetch "https://docs.ag-ui.com/introduction" "ag-ui-architecture.md"
fetch "https://docs.ag-ui.com/concepts/events" "ag-ui-events.md"
fetch "https://docs.ag-ui.com/concepts/tools" "ag-ui-tools.md"

# A2UI
fetch "https://a2ui.org/" "a2ui-v0.10-protocol.md"

# MCP
fetch "https://modelcontextprotocol.io/introduction" "mcp-architecture.md"

# Agent Skills
fetch "https://agentskills.io/specification" "agent-skills-spec.md"

echo "Done. Commit the updated references/ directory."
echo "Note: mcp-apps-spec-2026-01-26.md is maintained manually — see docs/design/v6.1.0/ for the SEP-1865 spec."

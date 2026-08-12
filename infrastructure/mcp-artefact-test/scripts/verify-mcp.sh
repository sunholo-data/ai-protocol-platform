#!/usr/bin/env bash
# verify-mcp.sh — prove an MCP server is reachable + serving the tools you expect
# THROUGH whatever URL a host would use. Run this before concluding "the host is
# broken" — usually the server/tunnel is fine and it's a host-side tools/list
# cache (refresh the connector).
#
# Usage:
#   verify-mcp.sh <url>                    # list tools (url may end in /mcp or not)
#   verify-mcp.sh <url> <tool> [jsonArgs]  # also call <tool>
# Examples:
#   verify-mcp.sh http://localhost:3001/mcp
#   verify-mcp.sh https://foo.trycloudflare.com/mcp increment-counter '{"by":1}'
set -euo pipefail

URL="${1:?usage: verify-mcp.sh <url> [tool] [jsonArgs]}"
TOOL="${2:-}"
ARGS="${3:-}"
[ -n "$ARGS" ] || ARGS='{}'   # NB: don't inline as ${3:-{}} — bash mis-parses the braces

case "$URL" in
  */mcp) MCP="$URL"; ROOT="${URL%/mcp}" ;;
  *)     ROOT="${URL%/}"; MCP="$ROOT/mcp" ;;
esac
hdr=(-H "Content-Type: application/json" -H "Accept: application/json, text/event-stream")
extract() { sed 's/^data: //' | grep -o '{.*}' | head -1; }

echo "→ healthz: $ROOT/healthz"
curl -s --max-time 20 "$ROOT/healthz" || echo "  (no /healthz — fine if your server omits it)"
echo

echo "→ initialize: $MCP"
curl -s --max-time 25 -X POST "$MCP" "${hdr[@]}" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify-mcp","version":"1"}}}' \
  | extract | python3 -c "import sys,json;d=json.load(sys.stdin).get('result',{});print('  server:',d.get('serverInfo'),'| protocol:',d.get('protocolVersion'))" 2>/dev/null \
  || { echo "  ✗ initialize failed (server/tunnel up?)"; exit 1; }

echo "→ tools/list:"
curl -s --max-time 25 -X POST "$MCP" "${hdr[@]}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | extract | python3 -c "
import sys,json
for t in json.load(sys.stdin)['result']['tools']:
    ui=(t.get('_meta') or {}).get('ui') or (t.get('_meta') or {}).get('openai/outputTemplate')
    print('   -', t['name'], '[UI]' if ui else '[tool]', '—', t.get('description','')[:52])
" 2>/dev/null || { echo "  ✗ tools/list failed"; exit 1; }

if [ -n "$TOOL" ]; then
  echo "→ tools/call $TOOL $ARGS"
  curl -s --max-time 25 -X POST "$MCP" "${hdr[@]}" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"$TOOL\",\"arguments\":$ARGS}}" \
    | extract | python3 -c "
import sys,json
r=json.load(sys.stdin).get('result',{})
c=(r.get('content') or [{}])[0]
print('  ->', c.get('text', c))
if r.get('structuredContent'): print('  structuredContent:', r['structuredContent'])
" 2>/dev/null || echo "  ✗ tools/call failed"
fi

echo
echo "If this passed but a HOST shows stale/missing tools → it's the host's"
echo "connector cache. Refresh / remove-and-re-add the connector."

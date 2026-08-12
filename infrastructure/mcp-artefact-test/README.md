# mcp-artefact-test — test an artefact in an external host

The platform renders artefacts in its **own** app via `StaticArtefactFrame`
(see [../../docs/ops/mcp-apps-iframe-guide.md](../../docs/ops/mcp-apps-iframe-guide.md)).
There's no built-in path to render a static artefact in an **external** host —
this tiny, self-contained MCP server is that path. Use it to verify an artefact
**renders AND its interactions reach the model** in ChatGPT / Copilot / Claude
Desktop / MCP Inspector.

## One command

```bash
scripts/test-artefact-in-host.sh <artefact-name>     # default: _template
```

Starts the server + a `cloudflared` quick tunnel and prints the connector URL.
Prereqs: `node`, `cloudflared` (`brew install cloudflared`), ChatGPT dev mode on.

## What it serves

Reads `infrastructure/mcp-sandbox/artefacts/<ARTEFACT>/v1/index.html` and offers
three tools so you can exercise all three cross-host channels:

| Tool | Kind | Exercises |
|---|---|---|
| `show-artefact` | UI tool (`ui://…`) | the widget renders (dual metadata: `ui.resourceUri` + `openai/outputTemplate`) |
| `increment-counter` | data/action tool (`openai/widgetAccessible`) | the **mutation round-trip** — the widget's `callServerTool()` |
| `Greet` | plain data tool | tool-calling + "always call at session start" behaviour |

## Verify the wire first

```bash
scripts/verify-mcp.sh http://localhost:3001/mcp                       # list tools
scripts/verify-mcp.sh http://localhost:3001/mcp increment-counter '{"by":1}'
```

If this passes but a host shows stale/missing tools, it's the host's `tools/list`
cache — refresh the connector.

## Notes

- **Dev-only, anonymous, no auth.** Never expose an artefact wired to private
  data. ChatGPT app-store / Copilot production require OAuth 2.1.
- Editing the **artefact HTML** is live (read per request); editing **`serve.ts`**
  needs a restart. The `*.trycloudflare.com` URL is ephemeral (re-import on
  restart).
- Cross-host bridge details + host matrix: the iframe guide (linked above),
  §"Testing an artefact in an external host".

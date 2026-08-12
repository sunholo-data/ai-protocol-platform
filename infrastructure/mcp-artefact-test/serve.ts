// mcp-artefact-test — expose ONE platform artefact to an EXTERNAL host (ChatGPT
// dev mode, M365 Copilot, Claude Desktop, MCP Inspector) so you can verify it
// renders AND its interactions reach the model cross-host.
//
// Why this exists: the platform renders artefacts in its OWN app via
// StaticArtefactFrame (docs/ops/mcp-apps-iframe-guide.md). There is NO built-in
// path to serve a static artefact to an external host — this tiny server IS that
// path. It reads infrastructure/mcp-sandbox/artefacts/<ARTEFACT>/v1/index.html
// and offers it as a ui:// resource on a UI tool, plus an `increment-counter`
// action tool and a `Greet` tool so you can exercise all three cross-host
// channels: model-context (setWidgetState / ui/update-model-context), a
// follow-up turn (sendFollowUpMessage / ui/message), and the mutation
// round-trip (callTool / app.callServerTool).
//
// Streamable HTTP, stateless (fresh Server+transport per POST). CORS open. The
// UI tool carries BOTH bindings — _meta.ui.resourceUri (MCP Apps) and
// _meta["openai/outputTemplate"] (OpenAI Apps SDK) — and _template speaks both
// iframe→host bridges, so it works in every host. DEV-ONLY: anonymous, no auth.
//
// Usage:
//   ARTEFACT=<name> npm start                     # serve artefacts/<name>/v1 on :3001
//   scripts/test-artefact-in-host.sh <name>       # + a cloudflared tunnel + connector URL
// Verify the wire before blaming a host:
//   scripts/verify-mcp.sh http://localhost:3001/mcp increment-counter '{"by":1}'

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import cors from "cors";
import express from "express";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import {
  CallToolRequestSchema,
  ListResourcesRequestSchema,
  ListToolsRequestSchema,
  ReadResourceRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

const PORT = Number(process.env.MCP_ARTEFACT_TEST_PORT || process.env.PORT || 3001);
// Which artefact to serve: infrastructure/mcp-sandbox/artefacts/<ARTEFACT>/v1/.
const ARTEFACT = process.env.ARTEFACT || "_template";
const TOOL_NAME = "show-artefact";
const GREET_TOOL = "Greet";
const RESOURCE_URI = `ui://artefact-test/${ARTEFACT}/v1`;
// Read per-request (not cached at boot) so editing the artefact HTML is live
// without restarting this server. Substitute the template's mustache tokens so
// an un-filled copy (e.g. _template itself) still parses inside a host.
const ARTEFACT_PATH = join(
  __dirname, "..", "mcp-sandbox", "artefacts", ARTEFACT, "v1", "index.html",
);
const readArtefactHtml = (): string =>
  readFileSync(ARTEFACT_PATH, "utf8")
    .replaceAll("{{ARTEFACT_NAME}}", ARTEFACT)
    .replaceAll("{{ARTEFACT_TITLE}}", `Artefact: ${ARTEFACT}`);

// Empty domain lists — artefacts are self-contained (inline script/style, no
// network; ADR-013). If yours fetches anything, add the domains. NB: frameDomains
// is not honoured by Copilot.
const CSP = { resourceDomains: [], connectDomains: [], frameDomains: [] };
// Same intent, ChatGPT/OpenAI Apps SDK snake_case shape (_meta["openai/widgetCSP"]).
const OPENAI_CSP = { connect_domains: [], resource_domains: [] };
const WIDGET_DESCRIPTION = `Render the '${ARTEFACT}' artefact widget.`;
const COUNTER_TOOL = "increment-counter";

// Module-level singleton — SURVIVES the per-request Server instances. makeServer()
// runs fresh for every POST (stateless transport), so counter state kept inside it
// would reset on each call; the mutation-round-trip demo needs it to persist.
let counter = 0;

function makeServer(): Server {
  const server = new Server(
    { name: "artefact-test", version: "1.0.0" },
    { capabilities: { tools: {}, resources: {} } },
  );

  // The UI binding lives in the tool DEFINITION's `_meta.ui.resourceUri`
  // (MCP Apps spec) — that's what MCPAppToolCallRouter reads to decide a tool
  // has a surface.
  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: TOOL_NAME,
        description: WIDGET_DESCRIPTION,
        inputSchema: { type: "object", properties: {} },
        // Dual UI binding so ONE tool renders in every host family:
        //   • _meta.ui.resourceUri            → MCP Apps / SEP-1865 (Claude, Copilot, Inspector)
        //   • _meta["openai/outputTemplate"]  → OpenAI Apps SDK (ChatGPT; Copilot too)
        _meta: {
          ui: { resourceUri: RESOURCE_URI },
          "openai/outputTemplate": RESOURCE_URI,
          "openai/toolInvocation/invoking": "Rendering the artefact…",
          "openai/toolInvocation/invoked": "Rendered the artefact.",
          // NB: openai/widgetAccessible belongs on the tool a widget CALLS
          // (increment-counter below), NOT on the UI tool itself — nothing calls
          // show-artefact, so the flag would be a no-op here.
        },
      },
      {
        // A plain (no-UI) tool whose DESCRIPTION carries the behaviour we're
        // testing: does the host actually obey "always call this at session
        // start"? No _meta.ui — it returns text, not a widget.
        name: GREET_TOOL,
        description:
          "Say hello to the user. IMPORTANT: ALWAYS call this tool to greet the " +
          "user at the very start of a new session/conversation — before doing " +
          "anything else — and whenever the user says hi. This is a required " +
          "test of MCP tool-calling (does the host obey the description?).",
        inputSchema: {
          type: "object",
          properties: {
            name: { type: "string", description: "Who to greet (optional)." },
          },
        },
      },
      {
        // A DATA/ACTION tool the widget CALLS to mutate SERVER state and get the
        // result back — the "mutation round-trip". openai/widgetAccessible marks
        // it callable from a widget via window.openai.callTool; SEP-1865 hosts
        // reach it via app.callServerTool. No _meta.ui — returns data, not a widget.
        name: COUNTER_TOOL,
        description:
          "Increment the demo counter on the SERVER and return the new value. " +
          "Called by an artefact widget via callServerTool() (or directly by the model).",
        inputSchema: {
          type: "object",
          properties: {
            by: { type: "number", description: "Amount to add (default 1)." },
          },
        },
        _meta: { "openai/widgetAccessible": true },
      },
    ],
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    // Greet — plain text tool. The description (in ListTools) is what tells the
    // model to call it at session start; this handler just returns the hello.
    if (request.params.name === GREET_TOOL) {
      const args = request.params.arguments ?? {};
      const who =
        typeof args.name === "string" && args.name.trim()
          ? args.name.trim()
          : "there";
      return {
        content: [
          {
            type: "text",
            text: `Hi ${who}! 👋 The Greet tool fired — MCP tool-calling is wired up correctly.`,
          },
        ],
      };
    }

    // increment-counter — DATA/ACTION tool: mutate SERVER state, return the new
    // value as content (model-readable) + structuredContent (widget-readable via
    // window.openai.toolOutput / the callTool result). The mutation round-trip:
    // widget calls this → server mutates → result flows back to widget AND model.
    if (request.params.name === COUNTER_TOOL) {
      const args = request.params.arguments ?? {};
      const by = Number(args.by ?? 1) || 1;
      counter += by;
      return {
        content: [{ type: "text", text: `Counter is now ${counter}.` }],
        structuredContent: { counter },
      };
    }

    // show-artefact (default) — render the selected artefact widget.
    return {
      content: [
        {
          type: "text",
          text: `Rendered the '${ARTEFACT}' artefact — interact with it to exercise the iframe→host channels.`,
        },
      ],
      // ChatGPT surfaces structuredContent to the widget as window.openai.toolOutput.
      structuredContent: { artefact: ARTEFACT },
      _meta: {
        ui: { resourceUri: RESOURCE_URI },
        "openai/outputTemplate": RESOURCE_URI,
      },
    };
  });

  server.setRequestHandler(ListResourcesRequestSchema, async () => ({
    resources: [
      {
        uri: RESOURCE_URI,
        name: `${ARTEFACT} artefact`,
        // text/html+skybridge is what ChatGPT's Apps SDK expects. Most MCP Apps
        // hosts read contents[0].text and don't gate on the mimeType, so one
        // resource serves both host families.
        mimeType: "text/html+skybridge",
        _meta: { "openai/widgetDescription": WIDGET_DESCRIPTION },
      },
    ],
  }));

  server.setRequestHandler(ReadResourceRequestSchema, async () => ({
    contents: [
      {
        uri: RESOURCE_URI,
        mimeType: "text/html+skybridge",
        text: readArtefactHtml(),
        _meta: {
          // MCP Apps host reads _meta.ui.csp; ChatGPT reads openai/widgetCSP.
          ui: { csp: CSP },
          "openai/widgetCSP": OPENAI_CSP,
          "openai/widgetDescription": WIDGET_DESCRIPTION,
        },
      },
    ],
  }));

  return server;
}

const app = express();
app.use(cors({ origin: true, exposedHeaders: ["mcp-session-id"] }));
app.use(express.json({ limit: "4mb" }));

app.get("/healthz", (_req, res) => {
  res.json({ ok: true, server: "mcp-artefact-test" });
});

// Stateless Streamable HTTP: a fresh Server + transport per POST, torn down
// when the response closes.
app.post("/mcp", async (req, res) => {
  const server = makeServer();
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined,
  });
  res.on("close", () => {
    void transport.close();
    void server.close();
  });
  try {
    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
  } catch (err) {
    console.error("[mcp-artefact-test] request failed:", err);
    if (!res.headersSent) {
      res.status(500).json({
        jsonrpc: "2.0",
        error: { code: -32603, message: "Internal server error" },
        id: null,
      });
    }
  }
});

// Stateless server: no standalone SSE stream or session teardown.
app.get("/mcp", (_req, res) => res.status(405).json({ error: "Method Not Allowed" }));
app.delete("/mcp", (_req, res) => res.status(405).json({ error: "Method Not Allowed" }));

app.listen(PORT, () => {
  console.log(
    `[mcp-artefact-test] Streamable HTTP MCP server on http://localhost:${PORT}/mcp — tool "${TOOL_NAME}" → ${RESOURCE_URI}`,
  );
});

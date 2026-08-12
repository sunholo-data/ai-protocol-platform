# Dev-only routes

Catalog of `/dev/*` routes the Aitana frontend ships for fixture-driven
smoke + iteration. These routes are gated to non-prod (Next.js layout
checks `NODE_ENV`); in prod builds the route is still reachable but the
layout returns a 404 / not-found.

Run `make dev` from the repo root to bring up the stack
(frontend :3456, backend :1956, MCP sandbox :3457). Then visit any of:

| Route | Purpose | Backend needed | Sandbox needed |
|---|---|---|---|
| [`/dev/file-browser`](http://localhost:3456/dev/file-browser) | File browser smoke (1A.4 / file-browser.md) | yes (lists docs from Firestore) | no |
| [`/dev/rich-media`](http://localhost:3456/dev/rich-media) | Rich-media chat-render smoke (1.3 / rich-media-rendering.md) | no (fixtures) | no |
| [`/dev/mcp-apps`](http://localhost:3456/dev/mcp-apps) | MCP Apps integration index — links to passive + active sub-routes (1.7 M3) | no (fixtures) | yes (for the iframe to load) |
| [`/dev/mcp-apps/passive`](http://localhost:3456/dev/mcp-apps/passive) | Mounts MCPAppToolCallRouter with the captured ext-apps map-server fixture; no `onChatMessage` bridge | no | yes |
| [`/dev/mcp-apps/active`](http://localhost:3456/dev/mcp-apps/active) | Full active iframe → host bridge + button panel that synthesises common notifications + adapter log | no (button panel works without backend) | yes (for the iframe; not the buttons) |

## Adding a new dev route

1. `frontend/src/app/dev/<feature>/page.tsx` (+ `passive`/`active` sub-routes if useful)
2. `frontend/src/app/dev/<feature>/__tests__/<feature>.test.tsx` — at least:
   - renders without crashing
   - exercises the primary interaction without needing a live backend
3. Add a row to this table
4. Cross-link from the relevant design doc (e.g. `docs/design/v6.1.0/<feature>.md`)

## Why dev routes (not Storybook)

Each dev route lives next to the components it exercises and uses the
same routing + provider stack as production. Lower friction than a
parallel Storybook config; harder to drift from real wiring. See the
discussion in [docs/design/v6.1.0/a2ui-workshop-demo.md](../design/v6.1.0/a2ui-workshop-demo.md)
for the original framing (the workshop W6 demo introduced the pattern).

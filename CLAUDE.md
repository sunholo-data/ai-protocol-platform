# AI Protocol Platform — contributor guide

This repository contains the shared Python/FastAPI/Google ADK backend,
Next.js/React frontend, CLI, and protocol integrations.

## Security

Never publish customer documents, extracted content, screenshots, client
identifiers, credentials, or deployment-specific operational details. Derived
artifacts require the same authorization as their source. Serve private
previews through authenticated backend routes that check the caller's access.
Use reserved example domains in fixtures. Keep real configuration outside Git.

## Shared source and forks

`sunholo-data/platform-source` is the private shared source. The public
`sunholo-data/ai-protocol-platform` repository is a filtered snapshot with its
own public history. Deployment forks merge shared source and keep customer
content separate. Put private extensions under a `private/` directory and use
`backend/extensions.py` as the optional integration boundary. Never merge
private source history into a public repository.

Change shared code in platform-source, then refresh the export or merge it
downstream. Public files must match source byte for byte: the exporter removes
private paths; it does not rewrite content. Run `make publish-check` before an
export and inspect binary assets as well as text.

## Development

- Backend: `cd backend && uv sync --dev --extra lint`, then `make dev`.
- Frontend: `cd frontend && npm ci`, then `npm run dev`.
- Local workshop: `make dev-local`; see WORKSHOP.md.
- Backend validation: `cd backend && make lint && make test-fast`.
- Frontend validation: `cd frontend && npm run quality:check`.
- Dependency policy: `make security-check`.
- User guide: `make guide-check`.

Use uv for Python commands. Read nested CLAUDE.md files before changing their
code. Preserve model choices and unrelated behavior. Use protocol renderers for
tool results, give every interaction pending/progress/terminal feedback, and
verify protocol changes using a real event stream or browser.

Deployment identity comes from private environment configuration and trigger
substitutions. Follow UPGRADING.md before promoting an environment. Do not
force-push shared deployment branches.

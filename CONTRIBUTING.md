# Contributing

Thanks for your interest in the Aitana Platform.

This repo is the open-source reference implementation for a protocol-native
AI assistant platform on Google ADK. We accept contributions that improve
the platform itself (the protocol plumbing, observability, dev workflow);
customer-specific skills, infrastructure, or branding lives in downstream
forks.

## Quick links

- **First time here?** Start with [WORKSHOP.md](WORKSHOP.md) — clone,
  `LOCAL_MODE=1`, `make dev`, chat in under 30 minutes.
- **Architecture:** [docs/design/v6.1.0/SEQUENCE.md](docs/design/v6.1.0/SEQUENCE.md)
- **Protocol talk:** [docs/talks/ai-ui-protocol-stack.md](docs/talks/ai-ui-protocol-stack.md)

## Reporting bugs

Open an issue on [GitHub Issues](https://github.com/sunholo-data/ai-protocol-platform/issues).
Helpful template:

- **What you ran:** `make dev`, `LOCAL_MODE=1`, OS, Node + Python versions
- **What you expected:** in your own words, no jargon
- **What happened:** include the full error and any URLs the error points at
- **Smallest reproduction:** a chat message, a CLI command, or a file diff

The team is small. We triage in weekly batches, not real-time.

## Proposing a change

For small fixes (typos, bug fixes <50 lines, additional tests) just open a
PR. Describe the change and link any related issue.

For larger changes (new endpoints, schema changes, protocol extensions,
new skills templates), open an issue first to talk through the design. We
have a strong bias toward:

- **Protocol over custom** — extending existing protocols (AG-UI, A2UI,
  MCP, A2A) rather than inventing parallel paths
- **API first** — the HTTP surface stays clean enough we'd be comfortable
  publishing it; anything we'd be embarrassed to ship gets pushed behind
  an interface
- **Observable by default** — OTel + structured logs land alongside the
  feature, not as a follow-up
- **Graceful degradation** — every cloud dependency must have an
  in-memory or no-op fallback so the workshop quick-start path keeps
  working

See [docs/product-axioms.md](docs/product-axioms.md) for the full list.

## Adding a new skill template

The fastest path to demonstrating a new capability is a skill in
`backend/skills/templates/`. Each template has a `SKILL.md` (the agent
instructions) and optional supporting files. Skill loading is dynamic —
no code change needed, just seed via `backend/scripts/seed_skills.py`.

PRs adding skill templates should include:

- A `SKILL.md` with author, version, model, tools, and any tool configs
- A test or evalset under `backend/tests/eval/evalsets/` so we know
  what "working" means
- An entry in WORKSHOP.md if the skill is workshop-facing

## Code style

- **Backend:** Python 3.11+, `uv` for deps, `ruff` for lint + format
  (line-length 120), type hints on all signatures. See
  [backend/CLAUDE.md](backend/CLAUDE.md).
- **Frontend:** TypeScript strict mode, React 19, Tailwind. Lint via
  `npm run quality:check:fast` (eslint + tsc + auth-fetch check).
- **Commit messages:** [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `chore:`).

## Testing

```bash
# Backend
cd backend && make test-fast    # fast unit + integration tests

# Frontend
cd frontend && npm run test:run
```

All PRs must pass:

- Frontend: lint + typecheck + vitest + build (`npm run docker:check`)
- Backend: ruff + pytest fast suite (`make test-fast`)
- Both wired into `.github/workflows/ci.yml`

New behaviour should land with a regression test that would have caught
the bug it fixes or covered the case it adds.

## What we don't accept

- **Sunholo / LangChain dependencies.** v6 is pure ADK + FastAPI by
  design ([CLAUDE.md](CLAUDE.md)).
- **Hardcoded customer-specific values.** Project IDs, email domains,
  branding strings live in env vars or [frontend/src/lib/branding.ts](frontend/src/lib/branding.ts).
- **Provider monoculture.** New skills should remain provider-agnostic
  where possible (the agent factory routes Gemini / Claude / OpenAI).
- **Half-finished features behind feature flags.** Land features
  fully-tested, with the docs page describing them, or don't land them.

## License

By submitting a contribution you agree to license it under the
[Apache License 2.0](LICENSE).

## Questions

- Open an issue with the `question` label.
- For workshop-specific questions (university courses using the
  template, conference attendees), reference WORKSHOP.md and tag
  `workshop`.

.PHONY: promote sweep-test-logs dev dev-restart dev-local proxy-check logs help cli-install cli-reinstall cli-uninstall cli-doctor cli-selftest-mock cli-selftest-live cli-selftest verify-a2a setup-gemini-enterprise smoke-long-stream handoff-e2e elicitation-e2e model-fallback-e2e adk-conformance fetch-ailang-wasm gate-obligation-artefact toolbox-install template-parity template-triage link-check materialize-config upstream-merge check-routing

# ── AILANG WASM deontic engine (ppa-obligation-analysis artefact, 7.6) ──────
# Runtime is pinned in ONE place. Bump AILANG_VERSION in a reviewed one-liner
# when ailang cuts a release; never mix runtime and bundle versions.
AILANG_VERSION ?= v0.29.0
DEONTIC_VERSION ?= 0.1.2

# Fetch the MCP Toolbox binary for local dev (.bin/toolbox, gitignored).
# Deployed, Toolbox is a sidecar CONTAINER built from
# infrastructure/mcp-toolbox/Dockerfile; locally it is this binary, on the same
# loopback port — so the mcp_servers registry entry is identical in both.
# The version is pinned in the script and asserted to match the Dockerfile tag
# by backend/tests/tool_tests/test_toolbox_config_safety.py.
toolbox-install:
	@chmod +x scripts/install-toolbox.sh
	@scripts/install-toolbox.sh

# Launch backend (port 1956) + frontend (port 3000) for local development.
# Logs stream to stdout; Ctrl-C stops both.
dev:
	@chmod +x scripts/dev.sh
	@scripts/dev.sh

# Clean restart — recovers from a corrupted Next .next cache (frontend 500 /
# "Cannot find module './NNN.js'" / no rendering after a rebuild or big
# refactor). Stops the servers, clears .next, relaunches. See scripts/dev-restart.sh.
dev-restart:
	@chmod +x scripts/dev-restart.sh
	@scripts/dev-restart.sh

# Launch backend + frontend in LOCAL_MODE (no GCP creds needed for Firestore
# / Vertex Sessions / Cloud Trace; in-memory Firestore auto-seeds the demo
# skills incl. "Workspace Demo" for the MULTI-SURFACE-A2UI demo).
# Model auth still required — set GOOGLE_API_KEY in backend/.env.
# See WORKSHOP.md for the full tier-1 quickstart.
dev-local:
	@chmod +x scripts/dev-local.sh
	@scripts/dev-local.sh

# Smoke-test the frontend→backend proxy bridge locally.
# Starts both servers, probes /api/proxy/health, then exits.
proxy-check:
	@chmod +x scripts/try-proxy-local.sh
	@scripts/try-proxy-local.sh

logs:
	@scripts/logs.sh

# --- CLI lifecycle ---

# Install the `aiplatform` CLI as a global uv tool. Idempotent: --force
# overwrites any prior install (e.g. the legacy `aitana` / `aitana-cli`
# binary). After this completes, `aiplatform --help` works from anywhere.
cli-install:
	@uv tool install --force ./cli
	@echo "Installed: $$(which aiplatform 2>/dev/null || echo '(not on PATH — check ~/.local/bin)')"

# Remove any prior install of this CLI under any historical name.
# Useful when migrating from the pre-2026-04-28 `aitana` binary.
cli-uninstall:
	@-uv tool uninstall aitana-cli 2>/dev/null
	@-uv tool uninstall aitana     2>/dev/null
	@-uv tool uninstall aiplatform 2>/dev/null
	@echo "Removed any previously installed aitana/aiplatform CLI tool."

# Clean reinstall: remove all historical names then install fresh.
cli-reinstall: cli-uninstall cli-install

# Verify the installed CLI matches the source. Catches the symptom that
# led to the 2026-04-28 rename (broken global binary pointing at a stale
# package layout).
cli-doctor:
	@if ! command -v aiplatform >/dev/null 2>&1; then \
	  echo "aiplatform not on PATH. Run: make cli-install"; exit 1; \
	fi
	@aiplatform --version || { echo "aiplatform installed but broken — run: make cli-reinstall"; exit 1; }

# --- CLI self-test ---

# Mock-backend smoke: boots a tiny SSE server on 127.0.0.1:0, runs the
# real `aiplatform skill probe` binary as a subprocess against it, and
# asserts the printed table. No GCP credentials, no network, no live
# backend. The transport-level safety net respx-mocked tests can't be.
cli-selftest-mock:
	@chmod +x scripts/cli-selftest-mock.sh
	@scripts/cli-selftest-mock.sh

# Live-backend smoke. Requires `make dev` running on :1956 + AIPLATFORM_ID_TOKEN
# + AIPLATFORM_SELFTEST_SKILL_ID (or pass the skill id as the first arg).
# Skips cleanly with exit 0 when any prereq is missing — safe for CI.
cli-selftest-live:
	@chmod +x scripts/cli-selftest-live.sh
	@scripts/cli-selftest-live.sh

# Combined self-test: mock smoke (always runs), then live smoke (skipped
# cleanly if backend or auth missing). Single command for "is the CLI
# wired up correctly" — the entry point future agents/teammates use.
cli-selftest:
	@echo "▶ mock smoke …"
	@$(MAKE) --no-print-directory cli-selftest-mock
	@echo
	@echo "▶ live smoke …"
	@$(MAKE) --no-print-directory cli-selftest-live
	@echo
	@echo "✓ aiplatform CLI self-test complete."

verify-a2a:
	@AP_URL=$${AP_URL:-http://localhost:3456} ./scripts/verify-a2a.sh

# Bootstrap a fresh GCP project ready to host a Gemini Enterprise app.
# Required env: PROJECT_ID, ORG_ID, BILLING_ACCOUNT. Optional: AP_URL.
# Walks through everything scriptable; pauses for the Console-only subscribe step.
setup-gemini-enterprise:
	@./scripts/setup-gemini-enterprise.sh

# MODEL-RELIABILITY M1: long-stream incident regression guard. >5-min SSE stream
# through the deployed proxy must end with the done marker. ENV=dev|test|prod|local.
smoke-long-stream:
	@./scripts/smoke-long-stream.sh $(or $(ENV),dev)

# ARTIFACT-PROMOTION (v6.20.0, AIPLA #47): promote a released version to the
# next env by COPYING the tested images by digest (backend + toolbox) and
# rebuilding only the frontend from the same tag with the target's config.
# Dry run by default — pass GO=1 to actually execute.
#   make promote FROM=test TO=prod VERSION=v6.20.0        # plan only
#   make promote FROM=test TO=prod VERSION=v6.20.0 GO=1   # execute
promote:
	@./scripts/promote-env.sh --from $(or $(FROM),dev) --to $(or $(TO),test) \
		--version $(VERSION) $(if $(GO),--yes,)

# HANDOFF-UNIFY (v6.10.0): acceptance harness for the unified ADK-native handoff.
# Streams the three handoff levels against a DEPLOYED env via real AG-UI streams
# and asserts on the event sequence (auto / confirm / confirm-with-form). This is
# the definition of done. ENV=dev|test.
handoff-e2e:
	@./scripts/handoff-e2e.sh $(or $(ENV),dev)

# ADK-CONTRACT-CONFORMANCE (v6.17.0): the hermetic "real ADK flow" guards that
# catch custom<->ADK boundary regressions (the class jsdom/isolated tests miss).
# Root passthrough to the backend target. This is the gate for a `google-adk`
# version bump — it MUST stay green when the pin in backend/pyproject.toml moves.
# See docs/design/v6.17.0/adk-contract-checklist.md.
adk-conformance:
	@cd backend && $(MAKE) adk-conformance

# v6.12.0: acceptance harness for the AGENT-authored elicitation path — streams a
# real AG-UI run against a SPECIALIST and asserts the model authors its own A2UI
# chat form via request_confirmation (AI-constructed fields render on the wire).
# The gap handoff-e2e left. ENV=dev|test.
elicitation-e2e:
	@./scripts/elicitation-e2e.sh $(or $(ENV),dev)

# v6.13.0: acceptance harness for cross-provider model fallback — streams a
# complex prompt to a Claude-thinking skill and asserts a tool-using turn NEVER
# dies on `tool_call_id`; proves the real path when a MODEL_FALLBACK crosses
# providers (Anthropic down -> Gemini). ENV=dev|test.
model-fallback-e2e:
	@./scripts/model-fallback-e2e.sh $(or $(ENV),dev)

# MODEL-RELIABILITY M3: probe every {model, region} pair in the models.yaml
# fallback chains against live Vertex — catches silent per-region model drift.
verify-regions:
	@cd backend && GOOGLE_CLOUD_PROJECT=$(or $(PROJECT),your-project-id) uv run python scripts/verify_regions.py

# MODEL-RELIABILITY M4: E2E fault-injection probe — scratch backend with
# FAULT_INJECT_MODEL armed proves retry -> fallback -> answer + events.
probe-fallback:
	@./scripts/probe-fallback.sh $(or $(SKILL),general-assistant)

# Fetch + checksum-verify the pinned AILANG WASM runtime and sunholo/deontic
# engine into the obligation artefact's (git-ignored) assets dir. Idempotent.
fetch-ailang-wasm:
	@AILANG_VERSION=$(AILANG_VERSION) DEONTIC_VERSION=$(DEONTIC_VERSION) ./scripts/fetch-ailang-wasm.sh

# M1 acceptance gate: byte-identical WASM-vs-CLI report, recompute latency,
# boot time, brotli size. Uses risk-gate scratchpad assets if present, else
# the fetched artefact assets.
gate-obligation-artefact:
	@./scripts/gate-obligation-artefact.sh

# One manual fire of the 6-hourly test-env bug sweep (normally cron on the
# Mac Studio: `0 */6 * * * scripts/one-test-log-sweep.sh`). Pre-checks the
# last 6h of your-project-id-test logs; escalates to headless claude triage
# + GitHub issue filing only when suspect entries exist. Log:
# ~/logs/one-test-log-sweep.log
sweep-test-logs:
	@./scripts/one-test-log-sweep.sh

# TEMPLATE-INVERT M1: how far is this repo from being publishable by DELETION
# ALONE? Runs the sanitizer and reports every file that exists in both trees
# with different CONTENT. Deletions are legal (the file just won't exist
# upstream); rewrites are not (same path, different bytes = a permanent merge
# conflict on every sync). Reaching 0 is the go/no-go gate for making the
# template upstream. See docs/design/template/template-repo-topology.md
template-parity:
	@./scripts/template-parity.sh

# Disposition worksheet for the gap: every differing file with its token
# classes and a suggested scrub / move-downstream / config-drive call.
template-triage:
	@./scripts/template-triage.py

# downstream feedback #11: only 20 of 55 design-doc references resolved in the published
# template. Promoting a doc to implemented/ silently invalidates every link to
# it, and the sanitizer deletes docs that surviving files still link to. Run
# this against the SANITIZED tree too — a link that resolves here but not there
# is exactly that bug.
link-check:
	@./scripts/link-check.py

# Create the local real-valued config files from their tracked .example
# templates (infrastructure/mcp-toolbox/tools.yaml, docs/ops/deployed-urls.md).
# Both are gitignored: tracking them would put generic content upstream and real
# content downstream at the same path. Idempotent; never overwrites.
materialize-config:
	@./scripts/materialize-config.sh

# TEMPLATE-INVERT M7: pull template changes down from platform-source. Plan-only
# by default; GO=1 commits. Prints upstream DELETIONS before you commit, because
# git stages those with no conflict and a fork has already lost its own content
# that way.
upstream-merge:
	@./scripts/upstream-merge.sh

# Advisory: did this change land in the right repo? Warns when a commit touches
# TEMPLATE content, which belongs in platform-source so every fork gets it.
# Never blocks — prototyping here is legitimate; leaving it here is the mistake.
check-routing:
	@./scripts/check-upstream-routing.sh

help:
	@echo "make dev                — start backend (1956) + frontend (3456) — cloud mode (real GCP/Vertex)"
	@echo "make template-parity    — TEMPLATE-INVERT: files the sanitizer REWRITES (goal: 0)"
	@echo "make template-triage    — TEMPLATE-INVERT: disposition worksheet for the gap"
	@echo "make link-check         — resolve every relative markdown link in docs/"
	@echo "make materialize-config — create local tools.yaml / deployed-urls.md from .example"
	@echo "make upstream-merge     — pull template changes down from platform-source (GO=1 to commit)"
	@echo "make check-routing      — advisory: does this change belong upstream?"
	@echo "make dev-local          — start backend + frontend in LOCAL_MODE (no GCP creds, in-memory Firestore)"
	@echo "make logs               — stream backend logs (OTEL noise filtered out)"
	@echo "make proxy-check        — smoke-test the proxy bridge (CI helper)"
	@echo
	@echo "make cli-install        — install the aiplatform CLI as a global uv tool"
	@echo "make cli-reinstall      — clean reinstall (uninstalls historical aitana names first)"
	@echo "make cli-doctor         — verify the installed aiplatform CLI is wired correctly"
	@echo "make cli-selftest       — run mock + live smokes (live skips cleanly if no backend)"
	@echo "make cli-selftest-mock  — offline end-to-end (real binary, mock SSE backend)"
	@echo "make cli-selftest-live  — diagnostic against running \`make dev\` backend"
	@echo
	@echo "make smoke-long-stream  — >5-min SSE survival probe vs deployed env (ENV=dev|test|prod|local)"
	@echo "make promote            — promote a release to the next env by digest-copy (FROM= TO= VERSION= [GO=1])"
	@echo "make verify-regions     — probe models.yaml chain {model,region} pairs on live Vertex (PROJECT=…)"
	@echo "make probe-fallback     — E2E fault-injection probe: retry -> fallback -> answer (SKILL=…)"
	@echo
	@echo "make fetch-ailang-wasm  — fetch+verify pinned AILANG wasm + deontic engine (AILANG_VERSION=…)"
	@echo "make gate-obligation-artefact — M1 gate: byte-identical WASM/CLI + latency + boot + brotli size"
	@echo
	@echo "make verify-a2a         — A2A spec-compliance probe (G43); set AP_URL=https://… for deployed"
	@echo "make setup-gemini-enterprise — bootstrap a fresh GCP project for hosting a Gemini Enterprise app"
	@echo "                                (PROJECT_ID=… ORG_ID=… BILLING_ACCOUNT=… [AP_URL=…])"

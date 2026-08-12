# Sprint Plan: AIPLATFORM-CLI-SELFTEST

## Summary

Make the freshly-renamed `aiplatform` CLI ([commit ded41b6](../../..)) self-verifiable end-to-end. Two paths: a **mock-backend smoke** that any future agent or fresh teammate can run with no GCP credentials, and a **live-backend smoke** that runs against a real `make dev` backend when one is up. Both land as single-command `make` targets so they're discoverable from `make help`.

**Duration:** ~0.5 day
**Scope:** Tooling (cli/, scripts/, root Makefile)
**Dependencies:** [TTFT-INSTR](implemented/ttft-instrumentation.md) ✅ — provides the `aiplatform skill probe` command and the `LATENCY_REPORT` AG-UI event the smoke asserts on.
**Risk Level:** Low — pure additive dev tooling. No product-surface changes, no new test infra.
**Sprint ID:** `AIPLATFORM-CLI-SELFTEST`

## Current Status Analysis

### Recent Velocity

- 7 commits in the past 24 hours (ttft-instrumentation across 5 milestones + the cli rename).
- Comparable sprint shape: `chat-history-fixes-sprint.md` shipped 430 LOC in 1 day across 3 milestones.
- This sprint is smaller — ~280 LOC, ~0.5 day.

### Existing Implementation

- `aiplatform` binary installed at `<local-path>` (verified `--version`, `--help`, `skill probe --help`).
- `aiplatform skill probe` already pytest-covered via `respx`-mocked SSE responses (4 tests in `cli/tests/test_cli_skill_probe.py`).
- A throwaway mock SSE server at `/tmp/aiplatform_mock_backend.py` proved the binary works end-to-end this session (real `httpx.stream` against a real socket; no respx involvement). That throwaway is what M1 codifies.
- `make cli-install/uninstall/reinstall/doctor` are wired in the root Makefile.
- The auth-smoke infrastructure (per memory) provides paired `.test` users with rotated passwords and the `make smoke-auth` flow — M2 reuses that.

### What's missing

- The mock-backend script lives in `/tmp` and isn't checked in. Future sessions or teammates have no scripted way to verify the CLI works without booting a real backend.
- No `make cli-smoke` (or similar) for "is the CLI plumbing correct, end-to-end".
- No live-mode smoke that actually exercises the chat path against a running backend (the existing pytest mock-only path can't catch SSE-buffering regressions in the proxy or transport layers).

## Proposed Milestones

### Milestone 1: Mock-backend smoke (offline, no creds)

**Scope:** tooling (Python + shell)
**Goal:** A checked-in script that boots a tiny Python SSE server on a local port, invokes `aiplatform skill probe` against it, and asserts the printed table contains the expected stage names + ms values. Returns non-zero on shape mismatch. Runnable in CI with no network, no GCP, no Firebase.
**Estimated:** ~140 LOC implementation + ~50 LOC tests = ~190 LOC
**Duration:** ~0.3 day

**Tasks:**
- [ ] `cli/tests/fixtures/mock_backend.py` — port the throwaway from `/tmp/aiplatform_mock_backend.py`. Listens on `127.0.0.1:0` (let OS pick port), emits a canonical event sequence ending in `LATENCY_REPORT`, threadable. ~80 LOC.
- [ ] `scripts/cli-selftest-mock.sh` — start mock, run `aiplatform skill probe` with `AIPLATFORM_API_URL` pointed at it, capture output, grep for stage names + LATENCY_REPORT marker text, kill mock, propagate exit code. ~60 LOC.
- [ ] `make cli-selftest-mock` target in root `Makefile`. ~5 LOC.
- [ ] `cli/tests/test_cli_selftest_mock.py` — pytest that imports the mock, runs `aiplatform skill probe` via `subprocess`, asserts exit 0 + presence of "first_model_token" + "412.30ms"-style ms values. ~50 LOC.
- [ ] Update `make help` block in root Makefile.

**Files to Create/Modify:**
- `cli/tests/fixtures/__init__.py` (new, empty)
- `cli/tests/fixtures/mock_backend.py` (new, ~80 LOC)
- `cli/tests/test_cli_selftest_mock.py` (new, ~50 LOC)
- `scripts/cli-selftest-mock.sh` (new, ~60 LOC, +x)
- `Makefile` (modify, +10 LOC)

**Acceptance Criteria:**
- [ ] `bash scripts/cli-selftest-mock.sh` exits 0 and prints the TTFT table (verified manually).
- [ ] `make cli-selftest-mock` works the same way.
- [ ] `cd cli && uv run pytest tests/test_cli_selftest_mock.py -v` passes.
- [ ] Script produces a clear non-zero exit + diagnostic when the binary is missing OR returns malformed output (forced by patching the binary path during the test).
- [ ] Mock server cleanly shuts down even if the script is Ctrl-C'd mid-run (trap + kill).

**Risks:**
- **Port conflicts.** Random ports occasionally collide. Mitigation: use `socketserver.ThreadingTCPServer(("127.0.0.1", 0), …)` then read `.server_address[1]` to learn the actual port; pass it to the CLI via env var.
- **`subprocess.run("aiplatform", …)` requires the binary on PATH.** If the user hasn't run `make cli-install` it'll fail. Mitigation: the smoke script and pytest both call `make cli-doctor` first and skip with a clear message if the binary is missing.

---

### Milestone 2: Live-backend smoke (real `make dev`)

**Scope:** tooling (shell)
**Goal:** `make cli-selftest-live` script that, given an already-running `make dev` backend on `:1956`, fires `aiplatform skill probe` against a known seed skill (or skips with a message if no seed is available) and asserts a non-zero `first_model_token_ms` lands in the output. Lets a teammate diagnose "is the chat path responsive" in one command instead of opening a browser.
**Estimated:** ~80 LOC + ~30 LOC = ~110 LOC
**Duration:** ~0.15 day

**Tasks:**
- [ ] `scripts/cli-selftest-live.sh` — pre-flight: `curl :1956/health`. Auth: try `make smoke-auth` to mint a Firebase token (existing infrastructure per memory `project_auth_smoke_infrastructure.md`). If auth fails, exit 0 with a clear "skipping live smoke — auth not configured" message (don't fail CI for missing creds). Run `aiplatform skill probe <seed-skill> --json`, jq the `first_model_token_ms` field, assert >0. ~80 LOC.
- [ ] `make cli-selftest-live` target. ~5 LOC.
- [ ] Optional: `--seed-skill <id>` flag with a fallback default that we'll document but not enforce (the seed-skill infrastructure is owned by [LOCAL_MODE](local-mode-and-workshop-readiness.md), not yet shipped).

**Files to Create/Modify:**
- `scripts/cli-selftest-live.sh` (new, ~80 LOC, +x)
- `Makefile` (modify, +5 LOC)

**Acceptance Criteria:**
- [ ] When `make dev` is up + `make smoke-auth` works + a seed skill exists: `make cli-selftest-live` exits 0 and prints a "first_model_token=XXms" line.
- [ ] When backend is down: clean error "backend not reachable on :1956 — run `make dev` first" + exit 1.
- [ ] When auth is unavailable: skip with exit 0 + clear message (this lets CI run it without breaking).
- [ ] Bash strict-mode (`set -euo pipefail`) and proper `trap` on cleanup.

**Risks:**
- **Auth coupling.** `make smoke-auth` relies on a specific `.test` user being seeded. The smoke script falls back to `AIPLATFORM_ID_TOKEN` env var if smoke-auth fails — same precedence as the production CLI auth resolution.
- **No seed skill in dev.** Until LOCAL_MODE (1.18) ships seed data, the live smoke needs a real skill in Firestore. Document this in the script's header and exit cleanly with skip when no skill id is supplied.

---

### Milestone 3: Combined target + docs

**Scope:** tooling (shell + docs)
**Goal:** Single `make cli-selftest` that runs the mock smoke first (always), then the live smoke (skipped if backend not up). README + Makefile help reflect the new commands. cli/README.md gets a short "Self-test" section.
**Estimated:** ~30 LOC
**Duration:** ~0.05 day

**Tasks:**
- [ ] `make cli-selftest` target in root Makefile (depends on `cli-selftest-mock` + `cli-selftest-live`). ~10 LOC.
- [ ] Update `make help` text.
- [ ] `cli/README.md` — add a "Self-test" subsection with one-liner usage.
- [ ] CLAUDE.md — add `make cli-selftest` to the commands table.

**Files to Create/Modify:**
- `Makefile` (modify, +10 LOC)
- `cli/README.md` (modify, +15 LOC)
- `CLAUDE.md` (modify, +2 LOC)

**Acceptance Criteria:**
- [ ] `make cli-selftest` runs both smokes and reports a unified summary (PASS/SKIP per smoke).
- [ ] `make help` shows the new target.
- [ ] cli/README.md "Self-test" section is one-glance scannable.

---

## Day-by-Day Breakdown

### Day 1 (~0.5 day total)
- **Morning:** M1 mock-backend smoke. Port the throwaway script, write the bash wrapper, write the pytest. Verify locally.
- **Midday:** M2 live-backend smoke. Bash + auth fallback + jq assert.
- **End-of-day:** M3 combined target + docs. Single commit, run all tests.

## Quality Gates

After each milestone:
```bash
cd cli && uv run pytest tests/ -q
cd cli && uv run ruff check .
bash -n scripts/cli-selftest-*.sh   # syntax check
shellcheck scripts/cli-selftest-*.sh 2>/dev/null || true   # nice-to-have
```

After the sprint:
```bash
make cli-selftest          # unified self-test
```

## Success Metrics

- [ ] `make cli-selftest-mock` exits 0 from a clean checkout (no live backend, no creds).
- [ ] `make cli-selftest-live` exits 0 against a running `make dev` + working auth.
- [ ] `make cli-selftest` (combined) exits 0 in both clean-checkout and full-stack-running scenarios.
- [ ] Existing 29 CLI tests still pass; no other test suite affected.
- [ ] `make help` lists all three new targets.
- [ ] cli/README.md "Self-test" section exists and is accurate.

## Dependencies

- [TTFT-INSTR](implemented/ttft-instrumentation.md) ✅ — provides the probe command + `LATENCY_REPORT` event.
- The CLI must already be installed via `make cli-install`. Both scripts call `make cli-doctor` first and bail out with a clear message if it isn't.
- M2 leans on the `make smoke-auth` infrastructure (memory: `project_auth_smoke_infrastructure.md`); falls back to skipping if unavailable.

## Open Questions

- **Should the mock smoke also verify exit codes for the off-mode and run-error paths?** The pytest in M4 of TTFT-INSTR already covers those via respx. The end-to-end mock smoke focuses on the happy path — the multi-process integration is what respx can't catch. (Decision: yes, keep mock smoke happy-path-only; cross-reference the respx tests in the script header.)
- **Does this sprint need a separate design doc?** No — pure dev tooling, no axiom impact, no product surface. The sprint plan IS the design.

## Notes

- **Why a real subprocess + real socket vs respx?** The 4 existing pytest cases use respx to stub `httpx.Response`, which catches code-level bugs but not transport-level ones (SSE buffering, connection lifecycle, stdin/stdout pipe semantics). The mock smoke runs the actual installed binary against a real socket so transport regressions surface here, not in production.
- **Why not require LOCAL_MODE first?** LOCAL_MODE ([1.18](local-mode-and-workshop-readiness.md)) is a separate workshop blocker. The live smoke gracefully skips when auth/seed-skill isn't available; once LOCAL_MODE ships, this sprint's live smoke will work with no setup at all.
- **Workshop relevance:** with this sprint shipped, a workshop attendee in 2026-07 can `git clone` → `make cli-install` → `make cli-selftest-mock` and have one-command proof their setup works. That's the same UX pitch as `terraform plan` or `kubectl get pods` — "show me it works without me having to set up the world."

# FE-BRINGUP-1 — `/api/proxy/health` 404 on Cloud Run

- **Status:** Resolved 2026-04-15 on `platform-frontend` rev `00008-ls8`
- **Scope:** `platform-frontend` (dev). `platform-backend` standalone deploy was briefly broken too while chasing fix #2.
- **User impact:** none — defect surfaced during bring-up, before any real traffic.
- **Why this writeup:** the fix was four compounding root causes, each of which independently looked like the answer. Took longer than it should have. This doc exists so the next person (including future sessions) doesn't repeat the same dead ends.

## TL;DR

Frontend proxy to sidecar was broken on Cloud Run while passing locally. Four things had to be true simultaneously; fixing any one didn't help:

1. Sidecar and ingress were both trying to listen on `:8080`.
2. Frontend defaulted `BACKEND_URL` to `localhost:8080` — its own port.
3. Once we moved to `localhost:1956`, Node resolved it to IPv6 `::1` while uvicorn bound IPv4 only.
4. Once the wire was correct, Cloud Run was marking the sidecar healthy before ADK's heavy import finished, so early requests hit a not-yet-listening process.

Fixed by, in order: `--port` + `BACKEND_URL` on the right containers ([fcaa492](https://github.com/sunholo-data/ai-protocol-platform/commit/fcaa492)), dual-role `${PORT:-1956}` restored so standalone didn't break ([f0eb654](https://github.com/sunholo-data/ai-protocol-platform/commit/f0eb654)), literal `127.0.0.1` ([51def9a](https://github.com/sunholo-data/ai-protocol-platform/commit/51def9a)), explicit sidecar startup probe ([1e9717a](https://github.com/sunholo-data/ai-protocol-platform/commit/1e9717a) / [9238d17](https://github.com/sunholo-data/ai-protocol-platform/commit/9238d17)).

## Timeline (condensed)

| Commit | Intent | Outcome |
|---|---|---|
| [25f502d](https://github.com/sunholo-data/ai-protocol-platform/commit/25f502d) | Ship sidecar proxy + health badge | Deployed green; `/api/proxy/health` returned 404 HTML |
| [96ebfea](https://github.com/sunholo-data/ai-protocol-platform/commit/96ebfea) | Hypothesis: catch-all `[...path]` route not emitted in standalone | Added direct `/api/proxy/health` route — still 404 |
| [11b2437](https://github.com/sunholo-data/ai-protocol-platform/commit/11b2437) | Delete broken catch-all | Confirmed catch-all wasn't the cause; still 404 |
| [fcaa492](https://github.com/sunholo-data/ai-protocol-platform/commit/fcaa492) | **Root cause #1+#2:** pin sidecar `--port=1956`, set `BACKEND_URL=http://localhost:1956` | Now 502 `fetch failed` with empty sidecar logs |
| [f0eb654](https://github.com/sunholo-data/ai-protocol-platform/commit/f0eb654) | Fix collateral damage: standalone `platform-backend` broke because Dockerfile had hardcoded `--port 1956` | Standalone green again |
| [51def9a](https://github.com/sunholo-data/ai-protocol-platform/commit/51def9a) | **Root cause #3:** `localhost` → IPv6; use literal `127.0.0.1` | Now 502 intermittent, sidecar sometimes not up |
| [1e9717a](https://github.com/sunholo-data/ai-protocol-platform/commit/1e9717a) | **Root cause #4:** add startup probe + `--cpu-boost` | `--cpu-boost` rejected (alpha/beta flag only) |
| [9238d17](https://github.com/sunholo-data/ai-protocol-platform/commit/9238d17) | Drop `--cpu-boost`, keep `tcpSocket.port=1956` probe | **Green.** Three stability runs <200ms. |
| [78714b7](https://github.com/sunholo-data/ai-protocol-platform/commit/78714b7) | Add post-deploy smoke so this class of bug fails loud | — |

## Root causes

### #1 — Sidecar and ingress both on `:8080`

**Symptom:** `/api/proxy/health` → 404 HTML with Next `vary: rsc, next-router-state-tree...` headers.

**Diagnosis:** those headers are Next's internal `_not-found` page. The frontend was fetching `localhost:8080/health` — which is its *own* ingress port. The fetch looped back into Next, which responded with 404 because no `/health` route exists on the frontend.

**Fix:** Cloud Run injects `PORT=8080` only into the ingress container. Sidecars must listen elsewhere. v5 (and now v6) use `:1956`.

**Lesson:** The tell for "frontend is talking to itself" is the *body* of the 404 — Next's HTML with its routing headers, not Cloud Run's edge 404. Always look at the body, not just the status code.

### #2 — `BACKEND_URL` defaulted to `localhost:8080`

Same symptom as #1, same fix. The defaults in [frontend/src/app/api/proxy/health/route.ts](../../../frontend/src/app/api/proxy/health/route.ts) and the `--set-env-vars` in `cloudbuild.yaml` both now pin `127.0.0.1:1956` and carry a comment explaining why.

**gcloud flag ordering gotcha:** `--set-env-vars` applies to the most recently selected `--container=`. Put `BACKEND_URL` between `--container=main` and `--container=sidecar`, not after both.

### #3 — `localhost` → IPv6, uvicorn → IPv4 only

**Symptom:** After #1/#2 were fixed, proxy returned 502 `{"error":"backend_unreachable","message":"TypeError: fetch failed"}` with *empty* sidecar logs (no request ever reached uvicorn).

**Diagnosis:** Node's DNS resolves `localhost` to `::1` (IPv6 loopback). Uvicorn's `--host 0.0.0.0` binds IPv4 only. Connect fails silently with no upstream log entry because the packet never left the frontend container. v5 uses the literal `127.0.0.1` for exactly this reason (we rediscovered the hard way).

**Fix:** literal `127.0.0.1` in both the proxy route and the `BACKEND_URL` env. `NODE_OPTIONS=--dns-result-order=ipv4first` in the Dockerfile is defensive but not sufficient on its own — belt AND braces.

**Lesson:** "silent fetch failed, no upstream log" ≈ DNS/address-family mismatch, ≈ sidecar crashed, or ≈ wrong port (but wrong port usually shows the loopback symptom of #1 instead).

### #4 — No sidecar startup probe

**Symptom:** After all three above were fixed, *some* requests still 502'd right after a new revision rolled out.

**Diagnosis:** Cloud Run's default readiness check is "container started" — for the sidecar that meant `docker run` returned. It doesn't wait for the socket to be listening. ADK's import graph is heavy (5.7s cold on laptop, 10–20s on Cloud Run because request-based billing throttles sidecar CPU when no request is in flight). So Cloud Run would mark the revision healthy in ~2.78s while uvicorn was still importing Google libraries, and the first frontend request would race the sidecar boot and lose.

**Fix:** explicit TCP startup probe on the sidecar port.

```yaml
- '--startup-probe=tcpSocket.port=1956,periodSeconds=5,failureThreshold=24,timeoutSeconds=5'
```

This has two effects: Cloud Run waits for the actual listener, *and* probing drives CPU to the sidecar during startup (request-based billing otherwise starves it).

**Not the fix:** `--cpu-boost` is only available in gcloud alpha/beta. Don't use it in a GA `gcloud run deploy` step; it will fail. The startup probe alone is sufficient.

**v5 didn't need this** because v5's backend image was lighter (Flask + Sunholo, no ADK). v6's ADK imports move the startup envelope past Cloud Run's default implicit readiness window.

## Why it took longer than anticipated

Process failures, ranked by minutes lost:

1. **Wrong opening hypothesis.** First theory was "Next 15 standalone doesn't emit the catch-all route." I spent commits [96ebfea](https://github.com/sunholo-data/ai-protocol-platform/commit/96ebfea) / [11b2437](https://github.com/sunholo-data/ai-protocol-platform/commit/11b2437) proving/disproving it before reading the 404 body carefully enough to see the Next-specific `vary` headers pointing at loopback. **Lesson:** read response bodies before theorising.
2. **Didn't consult the working v5 example early.** v5 already had a working sidecar deploy with `127.0.0.1:1956` and comments explaining why. I landed at the same answer by trial and error instead of diffing against v5 in the first ten minutes. User had to prompt: *"review the working example in frontend/ v5. Compare to what we are trying now and report differences."* **Lesson captured in:** [feedback_consult_google_dev_mcp.md](../../../../../.claude/projects/-Users-mark-dev-aitana-labs-platform/memory/feedback_consult_google_dev_mcp.md) — generalised to "consult the MCP / working example *before* coding, not only to verify afterwards."
3. **No post-deploy smoke in CI.** The first broken revision deployed green because Cloud Build's success criterion was "`gcloud run deploy` returned 0." Nothing exercised the proxy. **Lesson captured in:** [feedback_cicd_first.md](../../../../../.claude/projects/-Users-mark-dev-aitana-labs-platform/memory/feedback_cicd_first.md). **Landed in this incident:** `smoke-deployed` / `smoke-backend` steps in both cloudbuild files.
4. **Local repro didn't test the Docker image.** `scripts/try-proxy-local.sh` boots the backend via `uv run uvicorn` on the host, not the Docker container. Local passed, Cloud Run failed, and the gap was Dockerfile port behaviour. **Not yet fixed** — a Docker-based local repro is a reasonable follow-up if we hit another "works on laptop, fails on Cloud Run" bug. For now the post-deploy smoke catches it in CI within ~30s of deploy.
5. **Collateral damage from hardcoding.** First fix for #1 hardcoded `--port 1956` in the Dockerfile `CMD`, which broke the *standalone* backend deploy (where Cloud Run injects `PORT=8080`). Needed a second commit ([f0eb654](https://github.com/sunholo-data/ai-protocol-platform/commit/f0eb654)) to restore the `${PORT:-1956}` fallback. **Lesson:** the backend image is dual-role; never hardcode its port. Comment in [backend/Dockerfile](../../../backend/Dockerfile) now explains this explicitly so the next person doesn't "simplify" it back.

## Preventive changes that landed

All in-tree, all referenced from `CLAUDE.md`:

- **[gotcha_cloudrun_sidecar_ports.md](../../../../../.claude/projects/-Users-mark-dev-aitana-labs-platform/memory/gotcha_cloudrun_sidecar_ports.md)** — technical recipe (ports, 127.0.0.1, dual-role image, startup probe, debug symptoms → diagnosis table).
- **[cloudbuild.yaml](../../../cloudbuild.yaml)** — `smoke-deployed` step curls `/`, `/api/health`, `/api/proxy/health`; non-200 fails the build.
- **[backend/cloudbuild.yaml](../../../backend/cloudbuild.yaml)** — `smoke-backend` step hits the IAM-protected `/health` with an identity token.
- **[scripts/smoke-deployed.sh](../../../scripts/smoke-deployed.sh)** — laptop equivalent, env-aware.
- **[docs/ops/deployed-urls.md](../deployed-urls.md)** — canonical URL record per env.
- **Inline comments** on the `--port`, `BACKEND_URL`, and `--startup-probe` args pointing readers here so nobody "simplifies" them away.

## What to do next time you see similar symptoms

| Symptom | Most likely cause | First check |
|---|---|---|
| 404 HTML with `vary: rsc, next-router-state-tree...` | Frontend fetching itself (wrong port) | `BACKEND_URL` env on main container — should be `127.0.0.1:<sidecar-port>`, never `:8080` |
| 502 `backend_unreachable` + empty sidecar logs | DNS/IPv6 or sidecar crashed | Grep sidecar logs for startup; if none, it's DNS — confirm literal `127.0.0.1` |
| Intermittent 502 right after new revision rolls out | Sidecar not ready before traffic | `--startup-probe` present on sidecar? Check `gcloud run services describe` |
| Standalone backend won't start | Dockerfile hardcoded port | `CMD` must use `${PORT:-1956}` fallback |

#!/usr/bin/env bash
#
# verify_image.sh — prove the LINUX Obscura binary does what the audit found.
#
# Why this exists: the 2026-09-02 audit was run against `aarch64-macos`.
# Production is `x86_64-linux`, built with different features from a different
# toolchain. "It worked on the Mac" is not evidence about the artefact we ship,
# so every claim the design leans on is re-checked here, in the image.
#
# There is no Docker on the current dev machine. Run this either:
#   - in Cloud Build, as a step after the image build, or
#   - on any Linux host with Docker:  ./verify_image.sh <image-ref>
#
# Exits non-zero on the first failed check.

set -euo pipefail

IMAGE="${1:-}"
if [[ -z "$IMAGE" ]]; then
    echo "usage: $0 <image-ref>" >&2
    exit 2
fi

pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; exit 1; }

run() { docker run --rm --entrypoint sh "$IMAGE" -c "$1"; }

echo "Verifying $IMAGE"

# 1 — the binary loads at all. This is the glibc check: a bullseye or alpine
#     base fails here with "version 'GLIBC_2.35' not found".
version="$(run '/usr/local/bin/obscura --version' 2>&1)" \
    || fail "obscura --version did not run — check the base image's glibc (needs >= 2.35)"
[[ "$version" == *"obscura"* ]] || fail "unexpected --version output: $version"
pass "binary loads: $version"

# 2 — the worker binary is deliberately absent. If a future change needs
#     `scrape`, this check is the thing that should start failing.
if run 'test -e /usr/local/bin/obscura-worker' 2>/dev/null; then
    fail "obscura-worker present — image is ~103 MB heavier than intended"
fi
pass "obscura-worker absent (intended; \`scrape\` unsupported)"

# 3 — SSRF refusal on a loopback literal. The single most important behaviour
#     for a service running inside GCP: this is what stops a page reaching the
#     metadata server.
out="$(run '/usr/local/bin/obscura fetch http://127.0.0.1:1/ 2>&1 || true')"
[[ "$out" == *"not allowed"* ]] \
    || fail "loopback fetch was NOT refused — SSRF guard missing or disabled. Got: $out"
pass "SSRF: loopback literal refused"

# 4 — SSRF refusal via DNS. localtest.me is a real public name resolving to
#     127.0.0.1, so this exercises the RESOLVER-level guard, not the literal one.
#
#     Verified 2026-09-02: this path is genuinely blocked, but it surfaces as a
#     generic "error sending request", NOT the explicit "not allowed" of the
#     literal check — the resolver returns Err and reqwest flattens the reason
#     away. So asserting on a message here would be asserting on the wrong
#     thing, and asserting on "the fetch failed" alone proves nothing (nothing
#     is listening in the container either way).
#
#     Instead: serve real content on a local port, then prove Obscura does NOT
#     return it while a plain client can. That is unambiguous.
canary="OBSCURA-SSRF-CANARY-8842"
out="$(run "
  mkdir -p /tmp/ssrfsite && echo '<html><body>${canary}</body></html>' > /tmp/ssrfsite/index.html
  python3 -m http.server 8791 --directory /tmp/ssrfsite --bind 127.0.0.1 >/dev/null 2>&1 &
  sleep 1
  # Prove the server really is serving, or the check is vacuous.
  python3 -c \"import urllib.request;print('SERVING' if '${canary}' in urllib.request.urlopen('http://localtest.me:8791/').read().decode() else 'NOTSERVING')\" 2>&1
  echo '---OBSCURA---'
  /usr/local/bin/obscura fetch --dump markdown http://localtest.me:8791/ 2>&1 || true
")"
[[ "$out" == *"SERVING"* ]] \
    || fail "test server was not reachable, so this check would prove nothing. Got: ${out:0:300}"
[[ "$out" != *"---OBSCURA---"*"$canary"* ]] \
    || fail "SSRF BYPASS: Obscura returned loopback content via a DNS name. Got: ${out:0:400}"
pass "SSRF: hostname resolving to loopback did not yield content"

# 5 — MCP stdio handshake. This is the transport the supervisor uses; if the
#     tool list does not come back, nothing else in the service can work.
mcp_out="$(run 'printf "%s\n%s\n%s\n" \
  "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"verify\",\"version\":\"1\"}}}" \
  "{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}" \
  "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}" \
  | /usr/local/bin/obscura mcp 2>/dev/null')"
[[ "$mcp_out" == *"browser_markdown"* ]] \
    || fail "MCP tools/list did not include browser_markdown"
[[ "$mcp_out" == *"browser_screenshot"* ]] \
    || fail "MCP tools/list did not include browser_screenshot — is this the no-render build?"
pass "MCP stdio handshake returns the expected tools"

# 6 — real extraction against a live page. Left last because it is the only
#     check needing egress; everything above is hermetic.
if [[ "${SKIP_NETWORK_CHECK:-}" == "1" ]]; then
    echo "  SKIP  live extraction (SKIP_NETWORK_CHECK=1)"
else
    out="$(run '/usr/local/bin/obscura fetch --dump markdown https://example.com/ 2>&1 || true')"
    [[ "$out" == *"Example Domain"* ]] \
        || fail "markdown extraction of example.com did not contain expected text. Got: ${out:0:300}"
    pass "live markdown extraction works"
fi

echo
echo "All checks passed for $IMAGE"

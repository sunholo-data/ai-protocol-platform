# Obscura pin

Everything the image depends on about Obscura, recorded so a bump is a
deliberate, reviewable act rather than a rebuild that silently drifts.

## Pinned artefact

| | |
|---|---|
| Upstream | `h4ckf0r0day/obscura` (Apache-2.0) |
| Our fork | `sunholo-data/obscura` — byte-identical to upstream `14da23d` as of 2026-09-02 |
| Release tag | `v0.2.1` (published 2026-08-23) |
| Asset | `obscura-x86_64-linux-stealth.tar.gz` |
| SHA-256 | `49856870420960ce489d2d1ff40fffac5b8c016604b9af0ded8ed6373abd9302` |
| Size | 86,399,647 bytes compressed |

The Dockerfile verifies that checksum and **fails the build on a mismatch**.
Never relax that to a warning: an unverified 86 MB binary that runs untrusted
JavaScript is the worst possible thing to fetch on trust.

**Never track `main`.** Upstream took 700+ commits in five months from a long
tail of one-PR contributors. Owning the fork and pinning a tag is what makes the
supply chain reviewable. Bumping is a PR with a re-run of the checks below.

## Why the release binary and not a source build

Obscura vendors V8. Building it from source takes tens of minutes, which would
make every CI iteration unaffordable and every cold rebuild a coffee break. The
release asset is the same artefact the audit tested.

The tradeoff is honest: we trust upstream's GitHub Actions build for the bytes,
mitigated by (a) the pinned checksum above, (b) the fork, and (c) the fact that
the process runs with a service account holding no permissions.

## Why `stealth`, not the plain build

`stealth` adds TLS impersonation via `wreq` and tracker blocking. It does **not**
weaken the SSRF guard: the guard is implemented for both transports —
`reqwest::dns::Resolve` and `wreq::dns::Resolve` in `wreq_client.rs` — precisely
so `--stealth` never trades it away. Upstream
[#793](https://github.com/h4ckf0r0day/obscura/issues/793) reports the stealth
client *over*-blocks `--allow-private-network`; harmless here, because we never
enable it.

## Why `distroless/cc-debian12` specifically, and not bullseye

The binary is a dynamically linked PIE needing `libc.so.6`, `libgcc_s.so.1` and
`libm.so.6`, with symbol versions up to **`GLIBC_2.35`**:

```
$ file obscura
ELF 64-bit LSB pie executable, x86-64 … dynamically linked,
interpreter /lib64/ld-linux-x86-64.so.2, for GNU/Linux 3.2.0
```

- Debian 12 (bookworm) ships glibc **2.36** ✅
- Debian 13 (trixie) ships glibc **2.41** ✅
- Debian 11 (bullseye) ships glibc **2.31** ❌ — would fail at load with
  `version 'GLIBC_2.35' not found`

So the base image tag is load-bearing. Do not "modernise" it to a smaller
base (`distroless/static`, `alpine`) — this binary is not static and musl is not
glibc.

**The base is pinned to `python:3.12-slim-bookworm`, not `python:3.12-slim`.**
The floating tag moved from bookworm to trixie between this file being written
and the first Cloud Build run on 2026-09-02. Trixie is fine on glibc, so nothing
broke — but a floating base is the wrong thing to hang a version floor on, and
the next move might not be upward.

## Why `obscura-worker` is NOT in the image

The release tarball contains two binaries. `obscura-worker` (103 MB) is spawned
only by:

- the `scrape` subcommand (`crates/obscura-cli/src/main.rs:1543-1578`), and
- `serve --workers N` (`main.rs:551-571`).

We use neither — the supervisor drives `obscura mcp` over stdio. Dropping it
takes the image from ~240 MB to ~140 MB, which matters because this service
scales to zero and pays the pull on every cold start.

**If a future phase wants batch crawling via `scrape`, the worker must come
back.** That is the one thing that breaks by omitting it.

## Verification checklist on any bump

The audit was run against `aarch64-macos`. **Production is `x86_64-linux` — a
different artefact from a different build.** These must be re-run in the image,
not assumed to transfer:

- [ ] `obscura --version` reports the expected version
- [ ] `obscura mcp` responds to a `tools/list` JSON-RPC handshake
- [ ] `browser_markdown` returns usable text for a JS-rendered page
- [ ] `browser_screenshot` returns a valid PNG
- [ ] **SSRF refusal**: `obscura fetch http://127.0.0.1:PORT/` is refused with
      *"Access to private/internal IP address … is not allowed"*
- [ ] A hostname resolving to loopback (e.g. `localtest.me`) is refused

`services/browser/verify_image.sh` runs all six. There is no Docker on the
current dev machine, so this runs in Cloud Build or on a Linux host — see the
script header.

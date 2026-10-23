# platform-browser — an isolated browsing service.
#
# Two stages on purpose:
#   1. `obscura` fetches and CHECKSUMS the pinned release binary. Nothing from
#      this stage's toolchain (curl, tar, its CA bundle) reaches the runtime.
#   2. the runtime carries the supervisor plus that one binary.
#
# Everything about the pin — tag, checksum, glibc floor, why the worker binary
# is absent — is recorded in OBSCURA_PIN.md. Read it before changing any ARG.

# ---------------------------------------------------------------------------
# Stage 1 — fetch and verify the pinned Obscura binary
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim AS obscura

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Bumping these three is a reviewable act. See OBSCURA_PIN.md.
ARG OBSCURA_VERSION=v0.2.1
ARG OBSCURA_ASSET=obscura-x86_64-linux-stealth.tar.gz
ARG OBSCURA_SHA256=49856870420960ce489d2d1ff40fffac5b8c016604b9af0ded8ed6373abd9302

WORKDIR /dl
RUN curl -fsSL -o obscura.tar.gz \
      "https://github.com/h4ckf0r0day/obscura/releases/download/${OBSCURA_VERSION}/${OBSCURA_ASSET}" \
 # Hard fail on mismatch. This is an 86 MB binary that will execute untrusted
 # JavaScript — never downgrade this to a warning.
 && echo "${OBSCURA_SHA256}  obscura.tar.gz" | sha256sum -c - \
 && tar xzf obscura.tar.gz \
 # obscura-worker is only used by `scrape` and `serve --workers`; we drive
 # `obscura mcp` over stdio and use neither. Dropping it saves ~103 MB on an
 # image that pays a pull on every cold start. See OBSCURA_PIN.md.
 && rm -f obscura-worker obscura.tar.gz \
 && chmod +x obscura

# ---------------------------------------------------------------------------
# Stage 2 — the supervisor
# ---------------------------------------------------------------------------
# PINNED to bookworm, not floating `python:3.12-slim`.
#
# The Obscura binary needs GLIBC_2.35, so the base distro is load-bearing —
# bullseye (2.31) and alpine (musl) both fail at load. Bare `python:3.12-slim`
# silently moved from bookworm to TRIXIE between this being written and the
# first Cloud Build run (2026-09-02). Trixie happens to be fine (glibc 2.41),
# but a base that can change under us without notice is exactly the wrong thing
# to depend on for a version floor. See OBSCURA_PIN.md.
FROM python:3.12-slim-bookworm

# curl is required by the uv installer below ("ERROR: need 'curl or wget'"),
# not optional tooling — the first build failed without it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# UV_INSTALL_DIR matters: the installer's default is /root/.local/bin, which is
# unreadable once we drop to the non-root `browser` user below. The first
# deployed revision crash-looped on `sh: 1: uv: Permission denied` -> exit(127).
# The backend image never hit this because it runs as root.
ENV UV_INSTALL_DIR=/usr/local/bin
ADD https://astral.sh/uv/0.5.24/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY --from=obscura /dl/obscura /usr/local/bin/obscura
COPY . .

# Not root. The threat model is a V8 escape inside the child process; the
# service account is the real boundary, but there is no reason to hand a
# hypothetical escape uid 0 as well.
RUN useradd --create-home --uid 10001 browser \
 && chown -R browser:browser /app \
 # uv writes its cache under $HOME; without a writable one it fails at runtime.
 && mkdir -p /home/browser/.cache && chown -R browser:browser /home/browser
USER browser

ARG COMMIT_SHA=""
ENV COMMIT_SHA=${COMMIT_SHA}
ENV OBSCURA_BIN=/usr/local/bin/obscura
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# Cloud Run injects PORT. 0.0.0.0 because a loopback bind fails the TCP startup
# probe and the whole revision never goes Ready — a trap this platform has hit
# before with sidecars.
CMD ["sh", "-c", "uv run uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]

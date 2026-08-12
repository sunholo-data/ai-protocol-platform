# Aitana — Cloud Run image for `modelcontextprotocol/ext-apps` map-server.
#
# We do NOT vendor the source. The image clones upstream at a pinned commit
# at build time so the public template stays clean (no third-party MIT code
# duplicated into our repo) and we can re-pin trivially when we want a new
# upstream.
#
# Build sequence is non-obvious because of a known monorepo-vs-published-pkg
# mismatch:
#   * The published `@modelcontextprotocol/ext-apps@1.0.0` does not include
#     the App.registerTool method that the map-server example calls at
#     runtime (added internally for v1.7.x).
#   * Workaround: build the monorepo root first (`bun run build` runs the
#     ext-apps build then `scripts/link-self.mjs` which symlinks the local
#     v1.7.1 build into node_modules at the right path).
#   * Only THEN does `vite build` in the map-server example produce a
#     dist/mcp-app.html that links against the local v1.7.1 App, not the
#     published v1.0.0.
# Captured in docs/talks/ai-ui-protocol-stack.md verification log
# (2026-04-30) and reproduced here for image build.
#
# main.ts honours PORT env var (defaults 3001 in dev). Cloud Run sets
# PORT=8080 so no override is needed.

# Pinned commit on modelcontextprotocol/ext-apps (= ext-apps v1.7.1).
# Bump deliberately, never on every build — the build-sequence workaround
# above is sensitive to upstream's monorepo layout.
ARG EXT_APPS_REF=0008d3b7

FROM oven/bun:1 AS build

# Toolchain: git for clone, plus node for npm + npx (vite-build uses npm
# under the hood). bun is in the base image.
RUN apt-get update -qq \
  && apt-get install -y -qq --no-install-recommends git ca-certificates curl \
  && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
  && apt-get install -y -qq --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# Clone + checkout the pinned commit. `git fetch --depth=1 <SHA>` requires
# the upstream to enable `uploadpack.allowReachableSHA1InWant`, which
# GitHub does NOT enable by default for arbitrary commits. Workaround:
# full clone + checkout (~10 MB; cheap enough). Pin via EXT_APPS_REF.
ARG EXT_APPS_REF
RUN git clone https://github.com/modelcontextprotocol/ext-apps.git ext-apps \
  && cd ext-apps \
  && git checkout "${EXT_APPS_REF}"

WORKDIR /work/ext-apps

# Step 1: monorepo root install + build. `bun run build` runs the
# generate:schemas + sync:snippets + bun build steps + the
# scripts/link-self.mjs that overwrites node_modules/@modelcontextprotocol/
# ext-apps with the local v1.7.1 build.
RUN bun install --no-progress \
  && bun run build

# Step 2: build the map-server example's mcp-app.html. The example's own
# build script runs tsc which fails on a real upstream type bug; we skip
# tsc (the runtime works with bun directly) and just run vite build to
# produce the inlined CesiumJS browser bundle.
WORKDIR /work/ext-apps/examples/map-server
RUN INPUT=mcp-app.html npx --yes vite build

# Runtime stage. Keep it slim — only what bun needs to run main.ts plus
# the freshly-built ext-apps + map-server source.
FROM oven/bun:1

WORKDIR /app

# Copy the entire ext-apps tree from the build stage. The map-server's
# main.ts imports from sibling files + the resolved (linked) ext-apps
# package in /work/ext-apps/node_modules.
COPY --from=build /work/ext-apps /app/ext-apps

WORKDIR /app/ext-apps/examples/map-server

# Cloud Run injects PORT=8080. main.ts reads PORT env var (verified
# 2026-05-01).
ENV PORT=8080
EXPOSE 8080

# bun runs TypeScript directly. No --watch in production.
CMD ["bun", "main.ts"]

# Aitana MCP App sandbox proxy — Cloud Run image.
#
# Builds the 3.5 KB browser-side bridge bundle at image-build time, then
# runs the tiny Express server (~120 LOC) that serves /sandbox.html with
# tamper-proof CSP HTTP headers. Used as the separate-origin host for the
# inner iframe rendering MCP App content (per MCP Apps spec, sandbox
# MUST live on a different origin from the host frontend so
# `allow-same-origin` on the inner iframe can't read host cookies).
#
# Local dev: scripts/dev.sh runs the equivalent on port 3457 (see
# infrastructure/mcp-sandbox/README.md). This image is the deployed twin.
#
# Single-stage build is fine — the runtime needs the dev tools (esbuild,
# tsx) only at build time, but the npm install footprint is small enough
# that a multi-stage split adds more complexity than it saves.

FROM node:22-alpine

WORKDIR /app

# Install deps first — better Docker layer caching when only source changes.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

# Copy source + entry points.
COPY src ./src
COPY sandbox.html serve.ts ./

# Static artefact subtree (served by serve.ts at /artefacts/*). The large WASM
# runtime assets (ppa-obligation-analysis) are git-ignored and must be present
# in the build context first — CI runs scripts/fetch-ailang-wasm.sh before this
# build (see the sandbox cloudbuild fetch step). Locally without a fetch the
# artefact ships without its wasm and degrades to "engine unavailable".
COPY artefacts ./artefacts

# Build the browser bundle (esbuild → public/sandbox.js + sandbox.html copy).
RUN npm run build

# Cloud Run sets PORT=8080 by default; serve.ts reads PORT then SANDBOX_PORT.
ENV PORT=8080
EXPOSE 8080

# Node 22's --experimental-strip-types runs TS directly without a build
# step (matches infrastructure/mcp-sandbox/package.json:start). Avoids
# adding a tsc transpile pass to a server that's basically one file.
CMD ["node", "--experimental-strip-types", "serve.ts"]

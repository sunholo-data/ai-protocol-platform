# Google MCP Toolbox for Databases, with our curated tools.yaml baked in.
#
# Pins the upstream image rather than vendoring the Go source — the same
# "pin upstream, no vendoring" pattern as infrastructure/mcp-ext-apps-map/.
#
# Baking tools.yaml (rather than mounting it from Secret Manager / GCS) makes
# git the single source of truth: every tool definition is a security artefact,
# so every change to one is a reviewed PR and a rebuilt image. Nothing in
# tools.yaml is secret — it is SQL, table names and column names.
#
# The image is distroless: no shell. That is why the Cloud Run startup probe is
# a tcpSocket check rather than an exec/HTTP one (Toolbox also ships no
# /healthz endpoint — upstream issue #2644).
#
# Runs as a SIDECAR on platform-frontend. Args are supplied by the deploy step
# in the repo-root cloudbuild.yaml:
#   --config=/app/tools.yaml --address=0.0.0.0 --port=5000 --disable-reload
# NOTE: --address=0.0.0.0, NOT 127.0.0.1. Cloud Run's startup probe can't reach a
# loopback-bound sidecar port (tcp probe DEADLINE_EXCEEDED). The isolation is
# that Cloud Run never routes external ingress to a sidecar port — not the bind
# address. Locally (scripts/dev.sh) we bind 127.0.0.1 since there's no prober.
FROM us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:1.7.0

COPY tools.yaml /app/tools.yaml

#!/bin/bash
set -euo pipefail

# Fetch Firebase config from Secret Manager and generate Docker build args.
# Used by Cloud Build to inject NEXT_PUBLIC_ env vars into the frontend build.

gcloud secrets versions access latest --secret=FIREBASE_ENV --project $_PROJECT_ID > .env.local

# Parse env file and create build args for Docker.
#
# Emit `--build-arg KEY` (no =VALUE). Docker inherits values from the build
# environment for value-less --build-arg, which sidesteps shell word-splitting
# on values that contain spaces (e.g. NEXT_PUBLIC_BRAND_APP_NAME="Acme Energy").
# The build-frontend step in cloudbuild.yaml does `set -a; source .env.local`
# before `docker build` so the env is populated when --build-arg KEY is resolved.
#
# Pre-fix: docker_args contained `--build-arg KEY=VALUE`, and the build step
# did `docker build $$ARGS ...`. When a value had spaces, the shell tokenised
# them as separate words, breaking `docker build` with "requires exactly 1
# argument" (v6.4.0 ONE-DEMO M1 found this when adding spaced BRAND values).
KEYS=$(grep -E "^(NEXT_PUBLIC_|MAILGUN_WEBHOOK_SECRET)" .env.local | cut -d= -f1 | sort -u)
DOCKER_ARGS=""
for k in $KEYS; do
  DOCKER_ARGS="$DOCKER_ARGS --build-arg $k"
done
DOCKER_ARGS="$DOCKER_ARGS --build-arg SKIP_QUALITY_CHECKS=false"

# #18 (template-dx-hardening.md): Docker silently ignores --build-arg values
# for ARGs that the target Dockerfile doesn't declare. Pre-G18 this meant
# a fork could add NEXT_PUBLIC_FOO to its FIREBASE_ENV secret, see this
# script pass it as --build-arg, and STILL get `undefined` at Next.js
# runtime because frontend/Dockerfile didn't have a matching ARG/ENV pair.
# The build succeeded; the feature shipped wrong-but-running. (See
# template-dx-hardening.md #18 for the original AIPLA incident — the
# anonymous-group sign-in button rendered despite being conditionally
# suppressed because NEXT_PUBLIC_AUTH_MODE was silently dropped.)
#
# This check compares the NEXT_PUBLIC_* keys in .env.local against the
# ARG declarations in frontend/Dockerfile. Any var in .env.local that
# isn't declared in the Dockerfile fails the build LOUDLY, here, instead
# of silently shipping a broken UI.
DOCKERFILE_PATH="frontend/Dockerfile"
if [ -f "$DOCKERFILE_PATH" ]; then
  ENV_VARS=$(grep -E "^NEXT_PUBLIC_" .env.local | cut -d= -f1 | sort -u)
  DECLARED_ARGS=$(grep -E "^ARG NEXT_PUBLIC_" "$DOCKERFILE_PATH" | awk '{print $2}' | sort -u)
  MISSING=$(comm -23 <(echo "$ENV_VARS") <(echo "$DECLARED_ARGS"))
  if [ -n "$MISSING" ]; then
    echo "ERROR: get-firebase-config.sh — .env.local has NEXT_PUBLIC_* vars" >&2
    echo "       that aren't declared as ARG in $DOCKERFILE_PATH. Docker" >&2
    echo "       would silently drop them and the resulting build would" >&2
    echo "       have process.env.NEXT_PUBLIC_X === undefined at runtime." >&2
    echo "" >&2
    echo "Missing ARG declarations:" >&2
    echo "$MISSING" | sed 's/^/  - /' >&2
    echo "" >&2
    echo "Fix: add 'ARG <NAME>' + 'ENV <NAME>=\$<NAME>' for each missing" >&2
    echo "var in $DOCKERFILE_PATH (see the warning block at the top of" >&2
    echo "the file). Then re-run the build." >&2
    exit 1
  fi
fi

echo "$DOCKER_ARGS" > /workspace/docker_args

echo "Generated Docker build args from Secret Manager"

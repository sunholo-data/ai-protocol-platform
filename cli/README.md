# aiplatform CLI

Admin CLI for the Aitana Labs v6 platform. Manages buckets, folders, groups,
skills, and does dry-run access checks against the backend REST API.

> **Renamed 2026-04-28:** the binary was previously called `aitana`. The
> current name is `aiplatform` to avoid clashing with unrelated CLIs that
> share the brand prefix on contributors' machines. The Aitana Labs brand,
> backend, and GCP projects are unchanged — only this binary moved.

## Install

```bash
# From the repo root — recommended (handles uninstall + install + sanity check):
make cli-install
make cli-doctor                       # verifies the global binary works
make cli-selftest-mock                # end-to-end smoke against a mock backend

# Or directly:
cd cli
make install                          # uv sync — pulls deps into ./.venv
uv run aiplatform --help              # verify locally

# Or install as a global tool (recommended for daily use):
uv tool install --force ./cli         # creates ~/.local/bin/aiplatform
aiplatform --help                     # verify globally
```

If you previously had `aitana` installed as a global tool, `make cli-reinstall`
removes it first; alternatively, manually:

```bash
uv tool uninstall aitana-cli 2>/dev/null || true   # old name
uv tool uninstall aitana     2>/dev/null || true   # transitional
```

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

## Self-test

After install, verify the binary is wired up correctly:

```bash
make cli-selftest        # mock smoke (always) + live smoke (skips cleanly if no backend)
make cli-selftest-mock   # offline end-to-end against a real socket — no GCP, no creds
make cli-selftest-live   # against a running `make dev` backend (skips when env missing)
```

The mock smoke runs the *real installed binary* against an in-process SSE
mock — catches transport-level regressions (SSE buffering, `httpx.stream`
lifecycle, stdout pipe semantics) that the respx-mocked unit tests cannot
see. The live smoke is a one-command "is chat responsive" diagnostic; it
needs `AIPLATFORM_ID_TOKEN` (Firebase ID token) and a seed skill id (env
var `AIPLATFORM_SELFTEST_SKILL_ID` or first arg).

## Authentication

The CLI needs a Firebase/Google ID token for every call. Resolution order:

1. `$AIPLATFORM_ID_TOKEN` environment variable (preferred for CI).
2. `gcloud auth print-identity-token` (local dev).

If neither is available, the command fails with a clear error message — not
an opaque 401.

```bash
# Option A: explicit token
export AIPLATFORM_ID_TOKEN="$(gcloud auth print-identity-token)"

# Option B: let the CLI shell out to gcloud on each call
gcloud auth login
```

## Environments

Pick a target backend with `--env`:

| `--env`  | Default URL                                                 | Override env var            |
|----------|-------------------------------------------------------------|-----------------------------|
| `local`  | `http://localhost:1956`                                     | `AIPLATFORM_API_URL_LOCAL`  |
| `dev`    | `https://your-service-url.example`         | `AIPLATFORM_API_URL_DEV`    |
| `test`   | placeholder (not yet cut)                                   | `AIPLATFORM_API_URL_TEST`   |
| `prod`   | placeholder (not yet cut)                                   | `AIPLATFORM_API_URL_PROD`   |

`AIPLATFORM_API_URL` (unsuffixed) wins over everything else if set.

## Command groups

```text
aiplatform --help
aiplatform bucket  { list | show | create | grant | revoke }
aiplatform folder  { list | create }
aiplatform docs    { folder { list | new } | upload }
aiplatform user    { find | show | activity | refresh-claims }   # start from a PERSON
aiplatform groups  { grant | revoke | show }
aiplatform access  { check }
aiplatform skill   { probe | chat | … }
aiplatform logs    [ -g … ] | logs client
```

### Bucket

```bash
# list visible buckets (optional filters)
aiplatform --env dev bucket list --tag ops --access-type specific

# show one bucket
aiplatform --env dev bucket show bkt-abc123

# create a private bucket
aiplatform --env dev bucket create \
    --display-name "Ops Reports" \
    --gcs-bucket aitana-ops-reports-dev \
    --access-type private

# create a specific-emails bucket
aiplatform --env dev bucket create \
    --display-name "Finance" \
    --gcs-bucket aitana-finance-dev \
    --access-type specific \
    --email alice@yourcompany.com --email bob@yourcompany.com

# grant / revoke a user on a 'specific' bucket
aiplatform --env dev bucket grant bkt-abc123 --email carol@yourcompany.com
aiplatform --env dev bucket revoke bkt-abc123 --email alice@yourcompany.com
```

### Folder

```bash
# list folders in a bucket
aiplatform --env dev folder list --bucket bkt-abc123

# create a folder that inherits the parent bucket's ACL
aiplatform --env dev folder create \
    --bucket bkt-abc123 \
    --path "reports/2026" \
    --display-name "2026 Reports"

# create a folder with its own 'specific' ACL
aiplatform --env dev folder create \
    --bucket bkt-abc123 \
    --path "secret" \
    --display-name "Secret" \
    --access-type specific \
    --email alice@yourcompany.com
```

### Skill — TTFT probe

```bash
# fire one chat turn and print the per-stage TTFT breakdown
aiplatform --env local skill probe my-skill --message "Hello"

# raw JSON for scripting
aiplatform --env local skill probe my-skill --json
```

See [docs/design/v6.1.0/implemented/ttft-instrumentation.md](../docs/design/v6.1.0/implemented/ttft-instrumentation.md).

### User — who is this person, which env, why can't they see X

```bash
aiplatform user find arias                                 # name/email needle across dev+test+prod
aiplatform --env prod user show msc@example.com            # record, tags, effective roles, claim propagation
aiplatform --env prod user activity msc@example.com --since 2h
aiplatform --env prod user refresh-claims msc@example.com  # force a fresh grant into their token (admin token)
```

Reads need only gcloud application-default credentials with Firebase Auth
viewer + Firestore read on the target project — no backend token. `find`
prints matches only; it is deliberately not a user-list export.

### Groups

```bash
aiplatform --env prod groups grant  --email a@b.com --tag tenant-admin:b.com
aiplatform --env prod groups revoke --email a@b.com --tag tenant-admin:b.com
aiplatform --env prod groups show   --email a@b.com
```

Aitana-admin gated server-side; a grant is `on_next_refresh` (≤1h) unless
you follow it with `user refresh-claims`.

### Access check (backend TODO)

```bash
# dry-run access check for the current user
aiplatform --env dev access check --bucket bkt-abc123

# check as another user (admin-only server-side)
aiplatform --env dev access check --folder fld-1 --as-email alice@yourcompany.com
```

> The `/api/access/check` endpoint is not yet implemented server-side.

## Development

```bash
make install     # uv sync
make test        # pytest (uses respx to mock backend)
make lint        # ruff check + format --check
make format      # ruff check --fix + format
```

Tests use Click's `CliRunner` plus [respx](https://lundberg.github.io/respx/)
to assert exact URL + method + payload for every subcommand.

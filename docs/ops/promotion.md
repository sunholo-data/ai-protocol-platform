# Runbook — releasing and promoting (dev · test · prod)

How code reaches each environment. For one-time environment *creation* see
[env-cut-runbook.md](env-cut-runbook.md); this is the routine path.

Design rationale: [build-once-artifact-promotion.md](../design/v6.20.0/build-once-artifact-promotion.md).
Adopted from the AIPLA fork's upstream feedback #46/#47.

## The three routes

| Env | Fires on | Command | What ships |
|---|---|---|---|
| **dev** | push to `dev` | `git push origin dev` | Rebuild of all three containers from the branch tip |
| **test** | push to `test` **or** a `v*` git tag | `aiplatform deploy release --version vX.Y.Z --yes` | Rebuild from the tag, CI-gated. **This is the build that creates the immutable `:vX.Y.Z` images a promotion later copies.** |
| **prod** | push to `prod` | (branch merge, today) | Rebuild from the branch tip |

**Prod becomes promote-only once v6 is ratified on test (`test.yourcompany.com`).**
Until then its branch trigger is unchanged and no prod promote trigger exists —
deliberately. Nothing in the v6.20.0 work touches prod.

**Why the `test` branch still exists** even though releases are tag-driven: two
triggers on it (`mcp-sandbox`, `mcp-ext-apps-map`) are NOT part of the release
pipeline, so the branch is still their only deploy path. Retiring both branches
is planned for after the prod release (~2026-08-31) — the blockers, the audit
behind them, and the order of work are in
[env-promotion-audit.md § Roadmap](env-promotion-audit.md#roadmap--retire-the-test--prod-branches).

## Release: cut a version tag

```bash
aiplatform deploy release --version v6.21.0            # dry run — prints, changes nothing
aiplatform deploy release --version v6.21.0 --yes      # tag + push
```

Fires `trigger-aitana-test-release`: CI gate → build ui/backend/toolbox → tag
each `:vX.Y.Z` **and** `:${SHORT_SHA}` → deploy test **by digest** → smoke → seed.

```bash
gcloud builds list --project=your-deploy-project-id --region=europe-west1 \
  --limit=3 --filter='substitutions.TAG_NAME=v6.21.0' \
  --format="table(status,substitutions.TRIGGER_NAME,id)"
```

## Promote: move the tested artifact forward

```bash
make promote FROM=dev TO=test VERSION=v6.21.0          # dry run — prints the plan
make promote FROM=dev TO=test VERSION=v6.21.0 GO=1     # execute
```

or equivalently `aiplatform deploy promote --from dev --to test --version v6.21.0 [--yes]`.
Both call `scripts/promote-env.sh`, which is the single implementation.

Valid edges are **`dev → test`** and **`test → prod`** only. There is no
`dev → prod` shortcut — the entire point is that prod receives what test verified.

`cloudbuild.promote.yaml` then:

1. **`copy-backend` / `copy-toolbox`** — `crane copy` by digest, then re-reads the
   destination digest and **fails the build if it changed in transit**.
2. **`build-frontend`** — the UI *must* be rebuilt: `NEXT_PUBLIC_*` is inlined at
   compile time, so a copied UI carries the source env's Firebase project and API
   URLs. Rebuilt from the same tag with the target's config.
3. **`deploy`** — `gcloud run services update`, images only. Env vars, secrets and
   volumes set at env-cut are untouched.
4. **`smoke`** — any non-200 fails the promotion.

## Verify a promotion actually promoted

Digest equality is the whole point, so check it rather than trusting the green tick:

```bash
aiplatform deploy status --env dev
aiplatform deploy status --env test
```

The **backend** and **toolbox** digests must be identical across the two. The
**ui** digest will differ — that one was rebuilt, by design. If the backend
digests differ, something rebuilt it and the promotion is not a promotion.

Real output from the first promotion (2026-07-31, v6.20.1):

```
backend  sha256:f7d73167…   identical    <- copied
toolbox  sha256:a6a70c36…   identical    <- copied
ui       1648974b… -> 080c4884…          <- rebuilt with test's config
```

## Rollback

Cloud Run keeps revisions, so the fastest undo is a traffic shift, not a rebuild:

```bash
gcloud run revisions list --project=your-project-id-<env> \
  --region=europe-west1 --service=platform-frontend --limit=5

gcloud run services update-traffic platform-frontend \
  --project=your-project-id-<env> --region=europe-west1 \
  --to-revisions=<previous-revision>=100
```

Re-promoting an older tag also works and leaves a cleaner audit trail. Because
every build is tagged `:${SHORT_SHA}` and deployed by digest, an exact prior
build is always addressable — that is what M1 bought.

## Traps that have actually cost time

| Symptom | Cause |
|---|---|
| Tag pushed, **no build at all**, no error | The trigger has `includedFiles`. Path filters match a push's file *diff*, and a tag push has none. Drop them on tag triggers. |
| `invalid reference format` on `docker build -t …/backend:` | `BRANCH_NAME` is **empty on tag builds** (and `TAG_NAME` is empty on branch builds). Only `SHORT_SHA` is populated on both — tag with it, and guard the others with `-n`. |
| Deployed env has the wrong security settings | A `case` keyed on `BRANCH_NAME` fell through to `*)` on a tag build. **Key env-conditionals on `_PROJECT_ID`**, never on a ref name. This silently disabled two controls on a tag deploy to test. |
| `trigger not found` against a plausible project | Triggers live in the **deploy** project (`your-deploy-project-id`), not the target env's project. |
| Promotion fails at step 0, `_VERSION is required` | `--tag` sets the checkout revision; it does **not** set the `_VERSION` substitution. Both are needed and must agree. |
| Promote builds code you don't recognise | It uses the repo **at the tag**, not your working tree. That is deliberate — tag a new version to ship local changes. |
| `tag vX.Y.Z not found on origin` | Push the tag: `git push origin vX.Y.Z`. |

## Why a trigger and never `gcloud builds submit`

`builds submit .` uploads the **operator's local working tree** as the build
source, so a release would be built from whatever happens to be checked out on
someone's laptop. It bit the AIPLA fork exactly that way — their prod `ui:v0.1.3`
was built from an untagged commit. Running promotion as a **trigger with
`--tag`** makes the tag the single source of truth and removes the laptop from
the release path.

It also sidesteps two IAM traps that are invisible from the trigger path:
`builds submit` without `--service-account` runs as the Compute Engine default
SA (a second identity for one pipeline), and it needs `storage.objectViewer` on
the auto-created `<project>_cloudbuild` bucket. See
[gotchas.md](gotchas.md). A test asserts the executing paths never reintroduce it.

## Related

- [deployed-urls.md](deployed-urls.md) — what is live per env (**source of truth**)
- [env-cut-runbook.md](env-cut-runbook.md) — one-time environment creation
- [env-promotion-audit.md](env-promotion-audit.md) — drift classes + the IAM cascade
- [build-once-artifact-promotion.md](../design/v6.20.0/build-once-artifact-promotion.md) — why promotion copies rather than rebuilds

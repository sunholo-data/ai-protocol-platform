# Private tier: `**/private/**` is never published

**Status:** ✅ Shipped (2026-10-01)
**Decision:** Mark, 2026-10-01 — "a new excluded path so we have options, then
platform-source decides what is published or not".

## Why

platform-source publishes the public template by excluding `deploy/**`
(Terraform), and everything else ships. Some platform code is wanted in every
private fork — Aitana, Sunholo, the commercial forks — but not in the public
template: e.g. the knowledge-store ingest that ports multivac's
docparse → Vertex AI Search pipeline. Before this there were two bad choices:
publish it, or keep it in one fork and copy it between forks by hand.

## What

* **Withheld:** `deploy/**`, and any path under a directory named `private` at
  any depth (`**/private/**`). The list lives in `private/publish-exclude`;
  `private/scripts/publish_public.py` is the only publish path (`check`,
  `list`, `export DIR`). Both are themselves under `private/`, so the tooling
  never ships.
* **Where private code goes:**
  * backend extension packages: `backend/private/<name>/` with
    `routes.py` (`router`) and/or `tools.py` (`TOOL_REGISTRY`, `TOOL_CATALOG`);
  * their tests: `backend/private/<name>/tests/` or `backend/tests/private/`;
  * design docs: `docs/design/private/`;
  * ops scripts: `scripts/private/` or `private/scripts/`.
* **The seam:** public code never imports `private.*`. `backend/extensions.py`
  discovers private packages and mounts their routers (`fast_api_app.py`) and
  merges their tools (`adk/tools.py`). With the tier absent — the public
  template — every call is a no-op. A private module that fails to import is
  logged and skipped, never fatal.
* **The gate:** `make publish-check` / CI (`LOCAL_MODE safety` job) builds the
  export from `git ls-files` and fails on any direct private import in a
  published file, or published Python that doesn't compile without the tier.
  In the public repo the step finds no tooling and is skipped.

## Downstream

Private forks receive `private/` paths on `make upstream-merge` like any other
template content. `scripts/check-upstream-routing.sh` already treats them as
template content (they belong upstream). The invariant "public = platform-source
minus deleted folders" is unchanged — the private tier is more deleted folders.

## Publishing

Still a deliberate manual step: `python3 private/scripts/publish_public.py
export <clone of ai-protocol-platform>`, review the diff, commit, push. Run the
secret gate first. Nothing from 2026-09-30 onwards (tailnet auth, machine
principals, shared threads, the private tier itself) has been published yet.

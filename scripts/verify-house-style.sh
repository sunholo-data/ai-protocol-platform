#!/usr/bin/env bash
# verify-house-style.sh — prove that a REVISION keeps the tenant house style.
#
# The 2026-09-09 regression: generate an on-brand document, edit one passage,
# and it came back in Word's defaults with no way back. It shipped because the
# unit suite passed throughout — the defects lived BETWEEN the paths, not inside
# them, which is exactly the gap CLAUDE.md #10 exists for.
#
# So this asserts on the stored provenance of a real deployed run. The
# load-bearing field is `templatePartsCarried`: a template that is "applied" and
# carries nothing is what a lost house style looks like, and it is
# indistinguishable from success in the HTTP response.
#
#   scripts/verify-house-style.sh dev
#   scripts/verify-house-style.sh test
#
# Requires a brand template seeded for the identity's domain:
#   gs://<documents bucket>/brand/<domain>/templates/house-style.docx
set -euo pipefail

ENV="${1:-dev}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The per-env project map belongs to the DEPLOYMENT, not to this script — see
# scripts/_deploy_env.sh. Hardcoding it here would ship one deployment's project
# ids to every fork, which is the duplication TEMPLATE-INVERT M4b removed from
# six other scripts.
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/_deploy_env.sh"
PROJECT="$(project_for_env "$ENV")"
if [ -z "$PROJECT" ]; then
  echo "unknown env '$ENV' (or deploy.env not materialised — run 'make materialize-config')" >&2
  exit 2
fi

# A domain-scoped house style is invisible to an out-of-domain caller, so this
# has to be an identity on the deployment's OWN domain. There is no sensible
# default for a fork, so it is required rather than guessed.
IDENTITY="${AIPLATFORM_TEST_EMAIL:-}"
if [ -z "$IDENTITY" ]; then
  echo "set AIPLATFORM_TEST_EMAIL to an agent identity on your deployment's domain" >&2
  echo "  e.g. AIPLATFORM_TEST_EMAIL=agent-verify@example.com $0 $ENV" >&2
  exit 2
fi

echo "→ env=$ENV project=$PROJECT identity=$IDENTITY"

# The IN-DOMAIN identity, not the `.test` default: a domain-scoped house style is
# invisible to an out-of-domain caller, so the run would fail for the wrong
# reason and prove nothing.
eval "$(AIPLATFORM_TEST_EMAIL="$IDENTITY" GCP_PROJECT="$PROJECT" GOOGLE_CLOUD_PROJECT="$PROJECT" \
  "${REPO_ROOT}/.claude/skills/aiplatform-cli/scripts/mint-token.sh" 2>/dev/null)"
export AIPLATFORM_ID_TOKEN

reply="$(aiplatform --env "$ENV" skill chat document-writer \
  -m "Write one short paragraph titled 'House style guard' about PPA prices, and generate it as a Word document." 2>&1)"
DOC_ID="$(printf '%s' "$reply" | grep -o 'created_document:[0-9a-f-]*' | head -1 | cut -d: -f2)"

if [ -z "$DOC_ID" ]; then
  echo "FAIL: no document was created" >&2
  printf '%s\n' "$reply" >&2
  exit 1
fi
echo "→ generated $DOC_ID"

cd "${REPO_ROOT}/backend"
DOC_ID="$DOC_ID" PROJECT="$PROJECT" ENV_NAME="$ENV" uv run python - <<'PY'
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.getcwd()), "cli"))
from google.cloud import firestore

from aiplatform.http import resolve_base_url  # noqa: E402
import urllib.request  # noqa: E402
import json  # noqa: E402

doc_id = os.environ["DOC_ID"]
db = firestore.Client(project=os.environ["PROJECT"])
ref = db.collection("parsed_documents").document(doc_id)
failures: list[str] = []


def provenance() -> dict:
    return (ref.get().to_dict() or {}).get("generatedFrom") or {}


def check(step: str) -> None:
    prov = provenance()
    name = prov.get("templateFilename") or ""
    carried = int(prov.get("templatePartsCarried") or 0)
    tid = str(prov.get("templateDocId") or "")
    print(f"   {step}: template={name!r} carried={carried}")

    if not name:
        failures.append(f"{step}: no template applied — the house style did not resolve")
    if tid == doc_id:
        failures.append(f"{step}: the document was used as its OWN template — that destroys the styling")
    if prov.get("referenceDocApplied") and carried == 0:
        failures.append(f"{step}: applied but carried 0 parts — the document is unstyled")


print("→ generation")
check("generate")

# The regression itself. A revision naming NO template must inherit the house
# style; before the fix it applied nothing and the branding was gone for good.
base = resolve_base_url(os.environ["ENV_NAME"]).rstrip("/")
req = urllib.request.Request(
    f"{base}/api/documents/{doc_id}/revise",
    data=json.dumps({"contentMarkdown": "# House style guard\n\nRevised body text."}).encode(),
    headers={
        "Authorization": f"Bearer {os.environ['AIPLATFORM_ID_TOKEN']}",
        "Content-Type": "application/json",
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=180) as resp:
    body = json.loads(resp.read())
print(f"→ revision (templateUsed={body.get('templateUsed')!r})")
check("revise")

if failures:
    print("\nFAIL")
    for f in failures:
        print(f"  ✗ {f}")
    raise SystemExit(1)
print("\nPASS — the house style survived the revision")
PY

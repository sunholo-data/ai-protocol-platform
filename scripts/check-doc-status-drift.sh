#!/usr/bin/env bash
#
# Status-header drift check (2026-09-02 sweep).
#
# A design doc whose `**Status:**` says "Planned" for something that SHIPPED is
# an invitation to rebuild it — and the header is what a newcomer or an agent
# reads first and trusts most. The 2026-09-02 sweep found 14 such headers out of
# 94 numbered docs (~15%), three of them in a single triage.
#
# Heuristic, not proof: the repo stamps the item number in commit subjects
# (`feat(builder): … (1.1.71)`), so a doc claiming not-shipped whose number
# appears in a feat/fix subject is worth a human look. ADVISORY — prints and
# exits 0, because the signal has real false positives (numbering collisions:
# 1.1.14 and 1.1.60 are both known to be used twice).
#
# Deployment-agnostic: DESIGN_ROOT is the tree to scan and ITEM_RE is the
# numbering scheme its SEQUENCE.md tables use. The defaults are this
# deployment's (docs/design/aipla, 1.1.NN); a fork with v6.X.Y numbering sets
# ITEM_RE='[0-9]+\.[0-9]+\.[0-9]+' and DESIGN_ROOT=docs/design.
set -uo pipefail
cd "$(dirname "$0")/.."

DESIGN_ROOT="${DESIGN_ROOT:-docs/design/aipla}"
ITEM_RE="${ITEM_RE:-1\.1\.[0-9]{1,3}}"
export DESIGN_ROOT ITEM_RE

python3 - <<'PY'
import os, pathlib, re, subprocess

DESIGN_ROOT = os.environ.get("DESIGN_ROOT", "docs/design/aipla")
ITEM_RE = os.environ.get("ITEM_RE", r"1\.1\.[0-9]{1,3}")

mapping = {}
for seq in pathlib.Path(DESIGN_ROOT).rglob("SEQUENCE.md"):
    for line in seq.read_text(errors="ignore").splitlines():
        m = re.match(rf"\|\s*({ITEM_RE})\s*\|\s*\[[^\]]+\]\(([^)#]+\.md)\)", line.strip())
        if m:
            p = (seq.parent / m.group(2)).resolve()
            if p.exists():
                mapping.setdefault(m.group(1), p)

log = subprocess.run(["git", "log", "--pretty=format:%s"],
                     capture_output=True, text=True).stdout.splitlines()
SHIPPED = ("shipped", "implemented", "✅", "done", "partly")
flagged = []
for num, p in mapping.items():
    if "implemented/" in str(p):
        continue
    t = p.read_text(errors="ignore")
    m = re.search(r"^\*\*Status:?\*\*:?\s*(.+)$", t, re.M)
    if not m:
        continue
    status = m.group(1).lower()
    if any(k in status for k in SHIPPED):
        continue
    hits = [l for l in log if re.search(rf"\b{re.escape(num)}\b", l)
            and l.startswith(("feat(", "fix("))]
    if hits:
        flagged.append((num, p, hits[0]))

if not flagged:
    print("OK: no design doc claims 'not shipped' while carrying feat/fix commits.")
else:
    print(f"ADVISORY: {len(flagged)} doc(s) claim not-shipped but have feat/fix commits stamped with their number.")
    print("Verify against CODE before believing either — and check for a numbering collision.\n")
    for num, p, ex in sorted(flagged):
        print(f"  [{num}] {p.name}")
        print(f"        e.g. {ex[:100]}")
PY

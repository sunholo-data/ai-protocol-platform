#!/bin/bash
# generate_report.sh <sprint_id> <score> <result> <round>
SPRINT_ID=$1; SCORE=$2; RESULT=$3; ROUND=${4:-1}
mkdir -p "$(dirname "$0")/../../../state/evaluations"
cat > "$(dirname "$0")/../../../state/evaluations/eval_${SPRINT_ID}_round_${ROUND}.json" << EOF
{"sprint_id":"${SPRINT_ID}","round":${ROUND},"score":${SCORE},"result":"${RESULT}","timestamp":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
echo "Report written: eval_${SPRINT_ID}_round_${ROUND}.json"

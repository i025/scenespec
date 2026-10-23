#!/usr/bin/env bash
# CPU-only trusted baseline reproduction. Does not run an improvement agent.
set -euo pipefail
cd "$(dirname "$0")/.."
SCENE_PYTHON="${SCENE_RSI_PYTHON:-python3}"
SCENE_OUT="${1:-../phase1_run}"
SCENE_SEED="${2:-20261023}"
SCENE_PER_FAMILY="${3:-4}"
"$SCENE_PYTHON" -c 'import sys; sys.exit("Python 3.10+ required; set SCENE_RSI_PYTHON") if sys.version_info < (3, 10) else None'
if [[ -e "$SCENE_OUT" ]]; then
  echo "Output already exists; choose a new directory: $SCENE_OUT" >&2
  exit 2
fi
mkdir -p "$SCENE_OUT"
"$SCENE_PYTHON" -m unittest discover -s tests -v > "$SCENE_OUT/tests.log" 2>&1
"$SCENE_PYTHON" -m scene_rsi check-integrity > "$SCENE_OUT/integrity.json"
"$SCENE_PYTHON" -m scene_rsi make-dataset --out "$SCENE_OUT/dataset" --seed "$SCENE_SEED" --per-family "$SCENE_PER_FAMILY"
"$SCENE_PYTHON" -m scene_rsi evaluate --dataset "$SCENE_OUT/dataset" --split dev --out "$SCENE_OUT/dev.json"
"$SCENE_PYTHON" -m scene_rsi evaluate --dataset "$SCENE_OUT/dataset" --split val --out "$SCENE_OUT/val.json"
"$SCENE_PYTHON" -m scene_rsi benchmark --spec-dir examples --out "$SCENE_OUT/smoke.json"
"$SCENE_PYTHON" -m scene_rsi meta-prepare --repo . --dataset "$SCENE_OUT/dataset" --dev-report "$SCENE_OUT/dev.json" --out "$SCENE_OUT/meta_request" > "$SCENE_OUT/request_summary.json"
"$SCENE_PYTHON" -m scene_rsi meta-plan --bundle "$SCENE_OUT/meta_request" > "$SCENE_OUT/proposal_plan.json"
echo "Completed trusted baseline reproduction: $SCENE_OUT"

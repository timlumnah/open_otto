#!/bin/bash
# Score several models with and without injection hardening, then build the comparison table.
#
#   evals/run_matrix.sh ollama:qwen2.5:7b openrouter:qwen/qwen-2.5-72b-instruct anthropic:claude-haiku-5-5
#   RUNS=3 evals/run_matrix.sh openrouter:meta-llama/llama-3.1-8b-instruct
#
# Results go to evals/results/<model>_<mode>.json and the table to evals/results/TABLE.md.
# A model that fails to start is reported and skipped; the rest still run.
cd "$(dirname "$0")/.." || exit 1
RUNS="${RUNS:-5}"
export OPEN_OTTO_TIMEOUT="${OPEN_OTTO_TIMEOUT:-300}"
PY=".venv/bin/python"; [ -x "$PY" ] || PY=python3
mkdir -p evals/results
[ $# -eq 0 ] && { echo "usage: $0 provider:model [provider:model ...]"; exit 2; }
for model in "$@"; do
  slug=$(echo "$model" | tr -c 'A-Za-z0-9\n' '-')
  for mode in baseline hardened; do
    flag=""; [ "$mode" = "baseline" ] && flag="--no-hardening"
    echo "=== $model $mode $(date +%T)"
    $PY -m evals --model "$model" --runs "$RUNS" $flag --json "evals/results/${slug}_${mode}.json" 2>&1 | tail -6
  done
done
$PY -m evals.compare evals/results/*.json --out evals/results/TABLE.md

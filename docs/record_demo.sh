#!/bin/bash
# Plays the demo session with typed-out commands. Record it with:
#   asciinema rec -c "bash docs/record_demo.sh" docs/demo.cast --cols 100 --rows 30
# Needs OPEN_OTTO_MODEL (and its API key) in the environment. Runs in a throwaway directory.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"; cd "$WORK" || exit 1
export PYTHONPATH="$REPO"
type_cmd() { printf '\033[1;32m$\033[0m '; for ((i=0;i<${#1};i++)); do printf '%s' "${1:i:1}"; sleep 0.04; done; sleep 0.4; echo; }
run() { type_cmd "python -m cli $*"; python3 -m cli "$@"; echo; sleep "${PAUSE:-1.5}"; }
run init --demo
run list
run followups
PAUSE=2 run draft-followups
run drafts
PAUSE=4 run show 2
run approve 2
run sent 2
rm -rf "$WORK"

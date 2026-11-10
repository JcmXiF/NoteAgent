#!/usr/bin/env bash
# Run the deterministic behaviour evals and keep the process output.
#
# The runner already keeps a failed case's temporary project, event log and
# checks. This adds stdout/stderr beside them, so CI uploads one folder that
# explains both what failed and how the run got there.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p build/evals
python -m evals "$@" 2>&1 | tee build/evals/output.txt

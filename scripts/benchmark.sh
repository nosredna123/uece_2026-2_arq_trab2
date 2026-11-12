#!/usr/bin/env bash
# Runs the load scenarios for several participant counts and records the results
# (CSV, Markdown report and charts) under docs/validation/benchmarks/<timestamp>/.
# Usage: bash scripts/benchmark.sh [PARTICIPANTS ...] [--repeat N] [--duration S]
#        [--no-reset] [--stop-at saturation|verificar|never] [--keep-going]
#        [--baseline S] [--catch-up-timeout S] [--output DIR]
# By default every run starts from the initial data (scripts/reset-demo.sh), which
# deletes all local data. See bash scripts/benchmark.sh --help.

set -Eeuo pipefail

ROOT="$(git rev-parse --show-toplevel)"
PYTHON_BIN="${PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
  else
    PYTHON_BIN=python3
  fi
fi
command -v docker >/dev/null 2>&1 || { printf 'ERROR: Docker CLI is required.\n' >&2; exit 1; }
exec "$PYTHON_BIN" "$ROOT/scripts/benchmark.py" "$@"

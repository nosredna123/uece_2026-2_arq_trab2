#!/usr/bin/env bash

set -Eeuo pipefail

ROOT="$(git rev-parse --show-toplevel)"
ENV_FILE="${EVENTHUB_ENV_FILE:-$ROOT/infra/.env}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ -f "$ENV_FILE" ]] || fail "Missing infra/.env. Copy infra/.env.example and configure local synthetic values; this script never creates or overwrites it."
command -v docker >/dev/null 2>&1 || fail "Docker CLI is required."

COMPOSE=(
  docker compose
  --env-file "$ENV_FILE"
  -f "$ROOT/infra/docker-compose.yml"
  --profile test
)
timeout_seconds="${DEMO_WEB_TEST_TIMEOUT_SECONDS:-900}"
[[ "$timeout_seconds" =~ ^[0-9]{1,5}$ ]] || fail "DEMO_WEB_TEST_TIMEOUT_SECONDS must be an integer."
timeout_number=$((10#$timeout_seconds))
((timeout_number >= 60 && timeout_number <= 3600)) \
  || fail "DEMO_WEB_TEST_TIMEOUT_SECONDS must be between 60 and 3600."

printf 'Building the isolated repository Python test image.\n'
"${COMPOSE[@]}" build python-suite-test

printf 'Running the fail-fast Python suite with a %s-second bound.\n' "$timeout_number"
"${COMPOSE[@]}" run --rm python-suite-test \
  timeout --signal=INT --kill-after=30s "${timeout_number}s" \
  python -m pytest -x --import-mode=importlib

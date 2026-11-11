#!/usr/bin/env bash

set -Eeuo pipefail

ROOT="$(git rev-parse --show-toplevel)"
ARTIFACTS_DIR="${WEBAPP_TEST_ARTIFACTS_HOST_DIR:-$ROOT/docs/validation/artifacts/webapp}"
ARTIFACTS_DIR="$(mkdir -p "$ARTIFACTS_DIR" && cd "$ARTIFACTS_DIR" && pwd)"
TEST_ENV="$(mktemp "${TMPDIR:-/tmp}/eventhub-webapp-browser.XXXXXX.env")"
PROJECT="eventhub-webapp-browser-$$"
outage_probe_active=0

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "Docker CLI is required."

cat >"$TEST_ENV" <<'EOF'
DEMO_WEB_ENABLED=true
DEMO_LAB_ENABLED=true
IDENTITY_LAB_ENABLED=true
DEMO_WEB_PAID_START_DELAY_SECONDS=40
DEMO_WEB_ACTIVITY_DURATION_SECONDS=30
DEMO_WEB_FREE_START_DELAY_SECONDS=90
DEMO_LAB_FIXTURES_PATH=/run/eventhub/scenario-participants.json
DEMO_LAB_MAX_PARTICIPANTS=50
DEMO_LAB_MAX_CONCURRENCY=10
DEMO_LAB_RUN_TIMEOUT_SECONDS=60
DEMO_LAB_SETTLE_TIMEOUT_SECONDS=30
DEMO_LAB_RUN_TTL_SECONDS=1800
DEMO_LAB_MAX_RETAINED_RUNS=5
EOF

COMPOSE=(
  docker compose
  --project-name "$PROJECT"
  --env-file "$ROOT/infra/.env.example"
  --env-file "$TEST_ENV"
  -f "$ROOT/infra/docker-compose.yml"
  -f "$ROOT/infra/docker-compose.demo-web.yml"
  -f "$ROOT/infra/docker-compose.demo-lab.yml"
  -f "$ROOT/infra/docker-compose.webapp-browser-tests.yml"
  --profile demo-web
  --profile webapp-browser
)

cleanup() {
  status=$?
  trap - EXIT
  if ((outage_probe_active)); then
    if ! "${COMPOSE[@]}" up -d --wait reporting identity-mock; then
      printf 'ERROR: Failed to restore services after browser outage probes.\n' >&2
      ((status == 0)) && status=1
    fi
  fi
  if ! "${COMPOSE[@]}" down --volumes --remove-orphans; then
    printf 'ERROR: Failed to remove isolated browser-test project %s.\n' "$PROJECT" >&2
    ((status == 0)) && status=1
  fi
  rm -f "$TEST_ENV"
  exit "$status"
}
trap cleanup EXIT

export WEBAPP_TEST_ARTIFACTS_HOST_DIR="$ARTIFACTS_DIR"

printf 'Building the pinned Playwright browser-test image.\n'
"${COMPOSE[@]}" build webapp-browser-test

printf 'Starting the isolated backend and presentation services.\n'
"${COMPOSE[@]}" up -d --wait demo-web

printf 'Running real-browser journeys.\n'
"${COMPOSE[@]}" run --no-deps --rm webapp-browser-test

printf 'Checking the Reporting outage response in an isolated browser context.\n'
outage_probe_active=1
"${COMPOSE[@]}" stop reporting
"${COMPOSE[@]}" run --no-deps --rm \
  -e WEBAPP_TEST_REPORTING_FAILURE=1 \
  webapp-browser-test \
  python -m pytest -x --import-mode=importlib \
  tests/webapp/test_failures_and_isolation.py::test_reporting_failure_is_visible_and_not_an_empty_report
"${COMPOSE[@]}" up -d --wait reporting

printf 'Checking the Identity outage response in an isolated browser context.\n'
"${COMPOSE[@]}" stop identity-mock
"${COMPOSE[@]}" run --no-deps --rm \
  -e WEBAPP_TEST_IDENTITY_FAILURE=1 \
  webapp-browser-test \
  python -m pytest -x --import-mode=importlib \
  tests/webapp/test_failures_and_isolation.py::test_identity_failure_is_visible_and_does_not_create_an_event
"${COMPOSE[@]}" up -d --wait identity-mock
outage_probe_active=0

printf 'Browser acceptance completed. Screenshots: %s\n' "$ARTIFACTS_DIR"

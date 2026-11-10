#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
COMPOSE=(docker compose --env-file "$ROOT/infra/.env" -f "$ROOT/infra/docker-compose.yml")

if [[ ! -f "$ROOT/infra/.env" ]]; then
  echo "Missing infra/.env; copy infra/.env.example and configure synthetic local values." >&2
  exit 1
fi

service_url() {
  local mapping
  mapping=$("${COMPOSE[@]}" port "$1" 8080)
  printf 'http://%s:%s' "${mapping%:*}" "${mapping##*:}"
}

run_scenario() {
  local selection="$1"
  cd "$ROOT"
  EVENTHUB_RUN_FAILURE_INTEGRATION=1 \
    "${PYTHON:-python3}" -m pytest -x --import-mode=importlib \
    tests/integration/failure-scenarios/test_failure_scenarios.py -k "$selection"
}

restore_defaults() {
  if ! BILLING_MOCK_SCENARIO=APPROVED \
    RESERVATION_EXPIRY_MINUTES=30 \
    NOTIFICATION_MOCK_AVAILABLE=true \
    "${COMPOSE[@]}" up --detach --force-recreate registration billing-mock notification-mock; then
    echo "Could not restore the default registration and mock scenarios." >&2
    return 1
  fi
}
trap restore_defaults EXIT

BILLING_MOCK_SCENARIO=REFUSED \
  "${COMPOSE[@]}" up --build --detach --wait --wait-timeout 240 \
  postgres rabbitmq identity-mock event-management registration attendance reporting \
  billing-mock notification-mock

export EVENTHUB_EVENT_MANAGEMENT_URL="$(service_url event-management)"
export EVENTHUB_REGISTRATION_URL="$(service_url registration)"
export EVENTHUB_REPORTING_URL="$(service_url reporting)"

run_scenario refusal_releases_capacity

BILLING_MOCK_SCENARIO=TIMEOUT \
  RESERVATION_EXPIRY_MINUTES=1 \
  "${COMPOSE[@]}" up --detach --force-recreate --wait --wait-timeout 120 \
  registration billing-mock
run_scenario payment_timeout_expires_reservation

BILLING_MOCK_SCENARIO=APPROVED \
  RESERVATION_EXPIRY_MINUTES=30 \
  NOTIFICATION_MOCK_AVAILABLE=false \
  "${COMPOSE[@]}" up --detach --force-recreate --wait --wait-timeout 120 \
  registration billing-mock notification-mock
run_scenario notification_unavailable
run_scenario reporting_outage

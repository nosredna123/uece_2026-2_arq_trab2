#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
COMPOSE=(docker compose --env-file "$ROOT/infra/.env" -f "$ROOT/infra/docker-compose.yml")

if [[ ! -f "$ROOT/infra/.env" ]]; then
  echo "Missing infra/.env; copy infra/.env.example and configure synthetic local values." >&2
  exit 1
fi

BILLING_MOCK_SCENARIO=APPROVED "${COMPOSE[@]}" up \
  --build \
  --detach \
  --wait \
  --wait-timeout 240 \
  postgres rabbitmq identity-mock event-management registration attendance reporting \
  billing-mock notification-mock

service_url() {
  local mapping
  mapping=$("${COMPOSE[@]}" port "$1" 8080)
  printf 'http://%s:%s' "${mapping%:*}" "${mapping##*:}"
}

export EVENTHUB_EVENT_MANAGEMENT_URL="$(service_url event-management)"
export EVENTHUB_REGISTRATION_URL="$(service_url registration)"
export EVENTHUB_ATTENDANCE_URL="$(service_url attendance)"
export EVENTHUB_REPORTING_URL="$(service_url reporting)"
export EVENTHUB_RUN_INTEGRATION=1

cd "$ROOT"
"${PYTHON:-python3}" -m pytest -x --import-mode=importlib \
  tests/integration/paid-registration-flow/test_paid_registration_flow.py

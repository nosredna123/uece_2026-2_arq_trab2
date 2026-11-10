#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
COMPOSE=(docker compose --env-file "$ROOT/infra/.env" -f "$ROOT/infra/docker-compose.yml")

if [[ ! -f "$ROOT/infra/.env" ]]; then
  echo "Missing infra/.env; copy infra/.env.example and configure synthetic local values." >&2
  exit 1
fi

if ! grep -Eq '^WAITLIST_ENABLED=true$' "$ROOT/infra/.env"; then
  echo "The concurrency test requires WAITLIST_ENABLED=true in infra/.env." >&2
  exit 1
fi

WAITLIST_ENABLED=true "${COMPOSE[@]}" up \
  --build \
  --detach \
  --force-recreate \
  --wait \
  --wait-timeout 240 \
  postgres rabbitmq identity-mock event-management registration

service_url() {
  local mapping
  mapping=$("${COMPOSE[@]}" port "$1" 8080)
  printf 'http://%s:%s' "${mapping%:*}" "${mapping##*:}"
}

export EVENTHUB_EVENT_MANAGEMENT_URL="$(service_url event-management)"
export EVENTHUB_REGISTRATION_URL="$(service_url registration)"
export EVENTHUB_RUN_CONCURRENCY=1

cd "$ROOT"
"${PYTHON:-python3}" -m pytest -x --import-mode=importlib \
  tests/concurrency/test_capacity_contention.py

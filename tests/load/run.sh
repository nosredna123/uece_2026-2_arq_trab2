#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
PROJECT_NAME="eventhub-load-$$"
PYTHON_BIN="${PYTHON:-python3}"
COMPOSE=(
  docker compose
  --project-name "$PROJECT_NAME"
  --env-file "$ROOT/infra/.env.example"
  -f "$ROOT/infra/docker-compose.yml"
)

mapfile -t AVAILABLE_PORTS < <(
  "$PYTHON_BIN" - <<'PY'
import socket

sockets = []
try:
    for _ in range(7):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        sockets.append(listener)
    for listener in sockets:
        print(listener.getsockname()[1])
finally:
    for listener in sockets:
        listener.close()
PY
)
if (( ${#AVAILABLE_PORTS[@]} != 7 )); then
  echo "Could not select isolated host ports for the load-test stack." >&2
  exit 1
fi

export POSTGRES_PORT="${EVENTHUB_LOAD_POSTGRES_PORT:-${AVAILABLE_PORTS[0]}}"
export RABBITMQ_HOST_PORT="${EVENTHUB_LOAD_RABBITMQ_PORT:-${AVAILABLE_PORTS[1]}}"
export RABBITMQ_MANAGEMENT_PORT="${EVENTHUB_LOAD_RABBITMQ_MANAGEMENT_PORT:-${AVAILABLE_PORTS[2]}}"
export EVENT_MANAGEMENT_PORT="${EVENTHUB_LOAD_EVENT_MANAGEMENT_PORT:-${AVAILABLE_PORTS[3]}}"
export REGISTRATION_PORT="${EVENTHUB_LOAD_REGISTRATION_PORT:-${AVAILABLE_PORTS[4]}}"
export ATTENDANCE_PORT="${EVENTHUB_LOAD_ATTENDANCE_PORT:-${AVAILABLE_PORTS[5]}}"
export REPORTING_PORT="${EVENTHUB_LOAD_REPORTING_PORT:-${AVAILABLE_PORTS[6]}}"

cleanup() {
  result=$?
  trap - EXIT
  if ! "${COMPOSE[@]}" down --volumes --remove-orphans; then
    echo "Failed to clean up isolated Compose project $PROJECT_NAME." >&2
    result=1
  fi
  exit "$result"
}
trap cleanup EXIT

"${COMPOSE[@]}" up \
  --build \
  --detach \
  --wait \
  --wait-timeout 300 \
  postgres rabbitmq identity-mock event-management registration

export EVENTHUB_EVENT_MANAGEMENT_URL="http://127.0.0.1:$EVENT_MANAGEMENT_PORT"
export EVENTHUB_REGISTRATION_URL="http://127.0.0.1:$REGISTRATION_PORT"
cd "$ROOT"
"$PYTHON_BIN" tests/load/load_test.py "$@"

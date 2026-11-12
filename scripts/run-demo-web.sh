#!/usr/bin/env bash

set -Eeuo pipefail

ROOT="$(git rev-parse --show-toplevel)"
ENV_FILE="${EVENTHUB_ENV_FILE:-$ROOT/infra/.env}"
DEFAULT_ENV_FILE="$ROOT/infra/.env.example"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

read_env_value() {
  local key="$1"
  local environment_value
  environment_value="$(printenv "$key" 2>/dev/null || true)"
  if [[ -n "$environment_value" ]]; then
    printf '%s\n' "$environment_value"
    return
  fi
  if grep -q "^${key}=" "$ENV_FILE"; then
    awk -F= -v key="$key" '$1 == key { value = substr($0, index($0, "=") + 1) } END { print value }' "$ENV_FILE"
  else
    awk -F= -v key="$key" '$1 == key { value = substr($0, index($0, "=") + 1) } END { print value }' "$DEFAULT_ENV_FILE"
  fi
}

[[ $# -eq 0 ]] || fail "This launcher takes no options; update infra/.env and run it again."

[[ -f "$ENV_FILE" ]] || fail "Missing infra/.env. Copy infra/.env.example and configure local synthetic values; this script never creates or overwrites it."
command -v docker >/dev/null 2>&1 || fail "Docker CLI is required."

web_enabled="$(read_env_value DEMO_WEB_ENABLED)"
identity_lab_enabled="$(read_env_value IDENTITY_LAB_ENABLED)"
demo_lab_enabled="$(read_env_value DEMO_LAB_ENABLED)"
[[ "$web_enabled" == true ]] || fail "Set DEMO_WEB_ENABLED=true in infra/.env before starting the presentation app."
[[ "$identity_lab_enabled" == true || "$identity_lab_enabled" == false ]] \
  || fail "IDENTITY_LAB_ENABLED must be exactly true or false."
[[ "$demo_lab_enabled" == true || "$demo_lab_enabled" == false ]] \
  || fail "DEMO_LAB_ENABLED must be exactly true or false."
[[ "$identity_lab_enabled" == "$demo_lab_enabled" ]] || fail "IDENTITY_LAB_ENABLED and DEMO_LAB_ENABLED must match."

web_port="${DEMO_WEB_PORT:-$(read_env_value DEMO_WEB_PORT)}"
[[ "$web_port" =~ ^[0-9]{1,5}$ ]] || fail "DEMO_WEB_PORT must be a valid TCP port."
port_number=$((10#$web_port))
((port_number >= 1024 && port_number <= 65535)) || fail "DEMO_WEB_PORT must be between 1024 and 65535."

outbox_interval="$(read_env_value REGISTRATION_OUTBOX_PUBLISH_INTERVAL_MS)"
export REGISTRATION_OUTBOX_PUBLISH_INTERVAL_MS="${outbox_interval:-1000}"

COMPOSE=(
  docker compose
  --env-file "$DEFAULT_ENV_FILE"
  --env-file "$ENV_FILE"
  -f "$ROOT/infra/docker-compose.yml"
  -f "$ROOT/infra/docker-compose.demo-web.yml"
)
BASE_COMPOSE=(
  docker compose
  --env-file "$DEFAULT_ENV_FILE"
  --env-file "$ENV_FILE"
  -f "$ROOT/infra/docker-compose.yml"
)

service=demo-web
profile=demo-web
if [[ "$demo_lab_enabled" == true ]]; then
  for key in \
    DEMO_LAB_FIXTURES_PATH \
    DEMO_LAB_MAX_PARTICIPANTS \
    DEMO_LAB_MAX_CONCURRENCY \
    DEMO_LAB_RUN_TIMEOUT_SECONDS \
    DEMO_LAB_SETTLE_TIMEOUT_SECONDS \
    DEMO_LAB_RUN_TTL_SECONDS \
    DEMO_LAB_MAX_RETAINED_RUNS; do
    [[ -n "$(read_env_value "$key")" ]] || fail "$key must be set in infra/.env when the load scenarios are enabled."
  done
fi
if [[ "$demo_lab_enabled" == true ]]; then
  COMPOSE+=(-f "$ROOT/infra/docker-compose.demo-lab.yml")
fi
COMPOSE+=(--profile "$profile")

"${COMPOSE[@]}" config --quiet || fail "Compose configuration is invalid; validate infra/.env and the selected profile."

running_id=$("${COMPOSE[@]}" ps --status running -q "$service") \
  || fail "Could not determine whether the presentation app is already running."
if [[ -z "$running_id" ]]; then
  if command -v ss >/dev/null 2>&1; then
    listener="$(ss -H -ltn "sport = :$port_number")" || fail "Could not inspect localhost port $port_number."
  elif command -v lsof >/dev/null 2>&1; then
    if listener="$(lsof -nP -iTCP:"$port_number" -sTCP:LISTEN 2>/dev/null)"; then
      :
    else
      listener=""
    fi
  else
    fail "Port collision checks require ss or lsof; install a platform port-inspection tool."
  fi
  [[ -z "$listener" ]] || fail "Localhost port $port_number is already used by an unrelated process; choose another DEMO_WEB_PORT."
fi

printf 'Building and starting the EventHub services; Compose will recreate only services whose configuration changed. Existing volumes are preserved.\n'
if "${BASE_COMPOSE[@]}" up --build --detach --wait --wait-timeout 600; then
  :
else
  status=$?
  printf 'ERROR: could not prepare the EventHub services. Check the Compose output and configured ports.\n' >&2
  exit "$status"
fi

printf 'Starting the presentation app.\n'
if "${COMPOSE[@]}" up --detach --build --wait --wait-timeout 240 "$service"; then
  :
else
  status=$?
  printf 'ERROR: could not start %s. Check the Compose output and unrelated port collisions.\n' "$service" >&2
  exit "$status"
fi

printf 'EventHub presentation app is ready at http://localhost:%s\n' "$web_port"
printf 'Prerequisites: healthy Event Management, Registration, Attendance, Reporting, and Identity services; Billing and Notification mocks are started for domain flows.\n'
if [[ "$demo_lab_enabled" == true ]]; then
  printf 'Load scenarios are enabled; run: bash scripts/cenario.sh [pico-inscricoes|ultima-vaga|envios-repetidos] (no argument: all in parallel)\n'
else
  printf 'Load scenarios are disabled (set DEMO_LAB_ENABLED=true and IDENTITY_LAB_ENABLED=true to enable them).\n'
fi

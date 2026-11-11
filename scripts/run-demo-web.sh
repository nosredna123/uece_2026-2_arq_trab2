#!/usr/bin/env bash

set -Eeuo pipefail

ROOT="$(git rev-parse --show-toplevel)"
ENV_FILE="${EVENTHUB_ENV_FILE:-$ROOT/infra/.env}"
restart_identity_pool=false

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
  awk -F= -v key="$key" '$1 == key { value = substr($0, index($0, "=") + 1) } END { print value }' "$ENV_FILE"
}

while (($#)); do
  case "$1" in
    --restart-identity-pool)
      restart_identity_pool=true
      ;;
    *)
      fail "Unknown option '$1'. Only --restart-identity-pool is supported."
      ;;
  esac
  shift
done

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

COMPOSE=(
  docker compose
  --env-file "$ENV_FILE"
  -f "$ROOT/infra/docker-compose.yml"
  -f "$ROOT/infra/docker-compose.demo-web.yml"
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
    [[ -n "$(read_env_value "$key")" ]] || fail "$key must be set in infra/.env when the Scenario Lab is enabled."
  done
fi
if [[ "$demo_lab_enabled" == true ]]; then
  COMPOSE+=(-f "$ROOT/infra/docker-compose.demo-lab.yml")
fi
COMPOSE+=(--profile "$profile")

"${COMPOSE[@]}" config --quiet || fail "Compose configuration is invalid; validate infra/.env and the selected profile."

if [[ "$demo_lab_enabled" == true ]]; then
  identity_id=$("${COMPOSE[@]}" ps --status running -q identity-mock) \
    || fail "Could not determine whether the Identity mock is running."
  if [[ -n "$identity_id" ]]; then
    identity_environment="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$identity_id")" \
      || fail "Could not inspect the running Identity mock configuration."
    if [[ "$identity_environment" != *$'IDENTITY_LAB_ENABLED=true'* ]]; then
      if [[ "$restart_identity_pool" != true ]]; then
        fail "The running Identity mock has not loaded the scenario pool. After enabling both lab flags, rerun with --restart-identity-pool once; the browser never restarts Identity."
      fi
      printf 'Explicitly restarting Identity once to load the opted-in scenario pool.\n'
      "${COMPOSE[@]}" up --detach --force-recreate --wait --wait-timeout 120 identity-mock \
        || fail "Identity mock could not be restarted with the opted-in scenario pool."
    fi
  fi
elif [[ "$restart_identity_pool" == true ]]; then
  fail "--restart-identity-pool is only valid when both lab flags are true."
fi

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

printf 'Starting the presentation app with existing EventHub services; backend port readiness checks are not rerun.\n'
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
  printf 'Scenario Lab is enabled with the fixed read-only fixture pool and configured Registration waitlist.\n'
else
  printf 'Scenario Lab is disabled; the presentation app remains available for regular journeys.\n'
fi

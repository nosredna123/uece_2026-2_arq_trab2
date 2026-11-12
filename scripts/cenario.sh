#!/usr/bin/env bash
# Runs a pre-defined load scenario against the running presentation stack.
# Usage: bash scripts/cenario.sh [pico-inscricoes|ultima-vaga|envios-repetidos]
# Without an argument, all scenarios run in parallel with a random mixed load.

set -Eeuo pipefail

ROOT="$(git rev-parse --show-toplevel)"
ENV_FILE="${EVENTHUB_ENV_FILE:-$ROOT/infra/.env}"
DEFAULT_ENV_FILE="$ROOT/infra/.env.example"
SCENARIOS="pico-inscricoes | ultima-vaga | envios-repetidos"

fail() {
  printf 'ERRO: %s\n' "$*" >&2
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
  if [[ -f "$ENV_FILE" ]] && grep -q "^${key}=" "$ENV_FILE"; then
    awk -F= -v key="$key" '$1 == key { value = substr($0, index($0, "=") + 1) } END { print value }' "$ENV_FILE"
  else
    awk -F= -v key="$key" '$1 == key { value = substr($0, index($0, "=") + 1) } END { print value }' "$DEFAULT_ENV_FILE"
  fi
}

[[ $# -le 1 ]] || fail "Informe no máximo um cenário: $SCENARIOS (ou nenhum, para todos)"
scenario=()
if [[ $# -eq 1 ]]; then
  case "$1" in
    pico-inscricoes | ultima-vaga | envios-repetidos) scenario=("$1") ;;
    *) fail "Cenário desconhecido '$1'. Opções: $SCENARIOS (ou nenhum, para todos)" ;;
  esac
fi

[[ -f "$ENV_FILE" ]] || fail "Arquivo infra/.env não encontrado; rode scripts/run-demo-web.sh primeiro."
command -v docker >/dev/null 2>&1 || fail "Docker CLI é necessário."
[[ "$(read_env_value DEMO_LAB_ENABLED)" == true ]] \
  || fail "Os cenários exigem DEMO_LAB_ENABLED=true e IDENTITY_LAB_ENABLED=true em infra/.env."

COMPOSE=(
  docker compose
  --env-file "$DEFAULT_ENV_FILE"
  --env-file "$ENV_FILE"
  -f "$ROOT/infra/docker-compose.yml"
  -f "$ROOT/infra/docker-compose.demo-web.yml"
  -f "$ROOT/infra/docker-compose.demo-lab.yml"
  --profile demo-web
)

running_id=$("${COMPOSE[@]}" ps --status running -q demo-web 2>/dev/null || true)
[[ -n "$running_id" ]] || fail "O app de apresentação não está rodando; execute: bash scripts/run-demo-web.sh"

exec "${COMPOSE[@]}" exec -T \
  -e "SCENARIO_PEAK_PARTICIPANTS=$(read_env_value SCENARIO_PEAK_PARTICIPANTS)" \
  -e "SCENARIO_PEAK_DURATION_SECONDS=$(read_env_value SCENARIO_PEAK_DURATION_SECONDS)" \
  -e "SCENARIO_MIXED_DURATION_SECONDS=$(read_env_value SCENARIO_MIXED_DURATION_SECONDS)" \
  -e "SCENARIO_CHECK_IN_MIN_PERCENT=$(read_env_value SCENARIO_CHECK_IN_MIN_PERCENT)" \
  -e "SCENARIO_CHECK_IN_MAX_PERCENT=$(read_env_value SCENARIO_CHECK_IN_MAX_PERCENT)" \
  -e "SCENARIO_CERTIFICATE_MIN_PERCENT=$(read_env_value SCENARIO_CERTIFICATE_MIN_PERCENT)" \
  -e "SCENARIO_CERTIFICATE_MAX_PERCENT=$(read_env_value SCENARIO_CERTIFICATE_MAX_PERCENT)" \
  demo-web python -u -m demo_web.scenarios "${scenario[@]}"

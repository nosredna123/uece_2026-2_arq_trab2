#!/usr/bin/env bash
# Restores the presentation stack to its first-deploy state: stops every
# container, deletes the local data volumes, and relaunches the demo so the
# initial seed data is loaded again.
# Usage: bash scripts/reset-demo.sh [--yes]

set -Eeuo pipefail

ROOT="$(git rev-parse --show-toplevel)"
ENV_FILE="${EVENTHUB_ENV_FILE:-$ROOT/infra/.env}"
DEFAULT_ENV_FILE="$ROOT/infra/.env.example"

fail() {
  printf 'ERRO: %s\n' "$*" >&2
  exit 1
}

assume_yes=false
case "${1:-}" in
  "") ;;
  -y | --yes) assume_yes=true ;;
  *) fail "Opção desconhecida: $1. Uso: bash scripts/reset-demo.sh [--yes]" ;;
esac
[[ $# -le 1 ]] || fail "Uso: bash scripts/reset-demo.sh [--yes]"
[[ -f "$ENV_FILE" ]] || fail "Arquivo $ENV_FILE não encontrado."

if [[ "$assume_yes" != true ]]; then
  printf 'Isto apagará TODOS os dados locais (eventos, inscrições, check-ins, certificados,\n'
  printf 'filas e dados dos mocks) e recarregará apenas os dados sintéticos iniciais.\n'
  read -r -p 'Digite "sim" para continuar: ' answer
  [[ "$answer" == "sim" ]] || { printf 'Cancelado; nada foi alterado.\n'; exit 0; }
fi

printf 'Parando os contêineres e apagando os volumes de dados...\n'
docker compose \
  --env-file "$DEFAULT_ENV_FILE" \
  --env-file "$ENV_FILE" \
  -f "$ROOT/infra/docker-compose.yml" \
  -f "$ROOT/infra/docker-compose.demo-web.yml" \
  -f "$ROOT/infra/docker-compose.demo-lab.yml" \
  --profile demo-web \
  down --volumes --remove-orphans

printf 'Subindo o ambiente novamente com os dados iniciais...\n'
bash "$ROOT/scripts/run-demo-web.sh"
printf 'Pronto: dados restaurados ao estado inicial. Entre novamente no navegador.\n'

#!/usr/bin/env bash
set -Eeuo pipefail

rabbitmq-server &
server_pid=$!

shutdown() {
  local status=0
  kill -TERM "$server_pid" || status=$?
  wait "$server_pid" || status=$?
  if (( status != 0 && status != 143 )); then
    exit "$status"
  fi
  exit 0
}

trap shutdown TERM INT

for attempt in {1..30}; do
  if rabbitmq-diagnostics -q check_running >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
rabbitmq-diagnostics -q check_running
users="$(rabbitmqctl list_users)"
if awk -v username="$RABBITMQ_USER" '$1 == username { found = 1 } END { exit !found }' <<< "$users"; then
  rabbitmqctl change_password "$RABBITMQ_USER" "$RABBITMQ_PASSWORD"
else
  rabbitmqctl add_user "$RABBITMQ_USER" "$RABBITMQ_PASSWORD"
fi
rabbitmqctl set_user_tags "$RABBITMQ_USER" management
rabbitmqctl set_permissions -p / "$RABBITMQ_USER" '.*' '.*' '.*'

wait "$server_pid"

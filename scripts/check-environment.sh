#!/usr/bin/env bash

set -Eeuo pipefail

readonly diagnostic_image="busybox:1.36.1"
readonly min_cpus=2
readonly min_memory_bytes=$((4 * 1024 * 1024 * 1024))
readonly min_disk_kib=$((5 * 1024 * 1024))
readonly expected_network_response="eventhub-network-ok"
readonly container_suffix="${UID:-0}_$$_$(date +%s)"
readonly network_name="eventhub-readiness-${container_suffix}"
readonly volume_name="eventhub-readiness-${container_suffix}"
readonly server_name="eventhub-readiness-http-${container_suffix}"

created_network=false
created_volume=false
created_server=false

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  local status=$?
  local cleanup_failed=false
  trap - EXIT INT TERM

  if [[ "$created_server" == true ]] && docker inspect "$server_name" >/dev/null 2>&1; then
    if ! docker rm -f "$server_name" >/dev/null; then
      printf 'FAIL: could not remove diagnostic container %s\n' "$server_name" >&2
      cleanup_failed=true
    fi
  fi

  if [[ "$created_volume" == true ]] && docker volume inspect "$volume_name" >/dev/null 2>&1; then
    if ! docker volume rm "$volume_name" >/dev/null; then
      printf 'FAIL: could not remove diagnostic volume %s\n' "$volume_name" >&2
      cleanup_failed=true
    fi
  fi

  if [[ "$created_network" == true ]] && docker network inspect "$network_name" >/dev/null 2>&1; then
    if ! docker network rm "$network_name" >/dev/null; then
      printf 'FAIL: could not remove diagnostic network %s\n' "$network_name" >&2
      cleanup_failed=true
    fi
  fi

  if [[ "$status" -ne 0 ]]; then
    exit "$status"
  fi
  if [[ "$cleanup_failed" == true ]]; then
    exit 1
  fi
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'EventHub environment readiness check\n'

command -v git >/dev/null 2>&1 || fail "Git is required; install it using docs/development/environment-setup.md."
git_version=$(git --version) || fail "Git could not report its version."
[[ "$git_version" =~ git\ version\ 2\. ]] || fail "Git 2.x is required; found: $git_version"
printf 'PASS: %s\n' "$git_version"

command -v docker >/dev/null 2>&1 || fail "Docker CLI is required; install Docker Engine/Desktop and Compose v2."
docker_versions=$(docker version --format '{{.Client.Version}} {{.Server.Version}}') \
  || fail "Docker client/daemon check failed; start the daemon and verify the selected context."
read -r docker_client_version docker_server_version <<<"$docker_versions"
[[ -n "${docker_client_version:-}" && -n "${docker_server_version:-}" ]] \
  || fail "Docker client and server versions were not both reported."
docker_context=$(docker context show) || fail "Could not determine the active Docker context."
docker_compose_version=$(docker compose version --short) \
  || fail "Docker Compose v2 is required; install the Compose CLI plugin."
[[ "$docker_compose_version" =~ ^v?2\. ]] \
  || fail "Docker Compose v2 is required; found: $docker_compose_version"
printf 'PASS: Docker client %s, server %s, context %s\n' \
  "$docker_client_version" "$docker_server_version" "$docker_context"
printf 'PASS: Docker Compose %s\n' "$docker_compose_version"

daemon_resources=$(docker info --format '{{.NCPU}} {{.MemTotal}}') \
  || fail "Docker daemon is unavailable in context '$docker_context'; check docker info."
read -r daemon_cpus daemon_memory_bytes <<<"$daemon_resources"
[[ "${daemon_cpus:-}" =~ ^[0-9]+$ && "${daemon_memory_bytes:-}" =~ ^[0-9]+$ ]] \
  || fail "Docker daemon did not report valid CPU and memory resources."
(( daemon_cpus >= min_cpus )) \
  || fail "Docker provides ${daemon_cpus} CPU(s); at least ${min_cpus} are required."
(( daemon_memory_bytes >= min_memory_bytes )) \
  || fail "Docker provides $((daemon_memory_bytes / 1024 / 1024)) MiB; at least 4096 MiB are required."
printf 'PASS: Docker resources: %s CPU(s), %s MiB memory\n' \
  "$daemon_cpus" "$((daemon_memory_bytes / 1024 / 1024))"

disk_available_kib=$(df -Pk . | awk 'NR == 2 { print $4 }') \
  || fail "Could not determine free disk space for the project filesystem."
[[ "$disk_available_kib" =~ ^[0-9]+$ ]] \
  || fail "Could not parse free disk space for the project filesystem."
(( disk_available_kib >= min_disk_kib )) \
  || fail "Project filesystem has $((disk_available_kib / 1024)) MiB free; at least 5120 MiB are required."
printf 'PASS: project filesystem: %s MiB free\n' "$((disk_available_kib / 1024))"

if command -v ss >/dev/null 2>&1; then
  port_check_tool="ss"
elif command -v lsof >/dev/null 2>&1; then
  port_check_tool="lsof"
else
  fail "Port checking requires 'ss' (Linux/WSL) or 'lsof' (macOS); install the platform tool."
fi

expected_ports=${EVENTHUB_CHECK_PORTS:-"5432 5672 15672"}
[[ "$expected_ports" != *","* ]] \
  || fail "EVENTHUB_CHECK_PORTS must be a whitespace-separated list of TCP ports."
seen_ports=" "
for port in $expected_ports; do
  [[ "$port" =~ ^[0-9]{1,5}$ ]] || fail "Invalid TCP port '$port' in EVENTHUB_CHECK_PORTS."
  port_number=$((10#$port))
  (( port_number >= 1 && port_number <= 65535 )) \
    || fail "TCP port '$port' must be between 1 and 65535."
  case "$seen_ports" in
    *" $port_number "*) fail "TCP port '$port_number' is listed more than once." ;;
  esac
  seen_ports+="$port_number "

  if [[ "$port_check_tool" == "ss" ]]; then
    listener=$(ss -H -ltn "sport = :$port_number") \
      || fail "Could not inspect TCP port $port_number with ss."
  else
    if listener=$(lsof -nP -iTCP:"$port_number" -sTCP:LISTEN 2>/dev/null); then
      :
    else
      listener=""
    fi
  fi

  [[ -z "$listener" ]] \
    || fail "Expected TCP port $port_number is already in use; stop its owner or configure a different port mapping."
  printf 'PASS: TCP port %s is available\n' "$port_number"
done

docker run --rm "$diagnostic_image" sh -c 'printf "container-ok\n"' \
  || fail "Could not run the diagnostic container; check Docker permissions, image access, and daemon health."
printf 'PASS: diagnostic container execution\n'

docker network create "$network_name" >/dev/null \
  || fail "Could not create a temporary Docker bridge network."
created_network=true

docker volume create "$volume_name" >/dev/null \
  || fail "Could not create a temporary Docker volume."
created_volume=true

docker run --rm --mount "type=volume,source=$volume_name,target=/probe" "$diagnostic_image" \
  sh -c 'printf "volume-ok\n" > /probe/check; test "$(cat /probe/check)" = "volume-ok"' \
  || fail "Could not write and read data in a temporary Docker volume."
printf 'PASS: temporary Docker volume write/read\n'

docker run -d --name "$server_name" --network "$network_name" "$diagnostic_image" \
  sh -c 'mkdir -p /tmp/www; printf "eventhub-network-ok\n" > /tmp/www/health; httpd -f -p 8080 -h /tmp/www' \
  >/dev/null || fail "Could not start the temporary network diagnostic container."
created_server=true

network_response=$(docker run --rm --network "$network_name" "$diagnostic_image" sh -c '
  attempt=0
  while [ "$attempt" -lt 10 ]; do
    if wget -q -T 2 -O - "http://'"$server_name"':8080/health"; then
      exit 0
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  exit 1
') || fail "Temporary containers could not communicate over a Docker bridge network."
[[ "$network_response" == "$expected_network_response" ]] \
  || fail "Docker network returned an unexpected diagnostic response."
printf 'PASS: temporary Docker bridge network container-to-container request\n'

printf 'PASS: environment meets the documented readiness checks.\n'

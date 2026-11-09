# Environment readiness evidence

**Recorded:** 2026-11-09 14:55:11 -03:00  
**Gate:** `./scripts/check-environment.sh`  
**Result:** PASS

The gate passed on the recorded machine. Evidence contains environment
metadata only; no environment variables, credentials, or `.env` values were
read or recorded.

| Check | Result |
|---|---|
| Operating system / architecture | Linux Mint 22.3 / x86_64 |
| Git | 2.43.0 |
| Docker CLI / daemon | 29.1.3 / 29.1.3 |
| Docker context | `default` |
| Docker Compose | v2.40.3 |
| Docker daemon resources | 16 CPUs / 60,121 MiB memory |
| Project filesystem free space | 136,314 MiB |
| TCP 5432 (PostgreSQL) | Available |
| TCP 5672 (RabbitMQ AMQP) | Available |
| TCP 15672 (RabbitMQ management, if exposed) | Available |
| Disposable container execution | PASS |
| Temporary volume write/read | PASS |
| Temporary bridge-network HTTP request | PASS |
| Temporary diagnostic-resource cleanup | PASS |

The port checks use the initial PostgreSQL/RabbitMQ expectations. Application
host ports are not defined until Phase 2; add their mappings to
`EVENTHUB_CHECK_PORTS` when the Compose configuration is established. Passing
this gate indicates host readiness only; it does not validate services or
application builds that have not yet been created.

## Reproduction

From the repository root, run:

```sh
./scripts/check-environment.sh
```

The script exits nonzero and prints an actionable message for a failed
prerequisite or diagnostic. It creates uniquely named temporary resources and
removes only those resources. Do not interpret this host readiness result as a
test of the EventHub Compose stack.

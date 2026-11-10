# Repeatable API load test

From the repository root, run:

```sh
bash tests/load/run.sh
```

The runner starts an isolated Compose project using only the synthetic values
in `infra/.env.example`. It selects available ephemeral host ports and removes
its project-specific containers and database volumes when the command exits.
Override individual ports with the corresponding `EVENTHUB_LOAD_*_PORT`
variables when needed.

The workload provisions one published event with free activities, then measures
public event-catalog queries and participant registrations separately. Each
profile sends ten requests per virtual user per operation: the baseline is two
concurrent users (20 requests per operation) and the peak is 40 users (400
requests per operation, 20× concurrency). Every registration uses a different
activity, so successful operations do not depend on retries or fixture
mutation. The report includes request success, throughput, and p50/p95/p99/max
latency. Any unsuccessful measured request fails the command.

The specification defines no latency SLA. The test records comparative
measurements and verifies successful API behavior; it does not claim that a
particular latency threshold has been met. To run against an already-running
stack, invoke `tests/load/load_test.py` with both service URL options and use
the same profile arguments.

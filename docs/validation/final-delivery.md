# EventHub architecture, validation, and defense guide

## Purpose and scope

This document brings together the architecture decisions, versioned rationales,
contracts, implementation evidence, and demonstration notes for the EventHub
prototype. It is an index and evidence summary, not a production-readiness or
security certification.

The delivered prototype has four executable domain services—Event Management,
Registration, Attendance & Certification, and Reporting—and three explicit
dependency mocks—Identity & Access, Billing, and Notification. PostgreSQL and
RabbitMQ run as local Compose infrastructure. The mocks make the integration
flow and failure outcomes reproducible; they do not stand in for production
identity, payment, or notification providers.

## Architecture and contracts

Use the editable [container diagram](../architecture/containers.puml) and the
Mermaid [context map](../architecture/context-map.md) for service boundaries
and dependencies. The [paid-registration flow](../architecture/paid-registration-flow.md)
shows the success path and failure behavior; the
[data-ownership model](../architecture/data-ownership.md) and
[service-boundary inventory](../architecture/service-boundaries.md) explain
ownership and prototype scope.

The PlantUML files are the editable sources for the diagrams. The existing
`services.png` is an earlier generated export and is not the authoritative
as-built view; use the linked sources and contracts when reviewing current
behavior.

- HTTP routes and schemas: [OpenAPI contract index](../contracts/README.md)
  and [OpenAPI specifications](../contracts/openapi).
- Event envelope, schemas, routing keys, queues, and mock behavior:
  [AsyncAPI specification](../contracts/asyncapi/eventhub.yaml) and
  [messaging behavior contract](../contracts/asyncapi/README.md).
- Correlation identity and structured diagnostics:
  [observability conventions](./observability.md).

## Decisions and trade-offs

The accepted decisions and their machine-readable rationales are versioned
together. The ADRs record the original choice and its obligations; validation
results are summarized separately below.

| ADR | Decision | Rationale record |
|---|---|---|
| [ADR-001: service boundaries](../adr/ADR-001-service-boundaries.md) | Four independently buildable domain services; Identity, Billing, and Notification remain explicit mocks. | [ADR-001.json](../rationales/ADR-001.json) |
| [ADR-002: data ownership](../adr/ADR-002-data-ownership.md) | Separate logical databases and service credentials; Event Management freezes an idempotent admission-capacity snapshot. | [ADR-002.json](../rationales/ADR-002.json) |
| [ADR-003: registration consistency](../adr/ADR-003-registration-consistency.md) | Serialize capacity and FIFO transitions with a Registration-owned PostgreSQL activity-row lock. | [ADR-003.json](../rationales/ADR-003.json) |
| [ADR-004: messaging and resilience](../adr/ADR-004-messaging-and-resilience.md) | Transactional outboxes, publisher confirms, at-least-once delivery, inbox deduplication, and observable recovery. | [ADR-004.json](../rationales/ADR-004.json) |
| [ADR-005: prototype scope](../adr/ADR-005-prototype-scope.md) | Preserve authorization and core invariants while deferring production identity, payment, and delivery integrations. | [ADR-005.json](../rationales/ADR-005.json) |

### Deliberate trade-offs

- **Capability boundaries instead of a monolith or one service per requirement:**
  this demonstrates independent deployment, Java/Python interoperability,
  selective scaling, and failure isolation without splitting the capacity and
  waitlist state machine. The cost is more contracts, containers, and
  operational recovery paths.
- **Four logical databases on one local PostgreSQL cluster:**
  service-owned data and credentials avoid cross-service SQL and distributed
  transactions while keeping local resource use manageable. The cluster
  remains a shared infrastructure failure domain; it is not physical
  high-availability isolation.
- **Frozen admission capacity plus a row-locked Registration ledger:**
  local transactions protect the no-overbooking invariant and make retries
  recoverable. Capacity cannot be changed after admission activates, and a
  busy activity serializes mutations. The local load run is comparative
  evidence, not proof of production capacity.
- **Transactional outbox and at-least-once messaging:**
  durable intent, confirms, deduplication, and replay prevent ordinary
  database/broker failures from becoming silent data loss. Duplicate delivery,
  projection lag, quarantine, and operational repair are accepted complexity;
  exactly-once transport is not claimed.
- **Contract-backed mocks instead of external providers:**
  synthetic identity and configurable Billing/Notification outcomes make
  failure cases repeatable and keep the demo self-contained. They provide no
  evidence of real payment settlement, production authentication, or
  multichannel delivery.
- **Local Compose rather than a production platform:**
  the prototype is reproducible without cloud services, Kubernetes, or an API
  gateway. Production deployment, secrets management, provider integrations,
  monitoring, and high availability are intentionally out of scope.

## Validation evidence

| Area | Recorded result | Evidence and limits |
|---|---|---|
| Host readiness | PASS | [Environment readiness](./environment-readiness.md) records the successful gate and environment metadata without credentials. |
| Repository Python suite | 185 passed, 6 skipped | The canonical Python 3.12 Docker test image ran pytest with `-x`; the default run skips environment-dependent concurrency/failure/live-flow cases, which have dedicated runners. |
| Java and service test images | PASS | The Compose test-profile build completed for both Java test targets and the Python service/mock test targets. |
| Live paid-registration flow | 1 passed | The dedicated runner completed paid reservation, asynchronous approval, check-in, certificate, and Reporting projection; correlation response/error IDs were asserted. See the [T042 batch report](../../checkpoints/batch-report-T042.yaml). |
| Domain mutation testing | Event Management 100%; Registration 95%; Attendance 100%; Reporting 97.30% | Scores meet the ≥95% domain-core gate. Boundary/infrastructure exclusions are listed in [mutation-testing scope](./mutation-testing.md). |
| Load comparison | 840/840 requests succeeded | At 2→40 virtual users, event-query throughput changed from 519.48 to 1,664.10 requests/s (3.20×); registration throughput from 55.65 to 252.57 requests/s (4.54×). Full percentiles and environment are in [load evidence](./evidence.md). |
| Correlation diagnostics | PASS | HTTP responses/errors, messages, and structured logs carry correlation context; registration diagnostics include `registrationId`. The live flow and service test-profile build passed; see [observability](./observability.md) and the [T042 report](../../checkpoints/batch-report-T042.yaml). |
| Quality and architecture checks | PASS | Ruff, import-linter, and whitespace checks passed for the implementation changes; service ownership and boundaries are covered by repository tests. |

The six default-suite skips are not counted as passes. They correspond to
environment-dependent tests with dedicated runners. The load result is one
local comparative run, not a production-capacity guarantee or latency-SLA
claim. Health checks prove process/dependency readiness only; they do not
replace domain-flow tests. Mutation scores apply only to the explicitly scoped
domain cores, not the excluded HTTP, persistence, or messaging adapters.

Additional task-level outcomes and traceability are in the
[implementation checkpoint](../../checkpoints/tasks-to-impl.yaml) and
[batch reports](../../checkpoints).

## AI-use declaration

Generative AI assistance was used during implementation to draft and refine
software, tests, and documentation. The changes were checked with the automated
builds, tests, and quality gates identified in this repository. This statement
does not claim that AI independently proves correctness or replaces human
review. Project maintainers remain responsible for reviewing the deliverable,
the accuracy of claims, and the final demonstration.

## Demonstration and defense script

Allow about 7–10 minutes for the walkthrough, plus up to two minutes for the
live paid-registration scenario. Prepare the local environment and synthetic
`infra/.env` as described in the [README](../../README.md); never display or
share real secrets.

1. **Introduce the scope (1 minute).** Explain the four executable domain
   services, three mocks, PostgreSQL/RabbitMQ, and the intentionally excluded
   production integrations. Open the [context map](../architecture/context-map.md).
2. **Explain ownership and decisions (2 minutes).** Show the Registration
   capacity authority, isolated data ownership, outbox, and independent
   Reporting/Notification consumers. Refer to ADR-002, ADR-003, and ADR-004
   above; state the accepted costs of immutable capacity, hot-row serialization,
   at-least-once delivery, and projection lag.
3. **Run the integrated scenario (2–3 minutes).** From the repository root,
   execute:

   ```sh
   bash tests/integration/paid-registration-flow/run.sh
   ```

   Describe how the runner creates synthetic event/activity data, reserves a
   paid slot, receives the Billing mock's approval, and waits for check-in,
   certificate issuance, and the Reporting projection. The runner intentionally
   leaves its Compose stack and volumes running.
4. **Show diagnostic evidence (1 minute).** Point to the successful test
   result, correlation ID assertions, and the `registrationId`/event context
   convention in [observability documentation](./observability.md). If showing
   logs, do not expose credentials or `.env` values.
5. **Present quality and limits (2 minutes).** Summarize the 185-pass Python
   suite, Java/service test-profile build, domain mutation scores, and the
   single local load comparison. Explicitly distinguish relative load
   measurements from production sizing, and mocks from external integrations.
6. **Close and clean up only when appropriate.** Stop the local stack with the
   following command after the demonstration. It preserves data; remove volumes
   only when intentionally resetting the local prototype.

   ```sh
   docker compose --env-file infra/.env -f infra/docker-compose.yml down
   ```

For a quick repeat of the repository Python suite in the project image:

```sh
docker compose --env-file infra/.env -f infra/docker-compose.yml --profile test \
  run --rm python-suite-test python -m pytest -x --import-mode=importlib
```

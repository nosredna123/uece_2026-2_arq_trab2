# ADR-004: Transactional outbox and independent at-least-once consumers

**Status:** Accepted for the prototype\
**Date:** 2026-11-09\
**Version:** 1.0.0\
**Traceability:** RF07-RF11, RN02-RN05, RQ01, RQ03-RQ06, RQ08; plan 1.3 / T008

## Context

Database commit and RabbitMQ publication can fail independently. Billing may
approve after expiry; notification and report consumers may be unavailable.
The protocol must tolerate replay without pretending that message delivery is
exactly once or treating missing payment results as approvals.

## Alternatives

| Alternative | Benefits | Costs / reason not selected |
|---|---|---|
| Synchronous Billing/Notification/Reporting calls in admission | Straight-line request code. | Couples availability and holds up the core flow; cannot atomically commit remote effects. |
| Commit state, then publish without an outbox | Simple normal path. | A crash loses the event; publishing first can expose rolled-back state. |
| Transactional outbox, publisher confirms and idempotent consumers | Recovers commit/publication gaps with existing PostgreSQL/RabbitMQ. | Duplicate delivery is expected; needs durable deduplication, retry and operational visibility. |
| Distributed transaction or dedicated saga orchestrator | Central coordination. | Additional infrastructure is unnecessary for the Registration-owned state machine and mock integrations. |

## Decision

Use HTTP for immediate commands/queries and explicit identity/activity lookups.
Use RabbitMQ publish/subscribe for payment/refund requests and results,
registration facts, activity lifecycle facts needed by eligibility consumers,
notifications, attendance/certificates and reporting projections.

Every producer with durable state writes outgoing messages to its own
transactional outbox together with the state change. Billing mock results also
need durable operation deduplication and pending publication for replay tests;
a mock does not excuse a crash gap. Notification records only simulated effects.

The outbox publisher sends persistent messages to durable exchanges and
independent durable queues per consumer. Require publisher confirms and detect
unroutable messages (mandatory publication/returned-message handling); mark
published only after successful routing/confirmation. A crash after confirm
but before marking published causes a duplicate, not a lost business fact.
Queue provisioning must precede publication; durable topology is not a
guarantee against disk loss or an unprovisioned subscriber.

Consumers use manual acknowledgments. Validate messages at the boundary, then
commit inbox/deduplication identity, state/projection changes and any new outbox
records in one local transaction before acknowledging. Deduplicate by stable
event identity and correlate payment/refund results to the business operation
and attempt. Billing retries must not simulate a second charge/refund for the
same operation. Delivery is **at least once**; business effects are idempotent.

Registration mutations still follow [ADR-003](ADR-003-registration-consistency.md).
Do not assume global FIFO across queues/producers. Carry aggregate identity,
sequence/version and correlation metadata; stale or duplicate messages cannot
regress state. Projection gaps/conflicts require observable buffering/replay or
repair under T011/T012, not silently applying an older snapshot over newer data.

### Failure and recovery policy

- Broker failures leave durable outbox work pending. Retry transient failures
  with configurable backoff capped at a configured maximum delay, not a busy
  requeue loop. Observe age, attempts and last error; never silently drop work.
- Consumer downtime leaves its own queue pending and does not stop other queues.
  A database failure before commit must not acknowledge the message.
- Invalid/schema-incompatible or persistently unprocessable messages go to a
  durable quarantine/dead-letter queue with error and correlation metadata.
  Explicitly record the failure, alert and support authorized repair/replay.
  Quarantine is not successful processing. Avoid automatic replay loops.
- Before sending an unpublished payment request whose reservation has expired,
  Registration durably records its suppression/expiry instead of charging for
  a dead reservation. Publication already in flight may still reach Billing;
  late approval always records manual compensation, never reconfirms a slot.
- Payment absence or Billing timeout leads to reservation expiry, not approval.
  A simulated timeout may mean no response; the local deadline is authoritative.
- Refund business retries remain automatic and unlimited as approved. Transport
  backoff/quarantine does not cancel the refund obligation: keep it pending,
  surface blocked delivery, resume automatic attempts after repair, and retain
  the confirmed registration and slot until refund approval.
- Notification failure and Reporting lag never roll back registration. Expose
  simulated delivery failure and projection lag rather than fabricating success.

Use bounded HTTP connection/read timeouts and explicit dependency errors for
Identity/Event Management. No permissive identity fallback, stale eligibility
fallback or request retry without an idempotency contract. A separate Circuit
Breaker is deferred until measurements justify it; timeouts and durable recovery
are the prototype mechanisms. No gateway or saga platform is introduced.

Propagate registration/operation, event and correlation IDs across HTTP, messages
and structured logs. Expose dependency, outbox, retry, quarantine and projection
health without tokens or sensitive payloads. Runtime timeout/backoff values,
queue names, retention and payload schemas are validated external configuration
and T011/T016 contract work; this ADR does not invent numeric defaults.

## Evidence and justification

- [RN05/RQ03](../../especificação-preliminar-sistema.md) and
  [approved invariants 8-9](../architecture/domain-scenarios.md) require independent
  Notification/Reporting failures.
- The [paid flow failure matrix](../architecture/paid-registration-flow.md)
  identifies the commit/publication, duplicate-result and late-approval hazards.
- On 2026-11-09 the user approved this durable at-least-once approach, including
  explicit quarantine/replay and unlimited refund retries.
- The [plan](../../plan.md) already selects RabbitMQ and outbox. Crash recovery
  is a design obligation here, not a claimed passing integration result.

## Consequences

More storage and workers are needed for outbox/inbox and retry diagnostics.
Consumers are eventually consistent; broker recovery can create a backlog.
Operators must repair/replay quarantined work. Durable retention cannot be
shortened below required replay/idempotency windows without an explicit decision.
No exactly-once transport or production broker high availability is claimed.

## Validation and follow-up

T011/T012 define versioning, replay and deduplication retention. Integration gates
must stop/restart broker and consumers, inject crashes before/after commit and
confirm/ack, duplicate/reorder messages, detect unroutable/poison messages and
demonstrate late compensation and non-blocking Reporting/Notification outages.
T009 supplies the JSON rationale.

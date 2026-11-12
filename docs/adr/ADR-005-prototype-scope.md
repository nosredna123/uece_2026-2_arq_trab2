# ADR-005: Executable prototype scope and minimum security boundary

**Status:** Accepted for the prototype\
**Date:** 2026-11-09 (revised 2026-11-12)\
**Version:** 1.1.0\
**Traceability:** RF01-RF11, RN06-RN07, RQ01, RQ05-RQ08; plan 1.3 / T008

## Context

The assignment requires a representative end-to-end implementation using at
least two technologies, not full production integrations. Reducing scope must
not remove authentication, data isolation or the capacity/payment invariants.
The approved business scenarios clarify certificate and administrator scope.

## Alternatives

| Alternative | Benefits | Costs / reason not selected |
|---|---|---|
| Implement all seven production services | Broad functional coverage. | Identity lifecycle, real financial integration and delivery channels dilute the core architectural demonstration. |
| Only catalog/registration endpoints with in-memory stubs | Fast demonstration. | Does not demonstrate four real services, persistent cross-technology integration or recoverable failures. |
| Four persistent services plus three contract-backed executable mocks | Exercises the complete representative flow with controlled failures. | Mocks limit conclusions about production security, billing and external delivery. |

## Decision

Implement the four services selected by [ADR-001](ADR-001-service-boundaries.md)
with PostgreSQL persistence, migrations, documented HTTP/event contracts and
containerized build/test/runtime paths. Provide independently runnable mocks for
Identity & Access, Billing and Notification, clearly labelled in logs, contracts
and demonstrations. Mock scenarios are deterministic/configurable, including
invalid identity, refusal, timeout and unavailability.

Identity mock authenticates synthetic credentials and returns trusted principal,
role and organization facts. Protected domain services must validate those
facts through the mock contract; never trust participant/organization/role fields
asserted in the command body or arbitrary headers. Scope every protected
operation and lookup, not just the entry endpoint:

- Participants read/change only their own registrations and eligible presence/
  certificate resources.
- Organizers administer events/activities and read reports for their organization.
- Global administrators may manage events/activities and read reports across
  organizations, but cannot administer users or organizations.
- Public catalog access exposes only published public information. Invalid
  credentials on protected routes never downgrade to public access.
- Internal activation/eligibility APIs and mock scenario controls are restricted
  to authorized service/development callers, not exposed as public bypasses.
  Broker permissions restrict producers/consumers to their required topology.

Authentication failure, missing authorization or unavailable Identity yields
an explicit denial/dependency failure with no mutation. Exact token, internal
service credential and error shapes are specified in T010/T019/T020; this ADR
does not label the mock a production authentication system.

Certificate eligibility is confirmed registration plus one idempotent check-in
and completed activity. The original illustrative percentage threshold is
outside the approved prototype; no attendance-duration tracking is implied.
Reporting remains a real persistent, authorized event-fed projection.

Use local Docker Compose, without paid cloud, Kubernetes, API gateway, vector
database or shared domain library. Versioned JSON rationales in T009 and Git
provide decision history; semantic/vector infrastructure is not needed to
satisfy the process's rationale-memory goal. KISS does not waive required gates.

All business/operational settings are external and validated at startup, with
no silent permissive defaults. Follow the approved configurable reservation/
offer periods, refund policy and certificate policy. Protect the real `.env`,
version only synthetic examples and keep tokens/secrets out of logs. T016 must
establish ignore rules before creating a real `.env`; T008 creates neither.

## Evidence and justification

- [Plan](../../plan.md), summary and phases 3-5, and
  [constitution](../../constitution.md), sections 1.2-1.6, fix implementation
  scope, authentication and quality gates.
- [Approved domain invariants 10-13](../architecture/domain-scenarios.md) define
  administrator scope, certificate eligibility, ownership and configuration.
- The [specification](../../especificação-preliminar-sistema.md), sections 7-8,
  permits a representative subset but requires integrated behavior and testing.
- No production security certification, throughput result or delivery guarantee
  is established by these documents.

## Consequences

The prototype can demonstrate catalog, payment-dependent registration, failure
recovery, check-in/certificates and reports across Java/Python. It cannot claim
real financial settlement, production user lifecycle, multichannel delivery,
percentage attendance measurement or high availability. Frozen admission
capacity is the additional user-approved limitation in
[ADR-002](ADR-002-data-ownership.md). Business-rule changes require human approval
and versioned decision/rationale updates, not quiet mock behavior changes.

**Known limitation — paid cancellation refund (revision 1.1.0).** The refund
workflow for a *confirmed paid* registration is designed but not implemented in
Registration. The rule, contract and Billing mock exist:
[paid-registration-flow.md](../architecture/paid-registration-flow.md), the
`202`/`refundStatus` contract in `registration.yaml`, the `REFUND` Billing
message, and `CANCELLATION_WINDOW_DAYS`/`REFUND_PERCENTAGE` settings. Registration,
however, rejects that cancellation with `409 REFUND_WORKFLOW_REQUIRED`. It does
not request the refund, change `refundStatus`, release the seat or promote the
waiting list. The participant keeps the paid seat. This fails closed: a paid
seat is never freed without a refund. Free registrations, and paid ones still
`PENDING_PAYMENT` or `OFFERED`, cancel normally. The terminal mixed-load scenario
therefore cancels only free seats. Implementing the workflow requires storing
the approved payment ID, a cancellation-window check, a `REFUND` outbox request,
`REFUND` result handling with unlimited retry, and a single idempotent seat
release with FIFO promotion. That is recorded as follow-up work, not done here.

## Validation and follow-up

T019/T020 test invalid identity, participant ownership, cross-organization denial,
administrator limits and protected internal APIs. Later acceptance tests must
start four real services and three mocks with real PostgreSQL/RabbitMQ and
exercise success/failure paths; unit doubles do not substitute for integration.
Retain concurrency, contract, architecture, configuration and mutation gates
(at least 95% domain mutation score) from the constitution. Load baselines,
normal/20-times profiles and numeric latency criteria must be recorded before
the load experiment; none are invented or claimed measured here.
T009 supplies the JSON rationale before downstream implementation proceeds.

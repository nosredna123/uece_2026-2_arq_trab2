# ADR-001: Service boundaries by business capability

**Status:** Accepted for the prototype\
**Date:** 2026-11-09\
**Version:** 1.0.0\
**Traceability:** RF01-RF11, RQ01-RQ03, RQ05, RQ08; plan 1.3 / T008

## Context

EventHub has uneven load: catalog and registration may receive 20 times normal
traffic while certificate issuance remains nearly idle. Financial integration,
notification delivery, and reports must not become one registration transaction.
The assignment asks for justified candidate boundaries, not one service per RF.

## Alternatives

| Alternative | Benefits | Costs / reason not selected |
|---|---|---|
| One modular monolith | Simplest deployment and local transactions. | Does not demonstrate the required independently deployable, heterogeneous services or selective scaling. |
| One service per requirement | Small individual endpoints. | Splits tightly coupled capacity, registration, expiry and waitlist rules; adds network transactions without independent capabilities. |
| Seven capability boundaries, four implemented and three mocked | Separates load and failure domains while keeping atomic registration rules together. | Requires explicit contracts, asynchronous recovery and a clear distinction between mocks and real services. |

## Decision

Use the seven boundaries in the [context map](../architecture/context-map.md)
and [ownership table](../architecture/service-boundaries.md):

- Event Management owns event/activity authoring, lifecycle and the public catalog.
- Registration owns admissions, occupied capacity, payment/refund state, expiry,
  cancellation, FIFO offers and outbox. Do not split these into separate services.
- Attendance & Certification combines idempotent check-in and certificate
  eligibility because they share presence evidence and lifecycle.
- Reporting owns its event-fed, eventually consistent read model.
- Identity & Access, Billing and Notification remain contract-backed executable
  mocks in this prototype, not implemented production domain services.

Each real service is independently buildable/deployable and owns its migrations.
Java/Spring Boot implements Event Management and Registration; Python/FastAPI
implements Attendance & Certification and Reporting. No shared domain library
or cross-service SQL is permitted. APIs and versioned messages are the boundaries.
Notification and Reporting are independent downstream consumers, never
synchronous gates in a registration transaction.

Apply KISS inside each boundary: cohesive domain logic for Registration's state
machine; no mandatory heavyweight tactical DDD layers for simple CRUD or mocks.

## Evidence and justification

- The [specification](../../especificação-preliminar-sistema.md), sections 3-7,
  requires heterogeneous services, selective scaling and failure isolation.
- [Approved domain invariants](../architecture/domain-scenarios.md) keep capacity,
  expiry, payment and FIFO promotion under one authority.
- The [paid flow](../architecture/paid-registration-flow.md) demonstrates why
  Billing, Notification and Reporting cannot share the admission transaction.
- [Plan](../../plan.md), phases 1 and 5, and
  [constitution](../../constitution.md), section 1.2, already fix the four/three
  implementation split. This ADR records the trade-off; no graph optimizer,
  team-capacity measurement or performance experiment has been run.

## Consequences

- Catalog and Registration can scale separately from certificates and reports.
  Their scaling benefit remains a hypothesis until the planned load tests.
- Registration has a deliberately larger transactional responsibility, avoiding
  distributed locking for each slot transition.
- Contracts, projection lag and replay become operational responsibilities.
- Mocks demonstrate integration behavior but prove neither production identity
  security nor real payment/delivery reliability.

## Validation and follow-up

T010-T012 define contracts and data ownership; T017/T018 and the later integration
gates must reject cross-boundary imports/SQL and verify Java/Python interoperability.
T009 supplies the corresponding versioned JSON rationale; it is not delivered
by T008. Revisit this ADR if load evidence or a changed capability justifies a
different boundary, preserving the previous decision history.

# ADR-006: Bounded Python demonstration web app

**Status:** Accepted for the presentation prototype  
**Date:** 2026-11-10  
**Version:** 1.1.0
**Traceability:** REQ-001, REQ-002, REQ-003, REQ-015, REQ-017, REQ-019,
REQ-021; [webapp plan](../webapp/plan.md), plans 0.1, 1.1, 2.1, 5.3-5.5,
6.1-6.3

## Context

The existing EventHub prototype needs a clear, local presentation client for
its real catalog, registration, attendance and reporting APIs. The client must
make service-backed behavior distinguishable from simulated dependencies and
presentation-only examples. It must not weaken backend authorization or add
domain policy, persistence, or infrastructure merely to support a user
interface.

The agreed demonstration includes synthetic personas and a bounded Scenario
Lab. Neither is production identity nor a general-purpose load generator.
Presentation-layer code is not part of the domain-core mutation-score
denominator; it still requires focused tests and does not change any existing
domain quality gate.

## Alternatives

| Alternative | Benefits | Costs / reason not selected |
|---|---|---|
| Browser SPA with a Node build/runtime | Rich client-side interactions. | Adds a toolchain and runtime not needed for this didactic local flow. |
| Add UI routes and state to domain services | Fewer containers initially. | Couples presentation lifecycle and state to services that own domain behavior and data. |
| Add a gateway and shared session database | Supports centralized routing and multi-replica sessions. | Expands infrastructure and creates new state/ownership concerns without a prototype requirement. |
| Independent Python server-rendered presentation client | Small stack, explicit HTTP boundaries, easy local operation. | Sessions are intentionally process-local and are lost on restart. |

## Decision

Build an independent local Python web application using FastAPI, Jinja2,
HTTPX, plain CSS and minimal locally served JavaScript. It calls existing
service APIs; it does not import shared domain code, access service storage,
consume the broker, or add a generic gateway. No Node/npm toolchain or extra
host Python installation is required.

Every page has a prototype notice. Pages explain in plain text which
service owns each action and which dependencies are mocks. Earlier revisions
also showed per-section provenance badges (**LIVE SERVICE**, **SIMULATED
DEPENDENCY**, **DEMO PRESENTATION**), a label guide, and implementation/
operational status indicators; these were removed at the product owner's
request so the internal pages stay focused on the core business flow.
Error messages still name the failing service and correlation id.

Visitor, Participant 1, Participant 2 and Organizer are synthetic presentation
personas, not user accounts or authentication. Persona identity and session
state stay server-side; the browser receives only an opaque session
identifier. Protected requests still use the existing identity and service
authorization contracts. CSRF protection, participant isolation, bounded
session contents and expiry are required.

Use bounded, expiring in-memory sessions in one Uvicorn worker/replica. Do not
add Redis, a database, or durable session state. Restart may lose presentation
state; multiple workers/replicas are unsupported and must not be enabled as a
silent scaling option. Browser mutations call existing APIs with their
established authorization and idempotency behavior; uncertain outcomes remain
explicitly uncertain rather than being represented as a successful fallback.

The Scenario Lab is opt-in and limited to fixed, finite scenarios and an
explicitly bounded synthetic identity pool. It exercises existing service
APIs and reports observations; it does not modify domain data outside those
APIs, expose arbitrary traffic generation, or claim production load evidence.
Its twenty fixture identities are unique, unscoped participants and are
accepted by the identity mock only when both the mock and web client are
explicitly configured for the pool. The fixed fixture is size-bounded and
validated at startup; its tokens remain server-side and are never included in
browser responses or exported observations. The pool adds no credential
minting endpoint and grants no organizer or administrator authority.

The presentation adapter, templates and browser behavior are excluded from
the domain-core mutation-score calculation because they contain no domain
policy. This is not a waiver of test quality: adapter contracts, security
boundaries, browser behavior and failure states require focused tests, while
existing domain tests and the minimum 95% domain mutation gate remain intact.

## Evidence and justification

- The [webapp plan](../webapp/plan.md) records the agreed requirements,
  architecture boundaries, session limits, test gates and Scenario Lab scope.
- [Constitution](../../constitution.md), sections 1.2-1.7, requires KISS,
  service data ownership, fail-closed authorization, validated configuration,
  traceable tests and retained domain quality gates.
- [ADR-001](ADR-001-service-boundaries.md),
  [ADR-002](ADR-002-data-ownership.md),
  [ADR-004](ADR-004-messaging-and-resilience.md), and
  [ADR-005](ADR-005-prototype-scope.md) retain authority for service
  boundaries, data ownership, messaging, authorization and prototype scope.
- Existing HTTP contracts remain authoritative; this decision adds no domain
  endpoint or business rule.

## Consequences

The UI can guide a local demonstration while clearly identifying which facts
come from real services, simulated dependencies or presentation-only content.
The single-process session model is simple to run and test, but state is
ephemeral and horizontal scaling is out of scope. The service remains the
authority for every business transition and protected resource.

Presentation-specific tests provide evidence for the adapter and browser, but
do not establish domain mutation coverage or production-scale performance.
Domain invariants and all previously established test gates remain unchanged.
Any future proposal that changes an invariant or expands service/data
ownership requires human arbitration and versioned updates to affected ADRs
and rationales.

## Validation and follow-up

T046 validates rationale inventory and governance without weakening existing
checks. Subsequent tasks test settings and sessions, HTTP contracts, CSRF,
persona isolation, browser behavior, fixed Scenario Lab bounds and the
existing service regression suite. T070 adds fail-closed validation of the
opt-in, finite participant pool without changing service contracts. No
production identity, multi-worker session support, or general-purpose
load-testing capability is implied.

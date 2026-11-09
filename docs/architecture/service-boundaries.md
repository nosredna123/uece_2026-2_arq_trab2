# EventHub service boundaries

These accepted prototype boundaries follow [ADR-001](../adr/ADR-001-service-boundaries.md) and the business-approved
[domain scenarios](domain-scenarios.md). It distinguishes service ownership
from deployment: local containers may share PostgreSQL and RabbitMQ, but no
service may read another service's tables.

## Seven candidate boundaries

| Candidate / implementation | Owns and does | Explicitly does not own |
|---|---|---|
| **Identity & Access** — mock | Synthetic principals, credentials/tokens, roles (`participant`, `organizer`, `global administrator`), and organization membership for integration scenarios. Reject invalid identity. | No production identity lifecycle, user-management domain, or independent identity service. The approved global admin may manage events/activities and read reports across organizations, but cannot administer users or organizations. |
| **Event Management** — executable Java/Spring Boot | Event and activity definitions, organization association, lifecycle (create/edit/publish/cancel), schedules, location/responsibility, and public published catalog. | Registration/payment state, reservation concurrency, attendance, certificates, and reporting projections. |
| **Registration** — executable Java/Spring Boot | Registration state, reservation/occupancy accounting, payment and refund pending state, cancellation, FIFO waitlist and offers, expiry, idempotency, and transactional outbox. Enforce that occupied capacity never exceeds activity capacity. | Event authoring, credentials, direct Billing/Notification side effects, attendance/certificates, and reporting. |
| **Billing** — mock | Consume payment/refund requests and produce configured approval, refusal, or timeout results. Expose pending/failure scenarios for reproducible integration tests. | A real provider integration, ledger, financial accounting, or authority to confirm registrations. Registration applies results and enforces the approved state transitions. |
| **Notification** — mock | Consume relevant events, record simulated deliveries, and expose available/unavailable and reprocessing behavior. | Synchronous participation in registration transactions or authority over registration state. Its failure cannot roll back a successful registration. |
| **Attendance & Certification** — executable Python/FastAPI | Consume registration facts; own check-in and certificate records; apply the approved prototype eligibility rule (confirmed registration, one idempotent check-in, activity ended). Publish attendance/certificate facts for consumers. | Registration/payment mutations, event source data, and reporting queries. The proposed 80% attendance threshold is explicitly out of prototype scope; one check-in suffices. |
| **Reporting** — executable Python/FastAPI | Consume registration, payment, attendance, and certificate facts into its own eventually consistent projection; expose indicators authorized to the organizer's organization or global administrator. | Source-of-truth registration/event state, transaction decisions, or direct reads of another service's database. Reporting downtime cannot block registration. |

## Executable services and data ownership

The four implemented services each own a separate logical database,
database principal/credentials, migrations, and persistence lifecycle within
the shared local PostgreSQL cluster:

| Service | Owned data (logical) | Schema owner |
|---|---|---|
| Event Management | Events, activities, publishing/lifecycle state, schedules, organization association, configured capacity and eligibility metadata. | Event Management only |
| Registration | Registration and reservation states, occupied-capacity ledger, payment/refund attempts, waitlist order/offers, idempotency records, outbox. | Registration only |
| Attendance & Certification | Consumed registration eligibility facts, check-ins, activity completion/eligibility facts, issued certificates, consumer deduplication state. | Attendance & Certification only |
| Reporting | Event-fed registration/payment/occupancy/attendance/certificate projection, source-event offsets/deduplication state. | Reporting only |

This table states logical ownership, not a physical table design.
[ADR-002](../adr/ADR-002-data-ownership.md) selects separate logical databases and
a capacity snapshot frozen by Event Management before Registration opens
admission. [ADR-003](../adr/ADR-003-registration-consistency.md) selects per-activity
row locking for Registration's ledger and FIFO transitions. Provisioning details
and exact event/API contracts remain for T010-T012. Cross-service SQL, shared
domain tables, and shared credentials are prohibited.

Identity, Billing, and Notification mocks may keep only the minimal synthetic
or diagnostic state required by their contracts. They do not gain ownership of
the four executable services' domain data.

## Contract and dependency rules

1. **Synchronous HTTP:** use documented APIs for public catalog, protected
   commands/queries, and any required cross-service lookup. Never reach through
   an API to a service's database.
2. **Asynchronous events:** use RabbitMQ contracts for payment/refund requests
   and results, registration facts, attendance, certificates, notifications,
   and projections. Include the approved correlation and idempotency data in
   contract design (T010/T011/T042).
3. **Registration is the reservation authority:** Event Management owns
   configured activity metadata; Registration owns reservations and occupied
   slots. A request to register must use an explicit, agreed activity/capacity
   contract; no cross-database read is allowed. ADR-002/003 settle snapshot
   initialization, capacity immutability and local atomic admission; schemas
   and concrete states remain contract work.
4. **Authentication is not authorization:** the Identity mock supplies
   synthetic identity/role/organization facts; each protected service enforces
   participant ownership, organizer organization scope, or the approved global
   administrator scope.
5. **Failure isolation:** Notification and Reporting are downstream consumers,
   not synchronous conditions for a successful registration. Billing outcome
   is different: paid registration cannot confirm without approval.
6. **Configuration:** business policy values (reservation/offer durations and
   refund window/percentage) are external, validated configuration; they are
   not duplicated in service-specific code defaults. Global policy override
   per event is not approved.

## Deliberately outside this prototype

- Production identity management, payment provider integration, and delivery
  channels.
- A global API gateway, Kubernetes, a separate domain service per requirement,
  shared cross-service domain library, or shared database schema.
- Percentage-based certificate attendance measurement; approved eligibility
  requires a confirmed registration, one check-in, and completed activity.

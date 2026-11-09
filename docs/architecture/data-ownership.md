# Data ownership, state transitions and recovery

**Version:** 1.0.0\
**Traceability:** RF01-RF11, RN01-RN07, RQ04-RQ07; plan 2.1 / T012

This logical model implements [ADRs 002-004](../adr/ADR-002-data-ownership.md)
and the [approved domain baseline](domain-scenarios.md). Concrete migrations
belong to each service's implementation task; these are not shared SQL tables.

## Physical and access boundaries

| Runtime owner | Database | Logical entities | Migration authority |
|---|---|---|---|
| Event Management | event_management | event, activity, admission_snapshot, command_receipt, outbox | Flyway, Event Management only |
| Registration | registration | activity_ledger, registration, waitlist_entry, payment_operation, billing_attempt, refund_obligation, compensation_case, command_receipt, inbox, outbox | Flyway, Registration only |
| Attendance & Certification | attendance_certification | activity_projection, registration_projection, check_in, certificate, command_receipt, inbox, outbox | Alembic, Attendance only |
| Reporting | reporting | activity_projection, registration_projection, billing_operation_projection, check_in_projection, certificate_projection, inbox | Alembic, Reporting only |

Use separate database credentials restricted to the owner's database/schema.
Revoke default PUBLIC connection privileges where necessary; never use the
cluster bootstrap/superuser credential for runtime services. Migration roles
may perform DDL only inside the owner's database; runtime roles need DML, not
cross-database rights or unrestricted provisioning. Database ownership does not
make arbitrary cross-database grants acceptable. Do not export database handles,
entity models or migration modules to another service.

Mocks hold minimal durable synthetic/diagnostic state in their own mounted
stores: Identity uses configured synthetic fixtures; Billing stores operations,
attempt results and pending outgoing messages; Notification stores delivery/
failure records keyed by source event. They never use real-service credentials.
Separate test runs use isolated databases/volumes and synthetic principals.

## Event Management

- `event`: UUID id, immutable organizationId, title/description, startsAt/endsAt,
  status DRAFT/PUBLISHED/CANCELLED and optimistic metadata version.
- `activity`: UUID id, eventId FK within this database, schedule/location/
  responsible, configured capacity, integer price/currency, metadata version.
  Start precedes end and lies inside the parent event's period.
- `admission_snapshot`: unique activityId, snapshotId, snapshotVersion and frozen
  capacity. Freeze and competing capacity edits serialize on the same activity
  row. A retry returns the same snapshot even after a failed Registration commit;
  current publication/time eligibility is still revalidated.
- Parent publication/cancellation and schedule edits emit full `activity.changed`
  snapshots for affected activities in the same local transaction/outbox.
  Updating a parent period must not strand existing activity dates outside it.
- Metadata price changes apply to future validated registrations, not historical
  charges/refunds. Registration stores the accepted amount/currency and request
  time immutably. Activity updates propagate independently to local projections.

The internal admission operation validates publication and that the activity
has not started. It returns before Registration takes its ledger lock. As stated
in the [HTTP contract](../contracts/README.md), this is not a distributed lock:
cancellation after validation can race with an already validated request.
No automatic event-wide refund rule is inferred from participant cancellation.

## Registration records and constraints

| Record | Identity and constraints | Purpose |
|---|---|---|
| activity_ledger | activityId PK, snapshotId/version, frozen_capacity > 0, occupied between zero and capacity | Single row lock serializes all local slot effects. Snapshot conflict is an error. |
| registration | UUID id; participantId, activityId, organizationId; status, version, createdAt, accepted price/currency, deadlines | Authoritative enrollment and immutable purchase facts. |
| waitlist_entry | registrationId unique; activityId plus sequence unique | FIFO sequence assigned under the activity lock, not client time. |
| payment_operation | operationId PK; registrationId, kind, amount/currency, originalPaymentId for refund | Stable business operation across retry attempts. |
| billing_attempt | attemptId PK, operationId FK, source eventId, outcome | Distinguishes business retry from transport redelivery. |
| refund_obligation | one per refundable registration/payment; pending/approved, next attempt time | Durable unlimited retry obligation until approval. |
| compensation_case | unique late/conflicting financial outcome identity; reason, operation, status | Manual compensation never reopens a released slot. |

Enforce one active enrollment per participant/activity with a partial unique
constraint covering WAITLISTED, OFFERED, PENDING_PAYMENT and CONFIRMED.
Cancelled/refused/expired records remain historical; a new request may create a
new registration, not mutate the old attempt or its idempotency receipt.
Do not delete confirmed history just because the activity has ended.

Every transition locks activity_ledger first, then registration/offer rows.
Ledger initialization uses a unique activity key and validates the winner's
snapshot before continuing. Database READ COMMITTED and current reads after
the lock avoid stale ORM state. Check constraints backstop occupancy, but do
not replace transaction discipline. Foreign keys stay inside this database.

### Occupancy and registration state machine

| Current state / trigger | Next state | Occupancy effect |
|---|---|---|
| New free request with admission | CONFIRMED | +1 |
| New paid request with admission | PENDING_PAYMENT | +1, persist payment request and deadline |
| New request, full, waitlist enabled | WAITLISTED | zero; append FIFO |
| New request, full, waitlist disabled | explicit 409; no active enrollment | zero |
| WAITLISTED / eligible FIFO promotion | OFFERED | +1, offer deadline |
| OFFERED / accept valid free offer | CONFIRMED | zero, same slot |
| OFFERED / accept valid paid offer | PENDING_PAYMENT | zero, new payment operation/deadline |
| OFFERED / expiry | EXPIRED | -1 then atomically offer next eligible FIFO entry |
| PENDING_PAYMENT / valid correlated approval before deadline | CONFIRMED | zero |
| PENDING_PAYMENT / refusal | REFUSED | -1 then FIFO promotion |
| PENDING_PAYMENT / deadline reached | EXPIRED | -1 then FIFO promotion |
| WAITLISTED / own cancellation | CANCELLED | zero |
| OFFERED or PENDING_PAYMENT / own cancellation | CANCELLED | -1 then FIFO promotion; in-flight paid approval needs compensation |
| CONFIRMED free / eligible own cancellation | CANCELLED | -1 then FIFO promotion |
| CONFIRMED paid / eligible cancellation request | CONFIRMED, refund PENDING | zero |
| CONFIRMED, refund PENDING / refund refusal or timeout | unchanged | zero; schedule another attempt without limit |
| CONFIRMED, refund PENDING / refund approval | CANCELLED, refund APPROVED | -1 then FIFO promotion |
| Terminal / duplicate or stale command/result | unchanged | zero; replay receipt, reject conflict or record compensation |

OFFERED, PENDING_PAYMENT and CONFIRMED occupy one slot each. WAITLISTED,
REFUSED, EXPIRED and CANCELLED occupy none. Refund pending is an orthogonal
condition, not a second occupied status. Free registration does not generate
Billing requests; no fake financial approval is needed.

At/after a reservation deadline (`clock_timestamp() >= expiresAt`, evaluated
after row locking), approval cannot confirm even if expiry cleanup has not run.
Approval before deadline must commit its valid state transition atomically.
Expiry workers and result consumers share the same activity lock. Duplicate
messages never double-release, double-promote or reclaim a FIFO slot.

Declining an offer uses the cancellation endpoint; expiry advances the FIFO.
Offer acceptance revalidates current activity eligibility before the local
transaction and verifies the owned offer is still valid under the lock.
Use captured policy values for each created deadline; a configuration change
does not retroactively rewrite persisted reservation/offer deadlines.

For paid cancellation, persist createdAt and compare the request against the
configured window and current validated activity start. Only an eligible request
creates a refund obligation. Historical payment amount/currency determines the
refund, rounded half-up in minor units using decimal/integer arithmetic.
Correlation checks include operation, attempt, registration, kind, amount and
currency, not only a guessed registration ID.

## Idempotency, inbox and outbox

| Owner record | Unique key | Atomic relationship |
|---|---|---|
| Public command_receipt | authenticated subject + operation/target + Idempotency-Key | Canonical validated request hash, original response status/body and business mutation commit together. |
| Consumer inbox | consumer identity + eventId | Payload hash, durable outcome and local state/outbox commit before acknowledgment. |
| Producer outbox | eventId; aggregate/version/eventType where appropriate | Immutable envelope/payload with business state; delivery metadata changes separately. |
| Billing mock effect | operationId, with unique attemptId | One successful charge/refund per business operation; attempts persist their original simulated outcome. |
| Notification mock delivery | source eventId | At most one successful simulated delivery, failures remain diagnosable/retryable. |

Authorize before returning a stored public response. A key replay with changed
payload is 409; a duplicate eventId with changed content is quarantined rather
than considered processed. Concurrent requests for a key serialize/resolve
through its database uniqueness. No receipt is committed for a rolled-back
mutation. An in-progress retry must not return a fabricated completed response.

Outbox workers use persisted retry times/attempt metadata and safe claiming to
avoid concurrent publication of one item; duplicates are still possible after
confirm/crash. No network I/O occurs while holding the capacity ledger lock.
After confirm, update publication metadata; an unroutable/failed send stays
pending. An expired unsent payment request is durably suppressed and logged;
already in-flight requests may still yield a late compensation case.

## Attendance and Reporting projections

Activity and registration snapshots carry producer-local increasing versions.
Store the highest complete snapshot per aggregate; reject same-version differing
content, ignore older snapshots observably and buffer incomplete joins.
Do not infer global ordering across producers.

Attendance checks the principal owns the projected confirmed registration.
Use a unique check_in.registrationId and unique certificate.registrationId;
one check-in and one certificate survive duplicate requests/events. Certificate
requires confirmed status, check-in and ended activity from the latest locally
known activity facts, not an unverified caller date. Missing projection returns
explicit not-ready, never invented eligibility. Source updates are eventually
consistent; these checks do not claim a cross-service instantaneous read.

Reporting stores latest enrollment state and distinct financial operations,
check-ins and certificate facts. Counts are derived/replaced idempotently,
not incremented for each delivery. Scope every query by authenticated
organization, except the approved global administrator. Report timestamps
describe projection progress, not a guarantee of upstream freshness.

## Migration and retention policy

Use forward, versioned owner-local migrations, validated against a clean
database and an upgrade from the previous version. Never edit an applied
migration checksum or call automatic destructive schema recreation in runtime.
Test fixtures may reset only their explicitly isolated databases/volumes.

The prototype has **no automatic business-data/message deduplication purge**.
Keep command receipts, processed identities, financial results, suppression/
compensation cases and projection versions for the lifetime of the prototype
dataset. Never expire outstanding refund work, undelivered outbox or quarantined
messages. Disk growth is monitored and is a declared local-demo limitation.

Any later retention/archival feature requires a versioned decision defining a
replay horizon first, compatible retention for outbox/inbox/receipts and Billing
effects, and an approved deletion procedure. Do not arbitrarily remove inbox
identities while old events can still be replayed. A documented test reset
destroys the complete isolated dataset consistently; it is not runtime retention.

## Validation obligations

Later migrations and integration tests must prove data-access denial across
service credentials, unique-key races, rollback/commit-before-ack, counter
invariants, last-slot/FIFO races, deadline boundaries, late compensation and
projection replay. [HTTP](../contracts/openapi) and
[message](../contracts/asyncapi/eventhub.yaml) schema tests only validate contracts.

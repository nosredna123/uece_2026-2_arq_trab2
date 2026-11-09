# Messaging and mock contract v1

[eventhub.yaml](eventhub.yaml) uses AsyncAPI 2.6.0 with Draft-07
[JSON Schemas](../schemas). All messages use the common envelope. The broker
topology is durable, not exclusive and not auto-delete; messages use persistent
delivery mode 2, `eventId` as AMQP message ID and `correlationId` as correlation
ID. The logical exchange is `eventhub.v1` (topic). Deployment may namespace
topology for isolated test runs, consistently for all producers/consumers.

## Queues and subscriptions

| Durable queue | Routing keys | Consumer |
|---|---|---|
| billing.v1 | billing.requested | Billing mock |
| registration.v1 | billing.result | Registration |
| attendance.v1 | registration.changed, activity.changed | Attendance & Certification |
| reporting.v1 | registration.changed, activity.changed, billing.result, attendance.recorded, certificate.issued | Reporting |
| notification.v1 | registration.changed, activity.changed, attendance.recorded, certificate.issued | Notification mock |

Each queue has a separate durable `<queue>.quarantine` queue; failed delivery
must preserve original event identity/body and diagnostic metadata. An explicit
confirmed re-publication followed by acknowledgment is acceptable; unreliable
dead-letter forwarding must not lose the original message. Permissions isolate
publishers to owned routing keys and consumers to assigned queues. Provision all
bindings before producers start; mandatory returns alone cannot detect one
missing subscriber if other queues still match.

## Identity, ordering and projections

The envelope version is `1.0.0`. Reject incompatible major/schema versions for
repair; never silently strip unknown business fields. Keep event IDs unchanged
on transport retries. Aggregate versions are increasing per producer and
aggregate, not global sequence numbers.

- `registration.changed` is a full snapshot keyed by registrationId. It feeds
  eligibility, occupied counts and simulated notices; status changes include
  waiting, offered, paid pending, confirmed, refused, expired and cancelled.
- `activity.changed` is a full activity lifecycle snapshot. Emit on publish,
  relevant edits and event cancellation (for each affected activity), so
  Attendance receives schedule changes without cross-database reads.
- For these full snapshots, compare the version per aggregate/producer and
  replace only with a newer version; earlier complete snapshots can be skipped
  observably. Same-version conflicting content is quarantined. Missing joined
  activity/registration facts remain pending, never fabricated.
- `attendance.recorded` and `certificate.issued` are immutable unique business
  facts. Deduplicate by registration/certificate identity as well as event ID.
  Their aggregate is registrationId with a producer-local increasing version;
  neither fact may be discarded merely because the other arrived first.
- Billing uses operationId as aggregateId. Correlate result operation/attempt,
  kind, registration, amount and currency against a durable request. Event
  timestamps or stream order do not override Registration's locked state/deadline.
- Reporting derives occupancy from latest registration snapshots; it does not
  increment/decrement blindly per delivery. Payment/refund counts are distinct
  business operations with outcome state, not message/attempt counts. A later
  successful refund does not count each previous refusal as another refund.
  Late approved payments are visible financial outcomes, not confirmed slots.

All envelopes propagate original correlation identity. `occurredAt` is UTC;
UUID formats and payload schemas are validated before business processing.
Projection timestamps and outbox/quarantine lag are observable.

## Billing MOCK behavior

`BILLING_MOCK_SCENARIO` selects `APPROVED`, `REFUSED` or `TIMEOUT`; a separate
refund scenario may override it for reproducible refund recovery tests. Scenario
changes occur through controlled restart/configuration, not an anonymous API.

Persist received requests/attempts and outbox results. PAYMENT requests require
expiresAt; REFUND requests require originalPaymentId. Same attempt redelivery
replays its original result (or lack of result), not a new random outcome.
Successful operation IDs cannot charge/refund twice even if a different attempt
is submitted. Conflicting amount/currency/registration for one operation is an
observable invalid request, not a second charge.

TIMEOUT deliberately publishes no result; persist the simulated timeout so it
is diagnosable. Registration expires payment reservations by its clock. A later
refund attempt has a new attemptId and eventId, the same refund operationId and
originalPaymentId. Refusal/timeout can be followed by approval after scenario
change; successful operation deduplication remains stable. Refund amount uses
integer minor units rounded half-up from the configured percentage, never binary
floating point. A zero rounded refund is allowed; payments must be positive.

## Notification MOCK behavior

Consume independently, derive the simulated notice from registration/activity/
attendance/certificate facts, and persist the source event ID, simulated outcome
and correlation identity. Duplicate input cannot duplicate a simulated delivery.
`NOTIFICATION_MOCK_AVAILABLE=false` records diagnostic failure and leaves work
retryable, never marks it delivered. A successful replay after recovery records
one simulated delivery. No real email, SMS or participant credentials are used.

## Recovery and retention

Outbox and inbox commits, publisher confirms and commit-before-ack follow
[ADR-004](../../adr/ADR-004-messaging-and-resilience.md). Configurable backoff is
capped in delay, not silent abandonment. Quarantine requires explicit repair/
replay and visibility; unlimited refund obligations survive it. No message TTL
may silently erase a business obligation. Retention and concrete persistent
records are T012; operational values are external validated T016 settings.

Validate payload schemas and fixtures with `python -m pytest -x` after installing
the repository validation requirements. For full AsyncAPI document validation
use `@asyncapi/parser@3.4.0` with `parse` and the file path as `source`; fail on
severity-0 diagnostics. This document and payload checks are not runtime evidence.

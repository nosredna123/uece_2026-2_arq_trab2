# Paid registration: end-to-end flow and failures

**Domain baseline:** [approved scenarios](domain-scenarios.md), version 1.1.0\
**Service boundaries:** [context map](context-map.md) and
[service boundaries](service-boundaries.md)  
**Traceability:** RF04–RF08, RN01–RN05, RQ03–RQ06

## Normal flow

```mermaid
sequenceDiagram
  autonumber
  actor P as Participant
  participant EM as Event Management
  participant IAM as Identity & Access mock
  participant REG as Registration
  participant DB as Registration-owned database/outbox
  participant MQ as RabbitMQ
  participant BILL as Billing mock
  participant NOTIF as Notification mock
  participant ATT as Attendance & Certification
  participant REP as Reporting

  P->>EM: Browse published event/activity
  EM-->>P: Public activity details and configured capacity
  P->>REG: Request paid registration + identity
  REG->>IAM: Validate identity and participant scope
  IAM-->>REG: Valid principal / denied
  REG->>EM: Validate published activity and paid-registration rules
  EM-->>REG: Activity facts / unavailable or ineligible
  REG->>DB: Atomically reserve available capacity + persist payment intent in outbox
  DB-->>REG: Pending-payment reservation, expiry, registrationId
  REG-->>P: Pending payment; not confirmed
  REG->>MQ: Publish outbox payment request (idempotent event identity)
  MQ->>BILL: Deliver configured payment request
  alt Billing approval before reservation expiry
    BILL->>MQ: Approved result
    MQ->>REG: Deliver correlated payment result
    REG->>DB: Confirm only if reservation is still valid
    DB-->>REG: Confirmed, idempotently
    REG-->>P: Registration confirmed
    REG->>MQ: Publish registration/payment facts
    MQ-->>NOTIF: Consume notification event asynchronously
    MQ-->>ATT: Consume registration eligibility event
    ATT->>MQ: Publish attendance/certificate facts when applicable
    MQ-->>REP: Consume registration/payment/attendance facts
  else Billing refuses payment
    BILL->>MQ: Refused result
    MQ->>REG: Deliver correlated payment result
    REG->>DB: Mark refused and release slot once
    DB-->>REG: Refused; slot released
    REG-->>P: Registration refused
    REG->>MQ: Publish terminal registration/payment facts
    MQ-->>NOTIF: Consume notification event asynchronously
    MQ-->>REP: Update own projection asynchronously
  else No result before reservation expiry
    Note over REG,DB: Reservation deadline expires; approval is not inferred.
    REG->>DB: Expire pending reservation and release slot once
    DB-->>REG: Expired; slot released
    REG-->>P: Registration expired
    REG->>MQ: Publish terminal registration/payment facts
    opt Late approval is received
      BILL->>MQ: Late approved result
      MQ->>REG: Deliver correlated late result
      REG->>DB: Preserve expired state; record manual compensation
    end
  end
```

The diagram states business-level interactions, not wire-level contracts. The
implemented request/result envelope and routing keys are defined in the
[AsyncAPI contract](../contracts/asyncapi/eventhub.yaml).
[ADR-002](../adr/ADR-002-data-ownership.md) selects an idempotent, frozen
capacity snapshot from Event Management before admission; capacity cannot
change after activation.
[ADR-003](../adr/ADR-003-registration-consistency.md) selects a per-activity
Registration row lock and atomic state/idempotency/outbox writes.
[ADR-004](../adr/ADR-004-messaging-and-resilience.md) selects publisher confirms,
commit-before-acknowledgment, deduplication and observable retry/quarantine.
No cross-service database access is allowed. Numeric operational settings and
retry delays are validated configuration; HTTP and message wire details are
defined in the [OpenAPI](../contracts/openapi) and
[AsyncAPI](../contracts/asyncapi/eventhub.yaml) contracts.

## State and capacity rules

1. A paid registration request first passes identity and authorization checks,
   then validates that the activity is published and accepts registrations.
2. Registration may create a pending-payment reservation only when its agreed
   admission mechanism can preserve the capacity invariant. The reservation
   occupies one slot and receives the configured expiry (initially 30 minutes).
3. Persist the payment intent and outbox record with the reservation before
   reporting pending status. An unpublishable outbox record is retried or
   surfaced as an operational failure; it must not be reported as a successful
   confirmed registration.
4. Only an approval matching a still-valid reservation may transition it to
   confirmed. Duplicate results are idempotent.
5. Refusal or expiry moves the reservation to a terminal non-confirmed state
   and releases the occupied slot exactly once. If waitlist promotion is
   enabled, offer the released slot in FIFO order with the configured initial
   30-minute offer expiry.
6. A payment approval received after expiry cannot reclaim a released slot or
   confirm the registration. Record the late result for manual compensation.
7. An accepted offer for a paid activity starts a new payment reservation and
   payment expiry; accepting the offer itself is not confirmation.
8. A payment result and downstream notification/reporting events are separate
   concerns: only the Billing outcome determines paid-registration
   confirmation. Notification and Reporting are not synchronous gates for
   that outcome.
9. Registration stores each accepted Billing result in its inbox in the same
   transaction as the state transition. Its event identity is retained for
   deduplication; operation, attempt and correlation identities must match the
   persisted payment request. A duplicate event is acknowledged without
   repeating the transition.
10. The scheduled expiry worker rechecks the deadline with database time after
    locking the activity and registration. It suppresses unpublished payment
    requests and releases/promotes exactly once; a later approval is retained
    as a manual-compensation disposition, never as a confirmation.

## Failure and recovery behavior

| Failure point | Required behavior | Capacity/registration outcome | Recovery / observability |
|---|---|---|---|
| Invalid identity or participant authorization | Reject the protected request; do not treat it as anonymous. | No reservation and no capacity change. | Return an explicit authentication/authorization error; record a correlation ID without exposing credentials. |
| Identity mock unavailable | Fail the protected request explicitly; never skip identity validation. | No reservation and no capacity change. | Report dependency failure and correlation ID; retry the request after the mock recovers. |
| Event Management says activity is unpublished, closed, or ineligible | Reject the request according to the activity rule. | No reservation and no capacity change. | Return an explicit domain response. |
| Event Management unavailable during activity validation | Do not infer publication or eligibility and do not silently use stale/unverified activity data. | No new reservation. | Surface the dependency failure and correlation ID; retry after recovery. Caching/fallback policy is not approved. |
| Capacity is exhausted | Reject, or append the participant to the waitlist when enabled. | No extra occupied slot; never overbook. | Expose the resulting state. Waitlist order and offer policy follow the approved domain scenarios. |
| Registration persistence fails before reservation/outbox commit | Fail the request; do not claim a pending or confirmed registration. | No committed reservation or payment request. | Surface the storage error; ADR-003 requires rollback of the local state/idempotency/outbox transaction. |
| RabbitMQ unavailable after outbox commit | Keep the reservation pending only until its configured expiry; do not confirm it because publication failed. Retry persisted publication under ADR-004; durably suppress unsent expired payment requests. | Reserved capacity remains occupied while the reservation is valid; expiry releases it once. | Expose outbox age/failure and registration/event ID. An in-flight late approval still requires manual compensation. |
| Billing mock unavailable or not responding | Do not confirm without an approval. The pending reservation remains subject to its expiry. | Slot remains occupied until a valid result or expiry; expiry releases it once and may promote the waitlist. | Record request/result timeout and correlation; repeated delivery must be idempotent. Retry delays use validated external configuration. |
| Billing explicitly refuses payment | Apply the refusal once and reject/close the pending registration. | Release the reservation once; offer the slot to the next FIFO participant if waitlist is enabled. | Emit an observable terminal result and relevant downstream event. |
| No Billing result before reservation expiry | Expire the pending reservation. A later approval does not reverse expiry. | Release the slot once; waitlist promotion may proceed. | Record timeout/expiry. A late approval is routed for manual compensation; it cannot consume a subsequently allocated slot. |
| Duplicate or out-of-order Billing result | Apply only a result valid for the current reservation state. | Never double-confirm, double-release, or reclaim an expired slot. | Deduplicate by stable event/registration identity; conflicting late results remain diagnosable. |
| Registration database fails while applying Billing result | Do not acknowledge successful state transition unless it is durably applied. | Existing committed reservation remains pending until safely resolved/expired. | ADR-004 requires local state/deduplication commit before acknowledgment; redelivery is idempotent. Message schemas are defined in the AsyncAPI contract. |
| Notification mock unavailable after confirmation | Do not roll back or delay the confirmed registration. | Registration remains confirmed and occupies its slot. | Retain/observe failed delivery for reprocessing under the later notification contract. |
| Reporting unavailable or projection delayed | Do not block registration, confirmation, or capacity release. | Registration state remains authoritative and unchanged. | Reporting catches up from its event-fed projection; expose projection lag/failure. |
| Attendance & Certification unavailable | Do not block registration. | Registration state remains authoritative and unchanged. | Consumer resumes registration/activity events; idempotency prevents duplicate eligibility facts. |

Dependency failures never turn into success-shaped fallbacks. Each exposed error
or asynchronous failure must retain the registration/correlation identity
needed for diagnosis while excluding secrets. Retry timing, dead-lettering,
and quarantine behavior follow the validated configuration and
[AsyncAPI contract](../contracts/asyncapi/README.md), under the recovery policy
selected by ADR-004; this flow does not invent numeric settings.

## Paid cancellation and refund

For completeness, the approved domain baseline also governs cancellation of a
confirmed paid registration:

- The participant may request cancellation within the configured seven-day
  window after requesting registration and before the activity starts (the
  first limit reached applies).
- The Billing mock is asked to refund the configured 90% of the paid amount.
- Until refund approval, the registration remains confirmed and continues to
  occupy the slot. A refusal/timeout leaves the refund pending and triggers
  automatic retries without a limit; it does not cancel the registration or
  promote the waitlist.
- Once refund approval is applied idempotently, Registration cancels the
  registration and releases the slot once; the next eligible FIFO waitlist
  participant may then receive an offer.

The percentages, time window, persistence of retries, and Billing mock
result-message details are configuration and contract decisions, not constants
to embed in this diagram.

## Validation scenarios

The sequence and failure table are intended to be exercised by:

- paid registration with Billing approval, refusal, and timeout/expiry;
- duplicate and late payment results;
- exhausted capacity, FIFO promotion, offer expiry, and paid-offer payment;
- Identity/Event Management/RabbitMQ/Billing outages at the boundaries above;
- Notification and Reporting outages after registration without rollback;
- cancellation/refund approval and failed refund retry while the slot remains
  occupied;
- concurrent registration proving occupied capacity never exceeds the limit.

These are scenario/test targets, not claims that implementation or integration
tests already exist.

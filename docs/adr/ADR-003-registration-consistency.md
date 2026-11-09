# ADR-003: Serialize activity admission with PostgreSQL row locks

**Status:** Accepted for the prototype\
**Date:** 2026-11-09\
**Version:** 1.0.0\
**Traceability:** RF05-RF07, RN01-RN04, RQ04, RQ06; plan 1.3 / T008

## Context

Reservations, confirmed registrations and active FIFO offers share one finite
capacity. Payment results, expiry workers and participant commands race across
Registration replicas. An in-memory lock or a read-then-insert count cannot
protect this invariant. Billing is external to the database transaction.

## Alternatives

| Alternative | Benefits | Costs / reason not selected |
|---|---|---|
| Count slots, then insert without serialization | Very little code. | Concurrent requests can all observe the same free slot and overbook. |
| Conditional/versioned updates | Can atomically enforce a counter limit. | Multi-record FIFO, expiry and refund transitions still require coordinated retries and consistent lock ordering. Viable future alternative if measured contention justifies it. |
| Per-activity PostgreSQL row lock | One clear transaction boundary for ledger and FIFO state. | Serializes a hot activity and requires short transactions and bounded lock waits. |
| Distributed lock, two-phase commit or saga platform | Can coordinate multiple stores. | Adds failure modes and infrastructure; all slot authority already resides in one Registration database. |

## Decision

Use a Registration-owned ledger row per activity, initialized under
[ADR-002](ADR-002-data-ownership.md). Every capacity-affecting transition locks
that row with `SELECT ... FOR UPDATE` before reading/mutating reservation,
offer, queue or occupancy state. Use PostgreSQL READ COMMITTED with this
serialization; an ORM read must occur after lock acquisition, not from a stale
pre-lock cache. All paths use the same lock order: activity first, then its
registration/offer records. Do not hold database locks during HTTP or broker I/O.

Within one local transaction:

- Validate the current state and idempotency key/payload; identical retries
  return the recorded outcome, while key reuse for a different command conflicts.
  Unique constraints prevent duplicate active enrollment and duplicate effects.
- Enforce `0 <= occupied <= frozen_capacity`; occupied includes confirmed
  registrations, pending-payment reservations and active slot-holding offers.
  Waiting without an offer does not occupy a slot.
- Create/free/transfer a slot, persist state and stable event identities, and
  append outbox records atomically. Commit before reporting successful mutation.
  Database constraints backstop the counter; only ledger-locked paths may update
  it. Rollback includes idempotency and outbox writes.
- On release, promote the next eligible FIFO entry in the same transaction, so
  a new request cannot bypass an already queued eligible participant.
  Expiry/refusal of an offer advances the queue exactly once.

Free admission confirms without inventing a Billing approval. Paid admission
creates a pending reservation with its configured deadline. Accepting a valid
paid offer transfers its occupied slot into a new payment reservation without
incrementing occupancy twice; accepting a free offer confirms it.

For payment-result/expiry races, acquire the same activity lock and then evaluate
the persisted deadline using the database's current wall clock, not a client
timestamp or transaction-start timestamp captured before waiting for the lock.
Only a correlated approval applied while `now < expires_at` can confirm a pending
reservation. At or after the deadline, expire/release once and record an approval
as manual compensation; a provider timestamp before expiry cannot resurrect it.
Duplicate/conflicting results cannot reverse terminal states.

Expiry workers must apply this deadline check even when no Billing message
arrives. Delayed cleanup never makes expired reservations confirmable. While
cleanup is delayed, retained occupancy is conservative, not extra free capacity.

Paid cancellation keeps the registration confirmed and occupied until a
correlated refund approval. Persist unlimited automatic refund retries; only
approval cancels/releases/promotes once. A duplicate old refund result must not
be confused with another business operation. Preserve the approved configurable
seven-day window, 90% refund and before-activity-start limit.

## Evidence and justification

- [Approved invariants 1-7](../architecture/domain-scenarios.md) require atomic
  capacity, FIFO, refund and idempotent state transitions.
- The [paid flow](../architecture/paid-registration-flow.md) separates pending
  reservation from confirmed registration and forbids late slot reclamation.
- On 2026-11-09 the user explicitly chose per-activity PostgreSQL locking over
  conditional/versioned updates. No throughput benchmark has yet established
  its capacity under 20-times load.

## Consequences

- Multiple Registration replicas share the same database-enforced authority;
  increasing replica count does not create independent slot counters.
- A hot activity serializes mutations. Lock timeouts/deadlocks must be explicit,
  retryable failures with no partial state or false success.
- Snapshot initialization, counter changes and every worker/consumer path must
  follow the same discipline. Counter drift is an observable integrity error,
  not something to repair silently by granting capacity.

## Validation and follow-up

T023-T027 and integration tests must exercise competing last-slot requests,
approval versus expiry at the exact deadline, duplicate keys/results, rollback,
FIFO versus new admission, offer transfer, refund failure/retry and multiple
replicas. Property tests assert the occupancy invariant after every transition.
T012 defines concrete states/constraints; T009 supplies the JSON rationale.

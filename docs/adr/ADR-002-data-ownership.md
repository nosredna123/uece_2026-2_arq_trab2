# ADR-002: Isolated data and a frozen admission-capacity snapshot

**Status:** Accepted for the prototype\
**Date:** 2026-11-09\
**Version:** 1.0.0\
**Traceability:** RF03-RF06, RN01, RQ04-RQ05, RQ07-RQ08; plan 1.3 / T008

## Context

Event Management owns configured capacity, while Registration must enforce it
atomically with its occupied-slot ledger. A fresh HTTP lookup alone cannot prevent
a concurrent capacity reduction from violating RN01. Sharing a PostgreSQL host
does not authorize sharing domain tables or credentials.

## Alternatives

| Alternative | Benefits | Costs / reason not selected |
|---|---|---|
| Shared schema and cross-service SQL | Easy joins and global transactions. | Violates approved data ownership, couples migrations and bypasses service authorization. |
| Separate physical PostgreSQL installations | Strong infrastructure isolation. | Unnecessary local resource/operations cost; logical isolation satisfies the prototype. |
| Separate logical databases and immutable admission snapshot | Local atomic capacity enforcement with explicit ownership. | Capacity cannot be edited after admission activation; availability requires a separate Registration query. |
| Live capacity updates coordinated across services | Allows increases and safe decreases during admission. | Needs fencing, recoverable coordination and occupancy checks across failures; deferred rather than approximated with stale events. |

## Decision

Use one local PostgreSQL cluster with four separate logical databases and
restricted per-service credentials. No runtime principal receives grants to
another service's data or cluster-superuser privileges. Provisioning credentials
are separate from runtime credentials. Each owner runs its own Flyway (Java) or
Alembic (Python) migrations; no cross-database foreign keys or joins.

Ownership follows [service boundaries](../architecture/service-boundaries.md).
Reporting builds its own projection; Attendance stores consumed eligibility and
activity-completion facts. Necessary data crosses explicit contracts, not SQL.

For capacity synchronization:

1. Event Management may edit capacity before admission activation. A protected
   service-to-service activation/validation operation atomically freezes and
   returns an activity's capacity, stable snapshot identifier and version.
   Concurrent edits and activation serialize in Event Management's transaction.
2. The operation must validate publication/eligibility and be idempotent:
   retries return the same frozen capacity/version, never create a new limit.
3. Registration initializes its activity ledger from that frozen snapshot in
   its own transaction before admitting anyone. A unique activity identity
   prevents competing initializations. A conflicting snapshot is an explicit
   error, not an overwrite of an existing ledger.
4. If Registration fails after the remote freeze, the freeze remains durable.
   Retry initializes from the same snapshot. No automatic unfreeze or
   cross-database rollback is allowed.
5. After activation, capacity changes are rejected explicitly, even when
   occupancy later returns to zero. Other metadata can still be edited under
   its domain rules. A new activity or a future approved coordination protocol
   is required for a different capacity.
6. Registration still validates current publication/eligibility through Event
   Management for new requests; an outage grants no admission or stale fallback.
   The frozen snapshot is authority for the limit, not permission to ignore
   current activity status. Exact lifecycle race semantics belong in T010/T012.

Available slots are computed by Registration, not from Event Management's
configured capacity alone. Catalog availability queries use a Registration API;
if unavailable, report availability as unavailable, not a fabricated free count.
Remote HTTP calls finish before Registration acquires its local admission lock.

## Evidence and justification

- [Domain invariants 1 and 12](../architecture/domain-scenarios.md) require no
  overbooking and no direct access to another service's database.
- [Service boundaries](../architecture/service-boundaries.md) assign configured
  capacity and occupancy to different owners; T006/T007 left synchronization open.
- On 2026-11-09 the user explicitly approved freezing capacity after admission
  opens rather than a live-update protocol. This is a confirmed prototype
  constraint, not an inference from the original specification.
- Separate logical databases implement the local isolation allowed by the
  [plan](../../plan.md) without pretending to provide physical fault isolation.

## Consequences

- Once admission starts, a concurrent metadata edit cannot lower the capacity
  underneath already committed reservations or offers.
- Activation is recoverable without a distributed transaction, but can leave a
  safely frozen activity with no registrations after a failed first admission.
- One PostgreSQL host remains a shared infrastructure failure domain.
- Live capacity changes are a declared limitation, not silently ignored updates.

## Validation and follow-up

T010/T012 must specify activation, authorization, snapshot/version fields and
availability responses. Test edit-versus-activation races, retry after remote
commit/local failure, conflicting initialization, forbidden capacity edits and
database-permission isolation. These are future implementation checks, not
results established by this documentation. T009 supplies the JSON rationale.

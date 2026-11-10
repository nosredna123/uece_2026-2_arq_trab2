# EventHub contracts

The five OpenAPI 3.0.3 documents in [openapi](openapi) describe the four real
services and Identity mock. [common.yaml](openapi/common.yaml) is a reusable
component document, not a standalone service. Base URLs come from deployment
configuration; no gateway is assumed. Billing/Notification messaging is defined
in [asyncapi/eventhub.yaml](asyncapi/eventhub.yaml) and its
[behavior contract](asyncapi/README.md).

## HTTP conventions

- JSON request/response fields use camelCase, UUID identifiers, UTC RFC 3339
  timestamps, integer minor currency units plus ISO currency. Validate date
  ordering, foreign-key associations and organization scope as domain rules.
- Bearer credentials are opaque synthetic user tokens resolved by Identity.
  `X-Service-Key` is a separate, externally configured restricted service
  credential. Validate its allowed caller, not only its presence; tokens and
  keys must never be logged. Internal endpoints are not public development
  bypasses. Mock scenarios are environment-driven, not public control APIs.
- Identity resolves only configured synthetic tokens and returns trusted
  subject, role and organization facts. Organizer principals require an
  organization; participant and global-admin fixtures have no organization
  binding. Domain services enforce those facts and participant ownership.
- Every response (including errors) carries `X-Correlation-ID`. Generate a UUID
  when absent; reject malformed supplied values with a correlated 400. Shared
  error fields never contain credentials or foreign participant data.
- Mutating public operations require `Idempotency-Key`. Scope by validated
  subject, HTTP operation and target; canonical validated body identifies the
  payload. Same key/payload returns the original HTTP status/body, even if the
  resource has since changed; query GET for current state. Changed payload is
  409. Still authorize every replay. Retention is defined in
  [data ownership](../architecture/data-ownership.md).
- Default error responses define status meanings for every operation. Domain
  refusals (422), state/capacity conflicts (409), identity denial (401/403),
  absence/concealment (404) and dependency failure (503) are never 2xx.
- Collections have limit/offset bounds, stable ordering and array bodies.
  Concurrent updates can shift pages; no snapshot-pagination guarantee.

## Lifecycle and consistency

Event Management serializes activity capacity edits against admission activation.
Activation is an internal idempotent POST, returning stable capacity/snapshot
identity and current metadata. Registration validates it on each new admission,
before taking its local activity lock. A successful eligibility check precedes
the local admission decision; cancellation after that check can race with the
already validated request. No globally atomic event-cancellation/registration
transaction is claimed. New checks after cancellation are denied.

Participant cancellation of a confirmed paid registration requests a refund,
not an immediate cancellation. It returns 202 while still CONFIRMED/occupied.
Do not confuse event cancellation with completed participant refunds: automatic
refund/cancellation policy for an entire cancelled event is not approved by the
current domain baseline and must not be invented.

Attendance eligibility is event-fed and may lag; missing projection is explicit
409, not an invented confirmation. Reporting returns its own eventually
consistent projection with timestamps; it is never an admission authority.

## Validate

Use an isolated virtual environment and install [requirements-validation.txt](../../requirements-validation.txt),
then run `python -m pytest -x` from the repository root. Tests validate all five
OpenAPI specifications and local references with `openapi-spec-validator`.
Contract validation is not evidence that an unimplemented endpoint runs.

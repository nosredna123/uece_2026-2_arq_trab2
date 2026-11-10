# EventHub Reporting

Reporting maintains an eventually consistent, service-owned projection from
`activity.changed`, `registration.changed`, `billing.result`,
`attendance.recorded`, and `certificate.issued` events. It reads no Event
Management, Registration, Billing, or Attendance database. Identity is queried
only to validate the caller's bearer credential.

## API

`GET /organizations/{organizationId}/reports` returns activity-level
projections ordered by `activityId`. An organizer may query only their own
organization; a global administrator may query any organization. An optional
`activityId` filter and `limit` (1–100, default 20) / `offset` (default 0)
parameters support filtering and pagination. An empty projection returns an
empty array.

Counts are based on each aggregate's latest accepted snapshot. `occupied` is
the sum of confirmed, pending-payment, and offered registrations. Billing
outcomes are deduplicated by operation ID; amounts are deliberately not summed
across currencies. `projectedAt` exposes the last event projection time for
each activity.

## Runtime configuration

The service requires `DATABASE_URL`, `MIGRATION_DATABASE_URL`,
`RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`,
`IDENTITY_BASE_URL`, `INTERNAL_SERVICE_KEY`, `HTTP_TIMEOUT_SECONDS`,
`MESSAGE_RETRY_INITIAL_SECONDS`, and `MESSAGE_RETRY_MAX_SECONDS`. Compose
provides these from `infra/.env`; credentials in local examples are synthetic.

The durable `reporting.v1` RabbitMQ queue binds to the five event types above.
Successfully persisted messages are acknowledged; invalid messages are
published to `reporting.v1.quarantine` before acknowledgement, and transient
database or broker failures are retried.

## Tests

Run the service suite with:

```sh
python -m pytest -x --import-mode=importlib
```

The `test` Docker build target installs the pinned test dependencies and runs
the same fail-fast suite.

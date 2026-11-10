# Notification mock

This worker consumes the durable `notification.v1` queue, bound independently
to registration, activity, attendance, and certificate events. It validates
each source envelope, derives a deterministic notice category, and stores the
source event ID, correlation ID, outcome, and delivery record in SQLite. It
does not send email or SMS and does not persist participant contact data.

`NOTIFICATION_MOCK_AVAILABLE=true|false` controls the simulated outcome.
Unavailable deliveries are recorded as retryable diagnostics and requeued with
the configured capped exponential delay. A replay after recovery creates at
most one simulated delivery per source event. Conflicting content for an
existing event ID is quarantined with the original body and transport identity;
quarantine publication is confirmed before acknowledgment.

Required settings are `NOTIFICATION_MOCK_AVAILABLE`, `NOTIFICATION_STORE_PATH`,
`RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`,
`MESSAGE_RETRY_INITIAL_SECONDS`, and `MESSAGE_RETRY_MAX_SECONDS`. Mount a
persistent volume at the configured SQLite path. Queue topology is durable and
matches the v1 contract; provision the shared exchange and bindings before
producers start.

Build and run the isolated tests or runtime image with:

```sh
docker build --target test mocks/notification
docker build --target runtime -t eventhub-notification-mock mocks/notification
```

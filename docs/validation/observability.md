# Correlation and structured logs

## Identifier flow

All HTTP services and the Identity mock accept `X-Correlation-ID` as a UUID.
They generate one when the caller omits it and return the effective value in
the response header; API error bodies include the same `correlationId`. An
invalid supplied value is rejected with HTTP 400 and a new identifier in the
error body and response header.

Internal HTTP calls forward the effective identifier. Event envelopes preserve
it in `correlationId`, and RabbitMQ `correlation_id` must match that value;
`eventId` is carried as the AMQP message ID. Outbox retries, consumer
processing, and quarantine retain both identities. Registration-related
envelopes include `data.registrationId`, which is also the registration
aggregate ID where defined by the contract.

## Log format

Python services and mocks emit one JSON object per application log line.
The Java services use Spring Boot's ECS JSON console format. Request and
message processing logs include the correlation identity; when the operation
is registration-related, they also include `registrationId`. Other applicable
fields include `eventId`, `eventType`, `httpMethod`, `httpPath`, `statusCode`,
`errorType`, and `retrySeconds`. Fields that do not apply to a log event are
omitted. Logs contain identifiers and operational metadata, not bearer tokens,
service credentials, or request bodies.

The request, envelope, AMQP, and JSON-log fields are covered by contract tests
and the service test-profile build.

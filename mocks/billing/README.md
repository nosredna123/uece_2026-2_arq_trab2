# Billing mock

The worker consumes persistent `billing.requested` messages from durable
`billing.v1`, validates the v1 envelope and operation fields, and writes each
attempt and result outbox entry to its own SQLite store before acknowledging
the request. Result publication uses publisher confirms, persistent delivery,
the original correlation ID, and the operation ID as aggregate ID. Invalid
requests are preserved in `billing.v1.quarantine`; no message is treated as
approved when it is malformed or the worker cannot persist its outcome.

`BILLING_MOCK_SCENARIO` and `BILLING_MOCK_REFUND_SCENARIO` independently choose
`APPROVED`, `REFUSED`, or `TIMEOUT`. A timeout is durably recorded and emits no
result; redelivery of that same attempt preserves the timeout, while a new
attempt may use a changed scenario. Successful operation IDs are deduplicated.
Refund attempts must reference a successful matching payment; the result amount
is calculated from the original amount and `REFUND_PERCENTAGE` using integer
half-up rounding.

Optional `BILLING_MOCK_APPROVAL_DELAY_SECONDS` (`N` or `MIN-MAX` whole seconds,
0–60, default `0`) holds each payment result in the outbox for a random time in
that range before publishing it, so the registration stays `PENDING_PAYMENT`
for a while, as with a real payment provider. Refund results are not delayed.
The worker keeps consuming requests while results wait, and a 0.5 s timer
publishes them when they are due. The delay is stored with the result, so it
survives a restart. `scripts/run-demo-web.sh` uses `2-8` when the load scenarios
are enabled and the setting is unset.

Startup requires the RabbitMQ credentials, both mock scenarios,
`REFUND_PERCENTAGE`, `BILLING_STORE_PATH`, and retry limits from the validated
environment template. The store directory must be mounted durably at
`/data`; the container runs as UID/GID 10001 and exposes no public HTTP API.
The `registration.v1` and `reporting.v1` result queues and bindings must be
provisioned before the worker publishes results, per the
[AsyncAPI topology](../../docs/contracts/asyncapi/README.md).

Build the isolated test/runtime images with:

```sh
docker build --target test -t eventhub-billing-mock-test mocks/billing
docker build --target runtime -t eventhub-billing-mock mocks/billing
```

Only synthetic operations and identifiers are used. This mock is not a payment
provider and must not be configured with real customer or financial credentials.

# Failure scenario integration tests

These live tests verify paid-registration recovery and the independence of
Notification and Reporting from the primary registration flow:

- Billing refusal releases a paid reservation and promotes the next waitlisted
  participant into an offer.
- Billing timeout emits no result; a short reservation deadline expires the
  reservation and releases its capacity.
- Registration still succeeds while Notification is configured unavailable.
- Registration still succeeds while Reporting is stopped; after restart, its
  durable event queue recovers the report projection.

The waitlist case uses two distinct synthetic participants from the Identity
mock. The runner temporarily configures Billing and the reservation duration
for each case, then restores approved Billing, the default 30-minute deadline,
and available Notification.

Run from the repository root after creating `infra/.env` from
`infra/.env.example`:

```sh
PYTHON=/path/to/validation-venv/bin/python \
  bash tests/integration/failure-scenarios/run.sh
```

The runner builds and starts the Compose services, uses synthetic data with
unique identifiers per execution, and leaves the stack running with default
scenario settings. The reservation-expiry case waits for the scheduled
30-second registration expiry worker, so a full live run takes at least about
one minute.

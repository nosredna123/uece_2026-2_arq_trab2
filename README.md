# EventHub

EventHub is a locally runnable event-management prototype. The
application uses four executable services and three dependency mocks, backed
by PostgreSQL and RabbitMQ. Execution and acceptance tests are intended to run
in containers; host Java/Python runtimes and host databases are optional.

## Development environment

1. Install Git, Docker Engine/Desktop, and Docker Compose v2 as described in
   [the environment setup guide](docs/development/environment-setup.md).
2. From the repository root, run `./scripts/check-environment.sh` to verify
   Docker access, resources, required host ports, and temporary container,
   network, and volume operations.
3. Create and validate the ignored local environment file as described below.
4. Use the Compose commands in [the environment setup guide](docs/development/environment-setup.md)
   to build/test containers and run the health acceptance check. Domain
   acceptance flows remain pending; scaffold health is not proof of domain
   functionality.
5. Review [CI governance setup](docs/development/ci-governance.md) before
   opening pull requests; protected audit credentials and required repository
   rules are needed for the gates to pass.

## Local environment variables and secrets

The versioned [infra/.env.example](infra/.env.example) contains only synthetic
demonstration values. First verify the ignore rule, then create the local file:

```sh
git check-ignore -v infra/.env
cp infra/.env.example infra/.env
chmod 600 infra/.env
```

Do not put real passwords, tokens, keys, or other credentials in `.env.example`,
documentation, logs, or tracked files. Keep local secrets out of commits and
share them only through an approved secret-management channel. Do not create
`infra/.env` until the ignore rule exists and `git check-ignore` confirms it is
ignored. Never commit the real local file.

Validate without printing values, using an isolated environment with
[validation dependencies](requirements-validation.txt):

```sh
python scripts/validate-environment.py infra/.env
python -m pytest -x --import-mode=importlib
```

Full Python tests also require installing the service and mock `[test]` extras
as described in [services/README.md](services/README.md). The validator itself
may be run without a host Python installation:

```sh
docker run --rm -v "$PWD:/workspace:ro" -w /workspace python:3.12.9-slim-bookworm \
  sh -c 'pip install --no-cache-dir jsonschema==4.23.0 && python scripts/validate-environment.py infra/.env'
```

The local template uses plain `KEY=value` lines, no shell execution, quoted
values or interpolation. Do not `source` untrusted environment files.
The [schema](infra/environment.schema.json) defines every required variable.
The validator rejects missing/unknown keys, duplicate assignments, invalid
booleans/ranges, conflicting host ports and contradictory retry delays, without
printing supplied values.

### Configuration mapping and policy

Compose wiring must map `EVENT_*`/`REGISTRATION_*` database values to each Java
service's `DATABASE_URL/USER/PASSWORD` and `MIGRATION_USER/PASSWORD`; Python
`ATTENDANCE_*`/`REPORTING_*` URLs map to `DATABASE_URL` and
`MIGRATION_DATABASE_URL`. Never reuse bootstrap credentials for runtime.
Common integration/messaging variables are passed by their names. Billing
persists operations, attempts and result outbox messages at `BILLING_STORE_PATH`
on its dedicated volume. Notification persists retry diagnostics and simulated
deliveries at `NOTIFICATION_STORE_PATH` on its own dedicated volume.

- Reservation and FIFO offer periods start at the approved configurable 30
  minutes; cancellation window at seven days and refund percentage at 90.
  Persisted deadlines do not change retroactively when configuration changes.
- Waitlist may be enabled/disabled globally. Certificate policy is CHECK_IN
  with issuance after activity completion; configuration cannot disable that
  invariant. Event prices/capacity/dates are persisted data, not global defaults.
- Billing payment/refund scenarios independently select APPROVED/REFUSED/TIMEOUT.
  Identity and Notification availability select true/false. These are
  synthetic controls, not production integrations or public bypass endpoints.
- Retry initial/max delays and HTTP timeout are externally configured positive
  seconds; max delay cannot be below initial delay. Refund business attempts
  remain unlimited regardless of the delay cap.
- Java validated configuration records and Python typed settings fail startup
  for missing/invalid required service settings. Identity mock startup also
  requires and strictly validates its key and availability setting; remaining
  mocks must enforce the same template contract.

The host ports are 8081-8084 for the four services, 5432 for PostgreSQL, 5672
for RabbitMQ AMQP and 15672 for RabbitMQ management. Set different available
host ports explicitly if needed; RabbitMQ's in-network port remains 5672.
Identity is reachable only from the Compose network and is not published to the
host. No real local environment file is created by tests.

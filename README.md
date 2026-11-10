# EventHub

EventHub is a locally runnable event-management prototype. The
application uses four executable services and three dependency mocks, backed
by PostgreSQL and RabbitMQ. Execution and acceptance tests are intended to run
in containers; host Java/Python runtimes and host databases are optional.

## Requirements and development environment

- Git 2.x and Docker Engine/Desktop with the Docker Compose v2 plugin (`docker
  compose`).
- For the complete local stack, available localhost ports 5432, 5672, 15672,
  and 8081-8084; the default mappings can be changed in `infra/.env`.
- The readiness gate expects at least 2 CPUs, 4096 MiB of Docker memory, and
  5120 MiB of free space. See the [environment setup guide](docs/development/environment-setup.md)
  for platform-specific details and the port check.
- Host JDK, Maven, Python, PostgreSQL, and RabbitMQ are not required to build
  or run the application containers. Optional host-side Java tests use JDK 21
  and the Maven Wrapper. Host-side Python tests use Python 3.12 and the
  dependencies described in [services/README.md](services/README.md).

1. Install Git, Docker Engine/Desktop, and Docker Compose v2 as described in
   [the environment setup guide](docs/development/environment-setup.md).
2. From the repository root, run `./scripts/check-environment.sh` to verify
   Docker access, resources, required host ports, and temporary container,
   network, and volume operations.
3. Create and validate the ignored local environment file as described below.
4. Use the [build, test, and run commands](#build-test-and-run) below. The
   Compose health acceptance profile verifies startup only; the paid
   registration flow is a separate end-to-end domain test.
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
  requires and strictly validates its key and availability setting; Billing
  and Notification validate their required settings at startup as well.

The host ports are 8081-8084 for the four services, 5432 for PostgreSQL, 5672
for RabbitMQ AMQP and 15672 for RabbitMQ management. Set different available
host ports explicitly if needed; RabbitMQ's in-network port remains 5672.
Identity is reachable only from the Compose network and is not published to the
host. No real local environment file is created by tests.

## Build, test, and run

Run commands from the repository root after creating and validating
`infra/.env` as described above. The primary workflow uses Docker; the host
does not need Java, Maven, Python, PostgreSQL, or RabbitMQ for the build and
containerized test suite.

```sh
# Build the application runtime images.
docker compose --env-file infra/.env -f infra/docker-compose.yml build

# Build all service, mock, and repository-wide test images.
# The service test stages run their fail-fast tests during the build.
docker compose --env-file infra/.env -f infra/docker-compose.yml --profile test build

# Run the full repository Python test suite in the project test image.
docker compose --env-file infra/.env -f infra/docker-compose.yml --profile test \
  run --rm python-suite-test python -m pytest -x --import-mode=importlib

# Start the full stack and wait for health checks.
docker compose --env-file infra/.env -f infra/docker-compose.yml up --build -d --wait

# Inspect service status and follow logs when diagnosing startup.
docker compose --env-file infra/.env -f infra/docker-compose.yml ps
docker compose --env-file infra/.env -f infra/docker-compose.yml logs -f event-management registration

# Run the health-based acceptance profile. This checks startup, not the domain flow.
docker compose --env-file infra/.env -f infra/docker-compose.yml --profile acceptance \
  up --build --abort-on-container-exit --exit-code-from acceptance

# Stop containers while preserving local database and mock data.
docker compose --env-file infra/.env -f infra/docker-compose.yml down
```

The test-profile image build includes both Java service tests and the Python
service/mock tests. The repository-wide Python command above covers contracts,
documentation checks, and regular unit/integration tests; live integration
tests are invoked separately. The `down` command preserves persistent data.
Use `down --volumes` only when intentionally deleting the local prototype's
PostgreSQL, RabbitMQ, Billing, and Notification data.

### Paid-registration demonstration

There is no preloaded production-like dataset. The repeatable demonstration
creates synthetic data at runtime and exercises the services end to end:

1. Prepare a Python 3.12 virtual environment with the host validation
   dependencies from `requirements-validation.txt` (the runner uses the host
   Python process to drive HTTP requests).

   ```sh
   python3.12 -m venv .venv
   . .venv/bin/activate
   python -m pip install -r requirements-validation.txt
   ```

2. Run the integration flow from the repository root:

   ```sh
   bash tests/integration/paid-registration-flow/run.sh
   ```

3. The runner creates a one-seat paid event/activity, uses the Billing mock's
   approved-payment scenario, and waits for registration confirmation,
   check-in, certificate issuance, and the Reporting projection. The IDs and
   dates are generated for that run; no static sample records or real payment
   credentials are used.

This integration runner intentionally leaves its Compose stack and persistent
volumes running. Inspect them with
`docker compose --env-file infra/.env -f infra/docker-compose.yml ps`; stop the
stack with the `down` command above when finished. Do not remove volumes unless
you intend to erase the local demonstration data.

## Services and API overview

The public application endpoints are published on localhost. Organizer and
participant operations use a synthetic bearer credential accepted by the
Identity mock. Use `Idempotency-Key` on supported write operations;
Registration and Attendance require it for their idempotent commands. Services
accept `X-Correlation-ID` for request/message diagnostics and return the
effective ID in HTTP responses. See
[the observability contract](docs/validation/observability.md).

| Service | Base URL | Main API surface |
|---|---|---|
| Event Management | `http://localhost:8081` | Public `GET /events`, `GET /events/{eventId}`, `GET /events/{eventId}/activities`, and `GET /activities/{activityId}`; organizer `POST /events`, `PUT /events/{eventId}`, `POST /events/{eventId}/publish`, `POST /events/{eventId}/cancel`, `POST /events/{eventId}/activities`, and `PUT /activities/{activityId}`. |
| Registration | `http://localhost:8082` | Public `GET /activities/{activityId}/availability`; participant `POST /registrations`, `GET /registrations`, `GET /registrations/{id}`, `POST /registrations/{id}/cancel`, and `POST /registrations/{id}/accept-offer`. |
| Attendance & Certification | `http://localhost:8083` | Participant `POST /registrations/{id}/check-in`, `POST /registrations/{id}/certificate`, and `GET /registrations/{id}/certificate`. |
| Reporting | `http://localhost:8084` | Organization-authorized `GET /organizations/{organizationId}/reports`, optionally filtered by `activityId` and paginated with `limit`/`offset`. |

Java service health is available at `/actuator/health`; Python service health
is available at `/health`. The exact request schemas and authorization rules
are enforced by each service's contract tests and implementation. Identity's
`/internal/identity/validate` route is internal to the Compose network and
requires the configured service credential; it is not a public login API.
Event Management also exposes
`POST /internal/activities/{activityId}/admission` for Registration's
service-key-protected admission check; it is not a participant API.

## Implemented services and prototype boundaries

- **Executable domain services:** Event Management and Registration (Java),
  Attendance & Certification and Reporting (Python). Each owns its data and
  migrations. PostgreSQL and RabbitMQ are real local infrastructure
  dependencies in Compose; services exchange events asynchronously, and
  Reporting maintains an eventually consistent projection.
- **Dependency mocks:** Identity & Access returns configured synthetic
  identity fixtures; Billing simulates approved/refused/timed-out payment and
  refund outcomes; Notification simulates availability, delivery, and retry
  behavior. They are test/demo adapters, not external identity, payment, or
  email/SMS integrations. Identity, Billing, and Notification do not expose
  public host APIs in the Compose configuration.
- **Not production-ready:** the project does not provide real user signup or
  credential issuance, a payment processor, external notification delivery,
  production secrets management, or production hosting/monitoring. Synthetic
  credentials and mock scenario controls must remain isolated to local/demo
  use. Do not expose the prototype or reuse its example credentials in a
  deployed environment.

## Architecture and validation

See the [architecture, validation, and defense guide](docs/validation/final-delivery.md)
for the decision/rationale index, test evidence, trade-offs, AI-use declaration,
and demonstration walkthrough.

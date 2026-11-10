# Executable service foundations

Each service is an independent build context with its own migrations and tests.
There is deliberately no shared Java/Python domain library.

## Java scaffolds (T014)

For each of `event-management-java` and `registration-java`:

```sh
docker build --target test -t eventhub-SERVICE-test services/SERVICE
docker run --rm eventhub-SERVICE-test
docker build --target runtime -t eventhub-SERVICE services/SERVICE
```

Replace `SERVICE` with the directory name. Maven uses `-ff` and Surefire stops
after the first failure (the Java equivalents of pytest `-x`). Java 21 and
Maven are inside the build image; the final image contains only the JRE and jar
and runs as UID 10001. Dependencies are managed by Spring Boot 3.4.3, with
jqwik 1.9.2 explicitly pinned.

Event Management exposes the event/activity lifecycle, public published catalog,
and Registration-only admission snapshot endpoints. Its tests use an isolated
H2 database; the runtime uses PostgreSQL and Flyway. `REGISTRATION_SERVICE_KEY`
is a dedicated credential for the internal admission endpoint and must not be
replaced with the shared Identity mock credential. Registration currently
supports atomic free confirmation, paid reservations, participant-scoped state
and cancellation, durable command replay, non-activating availability reads,
and FIFO waitlisting. Releasing a slot automatically offers it to the next
waiter; offers can be accepted through the Registration API and expire after
the configured `WAITLIST_OFFER_EXPIRY_MINUTES` deadline. A scheduled worker
expires overdue offers and promotes the next waiter without exceeding frozen
activity capacity. Paid reservations atomically persist a Billing request in
Registration's outbox. Publisher confirms, persistent messages, stable event and
attempt identities, and capped retry delays make request publication replayable.
Billing results are correlated and deduplicated in the Registration inbox before
manual acknowledgment; approvals confirm only live reservations, while refusal
and database-clock expiry release a slot once and promote the next waiter. Late
approvals are recorded for manual compensation, and unpublished requests for
expired reservations are suppressed. Runtime requires explicit database,
migration and RabbitMQ credentials and checks dependency health. Typed settings
validate required business/integration configuration before startup; see the
root README.

Flyway V1 creates owner-local event/activity or capacity/enrollment tables.
Event Management V2 adds frozen-capacity state and durable command receipts;
V3 adds bounded database locks for concurrent idempotent commands. Registration
V2 adds durable command receipts for idempotent enrollment and cancellation;
V3 adds FIFO waitlist sequencing, V4 adds the Billing request outbox, V5 adds
expired-request suppression and the Billing result inbox, and V6 retains the
complete result payload for late-payment reconciliation. Later migrations must
be new versions, never edits to already applied SQL.
Runtime credentials differ from migration credentials; provisioning/grants are
the responsibility of the Compose foundation.

## Python scaffolds (T015)

The same Docker build/test commands apply to `attendance-certification-python`
and `reporting-python`. The test stage runs `python -m pytest -x`; the runtime
image contains only runtime wheels and migrations, not pytest/Hypothesis/httpx.
Python 3.12.9 and direct/transitive runtime versions in `constraints.txt` are fixed.
Each service has its own package and migration directory.

Runtime requires `DATABASE_URL` and `MIGRATION_DATABASE_URL` (SQLAlchemy
`postgresql+psycopg://` URLs with separate runtime/migration credentials).
Startup first applies `alembic upgrade head`, then starts Uvicorn on port 8080.
`/health` queries the configured database and reports 503 on failure; missing
configuration fails startup. Domain routes and event consumers are later tasks.
Unit tests explicitly use isolated SQLite files only for scaffold health checks;
actual Alembic DDL targets PostgreSQL and is validated separately against it.

For host-side full Python tests, install the validation requirements and all
service and mock packages with their `[test]` extras into an isolated Python
3.12 environment, then run:

```sh
python -m pip install -r requirements-validation.txt \
  -e "services/attendance-certification-python[test]" \
  -e "services/reporting-python[test]" \
  -e "mocks/identity-access[test]" \
  -e "mocks/billing[test]" \
  -e "mocks/notification[test]"
python -m pytest -x --import-mode=importlib
```

Importlib mode keeps independent services' identically named test modules
isolated without manipulating `sys.path` or sharing domain packages.

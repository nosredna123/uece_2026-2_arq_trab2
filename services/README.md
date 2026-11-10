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
replaced with the shared Identity mock credential. Runtime requires explicit
database, migration and RabbitMQ credentials and checks dependency health.
T023-T030 implement Registration's real domain APIs and messaging. Typed
settings validate required business/integration configuration before startup;
see the root README.

Flyway V1 creates owner-local event/activity or capacity/enrollment tables.
Event Management V2 adds frozen-capacity state and durable command receipts;
V3 adds bounded database locks for concurrent idempotent commands.
Later migrations must be new versions, never edits to already applied SQL.
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

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

`/actuator/health` is the only implemented HTTP endpoint at scaffold stage.
Tests deliberately isolate web startup from PostgreSQL/RabbitMQ and assert that
domain routes are not fake success endpoints. Runtime requires explicit database,
migration and RabbitMQ credentials and checks dependency health. T021-T030
implement the real domain APIs and messaging; T016 adds typed business settings.

Flyway V1 creates owner-local event/activity or capacity/enrollment tables.
Later migrations must be new versions, never edits to already applied SQL.
Runtime credentials differ from migration credentials; provisioning/grants are
the responsibility of the Compose foundation.

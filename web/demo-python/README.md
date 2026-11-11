# EventHub Python presentation app

The server-rendered FastAPI presentation app is an optional Docker Compose
profile. The normal `infra/docker-compose.yml` backend stack remains unchanged
and does not start the app.

## Start the app

From the repository root, create `infra/.env` only after verifying that Git
ignores it, then set `DEMO_WEB_ENABLED=true` in that local file. Do not edit or
replace it from a launch script; use `infra/.env.example` as the safe template.

```sh
bash scripts/run-demo-web.sh
```

The launcher uses the `demo-web` Compose profile, binds only to localhost, and
waits for healthy Event Management, Registration, Attendance, Reporting, and
Identity services; it also starts the Billing and Notification mocks. It
checks only the presentation port, so it works when the backend stack is
already running. A collision on the presentation port is
reported with guidance to choose another `DEMO_WEB_PORT`. It never calls
`docker compose down`, deletes volumes, or changes the local environment file.

Open the printed localhost URL. The ordinary service journeys use the
synthetic Identity fixture and existing service APIs; the app has no database,
broker, Docker socket, or arbitrary target access.

## Test without host runtimes

Run the bounded, fail-fast repository Python suite in the dedicated test image:

```sh
bash scripts/test-demo-web.sh
```

The test runner builds and runs the isolated `python-suite-test` image, applies
a 15-minute default timeout, and removes only its temporary test container. It
does not start or stop backend services or remove persistent volumes.
`DEMO_WEB_TEST_TIMEOUT_SECONDS` may set a value from 60 through 3600 seconds.

## Real-browser acceptance

Run the isolated Playwright/Chromium journeys against fresh synthetic services:

```sh
bash tests/webapp/run.sh
```

The runner builds the pinned browser image, uses the synthetic
`infra/.env.example` rather than `infra/.env`, assigns a unique Compose project,
publishes no service ports, and removes only that project's containers and
volumes. It exercises paid registration/check-in/certificate read-back,
two-person waitlist promotion, the real Scenario Lab, and bounded
Reporting/Identity outage probes; the probes restore their services before
cleanup, including on failure. Browser screenshots are saved under
`docs/validation/artifacts/webapp/`.

## Optional Scenario Lab

The Scenario Lab is disabled by default. To enable it, set both
`IDENTITY_LAB_ENABLED=true` and `DEMO_LAB_ENABLED=true` in `infra/.env`, then
provide the fixed fixture path and all six bounded limits required by
`scripts/validate-environment.py`. In the container, the scenario fixture path
is `/run/eventhub/scenario-participants.json`; the corresponding bind mount is
read-only and is added only by the lab Compose overlay. Registration must have
waiting-list support enabled before running FIFO scenarios.

When Identity is already running with the pool disabled, the launcher refuses
to start the lab until the one-time pool reload is explicit:

```sh
bash scripts/run-demo-web.sh --restart-identity-pool
```

This recreates only the Identity mock after the operator has opted in through
both flags. If Identity is not running, Compose starts it with the requested
configuration. No browser action restarts Identity. Billing and Registration
scenarios remain those selected by the operator; the web app does not change
mock state or service configuration.

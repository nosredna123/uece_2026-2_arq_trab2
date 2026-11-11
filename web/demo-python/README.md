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

The launcher first builds/starts the normal backend Compose stack and waits for
its health checks, then builds/starts the `demo-web` profile and waits for app
readiness. It binds the app only to localhost and also starts the Billing and
Notification mocks. You no longer need to run a separate backend `docker
compose up` command. Existing services are reconciled in place; the launcher
never calls `docker compose down`, deletes volumes, or changes the local
environment file. A presentation-port collision is reported with guidance to
choose another `DEMO_WEB_PORT`. The safe `infra/.env.example` supplies defaults
for omitted settings, with values in `infra/.env` taking precedence; the
launcher does not write either file.

Open the printed localhost URL. The ordinary service journeys use the
synthetic Identity fixture and existing service APIs; the app has no database,
broker, Docker socket, or arbitrary target access.

The persona selector includes separate synthetic organizer tenants for
GESAD/UECE, UECE, and the fictional Instituto Acadêmico Simulado do Ceará
(IASC). GESAD is a UECE research group; IASC is fictional and does not imply a
real partnership. Guided preparation creates records under the selected
organizer's organization. Scenario Lab runs are currently limited to the
GESAD/UECE persona because its fixed workload identities and records use that
organization. Generated records carry a Portuguese test-purpose prefix and
`SIMULADO` label.

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

For presenter timing, exact UI actions, narration, failure branches, and
non-destructive shutdown, follow the
[browser presentation script](../../docs/validation/webapp-presentation-script.md).

## Optional Scenario Lab

The Scenario Lab is disabled by default. To enable it, set both
`IDENTITY_LAB_ENABLED=true` and `DEMO_LAB_ENABLED=true` in `infra/.env`, then
provide the fixed fixture path and all six bounded limits required by
`scripts/validate-environment.py`. In the container, the scenario fixture path
is `/run/eventhub/scenario-participants.json`; the corresponding bind mount is
read-only and is added only by the lab Compose overlay. Registration must have
waiting-list support enabled before running FIFO scenarios.

When enabling the lab after the backend is already running, update both flags
and run `bash scripts/run-demo-web.sh` again. Compose detects the changed
Identity configuration and recreates the affected service; unchanged
containers are left running, and persistent volumes are preserved. No special
restart flag or browser action is needed. Billing and Registration scenarios
remain those selected by the operator; the web app does not change mock state
or service configuration.

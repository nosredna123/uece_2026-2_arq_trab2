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

The home page is a simulated sign-in: choose an organizer (GESAD/UECE, UECE,
or IASC) or a synthetic participant. There is no password. The selected user stays active on every page, shown in the header as
**Signed in as …**, until **Sign out** returns to the sign-in page. Pages
opened before signing in redirect to it.

The sign-in page includes separate synthetic organizer tenants for
GESAD/UECE, UECE, and the fictional Instituto Acadêmico Simulado do Ceará
(IASC). GESAD is a UECE research group; IASC is fictional and does not imply a
real partnership.

Each role sees only its own pages:
- **Organizers** land on **My events**. From there they can create and edit
  events and their activities, publish or cancel events, and open **Reports**
  for their own organization's events only.
- **Participants** land on **Events**. From there they can register, follow
  **My registrations**, and open the certificate for an attended activity.

## Initial presentation data

On startup the app pre-fills the stack with Brazilian-Portuguese synthetic data
from a single editable file,
[`seed-data.json`](./seed-data.json). It covers every domain entity for the
three organizer tenants:
- events, including one draft;
- free and paid activities;
- registrations: confirmed, waitlisted, and cancelled;
- check-ins and certificates.

All titles start with `[DADOS INICIAIS][SIMULADO][<organização>]`. The seeding
runs in the background through the real service APIs. Data appears within
seconds, and certificates appear about 5 minutes after the first start, once
their short activities have ended. The app logs `Initial presentation data
ready (batch …)` when it finishes.

Seeding happens **only once per database**. Times are relative to a batch
anchor recorded in each event description. Every later start (restart, stop and
start, or rerunning the launcher) finds that anchor and replays the services'
durable idempotency keys, so nothing is duplicated. The comment array `_leia-me`
inside the file explains the editable fields. Keys starting with `_` are
ignored.

Editing the file after the first seed adds the new or changed items as extra
records; it does not update the existing ones. To apply edits cleanly, reset
the local data (this deletes all local volumes) and launch again:

```sh
docker compose -p eventhub --env-file infra/.env -f infra/docker-compose.yml down -v
bash scripts/run-demo-web.sh
```

Set `DEMO_WEB_SEED_DATA_PATH` to an empty value to disable seeding. The
isolated browser acceptance stack does this.

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
volumes. An organizer creates and publishes the event through the UI. The
runner then exercises paid registration, check-in, certificate read-back and
the organizer report, two-person waitlist promotion, role isolation, and bounded
Reporting/Identity outage probes; the probes restore their services before
cleanup, including on failure. Browser screenshots are saved under
`docs/validation/artifacts/webapp/`.

For presenter timing, exact UI actions, narration, failure branches, and
non-destructive shutdown, follow the
[browser presentation script](../../docs/validation/webapp-presentation-script.md).

## Load scenarios (terminal)

Pre-defined load scenarios run from the terminal while the organizer watches
**Reports** in the browser:

```sh
bash scripts/cenario.sh pico-inscricoes   # many participants, seats for 25% of them
bash scripts/cenario.sh ultima-vaga       # two participants, one seat
bash scripts/cenario.sh envios-repetidos  # same request 10 times (idempotency)
bash scripts/cenario.sh                   # all of the above in parallel + mixed load
```

Without an argument, the three scenarios run in parallel with a random **mixed
load** (`carga-mista`). The mixed load publishes 3 more GESAD/UECE events
(`[CENÁRIO] Carga mista · …`) with 3 activities each and one paid activity per
event. Over `SCENARIO_MIXED_DURATION_SECONDS` (10–300, default 60), each of the
`SCENARIO_PEAK_PARTICIPANTS` participants registers in 2–4 random activities.
Capacities scale with that number: 2–5 seats per activity for 20 participants,
20–50 for 200.
About 20% of the confirmed seats in free activities are then cancelled; the
freed seat is offered to the next person on the waiting list, who accepts it.
Paid registrations show as `PENDING_PAYMENT` until the Billing mock approves
them. Paid confirmed seats are never cancelled: the refund workflow is a known
limitation (ADR-005 1.1.0), and Registration answers `409 REFUND_WORKFLOW_REQUIRED`.
In this mode the peak also lasts `SCENARIO_MIXED_DURATION_SECONDS`. Each output
line is prefixed with its scenario, and the run ends with a summary and
`Veredito geral: OK` or `Veredito geral: VERIFICAR`. The mixed load is random,
so every run differs.

Each run publishes a fresh `[CENÁRIO] …` event in the GESAD/UECE organization,
because the fixed scenario identities belong to it. It submits the
registrations through the real Registration API and prints each outcome, then
a Portuguese verdict (`Veredito: OK` or `Veredito: VERIFICAR`). The script runs
inside the running `demo-web` container (`python -m demo_web.scenarios`). It
fails clearly if the app is not running or the scenarios are disabled.

The peak scenario reads two optional settings, from the shell or `infra/.env`
(defaults come from `infra/.env.example`):
- `SCENARIO_PEAK_PARTICIPANTS`: 1–20000, default 20. Each participant is a
  distinct identity from the scenario pool (20,000 identities). The peak
  activity gets seats for 25% of them, rounded up (20 → 5 seats, 200 → 50), so
  the rest go to the waiting list. The mixed load uses the same number.
- `SCENARIO_PEAK_DURATION_SECONDS`: 1–300, default 30. Use a longer value to
  watch the numbers change in Reports while you refresh it.

Reports reads the counts from Reporting, which receives registration changes
through the Registration outbox relay. The relay runs every 30 s by default.
`scripts/run-demo-web.sh` lowers it to 1 s
(`REGISTRATION_OUTBOX_PUBLISH_INTERVAL_MS=1000`), so the Reports panel follows
each registration within about 2 s. Set the variable in `infra/.env` (100–30000
ms) to override this.

For example:

```sh
SCENARIO_PEAK_DURATION_SECONDS=60 bash scripts/cenario.sh pico-inscricoes
```

The scenarios are disabled by default. To enable them, set both
`IDENTITY_LAB_ENABLED=true` and `DEMO_LAB_ENABLED=true` in `infra/.env`, then
provide the fixed fixture path and all six bounded `DEMO_LAB_*` limits required
by `scripts/validate-environment.py`. In the container, the scenario fixture
path is `/run/eventhub/scenario-participants.json`; the corresponding bind
mount is read-only and is added only by the lab Compose overlay. Registration
must have waiting-list support enabled, or the peak scenario reports
`WAITLIST_DISABLED` errors.

When enabling the scenarios after the backend is already running, update both
flags and run `bash scripts/run-demo-web.sh` again. Compose detects the changed
Identity configuration and recreates the affected service; unchanged
containers are left running, and persistent volumes are preserved. The web
app does not change mock state or service configuration.

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
the local data (this deletes all local volumes and asks for confirmation;
add `--yes` to skip the prompt). The script relaunches the demo, which seeds
the initial data again:

```sh
bash scripts/reset-demo.sh
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
About 20% of the confirmed seats in free activities are then cancelled
(`SCENARIO_CANCEL_PERCENT`); the freed seat is offered to the next person on the
waiting list. About 20% of those people ignore the offer
(`SCENARIO_OFFER_LAPSE_PERCENT`), so Registration marks it `EXPIRED` after
`WAITLIST_OFFER_EXPIRY_MINUTES` and offers the seat to the next person; the
others accept it.
Paid registrations show as `PENDING_PAYMENT` until the Billing mock approves
them. With the load scenarios enabled, `scripts/run-demo-web.sh` makes the mock
wait a random 2–8 s before each approval (`BILLING_MOCK_APPROVAL_DELAY_SECONDS`),
so the Pending payment card in Reports rises and falls during a run. It also
refuses 10% of the payments at random (`BILLING_MOCK_PAYMENT_REFUSAL_PERCENT`),
and the output adds `Pagamentos recusados: N`. Paid confirmed seats are never cancelled: the refund workflow is a known
limitation (ADR-005 1.1.0), and Registration answers `409 REFUND_WORKFLOW_REQUIRED`.
Every activity also starts and ends during the run, at staggered moments (from
about 40% of the duration on). The activities are created far in the future,
and the organizer moves each one to "now" only after its registrations,
cancellations and waiting-list offers finish, so a saturated system starts them
later instead of rejecting late registrations. While an activity runs, a random
40–90% of its kept seats (paid ones included, once confirmed) check in. After it
ends, a random 50–100% of those check-ins get a certificate. Each activity draws
its own rates; change the ranges with the settings below. The output adds
`Check-ins: N de M inscrições confirmadas` and `Certificados emitidos: N de M
check-ins`, so the Attendance and Certificates cards in Reports move as well.
Under heavy load a request may time out. The script then resends it with the same
`Idempotency-Key` (up to 3 times) and reports `Reenvios após timeout (mesma
Idempotency-Key): N`. In this mode the peak also lasts `SCENARIO_MIXED_DURATION_SECONDS`. Each output
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
- `SCENARIO_CHECK_IN_MIN_PERCENT` / `SCENARIO_CHECK_IN_MAX_PERCENT`: 0–100,
  defaults 40 and 90. Range of the per-activity share of confirmed
  registrations that check in during the mixed load.
- `SCENARIO_CERTIFICATE_MIN_PERCENT` / `SCENARIO_CERTIFICATE_MAX_PERCENT`:
  0–100, defaults 50 and 100. Range of the per-activity share of check-ins that
  receive a certificate. For both pairs, MIN must not exceed MAX; set them equal
  for a fixed rate (for example `SCENARIO_CHECK_IN_MIN_PERCENT=100
  SCENARIO_CHECK_IN_MAX_PERCENT=100` checks everyone in). The mixed load prints
  the ranges it uses.
- `SCENARIO_CANCEL_PERCENT`: 0–100, default 20. Share of registrations in free
  activities that are cancelled before the activity starts.
- `SCENARIO_OFFER_LAPSE_PERCENT`: 0–100, default 20. Share of waiting-list
  offers the participant ignores. Set `WAITLIST_OFFER_EXPIRY_MINUTES=1` in
  `infra/.env` to see them expire during a run (the default is 30 minutes).
  With very large loads the run can outlast that expiry, so an offer the
  participant meant to accept may expire first. The mixed load reports those as
  "Ofertas que expiraram antes do aceite"; they are an expected outcome, not an
  error, and do not change the verdict.
- `SCENARIO_MAX_CONNECTIONS`: 1–200, default 20. Requests the load generator
  keeps in flight at once; the rest wait in its queue. Raise it to push the
  services harder (see the load benchmark below).

The **Waiting list** card shows the demand beyond the seats (waiting ÷
capacity) in small type beside the count, and **Certificates** shows the share of
check-ins that received one (certificates ÷ checked in).
The Reports overview also shows negative counts, tinted red, whose arrows turn
red when they grow: **Cancellations**, with the cancellation rate (cancelled ÷
(confirmed + cancelled)) beside the count; **No-shows** (confirmed registrations
without a check-in in activities that already ended), with the no-show rate
(no-shows ÷ confirmed in ended activities) beside it; and **Expired / refused**
(expired offers or reservations plus refused payments), with the share of seats
lost this way ((expired + refused) ÷ (confirmed + expired + refused)) beside it.

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

## Load benchmark (terminal)

`scripts/benchmark.sh` runs all the scenarios in parallel (`bash
scripts/cenario.sh`) once per participant count and records how the stack
behaved:

```sh
bash scripts/benchmark.sh                     # 100 500 1000 2000 3000 participants
bash scripts/benchmark.sh 500 1000 2000 --duration 60 --repeat 2
bash scripts/benchmark.sh 5000 10000 20000 --scenario pico-inscricoes --no-reset
```

`--scenario pico-inscricoes` runs only the registration peak
(`bash scripts/cenario.sh pico-inscricoes`) instead of every scenario with the
mixed load. It is much faster, since each participant sends a single
registration within `SCENARIO_PEAK_DURATION_SECONDS`, but it exercises only
Identity, Registration and PostgreSQL: payments, the waiting list, check-ins,
certificates and most of the Reporting work are left out. Use it to find the
registration limit; use the default (`--scenario all`) for the whole system.

**Warning:** before each run it restores the initial data with
`scripts/reset-demo.sh --yes`, which deletes every local registration and
signs out every browser session. Each reset adds a few minutes. Use
`--no-reset` to keep the data (faster, but later runs start from a fuller
database and are less comparable).

Options: `--scenario all|pico-inscricoes` (default `all`), `--repeat N` (runs
per count), `--duration S` (`SCENARIO_MIXED_DURATION_SECONDS`, 10–300, or
`SCENARIO_PEAK_DURATION_SECONDS`, 1–300, with `--scenario pico-inscricoes`), `--connections N`
(`SCENARIO_MAX_CONNECTIONS`, 1–200), `--stop-at` (see below),
`--keep-going` (same as `--stop-at never`), `--baseline S` (idle host sampling
before each run, default 10, 0 to skip), `--catch-up-timeout S` (default 300)
and `--output DIR` (default `docs/validation/benchmarks`).

`--stop-at` decides when the sweep ends early:

- `saturation` (default): after a run that saturated the host, so a long list of
  counts climbs until the machine's limit. A run whose verdict is VERIFICAR
  does not stop it.
- `verificar`: also after the first run whose verdict is not OK.
- `never`: runs every count.

A run whose scenarios did not start always stops the sweep.

While each scenario runs, the script samples `docker stats` and the host
(`/proc`) about every 2 s. When it ends, it waits until Reporting's counts
match Registration and Attendance (the Reports catch-up time). The scenario
client measures each request's latency per service when `SCENARIO_METRICS=1`,
which the benchmark sets; it prints one `METRICS {...}` line at the end. The
latency excludes queueing inside the client, which sends at most 20 requests at
a time.

Each run is also compared with the host resources available at that moment:

- **Baseline:** before the scenario starts, it samples the idle host (stack
  running, no load) to record the free cores and memory, the CPU used by other
  programs and the CPU clock.
- **During the run:**
  - the CPU not used by the EventHub containers (other programs, the Docker
    engine and kernel networking);
  - the kernel's stall time from `/proc/pressure`: the share of time tasks were
    ready but waiting for a CPU, memory or the disk;
  - the memory left;
  - the CPU clock, which a laptop lowers under sustained load.
- **Classification:** each run is labelled by what limited it:
  - *host*: the machine saturated (CPU ≥90% in at least a quarter of the
    samples, tasks waiting for a CPU ≥25% of the time, memory stalls ≥10%, or
    under 5% of the memory left);
  - *noisy*: other programs used at least 15% of the CPUs before the run, so
    compare it with care;
  - *architecture*: the host had spare capacity and the stack's design (one
    instance per service) set the limit.

The results go to `docs/validation/benchmarks/<UTC timestamp>/`:

- `runs.csv`: one row per run. It includes the verdicts, requests/s, failures,
  resends, p50/p95/p99/max latency (overall and per service), catch-up seconds,
  the mixed-load counts (including waiting-list offers that expired before
  acceptance), and container and host CPU/memory averages and peaks. It also
  has the baseline and stall/clock columns, the `limit` label and the top
  container by CPU and by memory.
- `resources.csv`: every resource sample, with a `phase` of `baseline`,
  `scenario` or `catch-up`. On the `host` rows `cpu_cores` holds the host CPU %
  and `mem_mib` the used memory; extra columns hold the available memory,
  background cores, stall percentages and clock.
- `report.md`: host description, configuration, results table, host resources
  during each run, plain-language findings, per-container peaks and charts.
- `charts/*.png`: latency, throughput, catch-up, CPU, the host CPU split
  (EventHub / other / free) per run and the per-container CPU timeline of the
  largest run. The charts need `matplotlib` (in `requirements-validation.txt`);
  without it they are skipped.
- `host.json` and `logs/` (each scenario and reset output).

`tests/load` is a separate, isolated API load test. The benchmark instead
measures the whole presentation stack as the audience sees it.

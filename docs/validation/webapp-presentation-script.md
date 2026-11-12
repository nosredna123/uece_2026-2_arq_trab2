# EventHub browser presentation script

## Purpose and timing

This is the live, local-browser presentation for the EventHub teaching
prototype. It shows five short demonstrations built on real service-owned
records. Identity, Billing and Notification are mocks; this is not a production
demonstration.

| # | Demonstration | Who | Target |
|---|---|---|---:|
| 1 | Create or edit an event and its activities | Organizer | 3 min |
| 2 | Simple reports for the organization's events | Organizer | 1.5 min |
| 3 | Browse events and register | Participant | 2.5 min |
| 4 | Get the certificate for an attended activity | Participant | 1.5 min |
| 5 | Registration peak run from the terminal, watched in Reports | Presenter + organizer | 3 min |

With a one-minute opening and a short close and Q&A, the full run takes about
**14 minutes**. For a **short version (about 6 minutes)**, present
demonstrations 2, 3 and 5 only. These are targets, not measured rehearsal
times.

## Preflight

### Safe local setup

1. Work from the repository root. Check that the local file is ignored before
   creating it, and do not replace an existing `infra/.env`:

   ```sh
   git check-ignore -q infra/.env
   if [ ! -e infra/.env ]; then cp infra/.env.example infra/.env; fi
   ```

2. Edit only the local synthetic configuration. Verify:

   ```text
   DEMO_WEB_ENABLED=true
   DEMO_WEB_PORT=8085
   WAITLIST_ENABLED=true
   BILLING_MOCK_SCENARIO=APPROVED
   DEMO_LAB_ENABLED=true
   IDENTITY_LAB_ENABLED=true
   DEMO_LAB_FIXTURES_PATH=/run/eventhub/scenario-participants.json
   DEMO_LAB_MAX_PARTICIPANTS=50
   DEMO_LAB_MAX_CONCURRENCY=10
   DEMO_LAB_RUN_TIMEOUT_SECONDS=60
   DEMO_LAB_SETTLE_TIMEOUT_SECONDS=30
   DEMO_LAB_RUN_TTL_SECONDS=1800
   DEMO_LAB_MAX_RETAINED_RUNS=5
   ```

   The two lab flags enable the terminal scenarios (demonstration 5). Optional:
   set `SCENARIO_PEAK_DURATION_SECONDS=60` so the peak lasts long enough to
   watch. Keep the synthetic service credentials private: do not show `.env`
   values on slides, in chat, logs, screenshots or the recording.

3. Validate the file without printing its values:

   ```sh
   python scripts/validate-environment.py infra/.env
   ```

4. Start (or restart) everything:

   ```sh
   bash scripts/run-demo-web.sh
   ```

   This builds and starts the backend services, waits for their health checks,
   then starts the presentation app. It keeps existing data and never runs
   `down`. Open the printed URL (default `http://localhost:8085`). If the
   launcher reports a port collision, choose another `DEMO_WEB_PORT`; never
   stop an unknown listener.

5. **Start at least 6 minutes before presenting.** On the first start with
   empty volumes, the app pre-fills the initial data from
   `web/demo-python/seed-data.json`: events titled
   `[DADOS INICIAIS][SIMULADO][…]` for GESAD/UECE, UECE and IASC, with
   registrations, check-ins and certificates. Certificates appear only after
   the short seeded activities end. The data is created once and is never
   duplicated by later restarts. See the
   [Demo Web README](../../web/demo-python/README.md#initial-presentation-data).

6. Before presenting, confirm:
   - the home page shows the **Who is using EventHub?** simulated sign-in;
   - the banner reads **“EventHub prototype · local demonstration · not
     production”**;
   - a terminal is open at the repository root, with no `.env` content
     visible;
   - the projector viewport is 1366×768.

### Data and control boundaries

- Event Management owns events and activities. Registration owns admission,
  payment state, capacity and the waiting list. Attendance owns check-ins and
  certificates. Reporting owns an asynchronous, event-fed projection.
- Every page action calls the existing service APIs. The app never deletes or
  resets domain records. Stop the app without deleting volumes. To start a
  rehearsal from the first-deploy data, run `bash scripts/reset-demo.sh`
  beforehand (it deletes every local volume and reseeds).
- The sign-in is simulated: each organizer sees only their own organization's
  events and reports, and each participant sees only their own registrations.

## Run of show

The service responses and the actual timestamps are authoritative; the times
below are targets.

### Opening (0:00–1:00)

On the sign-in page, point to the banner and the two groups of users.

> “Four domain services own the business state. Identity, Billing and
> Notification are mocks. This Python app is only a presentation client — not
> a fifth service. GESAD is a UECE research group; the other organizations are
> separate synthetic tenants.”

### Demonstration 1 — Organizer creates or edits an event (1:00–4:00)

1. Choose **Organizador — GESAD/UECE**. The header shows **Signed in as …**,
   and the page lists **My events** for GESAD/UECE only, including the seeded
   draft.
2. Click **New event**. Fill **Title** (for example
   `Workshop de Arquitetura de Software`), **Description**, **Starts at (UTC)**
   and **Ends at (UTC)**. Click **Create event**.
3. On the event page, under **Add an activity**, fill **Title**, the start and
   end times, **Location**, **Responsible**, **Capacity** (for example `30`) and
   **Price (R$, 0 = free)**. Click **Add activity**. Add a second activity if
   time allows.
4. Edit one field of the event (**Save event**) or of an activity
   (**Save activity**) to show editing.
5. Click **Publish event**. The status changes to published, and the event now
   appears to participants.

> “Event Management owns the event and its program. While the event is a draft
> it is invisible to participants; publishing freezes the admission snapshot
> that Registration uses to protect capacity.”

Alternative: open the seeded draft event with **Manage**, edit it and publish
it.

### Demonstration 2 — Organizer reports (4:00–5:30)

1. Click **Reports** in the header. The overview panel at the top sums all
   published events of the organization: **Events**, **Activities**,
   **Capacity**, **Confirmed**, **Pending payment**, **Waiting list**,
   **Free seats**, **Checked in**, **Certificates**, **Occupancy**
   (confirmed ÷ capacity) and **Attendance** (checked in ÷ confirmed).
   **Waiting list** shows, in small type, its size as a share of capacity, and
   **Certificates** the share of check-ins that received one.
   Red cards count bad news: **Cancellations**, **No-shows** (confirmed but not
   checked in after the activity ended) and **Expired / refused** (expired
   offers or reservations and refused payments). The first two show their rate
   in small type beside the count.
   Below it, the page lists each of the organization's
   **Published events** with, per activity: **Capacity**, **Confirmed**,
   **Pending payment**, **Waiting list**, **Checked in** and **Certificates**,
   plus a **Total** row.
2. Point to the seeded `[DADOS INICIAIS]…` events: they already have
   registrations, check-ins and certificates.
3. Point to **Updated at**: the table refreshes by itself.

> “Reports come from Reporting’s event-fed projection. It is eventually
> consistent: it can lag a few seconds behind Registration and Attendance,
> which remain the authorities. Each organizer sees only their own
> organization.”

### Demonstration 3 — Participant registers (5:30–8:00)

1. Click **Sign out** and choose **Participante simulado 02**. The participant
   lands on **Events**, which shows only published events from all
   organizations.
2. Open the event created in demonstration 1 (use **Search** if needed). The
   activities show **Available: N**.
3. Click **Register** on an activity, then **Confirm registration** once. For a
   paid activity, point out that Billing is simulated and no card data is
   collected.
4. The **Registration** page shows the live status: `PENDING_PAYMENT` first, or
   `CONFIRMED` if the mock has already approved. Follow the actual value; the
   page refreshes by itself.
5. Open **My registrations** to see the list.

> “The registration command is real; only payment is simulated. Repeating the
> same submission does not create a second registration, because the command
> carries an idempotency key.”

Optional: sign out, sign back in as the organizer and open **Reports** to see
the new registration counted.

### Demonstration 4 — Participant gets a certificate (8:00–9:30)

Uses the seeded data: in the seeded GESAD/UECE seminar, Participante simulado
02 attended the talk **Palestra: Arquitetura de Microsserviços na Prática**
(checked in) but has not requested a certificate yet.

1. Still as **Participante simulado 02**, open **My registrations** and select
   that talk. The registration is `CONFIRMED` and its check-in was seeded.
2. Click **Request certificate**, then **View certificate**.
3. Show the **Certificate of participation**. Use the browser **Print** command
   if you want.

> “Attendance issues the certificate only after a check-in and after the
> activity has ended. The record comes from Attendance; the printable layout
> is not a signed credential.”

Backup: **Participante simulado 01** already has issued certificates for the
GESAD/UECE seminar activities; select one and click **View certificate**.

### Demonstration 5 — Registration peak from the terminal (9:30–12:30)

1. Sign out and choose **Organizador — GESAD/UECE**, then open **Reports**.
   Keep this window visible.
2. In the terminal, run:

   ```sh
   bash scripts/cenario.sh pico-inscricoes
   ```

   The script publishes a fresh event `[CENÁRIO] Pico de inscrições · HH:MM:SS`
   with a 5-seat activity. Twenty synthetic participants then register over
   the configured window (default 30 s), and the script prints each outcome:

   ```text
   [  2.0s] Participante de cenário 05 → CONFIRMED
   [  2.5s] Participante de cenário 06 → WAITLISTED
   ```

3. Watch the Reports page refresh: in the new event's table **Confirmed** stops
   at 5 and **Waiting list** grows to 15 (with the default 20 participants; the
   activity always has seats for 25% of `SCENARIO_PEAK_PARTICIPANTS`); the overview panel totals rise with it.
   Each changed card shows an arrow and the difference from the previous update
   (`▲ +1`, `▼ −1 pp`) until that card changes again. Green means good news
   (more confirmed, check-ins, certificates, occupancy, attendance; a shorter
   waiting list), amber means a warning, and blue marks neutral counts. On the
   red cards the arrow is red when the count grows and green when it falls.
   Each registration appears about 2 s after the terminal line, because
   `run-demo-web.sh` runs the Registration outbox relay every 1 s instead of the
   default 30 s.
4. The script ends with the verdict, for example:

   ```text
   Resultado: 5 CONFIRMED, 15 WAITLISTED
   Vagas ocupadas: 5 de 5 — sem overbooking.
   Veredito: OK
   ```

> “Registration serializes admission per activity, so a burst never
> overbooks: the excess goes to the waiting list in arrival order. This is a
> small local burst — evidence for this run, not a production capacity
> guarantee.”

Other scenarios, if time allows (each one creates its own fresh event):

| Command | What it shows | Expected verdict |
|---|---|---|
| `bash scripts/cenario.sh ultima-vaga` | Two participants submit at the same time for the single seat. | Exactly 1 `CONFIRMED` and 1 `WAITLISTED`. |
| `bash scripts/cenario.sh envios-repetidos` | One participant submits the same registration 10 times at once (double click). | One registration; all replies are the same `CONFIRMED`. |
| `bash scripts/cenario.sh` (no argument) | All three scenarios in parallel, plus a random mixed load: 3 more events × 3 activities, paid seats, cancellations, waiting-list offers, check-ins and certificates (each activity starts and ends during the run), for about 60 s, longer with many participants. Watch the overview panel arrows move in every direction. | Each scenario `OK` and `Veredito geral: OK`. |

To make the peak slower or smaller for this run only:

```sh
SCENARIO_PEAK_DURATION_SECONDS=60 SCENARIO_PEAK_PARTICIPANTS=10 bash scripts/cenario.sh pico-inscricoes
```

### Close and Q&A (12:30–14:00)

Deliver the AI-use disclosure below and take one or two questions.

## Failure and recovery branches

| Situation | What to show / say | Safe recovery |
|---|---|---|
| A page shows an error with a service name and correlation id | Read the service and the correlation id. Do not call it an empty list. | Check `docker compose -p eventhub ps`, restore only the stated service, then reload the page. |
| **Reports could not be loaded.** | Reporting is unavailable; this is not "zero registrations". | Restore Reporting; the page recovers on reload. Never query a database directly. |
| No visible `PENDING_PAYMENT` | The Billing mock may approve immediately. Read out the actual status. | Do not submit again or change Billing to force a delay. |
| A new registration is missing from Reports | Projection lag. | Wait for the next automatic refresh. |
| **Request certificate** is rejected | The activity has not ended or there was no check-in. | Use the backup in demonstration 4 (Participante simulado 01). |
| `cenario.sh` says the app is not running | The presentation stack is down. | Run `bash scripts/run-demo-web.sh` and try again. |
| `cenario.sh` says the scenarios are disabled | `DEMO_LAB_ENABLED`/`IDENTITY_LAB_ENABLED` are not `true`. | Set both in `infra/.env` and rerun `bash scripts/run-demo-web.sh`. |
| The peak reports `WAITLIST_DISABLED` errors | The Registration waiting list is off. | Set `WAITLIST_ENABLED=true` and rerun the launcher before presenting. |
| `Veredito: VERIFICAR` | Show the printed counts and errors as they are. | Do not rerun only to get a green verdict; explain what the counts show. |
| Port unavailable | Show the launcher's port-collision message. | Choose a free `DEMO_WEB_PORT` and rerun the launcher. Never kill an unknown process. |

Controlled Reporting/Identity outages are validated by the isolated browser
runner (`bash tests/webapp/run.sh`), not by taking down the presentation
stack. See the [validation evidence](./webapp-evidence.md).

## Q&A prompts and answers

| Question | Answer |
|---|---|
| Why a Python/template presentation app? | It is a small server-rendered client for the existing APIs. It adds no domain service or database. |
| Why separate stores and APIs? | Each domain owns its records and invariants. Services exchange API requests and events rather than reading another service's tables. |
| Is this a real payment? | No. Billing is a mock with configured outcomes; the UI collects no card details. |
| Is delivery exactly once? | No. Transport is at-least-once; idempotency keys and the outbox tolerate retries and duplicates. |
| Can a participant cancel a paid seat? | Not yet. The refund workflow is designed and contracted, and the Billing mock supports it. Registration does not implement it yet, so it answers `409 REFUND_WORKFLOW_REQUIRED` and keeps the seat. It fails closed: a paid seat is never freed without a refund. This is a known limitation (ADR-005 1.1.0). |
| Why can the report lag? | Reporting builds an event-fed projection asynchronously. Registration and Attendance remain authoritative. |
| How does the peak avoid overbooking? | Registration serializes admission per activity against the published capacity snapshot; extra requests go to the waiting list. |
| Does the peak prove production scalability? | No. It is a small local burst with synthetic identities: evidence for that run, not an SLA. |
| What does a restart lose? | Only the browser sessions held in memory. Domain records stay in the service stores, and the initial data is not recreated. |
| What is needed for production? | Real identity, payment and notification integrations; secrets management; deployment hardening; monitoring; recovery and scale testing; and independent security review. |

## AI-use disclosure

“Generative AI assistance was used during implementation to draft and refine
software, tests, and documentation. The changes were checked with the automated
builds, tests, and quality gates identified in this repository. This statement
does not claim that AI independently proves correctness or replaces human
review. Project maintainers remain responsible for reviewing the deliverable,
the accuracy of claims, and the final demonstration.”

## Presenter checklist and non-destructive shutdown

- [ ] `infra/.env` is ignored, validated, synthetic and not visible to the audience.
- [ ] Billing is `APPROVED`; the waiting list and both lab flags are enabled.
- [ ] The launcher ran at least 6 minutes before presenting; seeded certificates are visible.
- [ ] The URL opens; the projector viewport is 1366×768.
- [ ] A terminal is ready at the repository root for `scripts/cenario.sh`.
- [ ] Actual statuses, timestamps, reports and verdicts are shown as returned.
- [ ] Failures remain visible; no command is retried just to change the result.

To stop only the presentation app while keeping the backend and all data, run
from the repository root:

```sh
docker compose -p eventhub --env-file infra/.env \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.demo-web.yml \
  -f infra/docker-compose.demo-lab.yml \
  --profile demo-web stop demo-web
```

This does not delete persistent volumes or service records. Do not run
`down --volumes` as routine cleanup. The
[API/script-based defense guide](./final-delivery.md#demonstration-and-defense-script)
remains available as an alternative to this browser presentation.

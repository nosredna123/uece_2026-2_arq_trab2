# EventHub browser presentation script

## Purpose and timing

This is the live, local-browser presentation for the EventHub teaching
prototype. It demonstrates real service-owned registration and attendance
records, while clearly identifying simulated dependencies and presentation-only
guidance. It is not a production demonstration.

| Version | Target duration | Includes |
|---|---:|---|
| Full | 14–16 minutes, including the Scenario Lab | Paid journey, attendance/certificate, waiting list, report, three bounded lab workloads, and Q&A. |
| Happy path | 10–12 minutes | Paid journey, attendance/certificate, waiting list, organizer projection, and Q&A; no lab workloads. |
| Short | 4–5 minutes | One fresh paid registration and the organizer report only; preflight the run and omit attendance/certificate, waiting list, print, lab, and failure demonstrations. |

These are presentation targets, not measured rehearsal times. T080 records
actual end-to-end timings. Configuration and service readiness are completed
before the clock starts. The countdowns shown by the app are the returned
activity schedule, not promises that a registration or payment has happened.

## Preflight

### Safe local setup

1. Work from the repository root. Check that the local file is ignored before
   creating it, and do not replace an existing `infra/.env`:

   ```sh
   git check-ignore -q infra/.env
   if [ ! -e infra/.env ]; then cp infra/.env.example infra/.env; fi
   ```

2. Edit only the local synthetic configuration. For the full script, verify:

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

   Keep the existing synthetic service credentials private even though they are
   examples. Do not copy `.env` values into slides, chat, logs, screenshots, or
   the recording. The example schedule is a paid start 180 seconds after
   preparation, a 60-second paid activity, and a free activity starting 900
   seconds after preparation. If the local file differs, use the actual
   `Starts` and `Ends` returned in Guided demo; do not alter dates mid-run.

3. Validate the selected file without printing its values:

   ```sh
   python scripts/validate-environment.py infra/.env
   ```

   The launcher starts/builds the normal backend services and waits for their
   health checks; Billing and Notification remain the existing mocks. When
   enabling the Scenario Lab on an already-running stack, Compose reconciles
   Identity to the opted-in configuration automatically. The operator action
   is limited to setting both lab flags and bounded values in `infra/.env`.
   Do not restart Identity during the presentation or to manufacture a
   workload result.

4. Start the optional app:

   ```sh
   bash scripts/run-demo-web.sh
   ```

   This command prepares the backend stack, then starts the presentation app;
   no separate backend `docker compose up` command is needed. It preserves
   existing service volumes and never runs `down`. Open the localhost URL
   printed by the launcher (default:
   `http://localhost:8085`). If the launcher reports a port collision, choose
   another unused `DEMO_WEB_PORT` in `infra/.env`; never stop an unknown
   listener.

   On the first start with empty volumes, the app pre-fills the initial
   presentation data from `web/demo-python/seed-data.json`. This includes
   events titled `[DADOS INICIAIS][SIMULADO][…]` for GESAD/UECE, UECE, and
   IASC, with registrations, check-ins, and certificates. Start at least 6
   minutes before presenting: the certificates exist only once their short
   seeded activities have ended. The data is created once and is never
   duplicated by later restarts. See the
   [Demo Web README](../../web/demo-python/README.md#initial-presentation-data).

5. Before presenting, confirm the home page shows the **Who is using EventHub?**
   simulated sign-in, the banner says
   **“EventHub prototype · local demonstration · not production”**, both
   participant contexts are clean, and no secret-bearing terminal or `.env`
   window is visible. Use a 1366×768 projector viewport. For the waiting-list
   segment, prepare a second private/incognito browser window (or a separate
   browser profile) so Participante simulado 01 and 02 retain independent
   sessions. Do not use two tabs sharing one persona cookie.

### Data and control boundaries

- **LIVE SERVICE** means observed from an existing EventHub service API.
- **SIMULATED DEPENDENCY** means an existing mock/simulator supplies the
  behavior; Billing is not a payment provider.
- **DEMO PRESENTATION** means guidance or synthetic browser state only.
- Event Management owns event/activity data; Registration owns admission,
  payment state, capacity transitions, and offers; Attendance owns check-in and
  certificate records; Reporting owns an asynchronous projection.
- Preparation and participant commands create real prototype records. The app
  never deletes or resets existing domain records. Stop the app without deleting
  volumes.
- Keep the prepared event/activity IDs and dates available for the presenter,
  but do not read UUIDs aloud. They are evidence references, not presentation
  content.

## Full 15-minute run of show

The time ranges are targets; service responses and actual timestamps are
authoritative. The full script includes one minute of Q&A in the final segment.

| Elapsed | Page | Persona | Exact click/action or value | Expected observed state | Speaker line | Capability / badge | Evidence |
|---|---|---|---|---|---|---|---|
| 0:00–1:00 | `/demo` (Guided demo) | Organizador — GESAD/UECE | Open **How to read the labels**. Point to the prototype banner and all three labels: **LIVE SERVICE**, **SIMULATED DEPENDENCY**, **DEMO PRESENTATION**. | The banner says this is a local demonstration, not production; each label has a text explanation. | “Four executable domain services own business state. Identity, Billing, and Notification are mocks. This Python app is an optional presentation client, not a fifth domain service. GESAD is a UECE research group; the other organizer profiles are separate synthetic tenants.” | Presentation shell / all three badges | [Context map](../architecture/context-map.md); [validation evidence](./webapp-evidence.md) |
| 1:00–2:00 | `/demo` | Organizador — GESAD/UECE | On the sign-in page choose **Organizador — GESAD/UECE** (header shows **Signed in as …**), then open **Guided demo**. Check **Confirmo a criação de novos dados simulados; registros anteriores serão mantidos.** Click **Prepare a new run** once. | **Setup progress** appears. The returned event is published; the paid and one-seat free activities show their actual IDs, start/end times, capacities, and schedule source. | “This explicit action asks Event Management to create and publish synthetic records in the GESAD/UECE tenant. It does not create a registration, payment, check-in, or certificate.” | Event Management / LIVE SERVICE; confirmation prompt / DEMO PRESENTATION | Prepared run status and returned dates; no preloaded-data claim |
| 2:00–4:00 | `/demo`, then paid checkout | Participante simulado 01 | Click **Sign out** in the header and sign in as **Participante simulado 01**, then open **Guided demo**. On the paid activity card click **Open participant checkout**. Before posting, point to **Simulated Billing** and the statement that no card details are collected. Click **Confirm registration** once. | The registration page shows the current service state, commonly `PENDING_PAYMENT` before an approved mock result or `CONFIRMED` if approval has already arrived. Follow the actual value; do not promise a visible pending interval. | “The registration command is real; payment behavior is simulated. The label on the page is ‘Confirm registration’; approval is controlled by the existing Billing mock.” | Registration / LIVE SERVICE; Billing / SIMULATED DEPENDENCY | Registration status; later, **Organizer reports** → **Service request history** (same browser session, kept across sign-out) shows service, method, status, elapsed time, and correlation ID |
| 4:00–6:00 | Registration status and certificate | Participante simulado 01 | Open **My registrations** and select the paid registration. Wait for the returned `Starts` time. When the service allows it, click **Check in**. If still before the returned `Ends` time, click **Request certificate** once to show the actual early rejection; after the returned end time, click **Request certificate** and then **View issued certificate**. Use the browser **Print** command only after the record is issued. | Attendance returns a check-in record. Certificate issuance is allowed only after the actual eligibility window; the certificate page says it is an Attendance record, not a signed credential. Print changes presentation, not service state. | “Check-in is an explicit Attendance command. The displayed clock is not eligibility proof. The record comes from Attendance; the printable layout is not independent proof of attendance.” | Attendance & Certification / LIVE SERVICE | Registration **Check-in observation**; certificate record and returned issue time |
| 6:00–8:00 | `/demo`, checkout, registrations | Participante simulado 01 and 02 in separate browser contexts | In the normal window stay signed in as **Participante simulado 01**, open the free activity checkout, click **Confirm registration**. In the separate private/profile window sign in as **Participante simulado 02**, open the same free activity checkout, click **Confirm registration**. Return to Participante simulado 01 and click **Cancel registration**. In the second window, wait for the service status `OFFERED`, then click **Accept offer**. | Participante simulado 01 is confirmed, Participante simulado 02 is waitlisted, cancellation returns `CANCELLED`, Registration returns an offer, and explicit acceptance returns `CONFIRMED`. The event page reports actual availability. | “These are independent browser sessions. Capacity is enforced by Registration; FIFO is established by the service’s offer, not by which browser I clicked first.” | Registration / LIVE SERVICE; no payment step for free activity | Both status pages; event page **Available: 0**; offer expiry if present |
| 8:00–9:00 | **Organizer reports** | Organizador — GESAD/UECE | In the normal window click **Sign out** and sign in as **Organizador — GESAD/UECE**, open **Organizer reports**, and filter by the actual activity ID if the table has multiple rows. Click **Apply report filters** or **Refresh now**. Point to **projectedAt (UTC)** and the counts. | Reporting returns its current event-fed projection. It can lag; a stale/empty/unavailable projection remains visibly distinct from service state. | “This is an eventually consistent projection, not admission authority, a payment receipt, or notification receipt. There is no direct SQL read.” | Reporting / LIVE SERVICE; polling controls / DEMO PRESENTATION | Report row, `projectedAt (UTC)`, **Service request history**; [known projection gap](./webapp-evidence.md#architecture-and-known-limits) |
| 9:00–13:00 | **Scenario Lab** | Organizador — GESAD/UECE | Stay signed in as **Organizador — GESAD/UECE**. Run **Última vaga**, then **Pico de inscrições**, then **Envios repetidos** using each **Preparar e executar …** button. After a completed last-seat run, click **Open a seat and observe FIFO promotion**; when the observed offer appears, click **Explicitly accept the observed offer**. If time permits, click **Export this observation**. | Last-seat: 2 participants contend for 1 place; inspect actual confirmed/waitlisted results. Rush: 20 requests for 5 places; inspect actual occupancy and waitlist. Duplicate: 10 same-key submissions should read back as one registration. The board displays `EXPECTED`, `MISMATCH`, or `INCONCLUSIVE` based on service responses/read-back. | “This is a bounded local burst against real Registration using synthetic identities scoped to GESAD/UECE. The other organizer tenants have separate records and cannot run this fixed-tenant lab. The winner is not predicted; the observations do not prove production-scale safety.” | Registration / LIVE SERVICE; synthetic actors / SIMULATED DEPENDENCY; preview and controls / DEMO PRESENTATION | Run board counters, statuses, elapsed time, samples, errors, and read-back; [saved synthetic exports](./webapp-evidence.md#scenario-lab-run-exports) |
| 13:00–14:00 | **Scenario Lab**, then `/` | Organizer | Point to **Recorded observations**, request history, architecture links, and the evidence report. Do not start another run. | The run record is available for review; a refresh/poll only reads retained observations. | “We can inspect the evidence without replaying it. The services remain the owners of business state.” | App history / DEMO PRESENTATION; API requests / LIVE SERVICE | [Architecture](../architecture/context-map.md); [browser evidence](./webapp-evidence.md) |
| 14:00–15:00 | No new mutation | Presenter | Deliver the AI-use disclosure and take one or two questions. | No state change. | “Generative AI assisted implementation and documentation; automated checks provide evidence, not proof. This prototype is not production-ready.” | Delivery disclosure | This script; [final delivery guide](./final-delivery.md) |

### Timing adjustments during the full version

- With the example settings, the paid activity starts three minutes after
  preparation and lasts one minute. Use the actual countdown and `Ends` value.
  If preparation or narration runs long, move the waiting-list segment before
  attendance and return to the paid activity when its real window opens.
- The example free activity starts after fifteen minutes. Registration and the
  one-seat waitlist may be demonstrated before that time; a schedule countdown
  is never presented as a Registration outcome.
- Billing can approve too quickly to display `PENDING_PAYMENT`. Say what the
  page actually returned and continue; never delay a request to manufacture a
  pending interval.
- If an expected state or projection is not available within its bounded
  polling window, show the current/unknown state and use the recovery branches
  below instead of extending the act with repeated submissions.

## Happy-path version (10–12 minutes)

Use the same preflight and one fresh run. Target **11 minutes**:

1. **0:00–1:00 — Orient:** banner, prototype boundary, and three badges.
2. **1:00–2:00 — Prepare:** Organizador — GESAD/UECE, consent checkbox, **Prepare a new run**,
   returned event/activity IDs and actual dates.
3. **2:00–4:00 — Paid registration:** Participante simulado 01, **Open participant
   checkout**, **Confirm registration**, actual status, and request-history
   correlation.
4. **4:00–6:00 — Attendance/certificate:** use actual start/end, check in,
   early rejection only if still eligible to demonstrate, then issue and print
   only after the service allows it.
5. **6:00–8:00 — Waiting list:** use separate Participante simulado 01 and 02
   contexts; register, cancel, observe `OFFERED`, explicitly accept.
6. **8:00–9:00 — Report:** show `projectedAt (UTC)` and actual counts.
7. **9:00–10:00 — Conclusion:** explain mock boundaries, evidence limits, and
   AI use.
8. **10:00–11:00 — Q&A:** choose one or two questions below.

This version omits all Scenario Lab workloads, lab exports, and controlled
failure demonstrations. It does not omit the actual end-time requirement for
certificate issuance; if the run is not eligible within the time box, state
that and omit certificate printing rather than changing the schedule.

## Short version (4–5 minutes)

Preflight a fresh run immediately before the presentation so the returned
paid-activity schedule is usable during the short slot. Target **4.5 minutes**:

1. **0:00–0:45 — Orient:** local/not-production banner and the LIVE SERVICE,
   SIMULATED DEPENDENCY, and DEMO PRESENTATION labels.
2. **0:45–1:30 — Prepare:** one explicit **Prepare a new run** action; show the
   returned paid activity and its actual schedule.
3. **1:30–3:15 — Paid flow:** Participante simulado 01 opens checkout and clicks
   **Confirm registration** once. State the actual registration outcome, then
   show the corresponding organizer projection if it is available.
4. **3:15–4:00 — Evidence/limits:** show `projectedAt (UTC)` and one correlation
   entry; say that Billing is a mock and Reporting is eventually consistent.
5. **4:00–4:30 — Close:** AI-use disclosure and questions.

This is paid registration plus report only. It explicitly omits check-in,
certificate request/print, waiting-list cancellation/acceptance, Scenario Lab,
and failure branches. If the registration remains pending or the report has no
row, show that exact state; do not claim completion or trigger another POST.

## Failure and recovery branches

| Situation | What to show / say | Safe recovery |
|---|---|---|
| Identity unavailable or a persona is rejected | Show the visible error, service name, and correlation ID. Do not call it an empty event list or a bad password. | Pause mutations. Check `docker compose --env-file infra/.env -f infra/docker-compose.yml ps` and the Identity logs. Restore only the known service; do not alter fixture credentials or stop an unknown stack. Retry only after service recovery and with the same current page/session intent. |
| Reporting unavailable | Show **Reporting unavailable** and the correlation ID; if a prior projection is shown it is marked stale. | Do not say there are zero registrations or query a database. Restore Reporting and click **Refresh now** once readiness returns. |
| No visible pending payment | Read out the actual status (`PENDING_PAYMENT` or `CONFIRMED`). Explain that the mock may approve asynchronously or immediately. | Do not click **Confirm registration** again or change Billing configuration to force a delay. |
| Paid activity has already started or ended | Show returned `Starts`/`Ends`. Explain that an early-certificate rejection applies only before the returned end time. | Skip the early-rejection demonstration if the window has closed. Do not edit the date or issue a certificate before eligibility. |
| Offer is expired or no longer `OFFERED` | Show the current Registration status; do not promise the offer is still valid. | Use **Refresh now** or reload to observe state. Do not accept an expired offer or replay the command with a new key. Use a new run only after explicit consent; retain the old records. |
| Report projection has no row yet | Show the empty, stale, or unavailable presentation and explain projection lag. | Wait only for the bounded refresh interval or click **Refresh now**. Do not query SQL or claim a notification receipt. |
| Preparation partially fails | Read the failed step, service, status/code, and correlation ID. Earlier returned steps remain evidence; later steps are not complete. | If the page says retry is safe, click **Resume preparation**; it reuses the same command key for the uncertain step. Otherwise fix the stated prerequisite before explicitly preparing another run. Never reset existing data. |
| Browser refresh or session loss | Explain that refresh reads current state and does not create a new command. A lost session can lose in-memory page references while service records remain. | Do not re-register with a new identity/key. Sign in again only as an authorized synthetic persona and read back a known record if its ID is available; otherwise stop and use a newly consented run. |
| Lab result is partial, unknown, or `INCONCLUSIVE` | Keep the unknown/error counts and raw read-back visible. Say that sent requests may have committed; no rollback is promised. | Click **Stop new requests and drain in-flight work** only for an active run. Review the retained observation/export after the stop response. Do not restart or resubmit merely to get a green verdict. |
| Scenario Lab controls are disabled | Show the read-only preview and the stated disabled/unavailable reason. | Verify both lab flags and the fixed fixture/limits, then run `bash scripts/run-demo-web.sh` again. Compose reconciles only services whose configuration changed; do not restart or stop unrelated services. |
| Localhost port is unavailable | Show the launcher's port-collision message. | Choose a free `DEMO_WEB_PORT`, validate `infra/.env`, and rerun the documented launcher. Never kill an unknown process. |

Controlled Reporting/Identity outages are validated by the isolated browser
runner, not by taking down a shared presentation stack. Use the
[validation evidence](./webapp-evidence.md) for those results; do not create an
outage on a shared or production system to imitate a failure branch.

## Q&A prompts and answers

| Question | Answer |
|---|---|
| Why a Python/template presentation app? | It is a small server-rendered client for the existing APIs. It keeps browser logic bounded and does not introduce another domain service or database. |
| Why separate stores and APIs? | Each domain owns its records and invariants. Services exchange API requests/events rather than reading another service's tables. |
| Is this a real payment? | No. Billing is a mock that supplies configured outcomes; the UI collects no card details and contacts no payment provider. |
| Is delivery exactly once? | No. The prototype uses idempotency and event/outbox handling to tolerate retries and duplicates; transport is at-least-once, not exactly once. |
| Why can the report lag? | Reporting builds an event-fed projection asynchronously. Registration and Attendance remain authoritative for their own state. |
| Why is capacity frozen? | Registration uses the Event Management admission snapshot and its own serialized transitions to protect capacity. Changing active capacity would weaken the demonstrated invariant. |
| What does a restart lose? | Browser/session and Scenario Lab observation references are in memory and may be lost. Domain records in their service stores are not deleted by stopping Demo Web. |
| Does the lab prove production scalability? | No. It is a bounded local burst using synthetic identities and a small sample. It is evidence for that observed run, not an SLA or capacity guarantee. |
| What does Stop do to in-flight work? | It stops queued/not-yet-sent work and drains/classifies in-flight requests within configured bounds. A request already sent may commit; stop is not rollback. |
| What is needed for production? | Production identity, payment, and notification integrations; secrets management; deployment hardening; operational monitoring; recovery and scale testing; and independent security/reliability review. |

## AI-use disclosure

“Generative AI assistance was used during implementation to draft and refine
software, tests, and documentation. The changes were checked with the automated
builds, tests, and quality gates identified in this repository. This statement
does not claim that AI independently proves correctness or replaces human
review. Project maintainers remain responsible for reviewing the deliverable,
the accuracy of claims, and the final demonstration.”

## Presenter checklist and non-destructive shutdown

- [ ] `infra/.env` is ignored, validated, synthetic, and not visible to the audience.
- [ ] Billing is `APPROVED`; Registration waitlist is enabled; service health is ready.
- [ ] Both lab flags and the bounded fixture configuration are enabled for the full version.
- [ ] The launcher URL opens; the required port is free; projector viewport is 1366×768.
- [ ] Participante simulado 01 and Participante simulado 02 use separate browser sessions.
- [ ] Consent is checked before preparing fresh records; no existing data will be reset.
- [ ] Narration distinguishes all three badges and mock/provider boundaries.
- [ ] Actual status, timestamps, reports, and lab read-back are shown without fabricated intervals or winners.
- [ ] Failure states remain visible; no mutation is retried with a new command identity.
- [ ] Screens and exports contain no real credentials or participant data.

To stop only the optional presentation app while preserving the backend and all
data, run from the repository root:

```sh
docker compose --env-file infra/.env \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.demo-web.yml \
  -f infra/docker-compose.demo-lab.yml \
  --profile demo-web stop demo-web
```

This does not delete containers' persistent volumes or service records. Do not
run `down --volumes` as routine cleanup. The existing
[API/script-based defense guide](./final-delivery.md#demonstration-and-defense-script)
remains available as an alternative to this browser presentation.

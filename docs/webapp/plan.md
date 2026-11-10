# Implementation plan: EventHub Python demonstration web app

**Date:** 2026-11-10  
**Status:** Implementation in progress; T045-T054 are complete.
**Scope:** A small, didactic, local presentation client for the existing prototype.  
**Task namespace:** T045-T080, continuing after the completed backend T001-T044.  
**Authority:** [constitution](../../constitution.md), approved domain scenarios,
existing HTTP/event contracts, and the user's agreed demonstration proposal.

## 1. Summary and source of requirements

Build a server-rendered Python web app using FastAPI, Jinja2, HTTPX, plain CSS,
and minimal locally served JavaScript. Run it in Docker Compose. Do not require
Node.js, npm, an additional host Python environment, or a frontend build process.
Use browser pages to demonstrate the real catalog, registration, waiting list,
check-in, certificate, and report journey. Include a clearly labeled mock
checkout, synthetic persona selection, and a guided presentation mode. After the happy path, a small **Scenario Lab**
generates bounded synthetic traffic against real services and visualizes
observed contention, idempotency, and waiting-list promotion.

The user explicitly requested a plan before code, a Python stack, a complete
demonstration journey with simulated pages where appropriate, labeled icons
identifying live/simulated/presentation sections, logical task precedence,
constitutional fidelity, and a detailed presentation plot in Markdown.
The subsequent request adds simple, didactic, visually attractive concurrency
and registration-peak demonstrations after the happy path.
The requirements below normalize that discussion into REQ identifiers for
traceability; they are not new RF/RN business rules or a replacement for the
original [backend plan](../../plan.md).

### Input inventory

- [Constitution](../../constitution.md), especially sections 1.1-1.7, and
  [ADR-006](../adr/ADR-006-demo-webapp.md) with its
  [versioned rationale](../rationales/ADR-006.json).
- [Domain baseline](../architecture/domain-scenarios.md),
  [service boundaries](../architecture/service-boundaries.md),
  [data ownership](../architecture/data-ownership.md), and ADR-001 through ADR-006.
- [Event Management](../contracts/openapi/event-management.yaml),
  [Registration](../contracts/openapi/registration.yaml),
  [Attendance](../contracts/openapi/attendance-certification.yaml),
  [Reporting](../contracts/openapi/reporting.yaml), and
  [common HTTP conventions](../contracts/openapi/common.yaml).
- [Messaging/mock behavior](../contracts/asyncapi/README.md) and
  [observability conventions](../validation/observability.md).
- [Identity fixtures](../../mocks/identity-access/src/identity_access_mock/fixtures.json).
- Existing paid-flow, failure, concurrency, and load runners under `tests/`.
- [Current delivery evidence](../validation/final-delivery.md), Compose,
  configuration schema/validator, Python test image, and CI workflow.

No separate topology, frontend design system, or existing frontend project
was found. This is additive presentation work, not a framework migration.
Existing contracts and the agreed discussion supply the design inputs.
No new domain API, database model, payment protocol, or domain policy is planned.

## 2. Scope and acceptance requirements

All requirements are required for the first version unless explicitly marked
out of scope. The existing services remain the authority for all business state.

| ID | Requirement and observable acceptance |
|---|---|
| REQ-001 | One local Python presentation container serves HTML/CSS/JS and calls existing HTTP APIs. No new database, broker consumer, Node/npm workflow, shared domain import, or generic gateway. |
| REQ-002 | Every page has a prototype banner and every functional section has a centrally defined icon plus text badge: LIVE SERVICE, SIMULATED DEPENDENCY, or DEMO PRESENTATION. Mixed pages label their sections separately; meaning never depends on color alone. |
| REQ-003 | Visitor, Participant 1, Participant 2, and Organizer are explicit synthetic personas, not real login. Backend authorization remains enforced; browser sessions, CSRF, participant data, and persona switching are isolated. Global-admin UI is excluded from version one. |
| REQ-004 | Browse/search/paginate published events and activities, render schedules/prices, and fetch authoritative availability. Failed availability is unavailable, not zero; an observation is not a reservation. |
| REQ-005 | Checkout is a DEMO PRESENTATION before creating a paid registration. One explicit confirmation command creates the registration and initiates existing simulated Billing. No card/bank fields, fake charge, provider call, or browser-controlled outcome. |
| REQ-006 | Display every Registration state, payment/offer expiry, refund state, and current own registrations. Provide bounded polling and explicit refresh; never interpret timeout or network failure as payment refusal or approval. |
| REQ-007 | Demonstrate a one-seat free activity using two participants: confirmation, waiting list, cancellation, FIFO offer, and acceptance. Show the real results and deadline; do not calculate a global queue position from a sequence number. |
| REQ-008 | Record check-in only through Attendance. Distinguish missing projection, ineligibility, unknown check-in state, and observed check-in success; issue/retrieve a certificate through Attendance after actual eligibility. |
| REQ-009 | Display a printable certificate view based on an issued backend record. Label the layout DEMO PRESENTATION; do not claim a signed credential, PDF-generation API, or verified attendance duration. |
| REQ-010 | Organizer can create a synthetic event, add paid/free activities, and publish via existing APIs. Track current-session drafts/IDs without inventing draft inventory endpoints or querying another service's storage. |
| REQ-011 | Organizer sees real activity reports with `projectedAt`, refresh/poll behavior, and distinct loading, empty, stale-last-result, and unavailable states. Projection freshness is not a heartbeat or admission authority. |
| REQ-012 | Guided preparation creates uniquely named synthetic records through existing APIs, then guides the paid journey and free waiting-list journey. Show schedules/countdowns and partial setup progress; no data deletion, clock manipulation, hidden state mutation, or automatic reconfiguration of mocks. |
| REQ-013 | All mutations use CSRF protection, backend idempotency, disabled duplicate submission, and Post/Redirect/Get. Retry an uncertain identical command with the same key; changed payload/new intent gets a new key. Never fake exactly-once presentation state. |
| REQ-014 | Propagate UUID correlation identity, preserve backend error codes/statuses, and show a small sanitized request history identifying service/method/status/elapsed time. It is browser-session HTTP evidence, not broker events, raw logs, or proof of notification delivery. |
| REQ-015 | Explicit validated external configuration, localhost binding, bounded session state, request timeouts, safe HTML rendering, no credential output, no Docker socket/host process controls, and no open proxy or secret-bearing browser payload. |
| REQ-016 | Provide simple containerized start/test commands that work with an existing EventHub stack, preserve user volumes/configuration, check readiness, and report failures. Existing backend runners and normal Compose operation remain compatible. |
| REQ-017 | Deliver unit/HTTP/contract, browser, real-service integration, accessibility/visual checks, and existing backend regression evidence, including honest gaps and skips. No claim that HTTP-only tests validate browser behavior. |
| REQ-018 | Deliver `docs/validation/webapp-presentation-script.md`: detailed timed presentation plot, exact actions/personas, expected evidence, speaker notes, live/mock explanations, happy-path-then-corner-case narrative, fallback/recovery paths, Q&A, and safe shutdown; rehearse and record evidence. |
| REQ-019 | Scenario Lab offers fixed, one-click presets for last-seat contention, a small registration rush, duplicate submission, and cancellation/FIFO promotion. Generate actual concurrent HTTP requests against fresh free activities; no paid stress traffic or user-entered targets. |
| REQ-020 | Visually present each lab run with participant tiles, seat occupancy, ordered waiting-list observations, a small request timeline, measured counters/timings, and a final evidence verdict. Animate observations, not invented outcomes; distinguish offered from confirmed and sampled safety from proof of the invariant. |
| REQ-021 | Provide an opt-in, finite synthetic participant pool recognized by Identity; preserve all four existing fixtures and default authorization behavior. Hard limits, one global active run, bounded retention/deadlines, safe stop, session isolation, and explicit unknown/partial results prevent the presentation from becoming a general load tool. |

### Explicitly out of scope

- Real user lifecycle/login, payments, email/SMS delivery, or cloud deployment.
- Interactive per-payment approve/refuse buttons; changing Billing's process-wide
  scenario from a browser would affect unrelated requests.
- Backend draft-list/detail extensions, general CRUD administration, global-admin
  screens, new certificate/read-status APIs, and new policy parameters in domain services.
- WebSockets, SPA state frameworks, chart libraries, CDN assets, gateways,
  Kubernetes, new persistence, or a frontend-facing Docker socket.
- Signed certificates, backend PDF generation, invented notification receipts,
  broker-event streaming, automatic refund completion, or test data reset/delete.
- A general-purpose load-testing console, arbitrary targets/credentials, unlimited
  users, destructive fault injection, or production performance certification.
  Existing concurrency/load runners remain the authoritative automated gates.

## 3. Technical context and deliberate simplicity

| Concern | Planned choice |
|---|---|
| Runtime | Python 3.12 using the existing project's pinned container conventions; non-root runtime image. |
| Presentation | FastAPI routes, Jinja2 autoescaped templates, local CSS, tiny progressive-enhancement JS. Ordinary links/forms work without JS; automatic polling/countdowns enhance them. |
| Service access | One managed HTTPX client with fixed, validated service base URLs and explicit operations. No arbitrary URL forwarding, internal admission proxy, direct SQL, or broker credentials. |
| State | Bounded, expiring, server-side in-memory browser sessions, one Uvicorn worker/replica. No Redis/database. Clearly document loss on restart and unsupported multiworker operation. |
| Session cookie | Opaque cryptographically random session ID only; HttpOnly, SameSite=Lax, path `/`, bounded lifetime. Local HTTP setting documented; Secure must be enabled if HTTPS is introduced. |
| Identity input | Read-only existing fixture JSON for the four standard personas. A separate finite scenario-participant fixture file is enabled explicitly in both the Identity mock and the lab. Validate names, roles, uniqueness, and bounds; no token-generation endpoint or mock-package imports. |
| Packaging | Independent `web/demo-python/` package, Dockerfile, runtime/test targets, constraints/dependency pins. Reuse compatible repository versions; resolve/pin any added dependencies during scaffold. |
| Compose | Opt-in `demo-web` profile, `demo-web` service, localhost port 8085 by default, configurable and validated. Separate browser-test profile/target. |
| New infrastructure | None on the host beyond existing Git/Docker/Compose. Python Playwright/Chromium run only in the browser-test image, without a host npm workflow. |
| Language/layout | Initial copy follows the existing English README and agreed badge labels. Plain, consistent wording; no translation framework. Desktop projector friendly, readable narrow-screen layout, keyboard accessible. |

This component is a presentation adapter, not a fifth domain service or API
gateway. The new ADR must document that bounded role and the single-process
session limitation, preserving ADR-001/004/005 invariants. A future requirement
for multiple replicas, durable UI drafts, real authentication, or interactive
payment outcomes requires a separate proposal, not hidden scope expansion.

### Configuration contract to implement

Extend `infra/.env.example`, `infra/environment.schema.json`,
`scripts/validate-environment.py`, and related tests coherently. Introduce the
web fields as optional for the backend-only workflow; require/validate them
when the web launcher/profile is selected. Do not silently rewrite an existing
ignored local environment file or make old backend configurations unusable.
Use a documented explicit setup step to add missing web settings.

- `DEMO_WEB_PORT` (example 8085; valid host port and no mapping collision).
- `DEMO_WEB_ENABLED` (explicit true required to enable synthetic persona mode).
- `DEMO_WEB_SESSION_TTL_SECONDS`, `DEMO_WEB_MAX_SESSIONS`,
  `DEMO_WEB_MAX_COMMANDS_PER_SESSION`, `DEMO_WEB_HISTORY_LIMIT`
  (positive bounds; expired/evicted session shows an explicit restart warning).
- `DEMO_WEB_POLL_INTERVAL_SECONDS`, `DEMO_WEB_POLL_TIMEOUT_SECONDS`
  (positive, timeout greater than interval; examples 2 and 60).
- `DEMO_WEB_PAID_START_DELAY_SECONDS`, `DEMO_WEB_ACTIVITY_DURATION_SECONDS`,
  `DEMO_WEB_FREE_START_DELAY_SECONDS` (examples 180, 60, 900; enforce positive
  values and ordering so the two journeys fit within a generated event).
- `DEMO_WEB_EVENT_BASE_URL`, `DEMO_WEB_REGISTRATION_BASE_URL`,
  `DEMO_WEB_ATTENDANCE_BASE_URL`, `DEMO_WEB_REPORTING_BASE_URL`
  (explicit internal HTTP origins; no caller-supplied URLs).
- `DEMO_WEB_IDENTITY_FIXTURES_PATH` (readable fixed mount path).
- Reuse `HTTP_TIMEOUT_SECONDS` for outgoing calls; do not copy business policy
  values into UI defaults. Session/poll/schedule values are presentation/test
  configuration, not reservation, refund, or eligibility policies.
- `DEMO_LAB_ENABLED`, `IDENTITY_LAB_ENABLED` (examples false in backend-only
  operation; explicit true in the documented lab setup).
- `DEMO_LAB_FIXTURES_PATH`, `IDENTITY_LAB_FIXTURES_PATH` (the same read-only
  `mocks/identity-access/scenario-participants.json` mounted in both containers;
  startup validates at most 50 unique additional PARTICIPANT identities and no
  collision with the original four).
- `DEMO_LAB_MAX_PARTICIPANTS`, `DEMO_LAB_MAX_CONCURRENCY`,
  `DEMO_LAB_RUN_TIMEOUT_SECONDS`, `DEMO_LAB_SETTLE_TIMEOUT_SECONDS`,
  `DEMO_LAB_RUN_TTL_SECONDS`, `DEMO_LAB_MAX_RETAINED_RUNS`
  (validated positive operational limits; examples 50, 10, 60, 30, 1800, 5).
  Server enforces independent safety ceilings of 50 participants and 10
  in-flight requests; these are prototype resource limits, not domain policy.
  Presets unavailable under lower operator limits are disabled with a reason,
  never silently resized.

Compose cannot give a profile-aware variable a silent permissive fallback.
Use a separate web Compose overlay if required to keep backend-only rendering
independent of missing web variables. Choose that mechanical wiring in T076
and document it; the default run path must remain copyable and validated.

## 4. Constitution check and applied guidelines

| Principle | Conformance / implementation obligation |
|---|---|
| 1.1 Domain and traceability | REQ table, tasks, checkpoints, ADR-006 plus rationale; no new domain rule. Escalate an actual policy change before implementation. |
| 1.2 KISS and boundaries | Four existing domain services remain unchanged. One Python presentation adapter, no domain imports/SQL, no general proxy. |
| 1.3 Fail-fast and protection | Real backend outcomes; bounded errors/retries, CSRF, validated personas, explicit dependency failures. No success-shaped fallbacks. |
| 1.4 External configuration | Validate all web settings and schedule/operational bounds; preserve ignored local secrets and backend compatibility. |
| 1.5 Code quality | Typed Python, repository lint/style, local shared UI helpers only, independent package, contracts for UI-facing JSON. |
| 1.6 Test gates | Tests before behavior; existing >=95% domain mutation gates stay intact. The UI adds no domain core; justify adapter/template mutation exclusion in ADR-006 and document its test coverage rather than claiming domain score for UI. |
| 1.7 Operability | Container-only official path, correlation diagnostics, live/mock labels, reproducible rehearsal and evidence. |

**Gate:** No constitutional waiver is requested. Adding domain logic or changing
an invariant blocks work pending human approval and versioned ADR/rationale
updates. ADR-006 records the agreed presentation scope, not a waiver to share data.

**Applied guidelines:** The available migration guideline catalog contains
Struts-to-Spring transformations, none applicable to this additive FastAPI/
Jinja2 client. Follow the constitution, existing Python package/container
patterns, HTTP contracts, and runtime-validation test-tier guidance instead.

## 5. Pages, sections, and interactions

### Navigation and labels

Navigation: **Home / Guided demo / Events / My registrations / Organizer /
Scenario Lab**. The lab is organizer-only and visibly opt-in.
Header always shows selected synthetic persona, prototype notice, and badge
legend. A small architecture explanation links each capability to its owner.

| Badge | Suggested icon | Meaning and disclosure |
|---|---|---|
| LIVE SERVICE | Server outline | Data/command comes from an implemented service. Not a production-readiness or current-health claim. Include service name. |
| SIMULATED DEPENDENCY | Flask outline | Executable Identity, Billing, or Notification mock; no external provider. |
| DEMO PRESENTATION | Presentation-panel outline | Screen/layout/guidance only; not an additional implemented business capability. |

Use locally rendered SVGs with visible text, decorative icons hidden from
assistive technology, a keyboard-operable explanation control, and contrast
suitable for projection. Status (loading/unavailable/stale) is a separate
indicator. One page can legitimately contain all three badge types.

### Required pages

| Route / page | Behavior | Important restrictions |
|---|---|---|
| `/` | Scope, architecture legend, persona selector, start guide. | Persona selector is SIMULATED DEPENDENCY / Identity, not a password login. |
| `/events` | Search and bounded pagination of public catalog. | No inference that published-event listing is organizer draft inventory. |
| `/events/{id}` | Activities, price/date details, availability and checkout links. | Per-activity unavailable/error state; avoid unbounded all-catalog fan-out. |
| `/activities/{id}/checkout` | Read-only summary; free and paid action wording differs. | No registration before explicit CSRF-protected POST. Price comes from service; no card data. |
| `/registrations` | Own registrations with real state/refund/deadlines and action links. | Visitor/organizer cannot use participant commands. |
| `/registrations/{id}` | Current state, bounded polling, retry/refresh, accept/cancel/check-in/issue actions. | Unknown check-in state is not false; `202` refund pending retains confirmed occupancy. |
| `/registrations/{id}/certificate` | Retrieved certificate record plus print stylesheet. | 404 is not a fabricated certificate; styling is DEMO PRESENTATION. |
| `/organizer` | Current-session creation workflow and scoped reports. | No drafts after session expiry invented from local cache. No ownership override. |
| `/demo` | Guided preparation, steps, IDs, schedule/countdown, links to relevant pages. | Mutations explicit; persona switching preserves guide resource references but never participant response data across personas. |
| `/lab` | Preset cards, prediction, run controls, live observation board, and final summary. | Organizer only; global run lock; no arbitrary load parameters or infrastructure controls. |
| `/lab/runs/{id}` | Session-owned read-only snapshots and final sanitized run summary. | Bounded polling; no synthetic credentials or other sessions' run details; stop is a CSRF-protected POST. |
| `/health` | Presentation process readiness/configuration check. | Backend service health displayed separately; Reporting failure does not mark a confirmed registration failed. |

Form POST endpoints and small same-origin read-only JSON polling endpoints
must be documented in the adapter's FastAPI OpenAPI output where applicable.
They call explicit public domain operations, never arbitrary backend paths.

### Service operation map

- Catalog: Event `GET /events`, `/events/{eventId}`,
  `/events/{eventId}/activities`, `/activities/{activityId}`; availability:
  Registration `GET /activities/{activityId}/availability`.
- Checkout: Registration `POST /registrations`, `{activityId}` only; participant
  derives from validated synthetic credential, not hidden form participant ID.
- My registrations: Registration `GET /registrations`, `GET /registrations/{id}`,
  `POST /registrations/{id}/cancel`, `POST /registrations/{id}/accept-offer`.
- Attendance: `POST /registrations/{id}/check-in`,
  `POST /registrations/{id}/certificate`, `GET /registrations/{id}/certificate`.
- Organizer/setup: Event `POST /events`, `POST /events/{id}/activities`,
  `POST /events/{id}/publish`; no new list-drafts or update/cancel administration UI.
- Reports: `GET /organizations/{organizationId}/reports` with bounded
  `activityId`, `limit`, `offset`; organization derives from organizer fixture.
- Dependency health: only documented health endpoints. Do not send backend
  credentials to the browser, call internal admission directly, or label
  generic process health as proof of business correctness.

### Mutations, sessions, and uncertain outcomes

1. Server creates a bounded command record before an outgoing mutation:
   session + persona + operation + target + canonical validated payload,
   command ID, idempotency key, correlation ID, and outcome/uncertainty.
2. Form submissions carry command identity plus a CSRF token. A per-command
   lock prevents two local submissions racing; backend idempotency remains
   authoritative. Refresh uses Post/Redirect/Get, never another implicit POST.
3. If an HTTP mutation times out, retain the same key and payload; show
   "Outcome not yet observed" and offer explicit retry/read-back. Do not
   automatically create a new command. Replaying a create may return the old
   initial state; follow it with GET for current state.
4. On payload change, generate a different command/key after explicit new intent.
   Never let a browser choose another subject's command or alter stored targets.
5. Session expiry/restart clears presentation memory only. Warn that backend
   effects may remain; return to own-registration lookup, not automatic retry
   with a fresh key. No cross-session exactly-once claim is made.
6. Persona changes clear person-specific results/history/polling and invalidate
   old forms. Session-local event/activity guide IDs may remain for demonstration;
   subsequent requests are freshly authorized by the selected persona/backend.
7. Disallow automatic non-GET side effects on navigation, poll, print, health,
   persona display, or page refresh. Persona changes themselves use protected POST.

### Guided data and timing

Create one unique event containing a paid activity and a one-seat free activity
using organizer APIs. Use fresh UUID-backed titles per preparation and server UTC
timestamps. With the example settings, the paid activity starts after three
minutes, lasts one minute, and the free activity starts after fifteen minutes;
derive an enclosing valid event period with a documented margin. All offsets
are external configuration and persisted results, not changed on later refresh.

Prepare data immediately before the interactive portion. Display actual times
and warn if the registration window has closed. "Prepare a new run" creates
new records only after explicit confirmation; it never deletes earlier runs.
Track completed setup commands/IDs so a partial failure retries the same step
without duplicating already-created events/activities. App restart may require
a fresh run and must say so.

Default Billing approval is a documented prerequisite, not a guaranteed outcome:
the UI observes whatever the configured mock actually returns. Waiting-list
demonstration requires enabled waiting list. Missing prerequisites are reported
with operator setup instructions, never corrected through Docker or configuration
writes by the web process. Do not expose hidden mock scenario switches.

### Scenario Lab: small experiments, real outcomes

The lab is a **traffic simulator**, not a simulated Registration service.
Synthetic actors are SIMULATED DEPENDENCY / Identity; the controller and
visualization are DEMO PRESENTATION; registration, occupancy, and promotion
responses are LIVE SERVICE. All three labels remain visible on the lab board.
An expected result is labeled **Prediction**, never displayed as an observation.

#### Four preset cards

| Preset | Bounded workload | What the audience should learn |
|---|---|---|
| **The last seat** | Two distinct synthetic participants, one free seat, concurrent start. | One should be CONFIRMED and the other WAITLISTED; the winner is not predetermined. |
| **Registration rush** | Twenty distinct synthetic participants, five free seats, at most ten requests in flight. | If all commands succeed and settle: five confirmed, fifteen waiting; capacity is limited by the service, not by the animation. This is a small burst, not a production-scale benchmark. |
| **Double-click storm** | One synthetic participant, one free seat, ten submissions sharing the same intent, idempotency key, and payload; bounded concurrency. | Repeated commands resolve to the same registration identity and a single occupied seat. Compare returned IDs and authoritative own-registration read-back. |
| **A seat opens** | After a settled last-seat/rush run, explicit cancel of one confirmed free registration; observe the next actual offered registration, then explicitly accept it. | Backend FIFO promotion and OFFERED versus CONFIRMED. Determine the next candidate from backend queue sequence/current states, never from avatar index or client completion order. |

Use fresh, uniquely named free activities sufficiently in the future for each
new run; frozen capacities are never edited to reset an experiment. Preparation
uses the existing organizer/API command ledger. Runs require waitlist enabled;
if unavailable or not observed during preparation, show an actionable blocked
state. The cancellation action operates only on the current run's records using
their own synthetic participant credentials. No general impersonation controls.

#### Visual storyboard

1. **Predict:** a card asks "What happens when 20 people try for 5 seats?"
   Show preset parameters, scope, labels, and one **Prepare and run** button.
2. **Observe:** a responsive row/grid of seat icons, participant tiles grouped
   by **Not sent / In flight / Confirmed / Waiting / Offered / Unknown or error**,
   and a compact CSS/SVG timeline of request dispatch/completion observations.
   Card labels use friendly synthetic names, not tokens or giant UUIDs.
3. **Explain:** an evidence panel shows configured capacity, latest observed
   occupied/available, completed commands, pending reads, and errors separately.
   Permit mixed/partial outcomes; WAITLISTED is not a request error.
4. **Conclude:** show **Expected behavior observed**, **Mismatch detected**,
   or **Inconclusive**, with registration IDs in expandable details and a small
   sanitized JSON download for the run. No automatic green "PASS" based only
   on command completion or a capacity gauge remaining within its drawn range.

Simple local CSS/SVG is sufficient: no canvas engine, charts dependency, or
animation framework. Counters and seat icons reflect raw observed values; if
occupied exceeds capacity, display the raw number and a clear red/text anomaly,
never clamp it to make the experiment look correct. Never show a live queue rank
unless it is justified by observed backend ordering; otherwise show a sequence
label and explain its meaning. Narrate before/after for a fast run rather than
delaying service calls or inventing a progression.

Provide readable text alongside color, reduced-motion support, keyboard controls,
bounded `aria-live` updates, and optional **Pause display updates** distinct from
**Stop sending requests**. Pausing never pauses backend work. A completed run can
be reviewed as a labeled **Recorded observations** timeline, not a new live run.

#### Runner and measurement limits

- One bounded asyncio task group in the single web process, a global lock, a
  concurrency semaphore, explicit start barrier for the two-person race,
  and a session-owned finite run record. Do not introduce Celery, Redis, a
  worker service, shell execution, Docker control, or an external load platform.
- A per-participant ledger retains key/target/payload before dispatch; duplicate
  preset deliberately reuses one command. Timeouts remain unknown until replay
  with the same key/read-back resolves them; never resend with new identity.
- Retain run state independent of the viewing page; refresh/navigation cannot
  restart requests. Block persona switching during a running lab experiment
  until stopped/drained, with an explicit explanation. Session expiry stops new
  dispatch; already-sent requests may commit and must not be described as rolled back.
- Stop cancels queued/not-sent work, drains in-flight HTTP operations up to the
  configured bound, and records unresolved outcomes. Release the global lock only
  after tasks are drained/cancelled and classified. No cancellation promises
  rollback; never leave unobserved background jobs after run teardown.
- Reconcile registrations and availability at the end through public APIs.
  If a snapshot conflicts with in-flight responses, display its timestamp and
  reconcile before assigning a verdict. Polling/sampling has its own small fixed
  request budget so the observer does not create unbounded load.
- Display actual request-completion latency (including declared client queuing,
  if measured), in-flight peak, elapsed time, HTTP errors, and approximate
  completed requests/second with sample size. Do not manufacture a smooth demand
  curve or interpret 20 samples as a statistically reliable production SLA.
- State "**No overbooking observed in N samples; final counts reconciled**",
  not "proved no overbooking at any instant." Link to existing automated
  concurrency/property tests for stronger invariant evidence. A contradictory
  observation is a failed experiment, not an animation defect to conceal.
- Initially show a zero-side-effect preview until preparation is explicitly
  confirmed. No new run on GET, page refresh, replay, or export. No cleanup
  deletes domain records; only expired in-memory observation data is removed.

## 6. Implementation phases and ordered tasks

T045-T054 are complete; T055-T080 remain unimplemented. Tests belong before the
corresponding behavior within each phase. File paths below are repository-relative
**planned** outputs.
No parallel markers are used: the primary execution order is T045 through T080.
Explicit prerequisites appear in section 7; numerical order is the safe default.

### Phase 0 -- Decision and scope gate

#### Plan 0.1 -- Record the bounded presentation decision

**Requirements:** REQ-001, REQ-002, REQ-015, REQ-017, REQ-019, REQ-021.  
**Inputs:** constitution, accepted discussion, ADR-001/004/005/006, current contracts.

- [x] T045 [Plan:0.1] Write `docs/adr/ADR-006-demo-webapp.md` and `docs/rationales/ADR-006.json` documenting Python server rendering, section labels, server-only synthetic personas, no domain/storage/gateway expansion, bounded single-worker sessions, and presentation-layer mutation exclusion. Preserve earlier decisions/versions; request human arbitration only for an actual invariant conflict.
- [x] T046 [Plan:0.1] Extend `tests/docs/test_rationales.py` to cover exactly ADR-001 through ADR-006 and their artifacts/versions/invariants; update `docs/rationales/README.md` and this plan's decision references. Run rationale/semantic/invariant gates without disabling existing protections.

**Done when:** ADR/rationale schema and inventory tests pass; no earlier
invariant is weakened; presentation scope is documented before implementation.
T070 subsequently updates the same decision with the opt-in lab fixture details.

### Phase 1 -- Blocking foundations

#### Plan 1.1 -- Independent package and validated settings

**Requirements:** REQ-001, REQ-015, REQ-016, REQ-017.
**Inputs:** existing Python Dockerfiles, schema/validator, Compose conventions.

- [x] T047 [Plan:1.1] Add `web/demo-python/tests/conftest.py`, `test_startup.py`, and `test_settings.py` plus `tests/configuration/test_demo_web_environment.py`; define missing/invalid settings, fixture roles, backend-only compatibility, session bounds, schedule ordering, host-port collisions, and startup-failure cases before implementation.
- [x] T048 [Plan:1.1] Create `web/demo-python/pyproject.toml`, `constraints.txt`, `.dockerignore`, `Dockerfile`, `src/demo_web/__init__.py`, `app.py`, and `settings.py`; integrate optional web fields into `infra/.env.example`, `infra/environment.schema.json`, and `scripts/validate-environment.py`. Pin compatible dependencies, enforce one worker, separate runtime/test dependencies, and pass T047 without modifying local `infra/.env`.

**Done when:** independent test/runtime images build; health and invalid-startup
tests pass; no Node or host install required; existing configurations still validate.

#### Plan 1.2 -- Explicit HTTP clients and diagnostic errors

**Requirements:** REQ-004, REQ-006, REQ-008, REQ-010, REQ-011, REQ-014, REQ-015.

- [x] T049 [Plan:1.2] Write `web/demo-python/tests/test_clients.py` and `test_errors.py` with HTTPX mock transports using valid existing contract payloads: all needed operations, authorization/correlation headers, timeouts, refused connections, 401/403/404/409/422/503, malformed success bodies, and credential redaction.
- [x] T050 [Plan:1.2] Implement `src/demo_web/clients.py`, `models.py`, and `errors.py` with typed operation-specific calls, managed client lifecycle, bounded HTTP timeouts, response validation, correlation/error preservation, no open redirects/proxy, and bounded sanitized per-session request history. Never retry mutations using an implicit new key.

**Done when:** each required backend operation is contract-tested; dependency
failures remain explicit; history contains no bearer/service keys or request bodies.

#### Plan 1.3 -- Persona sessions and safe command handling

**Requirements:** REQ-003, REQ-005, REQ-007, REQ-013, REQ-014, REQ-015.

- [x] T051 [Plan:1.3] Write `web/demo-python/tests/test_sessions.py` and `test_commands.py` for separate browser sessions, CSRF rejection/origin checks, invalid personas, expiry/restart, bounded eviction, same-command retry after timeout, concurrent duplicate POSTs, changed payload, persona invalidation, and PRG.
- [x] T052 [Plan:1.3] Implement `src/demo_web/sessions.py`, `commands.py`, and persona POST routes in `routes.py`: opaque cookies, server-side fixture lookup, bounded locked command records, safe CSRF validation, retained key/correlation per intent, explicit uncertainty, and persona-specific isolation. Use server-side command targets/payloads, not trusted hidden role/owner fields.

**Done when:** duplicate/uncertain submissions cannot cause a fresh-key retry;
cross-session/persona requests are denied; no backend effects on a GET or refresh.

### Phase 2 -- Didactic shell and labels

#### Plan 2.1 -- Accessible page structure and provenance badges

**Requirements:** REQ-002, REQ-003, REQ-014, REQ-015, REQ-017.

- [x] T053 [Plan:2.1] Add `web/demo-python/tests/test_pages.py` and `test_badges.py` verifying banner/legend, section-level badge semantics, keyboard explanation controls, escaped content, correct page status/errors, no token leakage, and distinct implementation/operational status indicators.
- [x] T054 [Plan:2.1] Implement `src/demo_web/presentation.py`, `templates/base.html`, `templates/home.html`, `templates/error.html`, `templates/partials/badge.html`, `templates/partials/request_history.html`, and `static/app.css`; centralize the three icon/text definitions and page metadata. Provide readable projector layout, focus visibility, semantic headings, and no remote assets.

**Done when:** mixed pages disclose each source correctly; badges are meaningful
without color; partial dependency failure cannot become an unlabelled empty page.

### Phase 3 -- Participant journey

#### Plan 3.1 -- Published catalog and availability

**Requirements:** REQ-002, REQ-004, REQ-014, REQ-017.

- [ ] T055 [Plan:3.1] Write `web/demo-python/tests/test_catalog.py`: search, pagination, unpublished 404, activity schedule/minor-unit price formatting, unavailable availability, stale observations, and no registration on page load.
- [ ] T056 [Plan:3.1] Implement catalog/detail routes in `routes.py` and `templates/events.html`, `templates/event_detail.html` using explicit Event/Registration clients. Fetch only the displayed activity page, label availability age and non-guarantee, and show partial failures without hiding valid catalog data.

**Done when:** visitor can browse without credentials; no fabricated availability
or unbounded catalog fetch; backend content is escaped and amounts are not floats.

#### Plan 3.2 -- Simulated checkout with actual registration command

**Requirements:** REQ-002, REQ-003, REQ-005, REQ-006, REQ-013.

- [ ] T057 [Plan:3.2] Write `web/demo-python/tests/test_checkout.py` covering no pre-submit side effects, free/paid wording, required participant, CSRF, backend price authority, duplicate submission, lost response/retry, and pending/refused outcomes.
- [ ] T058 [Plan:3.2] Implement `templates/checkout.html` and checkout GET/POST routes in `routes.py`; show LIVE SERVICE activity/registration sections and SIMULATED DEPENDENCY Billing disclosure inside a DEMO PRESENTATION checkout. Submit only `activityId` via the retained command, redirect to current registration state, and never collect card data or offer payment-outcome controls.

**Done when:** one explicit click initiates the actual paid protocol; no premature
success page, fake provider call, or hidden automatic registration is possible.

#### Plan 3.3 -- Registration states, waiting list, and bounded refresh

**Requirements:** REQ-003, REQ-006, REQ-007, REQ-013, REQ-014, REQ-017.

- [ ] T059 [Plan:3.3] Write `web/demo-python/tests/test_registrations.py` for all seven registration states, refund PENDING while CONFIRMED, offer deadline/acceptance, foreign-resource concealment, paused polling, terminal-state stops, errors retaining last-observed data labels, and lost mutation responses.
- [ ] T060 [Plan:3.3] Implement `templates/registrations.html`, `templates/registration_detail.html`, registration routes/poll responses in `routes.py`, and `static/status.js`; provide cancel/accept actions, UTC-derived readable times, bounded non-overlapping GET polling, explicit refresh/retry, and no global waitlist-position inference. Stop old polling on navigation/persona change.

**Done when:** states are backend-derived; timeout says unknown/pending rather
than refused; free FIFO promotion works through actual commands; refunds do not
prematurely release/display a cancelled slot.

### Phase 4 -- Organizer setup and attendance

#### Plan 4.1 -- Minimal organizer creation workflow

**Requirements:** REQ-003, REQ-010, REQ-012, REQ-013.

- [ ] T061 [Plan:4.1] Add `web/demo-python/tests/test_organizer.py` for organizer-only event/activity creation, scoped organization, schedule errors, session-local drafts, replay of failed steps, explicit publish, and no cross-organization override.
- [ ] T062 [Plan:4.1] Implement `templates/organizer.html` and create/add/publish routes in `routes.py`, reusing the command ledger. Show returned IDs/current-session draft steps, validate forms without duplicating domain policy, and explain loss of UI draft references after session expiry/restart.

**Done when:** an organizer can create/add/publish using only existing APIs;
the interface does not claim to list all drafts or store a second domain database.

#### Plan 4.2 -- Check-in, actual certificate, and print view

**Requirements:** REQ-002, REQ-008, REQ-009, REQ-013, REQ-017.

- [ ] T063 [Plan:4.2] Write `web/demo-python/tests/test_attendance.py`: check-in success/replay, unknown read state after reload, projection 409, ineligible 422, certificate-before-end rejection, foreign/missing certificate 404, issue/retrieve identity, escaped printable content, and no POST on print.
- [ ] T064 [Plan:4.2] Add check-in/issue/retrieve routes to `routes.py`, `templates/certificate.html`, and `static/print.css`; show record ownership/IDs and service-issued timestamps, distinguish live record from visual certificate layout, and render server errors/correlation IDs without inventing eligibility.

**Done when:** no check-in/certificate success exists without a real response;
the printable view contains a real issued record and a prototype disclaimer.

### Phase 5 -- Guided demo and report projection

#### Plan 5.1 -- Safe preparation and guided sequence

**Requirements:** REQ-002, REQ-005, REQ-007, REQ-010, REQ-012, REQ-013, REQ-014.

- [ ] T065 [Plan:5.1] Write `web/demo-python/tests/test_demo.py` for unique valid schedules, one paid/one free activity, repeated/partially failed preparation, explicit fresh-run intent, no data reset, stale schedule warnings, fixed run IDs, and no backend success inferred from countdowns.
- [ ] T066 [Plan:5.1] Implement `src/demo_web/demo.py`, `templates/demo.html`, and guided routes in `routes.py`; call the existing command/client helpers, show resumable setup steps and observed outcomes, link each persona/action explicitly, and display actual schedule/countdown. No Docker controls, business-rule override, unattended check-in, or per-payment scenario mutation.

**Done when:** a presenter can prepare and follow both journeys, including retry
after a partial setup failure, without scripts/SQL or fabricated backend events.

#### Plan 5.2 -- Organizer reports with eventual consistency

**Requirements:** REQ-002, REQ-003, REQ-011, REQ-014, REQ-017.

- [ ] T067 [Plan:5.2] Write `web/demo-python/tests/test_reports.py` for scoped organization, empty projection, `projectedAt`, counters, pagination/filtering, unavailable service, retained-but-stale data, and bounded report refresh independent of registration success.
- [ ] T068 [Plan:5.2] Implement report routes and `templates/reports.html`, reusing `static/status.js` and shared badges/history; display counts in simple tables/cards, source timestamps, and explicit eventual-consistency text. Do not poll entire unrelated organizations or invent payment/notification receipts.

**Done when:** report lag/failure is visible without changing registration state;
no unauthorized organization access or unavailable-as-zero behavior exists.

### Phase 5B -- Scenario Lab after the happy path

#### Plan 5.3 -- Opt-in synthetic crowd without weakened authorization

**Requirements:** REQ-003, REQ-015, REQ-019, REQ-021.

- [ ] T069 [Plan:5.3] Add `mocks/identity-access/tests/test_scenario_identities.py` and `web/demo-python/tests/test_lab_settings.py`: default four-fixture behavior, disabled pool rejection, finite participant-only pool, token/subject uniqueness, malformed/oversized fixture failure, preserved roles, no new admin authority, matching mock/client configuration, and absence of browser credential output.
- [ ] T070 [Plan:5.3] Add finite fictitious `mocks/identity-access/scenario-participants.json`; extend the mock's `src/identity_access_mock/settings.py` and `app.py` with explicit opt-in loading, while preserving default `fixtures.json` validation. Update `web/demo-python/src/demo_web/settings.py`, `infra/.env.example`, `infra/environment.schema.json`, `scripts/validate-environment.py`, mock README, and ADR-006/rationale to document the lab pool and hard resource limits. No credential-minting API, arbitrary token acceptance, or domain contract change.

**Done when:** twenty distinct lab actors authenticate through the actual mock
only when enabled; baseline personas and disabled-mode authorization regressions
pass; fixture credentials never reach the browser or exported run data.

#### Plan 5.4 -- Bounded concurrent experiment runner

**Requirements:** REQ-013, REQ-014, REQ-015, REQ-019, REQ-020, REQ-021.

- [ ] T071 [Plan:5.4] Write `web/demo-python/tests/test_scenarios.py` for fixed workloads, barrier/semaphore enforcement, one active global run, same-key parallel replays, precise dispatch counts, sample budgets, fresh free activities, per-run ownership, partial failures, stop/drain/expiry/shutdown, unknown outcomes, FIFO candidate selection, contradictory samples, and reconciled verdicts. Reuse scenarios/assertions from existing concurrency tests conceptually; do not import test code into runtime.
- [ ] T072 [Plan:5.4] Implement `src/demo_web/scenarios.py` and bounded run/control routes in `routes.py`, reusing explicit clients and command storage. Support last-seat/rush/duplicate presets plus explicit cancel/accept continuation, server-only lab identities, per-run timers/counters, sanitized snapshot/export, and real final read-back. Run worker tasks must be supervised by application lifespan; no subprocesses, infrastructure changes, paid traffic, or background task leaks.

**Done when:** generated concurrency reaches the service rather than being
silently serialized by the ordinary UI command lock. The duplicate preset
intentionally allows bounded concurrent sends of one prepared command/key;
ordinary form double-submit protection remains unchanged. Timeout/stop cannot
be shown as rollback or success, and no operator can select arbitrary targets.

#### Plan 5.5 -- Attractive observation board and browser acceptance

**Requirements:** REQ-002, REQ-017, REQ-019, REQ-020, REQ-021.

- [ ] T073 [Plan:5.5] Add `web/demo-python/tests/test_lab_pages.py` and `tests/webapp/test_scenario_lab.py`: exact preset preview, correct provenance, seat/counter values, non-clamped anomaly rendering, unknown/error distinction, reduced motion, run access controls, safe stop, refresh without restart, real two-user/rush/replay read-back, and explicit promotion/acceptance without presuming the winner.
- [ ] T074 [Plan:5.5] Implement `templates/lab.html`, `templates/partials/lab_board.html`, and `static/lab.js`; extend local CSS and navigation with four preset cards, participant groups, seat grid, small timeline, measured counters, accessible controls, evidence verdict, and recorded-observation review. Keep server state authoritative and browser polling bounded; no graphing dependency or fabricated slow-motion "live" results.

**Done when:** a presenter explains each experiment in under a minute after
setup, results are legible at projector resolution, a fast run remains reviewable,
and failures stay visible. Real browser execution occurs with T077-T078.

### Phase 6 -- Reproducible delivery, validation, and presentation

#### Plan 6.1 -- Containerized start/test path and integration wiring

**Requirements:** REQ-001, REQ-015, REQ-016, REQ-017, REQ-021.

- [ ] T075 [Plan:6.1] Add `tests/configuration/test_demo_web_compose.py` and shell-runner checks for fixed internal URLs, read-only fixture mounts, opt-in Identity pool wiring, localhost binding/port collision, no DB/broker keys or Docker socket, unchanged backend-only startup, already-running stack, error exits, and volume preservation.
- [ ] T076 [Plan:6.1] Wire `infra/docker-compose.yml` (and `infra/docker-compose.demo-web.yml` if needed for profile-aware validation), `infra/Dockerfile.python-tests`, `.github/workflows/ci.yml`, `.importlinter`, `scripts/run-demo-web.sh`, `web/demo-python/README.md`, and root `README.md`. Add independent runtime/test/browser targets, repository-wide test/package discovery, canonical no-host-runtime start/test commands, health waits, optional read-only lab fixture mounts, and full test/lint/architecture CI coverage. Explain the explicit one-time Identity restart needed when enabling its additional fixture pool; never silently restart it from a browser.

**Done when:** `bash scripts/run-demo-web.sh` starts/builds the required local
services and web app, prints the local URL and mock prerequisites, and works
with the current stack. It does not run the pre-start free-port gate against
EventHub's own running containers, overwrite `.env`, or delete volumes. It
reports unrelated port collisions explicitly. Test runner is separate, bounded,
fail-fast, and does not leave a browser helper running.

#### Plan 6.2 -- Browser journeys and evidence

**Requirements:** REQ-002 through REQ-017 and REQ-019 through REQ-021 as
applicable; especially REQ-017.

- [ ] T077 [Plan:6.2] Create `tests/webapp/conftest.py`, `test_paid_journey.py`, `test_waitlist_journey.py`, `test_failures_and_isolation.py`, and `run.sh`; use Python Playwright/Chromium in its pinned test image against real services and isolated data. Include `test_scenario_lab.py` in discovery. Cover refresh/double-submit, different browser contexts/personas, actual certificate/read-back, keyboard badges/forms, JS polling, missing/invalid credentials, and Reporting/Identity failure behavior without weakening backend gates.
- [ ] T078 [Plan:6.2] Execute the validation matrix below and write `docs/validation/webapp-evidence.md` with commands, image/runtime versions, exit codes, pass/fail/skip counts, timings, viewport checks, sanitized screenshots, test-data scope, known gaps, scenario-run exports, and recovery verification. Update `docs/architecture/containers.puml`, its PNG export, and `context-map.md` to show the presentation adapter without adding new domain ownership; link evidence from `docs/validation/final-delivery.md`.

**Done when:** all primary journeys pass in an actual browser and backend
regressions remain green. Missing browser/infra capability is UNVERIFIED, not
passed via mocks. No evidence claim predates the execution it describes.

#### Plan 6.3 -- Detailed Markdown presentation plot and rehearsal

**Requirements:** REQ-002, REQ-012, REQ-014, REQ-016, REQ-017, REQ-018,
REQ-019, REQ-020, REQ-021.

- [ ] T079 [Plan:6.3] Write `docs/validation/webapp-presentation-script.md` following section 9: preflight, timed acts, exact clicks/personas, expected real responses, badge disclosures, prepared-data timing, narration, happy-path-then-lab sequence, failure branches, short version, evidence references, questions/answers, AI disclosure, and non-destructive shutdown. Link it from both READMEs and `docs/validation/final-delivery.md`; preserve the existing API/script-based defense guide.
- [ ] T080 [Plan:6.3] Rehearse the full presentation on fresh synthetic data using only documented commands/actions; fix tightly coupled documentation/UX issues, re-run affected tests and full regression gates, and record actual timings/limits in `docs/validation/webapp-evidence.md`. Create `docs/webapp/checkpoints/tasks-to-impl.yaml` and task/batch reports as implementation proceeds; close every REQ with observed evidence, not checkbox presence alone.

**Done when:** another presenter can follow the Markdown plot without hidden
terminal fixes or secret exposure; the long and short scripts identify real/mock
boundaries and do not promise outcomes unavailable through existing contracts.

## 7. Explicit dependencies and safe execution order

Each task also consumes the constitution and this plan. Tests-first tasks
establish expected behavior and may initially fail until the paired implementation.
Do not mark an implementation complete while its required tests fail.

| Task | Prerequisite task IDs |
|---|---|
| T045 | None; consume the accepted proposal and existing backend artifacts. |
| T046 | T045 |
| T047 | T046 |
| T048 | T047 |
| T049 | T048 |
| T050 | T049 |
| T051 | T050 |
| T052 | T051 |
| T053 | T052 |
| T054 | T053 |
| T055 | T054 |
| T056 | T055 |
| T057 | T056 |
| T058 | T057 |
| T059 | T058 |
| T060 | T059 |
| T061 | T054, T052 |
| T062 | T061 |
| T063 | T060 |
| T064 | T063 |
| T065 | T062, T064 |
| T066 | T065 |
| T067 | T060, T062 |
| T068 | T067 |
| T069 | T052, T068 |
| T070 | T069 |
| T071 | T070 |
| T072 | T071 |
| T073 | T072 |
| T074 | T073 |
| T075 | T048, T066, T068, T074 |
| T076 | T075 |
| T077 | T076 |
| T078 | T077 |
| T079 | T078 |
| T080 | T079 |

Milestones: T054 = honest labeled shell; T060 = participant checkout/waitlist;
T068 = both complete journeys plus reports; T074 = Scenario Lab;
T076 = one-command local startup; T080 = rehearsed, evidenced deliverable.

## 8. Testing strategy and execution gates

**appType:** Server-rendered HTML with small read-only polling enhancements.  
**primaryValidationStack:** pytest + FastAPI TestClient + HTTPX mock transports
for independent adapter tests; Docker Compose with real Java/Python services,
PostgreSQL and RabbitMQ; Python Playwright with Chromium in a test container
for browser journeys. Browser dependencies must not enter the runtime image.

### Baseline and existing tests

Planning-time verification on 2026-11-10: Docker daemon available; host Node
v18.19.1 exists but is not a prerequisite or selected build tool. Canonical run:

```sh
docker compose --env-file infra/.env.example -f infra/docker-compose.yml \
  --profile test run --rm python-suite-test \
  python -m pytest -x --import-mode=importlib
```

Result: **185 passed, 6 skipped**, exit code 0; existing Starlette/AnyIO warning.
This is the existing image's backend baseline, not evidence for unbuilt UI tests.
Future tests must be installed/copied into the canonical image and rerun after
rebuilding it. Do not claim that a cached pre-UI image covers new files.

**legacyTestAssets (discovered):** existing pytest paid-flow, failure, concurrency,
and load runners, service tests, contract tests, architecture/configuration/ADR
tests, Java tests, and domain mutation gates. **Decision: reuse unchanged** for
backend semantics; add browser journeys, do not replace passing API tests with
UI mocks. No existing browser UI suite was found.

### Unit/HTTP test infrastructure

`web/demo-python/tests/conftest.py` provides an application factory, validated
synthetic fixtures, independent session clients, deterministic clock injection
for presentation expiry/countdown tests only, and HTTPX mock transport injection.
No test app construction may require live PostgreSQL/RabbitMQ.

Validate mock payloads against existing contracts so independently testable
clients do not invent response shapes. Test every relevant non-2xx and malformed
2xx response. Real integration tests still use actual database/server clocks.
Injecting the UI clock must never change domain service eligibility.

Test new startup and configuration fields through existing repository test
commands, enforce package import boundaries, and add dependency manifests to
Docker build/CI installation. Test badge provenance as an exact output contract,
not merely existence of a generic "mock" banner.

### Real browser/integration journeys

| Journey | PASS criteria |
|---|---|
| Paid registration and certificate | Create/publish unique data, browse, checkout POST once, observe actual confirmation, check in, verify early issuance is denied, wait until actual activity end, issue/retrieve same certificate, observe report. No fake success or bypassed timing. |
| Waiting list | One-seat free activity, participant 1 confirmed, participant 2 WAITLISTED, cancel 1, observe OFFERED, accept as 2, confirm; availability never exceeds capacity. |
| Command reliability | Double-click/refresh and lost-response retry retain command identity; read-back proves a single backend effect. Expired session is explicit, never automatically resubmits unknown intent. |
| Identity/isolation | Separate contexts, persona switching, invalid/missing identity, CSRF failure, old-form rejection, and foreign IDs reveal no participant data or authorization bypass. |
| Projection/failure | Reporting/Identity dependency errors visibly differ from empty data; pending operations remain pending/unknown; successful registration is not undone by a report failure. Restore any controlled test-only failure before teardown. |
| Presentation/accessibility | Every page/section has correct text/icon provenance, keyboard actions work, templates escape input, print uses real certificate, no browser/console errors or token leaks, status updates stop correctly. |
| Last-seat/rush lab | Actual distinct mock principals, synchronized two-user race and bounded twenty-user burst; reconcile expected confirmed/waiting counts and observe availability. Validate raw anomaly rendering independently with injected adapter responses; do not inject fake results into a live run. |
| Replay/promotion lab | Concurrent same-key sends return one registration ID and occupied seat; cancel a run-owned free registration, observe FIFO offer, then explicitly accept. No predetermined winner or client-order FIFO claim. |
| Lab controls/isolation | Disabled pool rejects lab tokens; default fixtures unchanged; unrelated sessions cannot inspect/control runs; stop, refresh, persona changes, expiry, server shutdown, and run lock enforce limits without pretending to undo sent requests. |

At least three multi-step browser files exercise setup, action, backend read-back,
and safe teardown; do not count single health probes or mocked HTTP tests as E2E.
Test screenshots at desktop/projector (1366x768) and narrow width (390x844),
no clipped essential actions; loading/errors do not shift an action under a click.
Request failures have useful correlation IDs. Screenshots must exclude credentials.

### Capability fallbacks and known gaps

- **Infra tier:** Docker Compose is required for real acceptance. If unavailable,
  mock-transport adapter tests may continue, but database/broker/domain integration
  is UNVERIFIED; fix/record the environment failure before release.
- **Browser tier:** Python Playwright/Chromium install occurs inside a test image.
  Host Node absence is irrelevant. If browser installation/runtime fails, record
  the exact command/error and run TestClient tests only as partial evidence;
  JS, rendering, focus, print, and browser-cookie behavior remain UNVERIFIED.
- These axes are independent; losing one does not excuse skipping the other.
  No new host installs or silently passing degraded release gates.

### Data and cleanup

Independent unit sessions, UUID-named events/activities per integration run,
no static existing IDs. Browser acceptance uses a uniquely named Compose project
and isolated ports/volumes, with the synthetic template, not the user's `.env`.
Discover actual mappings; never guess host port 5432 or stop unknown listeners.
Only the test's own resources may be removed. The ordinary demo launcher
preserves the main stack and its volumes. Do not copy the existing live runner's
global scenario changes into an unisolated browser-test suite.

### Task-level and final gates

For each behavioral implementation task: run focused tests with `-x`, fix
failures, then run all repository tests with `-x` using a rebuilt canonical
image; capture counts and exit codes. Java-affecting changes require the Java
test targets with existing fail-fast settings. Final gate includes full
test-profile build, browser journeys, lint/import boundaries, configuration
and contract/rationale checks, existing domain mutation thresholds, and
`git diff --check`. Distinguish intentional environment-dependent skips from
failed or missing new browser tests.

Per-task checkpoints/report files belong under `docs/webapp/checkpoints/`;
do not reset the completed backend task checkpoint. Reports record task IDs,
requirements, files, commands, exit codes, counts, limitations, and pending work.
Commit only when the user requests/authorizes the implementation workflow;
use task-prefixed messages and preserve existing co-author conventions.
Do not auto-merge or bypass protected semantic/invariant review.

## 9. Required presentation plot (future Markdown deliverable)

T079 must create **`docs/validation/webapp-presentation-script.md`**.
This plan does not substitute for that rehearsed, concrete script.

Required structure:

1. **Purpose, audience, and duration:** a full 14-16 minute version including
   the lab, a 10-12 minute happy-path version, and a 4-5 minute shortened
   version; actual timings confirmed during T080.
2. **Prerequisites:** one-command launch, validated web settings, default
   Billing approval and enabled waitlist, service readiness, browser URL,
   clean/new session, projector viewport, no secrets on screen.
3. **Timing sheet:** columns for elapsed time, page, persona, exact click/form
   value, expected state, speaker sentence, capability owner/badge, and evidence.
4. **Act 1 -- orient (about 1 minute):** prototype banner and all three badges;
   explain four domain services versus three mocks without claiming production.
5. **Act 2 -- prepare (about 1 minute):** select Organizer, prepare new data,
   show returned IDs/dates and real publish result; explain API-only data ownership.
6. **Act 3 -- paid participant (about 2 minutes):** Participant 1, catalog,
   mock checkout before POST, "Confirm registration and simulate payment",
   actual pending/current state and correlation trace. If approval is immediate,
   narrate the observed outcome instead of promising a visible pending interval.
7. **Act 4 -- attendance/certificate (about 1-2 minutes):** explicit check-in;
   show actual end-time countdown and early rejection only if applicable;
   issue/retrieve/print after eligibility, distinguish record from layout.
8. **Act 5 -- free waiting list (about 2 minutes):** participant 1 registers,
   participant 2 waits, participant 1 cancels, participant 2 accepts actual offer;
   explain frozen capacity/FIFO and no backend state created by persona switching.
9. **Act 6 -- organizer projection (about 1 minute):** real report counts and
   `projectedAt`, lag, eventual consistency, no direct SQL, no notification receipt claim.
10. **Act 7 -- corner cases (about 3-4 minutes):** transition with "One person
    was easy. What if everyone clicks together?" Predict/run the last-seat
    race, then the twenty-person rush; show actual seat/waitlist counts.
    Demonstrate same-key duplicate submissions and, if time permits, the
    explicit cancellation/offer/accept continuation. Explain non-deterministic
    winners, mock identities versus real contention, and small-burst limits.
    Review recorded observations if the live run finishes too quickly.
11. **Act 8 -- conclude (about 1 minute):** request inspector is HTTP evidence,
    link architecture/ADRs and tests, explicit mock boundaries, AI-use declaration,
    and limitations. Do not read all technical IDs aloud; explain their purpose.
12. **Failure branches:** Identity/Reporting unavailable, no pending interval,
    late presentation/window closed, offer expired, projection not ready,
    partial setup failure, session lost, browser refresh, unavailable port.
    Give recovery actions that preserve state and authorization. Never use a
    pretend success screenshot as evidence of a live failed step. Include a lab
    with partial/unknown results, stop/drain semantics, and disabled identity pool.
13. **Short version:** paid flow and report only with preflighted fresh data;
    state explicitly which waiting-list/print/lab/failure segments are omitted.
14. **Q&A:** why Python templates, why separate stores, why mock payment,
    why no exactly-once claim, why report lag, why immutable capacity,
    what restart loses, why sampled concurrency is not a scalability proof,
    what happens to in-flight requests on stop, and what is needed for production.
15. **Cleanup:** non-destructive stop command; reset/delete volumes not part
    of the normal demo. Preserve the existing script/API presentation alternative.

The final script must use actual implemented labels/routes/commands, not
unbuilt placeholders. Include a compact printable presenter checklist.

## 10. Planned project structure

```text
web/demo-python/
  pyproject.toml
  constraints.txt
  Dockerfile
  .dockerignore
  README.md
  src/demo_web/
    __init__.py
    app.py
    settings.py
    clients.py
    models.py
    errors.py
    sessions.py
    commands.py
    presentation.py
    routes.py
    demo.py
    scenarios.py
    templates/
      base.html, home.html, error.html
      events.html, event_detail.html, checkout.html
      registrations.html, registration_detail.html
      organizer.html, demo.html, certificate.html, reports.html
      lab.html, partials/lab_board.html
      partials/badge.html, partials/request_history.html
    static/app.css, static/status.js, static/print.css, static/lab.js
  tests/
    conftest.py
    test_startup.py, test_settings.py, test_clients.py, test_errors.py
    test_sessions.py, test_commands.py, test_pages.py, test_badges.py
    test_catalog.py, test_checkout.py, test_registrations.py
    test_organizer.py, test_attendance.py, test_demo.py, test_reports.py
    test_lab_settings.py, test_scenarios.py, test_lab_pages.py
tests/webapp/
  conftest.py
  test_paid_journey.py
  test_waitlist_journey.py
  test_failures_and_isolation.py
  test_scenario_lab.py
  run.sh
scripts/run-demo-web.sh
mocks/identity-access/scenario-participants.json  # opt-in synthetic crowd
mocks/identity-access/tests/test_scenario_identities.py
docs/webapp/
  plan.md
  checkpoints/spec-to-plan.yaml
  checkpoints/plan-to-tasks.yaml
  checkpoints/tasks-to-impl.yaml        # created during implementation
docs/adr/ADR-006-demo-webapp.md          # future task
docs/rationales/ADR-006.json             # future task
docs/validation/webapp-evidence.md       # future task
docs/validation/webapp-presentation-script.md  # future task
```

Keep related route handlers together initially; do not split into a large
framework of layers unless actual complexity demands it. All paths are planned
implementation targets, not claims that the files already exist.

## 11. Requirement Mapping

Each REQ has implementation tasks and observable evidence; a plan checkbox
alone is insufficient to close it. Original RF/RN/RQ obligations are preserved.

Tasks are obtained from the plan-item pairs above and the machine-readable
[plan-to-tasks checkpoint](checkpoints/plan-to-tasks.yaml).

| REQ ID | Description | Plan Items | Planned implementation evidence | Backend trace |
|---|---|---|---|---|
| REQ-001 | Python presentation-only client | 0.1, 1.1, 6.1 | ADR-006, package/Dockerfile, Compose | RQ01, RQ05, RQ08 |
| REQ-002 | Honest accessible icon/text labels | 0.1, 2.1, 3.1, 3.2, 4.2, 5.1, 5.2, 5.5, 6.2, 6.3 | central badge helper, section templates, browser evidence, script | RQ05, RQ08; prototype scope |
| REQ-003 | Synthetic personas and isolation | 1.3, 2.1, 3.2, 3.3, 4.1, 5.2, 5.3, 6.2 | sessions, persona/CSRF tests, browser isolation | RF01, RN07, RQ07 |
| REQ-004 | Catalog and real availability | 1.2, 3.1, 6.2 | clients, catalog routes/templates/tests | RF04, RN01 |
| REQ-005 | Checkout before simulated payment | 1.3, 3.2, 5.1, 6.2 | checkout, command tests, actual paid browser flow | RF05, RF07, RN02 |
| REQ-006 | Actual registration/refund states | 1.2, 3.2, 3.3, 6.2 | registration views, bounded polling/state tests | RF05, RN02-RN04 |
| REQ-007 | Real free waiting-list journey | 1.3, 3.3, 5.1, 6.2 | cancel/accept views, browser capacity read-back | RF06, RN01, RN04 |
| REQ-008 | Actual check-in/eligibility | 1.2, 4.2, 6.2 | Attendance client/routes and error tests | RF09-RF10, RN06 |
| REQ-009 | Printable backend-issued certificate | 4.2, 6.2 | certificate template, print CSS, record read-back | RF10, RN06 |
| REQ-010 | Scoped organizer create/publish | 1.2, 4.1, 5.1, 6.2 | organizer workflow, retained IDs, tests | RF02-RF03, RN07 |
| REQ-011 | Honest report projection | 1.2, 5.2, 6.2 | report routes/template, timestamps/failure tests | RF11, RQ03, RQ06 |
| REQ-012 | Resumable didactic demonstration | 4.1, 5.1, 6.2, 6.3 | demo helper/page, prep tests, rehearsed script | RQ08; existing RN policies |
| REQ-013 | CSRF/idempotency/uncertain commands | 1.3, 3.2, 3.3, 4.1, 4.2, 5.1, 5.4, 6.2 | command ledger, locks, retry/PRG/browser tests | RQ04, RQ07 |
| REQ-014 | Correlated honest HTTP evidence | 1.2, 1.3, 2.1, 3.1, 3.3, 5.1, 5.2, 5.4, 6.2, 6.3 | clients/history/errors and request inspector | RQ06 |
| REQ-015 | Safe validated bounded local app | 0.1, 1.1, 1.2, 1.3, 2.1, 5.3, 5.4, 6.1, 6.2 | settings/schema, sessions, redaction, Compose tests | RQ07-RQ08 |
| REQ-016 | Container-only start/test commands | 1.1, 6.1, 6.2, 6.3 | launcher, runtime/test images, README/rehearsal | RQ08 |
| REQ-017 | Browser and backend regression gates | 0.1, 1.1, 2.1, 3.1, 3.3, 4.2, 5.2, 5.5, 6.1, 6.2, 6.3 | CI/canonical image, browser tests, webapp evidence | RQ01, RQ04-RQ08 |
| REQ-018 | Detailed Markdown presentation plot | 6.3 | webapp-presentation-script.md, rehearsal evidence | RQ08; delivery scope |
| REQ-019 | Real concurrent scenario presets | 0.1, 5.3, 5.4, 5.5, 6.2, 6.3 | scenarios.py, lab routes, browser read-back, presentation | RN01, RN04, RQ04 |
| REQ-020 | Didactic observed-result visualization | 5.4, 5.5, 6.2, 6.3 | lab templates/JS, counters/verdict tests, evidence | RQ06, RQ08; presentation scope |
| REQ-021 | Bounded opt-in lab and identity pool | 0.1, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3 | mock fixture/loader, settings, run limits, isolation tests | RN07, RQ07-RQ08 |

## 12. Risks, limits, and definition of complete

- Fast Billing approval can skip a visibly pending interval; never force an
  artificial intermediate state. Record observed transitions only.
- Event/offer expiry and projection lag make live demos timing-sensitive;
  configurable fresh schedules, clear deadlines, explicit refresh, and rehearsal
  mitigate this without changing business rules.
- Presentation sessions are not durable and cannot be scaled across workers.
  Lost UI state must not be presented as rollback of backend data.
- Existing APIs do not support full draft administration or check-in-status
  reads. Keep those limitations visible; do not bypass them with SQL.
- Runtime configuration additions must not break strict backend validators,
  existing ignored environment files, container tests, or other project users.
- Adding ADR-006 requires updating the exact-five rationale inventory tests.
  Do not simply add the JSON and leave CI broken.
- The identity mock currently recognizes exactly two participants and two other
  personas. A genuine twenty-participant run therefore requires T069-T070;
  repeating two tokens must not be presented as twenty different participants.
- Scenario Lab adds no guarantee of production throughput or complete temporal
  safety proof. Keep measured samples, verdict assumptions, and hard limits visible.

**Complete means:** all 21 requirements implemented and evidenced, all 36 tasks
verified, correct labels on mixed sections, full paid/certificate/report and free
FIFO journeys running in an actual browser, no new host-tool prerequisite,
existing domain tests/mutation gates preserved, explicit failure/isolation cases
covered, a real bounded Scenario Lab demonstrated after the happy path, a
rehearsed Markdown presentation plot delivered, and no unreported
gaps or fake success. No implementation or completion is claimed by this plan.

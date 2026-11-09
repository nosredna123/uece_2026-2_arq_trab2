# EventHub context map

**Status:** candidate architecture for the prototype  
**Domain baseline:** [approved scenarios](domain-scenarios.md), version 1.0.0  
**Traceability:** RF01–RF11, RN01–RN07, RQ01–RQ08

EventHub evaluates seven candidate service boundaries. The runnable prototype
contains four domain services and three explicitly identified integration
mocks. A capability is not split into a service per requirement, and the mocks
do not represent production domain-service implementations.

## Context and dependency graph

```mermaid
flowchart LR
  Participant[Participant]
  Organizer[Organizer]
  GlobalAdmin[Global administrator]

  subgraph Runtime["Local Docker Compose runtime"]
    Identity["Identity & Access<br/>mock"]
    EventManagement["Event Management<br/>Java / Spring Boot"]
    Registration["Registration<br/>Java / Spring Boot"]
    Billing["Billing<br/>mock"]
    Notification["Notification<br/>mock"]
    Attendance["Attendance & Certification<br/>Python / FastAPI"]
    Reporting["Reporting<br/>Python / FastAPI"]
    Broker[(RabbitMQ)]
    Postgres[(PostgreSQL cluster<br/>separate logical data per service)]
  end

  Participant -->|"public published catalog"| EventManagement
  Participant -->|"register, status, cancel"| Registration
  Participant -->|"eligible check-in / certificate APIs"| Attendance
  Organizer -->|"manage own events / query own reports"| EventManagement
  Organizer -->|"authorized reporting API"| Reporting
  GlobalAdmin -->|"cross-organization event and report operations"| EventManagement
  GlobalAdmin -->|"cross-organization reporting API"| Reporting

  EventManagement -->|"validate identity and organization scope"| Identity
  Registration -->|"validate identity and participant scope"| Identity
  Attendance -->|"validate identity and participant scope"| Identity
  Reporting -->|"validate identity and organization scope"| Identity

  Registration -->|"published activity / capacity contract; details TBD"| EventManagement

  EventManagement --> EventDB[(Event Management data)]
  Registration --> RegistrationDB[(Registration data)]
  Attendance --> AttendanceDB[(Attendance & Certification data)]
  Reporting --> ReportingDB[(Reporting projection)]
  EventDB -.-> Postgres
  RegistrationDB -.-> Postgres
  AttendanceDB -.-> Postgres
  ReportingDB -.-> Postgres

  Registration -->|"persisted outbox publisher"| Broker
  Broker -->|"payment request"| Billing
  Billing -->|"approval / refusal / timeout result"| Broker
  Broker -->|"payment result"| Registration
  Broker -->|"registration / payment events"| Notification
  Broker -->|"registration events"| Attendance
  Attendance -->|"attendance / certificate events"| Broker
  Broker -->|"registration / payment / attendance events"| Reporting

  classDef implemented fill:#e7f4e4,stroke:#367a32,color:#1d421a;
  classDef mock fill:#fff0d6,stroke:#a36a00,color:#573900;
  class EventManagement,Registration,Attendance,Reporting implemented;
  class Identity,Billing,Notification mock;
```

Legend: green nodes are the four executable domain services; amber nodes are
the three integration mocks. The single PostgreSQL cluster is deployment
infrastructure only: each implemented service owns its own logical database
or schema and credentials. Dashed links indicate storage in that cluster, not
cross-service access.

## Interaction summary

| Producer | Consumer | Interaction | Boundary and purpose |
|---|---|---|---|
| Participant / organizer | Event Management | HTTP API | Public published catalog; event/activity management within authorization scope. |
| Participant | Registration | HTTP API | Request, query, and cancel own registrations; view reservation/offer state. |
| Registration | Event Management | Explicit API/contract to be finalized | Obtain published activity rules/capacity without reading Event Management data directly. Exact contract and capacity-update policy remain for ADRs/contracts (T008/T010/T012). |
| Protected domain services | Identity & Access mock | Identity validation contract | Resolve synthetic identity, role, and organization scope; each domain service enforces authorization. Exact token/API shape is T019/T020. |
| Registration | RabbitMQ | Asynchronous event publication | Persist registration/payment intent in its own outbox, then publish; do not make confirmation depend on Notification or Reporting. |
| Billing mock | RabbitMQ / Registration | Asynchronous request and result | Consume payment requests and publish configured approval, refusal, or timeout; Registration alone applies the corresponding state transition. |
| Registration | Notification mock | Asynchronous events | Record simulated delivery; notification failures do not undo or block a confirmed registration. |
| Registration | Attendance & Certification | Asynchronous registration events | Supply registration eligibility facts without database sharing. |
| Attendance & Certification | RabbitMQ / Reporting | Asynchronous attendance and certificate events | Record idempotent check-in and eligible certificate issuance; publish facts for the independent reporting projection. |
| Registration / Billing mock / Attendance & Certification | Reporting | Asynchronous events | Maintain Reporting's own eventually consistent read model; scope report queries by authorized organization. |

The approved domain rules are authoritative. Queue/exchange names, exact event
schemas, identity transport, retry/dead-letter behavior, and activity metadata
needed for certificate eligibility are contract decisions for the following
architecture/contract tasks; this map does not invent those wire details.

## Candidate deployment scope

| Candidate | Prototype treatment | Primary capability |
|---|---|---|
| Identity & Access | Contract + executable mock | Synthetic identity, roles, organization membership, and authorization facts. |
| Event Management | Executable Java service | Event and activity authoring/lifecycle and public published catalog. |
| Registration | Executable Java service | Capacity-safe registration lifecycle, payment wait, cancellation/refund, waitlist, and outbox. |
| Billing | Contract + executable mock | Simulate payment/refund approval, refusal, and timeout; no billing domain service. |
| Notification | Contract + executable mock | Consume notification-relevant events, record simulated delivery, and expose availability/failure scenarios. |
| Attendance & Certification | Executable Python service | Eligible idempotent check-in and certificate policy after activity completion. |
| Reporting | Executable Python service | Own event-fed projection and authorized operational indicators. |

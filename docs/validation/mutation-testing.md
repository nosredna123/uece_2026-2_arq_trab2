# Mutation testing scope

The required mutation score is at least 95% for each service's executable
domain core. CI runs all mutants in the selected core scope and fails if the
run is incomplete or the score is below the threshold.

| Service | Mutation-tested core | Excluded from the mutation score |
|---|---|---|
| Event Management | `EventManagementPolicy` | Spring application orchestration, HTTP adapters, persistence, and messaging |
| Registration | `CapacityPolicy` and `WaitlistPolicy` | Spring application orchestration, HTTP adapters, persistence, and messaging |
| Attendance & Certification | `attendance_service/domain.py` eligibility policies | HTTP/event contract parsing, SQL persistence, and event publishing |
| Reporting | `reporting_service/domain.py` activity-report aggregation | HTTP/event contract parsing, SQL projection storage, and identity/messaging adapters |

The exclusions are boundary and infrastructure code rather than domain rules.
They remain covered by the service contract, integration, and repository tests
run in the normal test suite; they are not represented as meeting the mutation
threshold. No code inside the selected domain core is excluded from mutation
analysis.

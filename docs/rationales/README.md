# Versioned architecture rationales

Each `ADR-NNN.json` accompanies the corresponding decision in [../adr](../adr).
`artifact` is the repository-relative ADR path; `version` matches its semantic
version. `rationale` explains the selected trade-off and `invariants` records
the obligations future changes must preserve.

The rationale inventory contains ADR-001 through ADR-006. The ADR-006
presentation decision adds no domain invariant and does not replace or weaken
the earlier service, ownership, consistency, messaging, or prototype decisions.
`tests/docs/test_rationales.py` verifies the exact inventory and each record's
artifact, version, rationale, and unique nonempty invariants.

Run all repository tests with `python3 -m pytest -x`. The rationale checks verify
the required shape, unique existing artifact paths, semantic-version agreement
and nonempty rationale/invariants. They are structural checks, not proof of
semantic adherence or runtime behavior.

When a requirement or premise changes, identify affected ADRs/rationales,
obtain human approval for invariant changes and update both versions together.
Preserve history through Git; never silently overwrite an earlier decision or
mark an inconclusive semantic audit as passed. The pull-request workflow runs
the fail-closed semantic audit; configure its protected endpoint and credential
as described in [CI governance](../development/ci-governance.md).

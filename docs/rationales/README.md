# Versioned architecture rationales

Each `ADR-NNN.json` accompanies the corresponding decision in [../adr](../adr).
`artifact` is the repository-relative ADR path; `version` matches its semantic
version. `rationale` explains the selected trade-off and `invariants` records
the obligations future changes must preserve.

Run all repository tests with `python3 -m pytest -x`. The rationale checks verify
the required shape, unique existing artifact paths, semantic-version agreement
and nonempty rationale/invariants. They are structural checks, not proof of
semantic adherence or runtime behavior.

When a requirement or premise changes, identify affected ADRs/rationales,
obtain human approval for invariant changes and update both versions together.
Preserve history through Git; never silently overwrite an earlier decision or
mark an inconclusive semantic audit as passed. Semantic CI auditing is T017.

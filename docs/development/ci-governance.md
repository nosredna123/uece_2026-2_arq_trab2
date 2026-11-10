# CI and pull request governance

The [CI workflow](../../.github/workflows/ci.yml) runs container builds and test
targets, Compose health acceptance, Python lint, contract/rationale validation,
architect review for changed invariants, and a semantic review against the
versioned architecture rationales. The aggregate `gates` job fails if any
required job fails, is cancelled, or is unavailable.

The Java test targets include ArchUnit rules that prevent Event Management and
Registration packages from depending on other service or mock domains. The
Python lint job runs Import Linter's independence contract across Attendance,
Reporting, and the Identity, Billing, and Notification mocks.

## Required repository configuration

Before relying on the workflow:

1. Configure the repository's `main` branch ruleset to require pull requests
   and the `gates` status check from GitHub Actions. Do not allow direct pushes
   that bypass the check. Enable stale-approval dismissal for code changes.
2. Add the protected Actions secret `EVENTHUB_SEMANTIC_AUDIT_API_KEY`, and
   Actions variables `EVENTHUB_SEMANTIC_AUDIT_ENDPOINT` and
   `EVENTHUB_SEMANTIC_AUDIT_MODEL`.
3. Configure `EVENTHUB_SEMANTIC_AUDIT_ENDPOINT` to an approved,
   organization-controlled HTTPS inference endpoint that implements the
   OpenAI-compatible chat-completions request/response format. The endpoint
   receives the pull request diff and all current rationale JSON files. Do not
   configure a public third-party endpoint for proprietary source. The workflow
   never sends repository content before an administrator configures the
   endpoint and protected API key.
4. Add the protected secret `EVENTHUB_ARCHITECT_REVIEWERS` containing the
   comma-separated GitHub usernames authorized to arbitrate invariant changes.
   At least one configured architect other than the pull request author must
   approve after the latest commit that changes rationale invariants.

Missing credentials, an unavailable endpoint, an invalid/inconclusive audit
response, a missing architect allowlist, or absent/stale human approval fail
closed. For pull requests from forks, repository secrets are unavailable; such
pull requests cannot pass these protected gates unless the maintainers provide
an approved, secure review path.

The semantic endpoint must return a JSON object in the chat-completions message
content with `decision: "APPROVED"` and an empty `findings` array. `REJECTED`,
`INCONCLUSIVE`, malformed, or non-empty findings fail the gate. Audit content,
credentials, and endpoint response bodies are not printed in workflow logs.

## Automatic merge

Automatic merge is opt-in. Add the `automerge` label to a pull request only
when it is eligible for automatic squash merge. The workflow requests
GitHub auto-merge only after `gates` succeeds; the branch ruleset and any
remaining repository review requirements still apply. Without the label, the
workflow never requests a merge.

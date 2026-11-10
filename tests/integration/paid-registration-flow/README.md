# Paid registration integration flow

Run the end-to-end acceptance test from the repository root with:

```sh
bash tests/integration/paid-registration-flow/run.sh
```

The runner starts or updates the Compose dependencies, four executable services,
and Identity, Billing, and Notification mocks using `infra/.env`. It forces the
local Billing mock to approve payments for this run, then exercises the public
activity catalog, paid reservation, asynchronous confirmation, check-in,
certificate issuance, and Reporting projection. The Compose stack and its
persistent volumes are intentionally left running; no local data is removed.
The host Python environment needs `requirements-validation.txt` installed.
Set `PYTHON` to the environment's Python executable when it is not `python3`.

The regular repository pytest suite collects this test but skips it unless
`EVENTHUB_RUN_INTEGRATION=1` is set. This keeps unit and contract tests usable
without Docker or a running Compose stack.

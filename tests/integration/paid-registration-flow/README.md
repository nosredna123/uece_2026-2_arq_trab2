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
The runner uses `PYTHON` when explicitly set, otherwise the repository's
`.venv/bin/python` when available, otherwise `python3`. It calls the interpreter
directly, so virtual-environment activation is not needed. It checks for
`httpx` and `pytest` before starting or changing the Compose stack and fails
with setup guidance if they are missing.

Prepare the project environment once:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-validation.txt
```

After that, the command above automatically uses this environment. To override
it, set `PYTHON` to another prepared interpreter's executable.

The regular repository pytest suite collects this test but skips it unless
`EVENTHUB_RUN_INTEGRATION=1` is set. This keeps unit and contract tests usable
without Docker or a running Compose stack.

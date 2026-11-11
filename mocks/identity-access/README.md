# Identity & Access mock

This deterministic mock resolves only the synthetic fixtures in
`src/identity_access_mock/fixtures.json`. Its internal validation endpoint
requires `X-Service-Key` and accepts only an opaque user `token`; role and
organization scope are always derived from the fixture. The mock returns
401 for a missing/invalid service credential or identity, and 503 when
`IDENTITY_MOCK_AVAILABLE=false`. It does not provide production identity,
user-management, or organization-management behavior.

`PARTICIPANT` and `GLOBAL_ADMIN` principals have no organization binding.
`ORGANIZER` principals require one. Domain services remain responsible for
enforcing role and ownership rules, including concealing another participant's
resources.
Two synthetic participant fixtures are provided for multi-participant flows
such as waitlist integration tests.

The service requires `INTERNAL_SERVICE_KEY` and
`IDENTITY_MOCK_AVAILABLE=true|false`; configuration is rejected when either
is missing or malformed. The scenario identity pool is disabled unless
`IDENTITY_LAB_ENABLED=true`. When enabled, the mock loads the
fixed `scenario-participants.json` file from
`IDENTITY_LAB_FIXTURES_PATH` and accepts exactly twenty unique,
unscoped `PARTICIPANT` identities. The fixture is limited to 32 KiB and is
validated against the four baseline fixtures; malformed, oversized, duplicate,
or privileged entries fail closed. Keep the fixture server-side: this mock
returns principal claims only and never returns identity tokens.

For isolated local verification:

```sh
docker build --target test -t eventhub-identity-mock-test mocks/identity-access
docker build --target runtime -t eventhub-identity-mock mocks/identity-access
docker run --rm -p 8085:8080 \
  -e INTERNAL_SERVICE_KEY=synthetic-internal-service-only \
  -e IDENTITY_MOCK_AVAILABLE=true \
  -e IDENTITY_LAB_ENABLED=false \
  eventhub-identity-mock
```

In the Compose stack, keep the mock on the internal network; do not publish its
port or use demonstration credentials outside the isolated prototype.

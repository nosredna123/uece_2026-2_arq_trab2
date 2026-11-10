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

The service requires `INTERNAL_SERVICE_KEY` and
`IDENTITY_MOCK_AVAILABLE=true|false`; configuration is rejected when either
is missing or malformed. For isolated local verification:

```sh
docker build --target test -t eventhub-identity-mock-test mocks/identity-access
docker build --target runtime -t eventhub-identity-mock mocks/identity-access
docker run --rm -p 8085:8080 \
  -e INTERNAL_SERVICE_KEY=synthetic-internal-service-only \
  -e IDENTITY_MOCK_AVAILABLE=true \
  eventhub-identity-mock
```

In the Compose stack, keep the mock on the internal network; do not publish its
port or use demonstration credentials outside the isolated prototype.

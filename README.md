# EventHub

EventHub is a locally runnable event-management prototype. The planned
application uses four executable services and three dependency mocks, backed
by PostgreSQL and RabbitMQ. Execution and acceptance tests are intended to run
in containers; host Java/Python runtimes and host databases are optional.

## Development environment

1. Install Git, Docker Engine/Desktop, and Docker Compose v2 as described in
   [the environment setup guide](docs/development/environment-setup.md).
2. From the repository root, run `./scripts/check-environment.sh` to verify
   Docker access, resources, required host ports, and temporary container,
   network, and volume operations.
3. Follow the guide for the Compose-based workflow. The Compose definition,
   service images, and acceptance-test commands are Phase 2 deliverables and
   are not present yet.

## Local environment variables and secrets

Phase 2 will provide `infra/.env.example` with fictitious development values.
The local configuration file is `infra/.env`; it must be ignored by Git before
being created. Once Phase 2 adds the example and ignore rule, copy the example
to `infra/.env`, then verify the local file is ignored:

```sh
cp infra/.env.example infra/.env
git check-ignore -v infra/.env
```

Do not put real passwords, tokens, keys, or other credentials in `.env.example`,
documentation, logs, or tracked files. Keep local secrets out of commits and
share them only through an approved secret-management channel. Do not create
`infra/.env` until the ignore rule exists and `git check-ignore` confirms it is
ignored. Phase 2 task T016 owns the `.env.example`, ignore rule, and
configuration validation; this repository does not yet include them.

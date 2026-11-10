# Capacity contention test

The live API test synchronizes two distinct synthetic participants at a barrier
and submits both registration requests at the same time for one available slot.
It requires `WAITLIST_ENABLED=true` and verifies that exactly one registration
is `CONFIRMED` while the other is `WAITLISTED`. After cancelling the winner, it
checks that the waitlisted participant becomes `OFFERED` and that the number of
`CONFIRMED` plus `OFFERED` registrations remains no greater than the activity's
capacity. The API's `occupied` and `available` values are asserted before and
after promotion.

Run from the repository root after creating `infra/.env` from
`infra/.env.example`:

```sh
PYTHON=/path/to/validation-venv/bin/python \
  bash tests/concurrency/run.sh
```

The barrier makes the collision reproducible; unique event and idempotency IDs
keep each execution independent. The runner starts the minimum Compose services
required by Event Management and Registration. While the concurrent requests
are in flight, it repeatedly samples the availability API and verifies
`occupied <= capacity` and `available == capacity - occupied`.

## Validation record

The live Compose run passed on 2026-11-10. With capacity set to one, the two
synchronized requests produced one `CONFIRMED` registration and one
`WAITLISTED` registration. After cancelling the confirmed registration, the
waitlisted registration became `OFFERED`; `occupied` remained one and
`available` remained zero. Thus confirmed/offered occupancy did not exceed
capacity during either observed state.

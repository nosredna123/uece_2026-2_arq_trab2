# Load-test evidence

## Event catalog queries and registrations

**Run:** 2026-11-10 14:37:13 -03:00  
**Measured profiles began:** 2026-11-10 14:37:17 -03:00  
**Command:** `bash tests/load/run.sh`  
**Profile:** 2 baseline virtual users, 40 peak virtual users (20×), ten
requests per virtual user per operation. The run issued 20 requests for each
baseline operation and 400 for each peak operation.  
**Result:** PASS — all 840 measured requests succeeded; no API errors. The test
provisioned 420 distinct free activities so every registration was new.

| Operation | Profile | Requests / successes | Requests/s | p50 ms | p95 ms | p99 ms | Max ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| Published event queries | Baseline, 2 users | 20 / 20 | 519.48 | 2.99 | 10.81 | 10.92 | 10.92 |
| Published event queries | Peak, 40 users | 400 / 400 | 1,664.10 | 15.54 | 33.93 | 42.87 | 48.43 |
| Free registrations | Baseline, 2 users | 20 / 20 | 55.65 | 23.20 | 148.27 | 148.34 | 148.34 |
| Free registrations | Peak, 40 users | 400 / 400 | 252.57 | 147.19 | 234.93 | 317.02 | 329.60 |

At 20× concurrency, observed query throughput was 3.20× baseline and
registration throughput was 4.54× baseline. Query p95 latency was 3.14×
baseline; registration p95 was 1.58× baseline. The measured peak completed all
queries and registrations successfully.

The run used the isolated Compose project and synthetic credentials from
`infra/.env.example`; its temporary containers and volumes were removed on
exit. The recorded host was Linux Mint 22.3 x86_64 with 16 CPUs and 60,121 MiB
of memory; see [environment readiness](./environment-readiness.md) for the
Docker/Compose versions and other host checks.

This is a single local run and a reproducible comparative baseline, not a
production-capacity claim. The specification provides no latency SLA, so the
measured latencies are recorded without a pass/fail latency threshold. The
20× figure denotes concurrent users and requests in this workload, not a
guarantee of 20× throughput.

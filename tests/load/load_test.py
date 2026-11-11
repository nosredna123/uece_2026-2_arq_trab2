#!/usr/bin/env python3
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


ORGANIZATION_ID = "30000000-0000-4000-8000-000000000001"


@dataclass(frozen=True)
class Sample:
    latency_ms: float
    status: int | None
    success: bool
    error: str | None = None


def send_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: dict | None = None,
    timeout: float,
) -> tuple[int, object]:
    encoded_body = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=encoded_body, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read())


def provision_workload(
    event_management_url: str,
    organizer_token: str,
    activity_count: int,
    timeout: float,
) -> tuple[str, list[str]]:
    run_id = str(uuid4())
    correlation_id = str(uuid4())
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    activity_start = start + timedelta(hours=1)
    activity_end = activity_start + timedelta(hours=1)
    event_end = activity_end + timedelta(days=1)

    def iso(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    event_headers = {
        "Authorization": f"Bearer {organizer_token}",
        "Content-Type": "application/json",
        "Idempotency-Key": f"load-event-{run_id}",
        "X-Correlation-ID": correlation_id,
    }
    status, event = send_json(
        "POST",
        f"{event_management_url}/events",
        headers=event_headers,
        body={
            "organizationId": ORGANIZATION_ID,
            "title": (
                f"[TESTE DE CARGA][SIMULADO][GESAD/UECE] "
                f"Programa acadêmico · {run_id[:8]}"
            ),
            "description": (
                "Evento acadêmico fictício para teste de carga do "
                "GESAD/UECE; não representa atividade real."
            ),
            "startsAt": iso(start),
            "endsAt": iso(event_end),
        },
        timeout=timeout,
    )
    if status != 201 or not isinstance(event, dict) or "id" not in event:
        raise RuntimeError(f"Could not create load-test event: HTTP {status}")
    event_id = event["id"]

    activity_ids = []
    for index in range(activity_count):
        activity_headers = {
            **event_headers,
            "Idempotency-Key": f"load-activity-{run_id}-{index}",
        }
        status, activity = send_json(
            "POST",
            f"{event_management_url}/events/{event_id}/activities",
            headers=activity_headers,
            body={
                "title": (
                    f"[TESTE DE CARGA][SIMULADO][GESAD/UECE] "
                    f"Atividade {index + 1:02} · {run_id[:8]}"
                ),
                "startsAt": iso(activity_start),
                "endsAt": iso(activity_end),
                "location": "Espaço simulado de demonstração",
                "responsible": "Equipe simulada do GESAD/UECE",
                "capacity": 10,
                "price": {"amountMinor": 0, "currency": "BRL"},
            },
            timeout=timeout,
        )
        if status != 201 or not isinstance(activity, dict) or "id" not in activity:
            raise RuntimeError(
                f"Could not create load-test activity {index}: HTTP {status}"
            )
        activity_ids.append(activity["id"])

    status, published = send_json(
        "POST",
        f"{event_management_url}/events/{event_id}/publish",
        headers={
            **event_headers,
            "Idempotency-Key": f"load-publish-{run_id}",
        },
        timeout=timeout,
    )
    if status != 200 or not isinstance(published, dict) or published.get("status") != "PUBLISHED":
        raise RuntimeError(f"Could not publish load-test event: HTTP {status}")
    return run_id, activity_ids


def measure_requests(
    operation: str,
    user_count: int,
    requests_per_user: int,
    request_for_index,
) -> dict:
    samples = []
    started = perf_counter()
    with ThreadPoolExecutor(max_workers=user_count) as executor:
        futures = [
            executor.submit(request_for_index, index)
            for index in range(user_count * requests_per_user)
        ]
        for future in as_completed(futures):
            samples.append(future.result())
    elapsed = perf_counter() - started

    latencies = sorted(sample.latency_ms for sample in samples)

    def percentile(value: float) -> float:
        return latencies[max(0, math.ceil(value * len(latencies)) - 1)]

    return {
        "operation": operation,
        "virtual_users": user_count,
        "requests": len(samples),
        "successful": sum(sample.success for sample in samples),
        "failed": sum(not sample.success for sample in samples),
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(len(samples) / elapsed, 2),
        "latency_ms": {
            "p50": round(percentile(0.50), 2),
            "p95": round(percentile(0.95), 2),
            "p99": round(percentile(0.99), 2),
            "max": round(latencies[-1], 2),
        },
        "errors": [
            {
                "status": sample.status,
                "error": sample.error,
            }
            for sample in samples
            if not sample.success
        ][:10],
    }


def measured_query(
    event_management_url: str,
    timeout: float,
):
    def request_query(_index: int) -> Sample:
        started = perf_counter()
        try:
            status, body = send_json(
                "GET",
                f"{event_management_url}/events?limit=100",
                headers={"X-Correlation-ID": str(uuid4())},
                timeout=timeout,
            )
            success = status == 200 and isinstance(body, list)
            error = None if success else "Expected HTTP 200 with a JSON array."
        except HTTPError as response:
            status = response.code
            error = response.read().decode("utf-8", errors="replace")[:200]
            success = False
        except URLError as error_value:
            status = None
            error = str(error_value.reason)
            success = False
        return Sample((perf_counter() - started) * 1000, status, success, error)

    return request_query


def measured_registration(
    registration_url: str,
    participant_token: str,
    run_id: str,
    activity_ids: list[str],
    activity_offset: int,
    timeout: float,
):
    def request_registration(index: int) -> Sample:
        activity_id = activity_ids[activity_offset + index]
        started = perf_counter()
        try:
            status, body = send_json(
                "POST",
                f"{registration_url}/registrations",
                headers={
                    "Authorization": f"Bearer {participant_token}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": f"load-registration-{run_id}-{activity_offset + index}",
                    "X-Correlation-ID": str(uuid4()),
                },
                body={"activityId": activity_id},
                timeout=timeout,
            )
            success = (
                status == 201
                and isinstance(body, dict)
                and body.get("status") == "CONFIRMED"
            )
            error = None if success else "Expected a confirmed HTTP 201 registration."
        except HTTPError as response:
            status = response.code
            error = response.read().decode("utf-8", errors="replace")[:200]
            success = False
        except URLError as error_value:
            status = None
            error = str(error_value.reason)
            success = False
        return Sample((perf_counter() - started) * 1000, status, success, error)

    return request_registration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repeatable EventHub API load test.")
    parser.add_argument(
        "--event-management-url",
        default=os.environ.get("EVENTHUB_EVENT_MANAGEMENT_URL"),
    )
    parser.add_argument(
        "--registration-url",
        default=os.environ.get("EVENTHUB_REGISTRATION_URL"),
    )
    parser.add_argument(
        "--organizer-token",
        default=os.environ.get(
            "EVENTHUB_LOAD_ORGANIZER_TOKEN", "synthetic-organizer-org-a"
        ),
    )
    parser.add_argument(
        "--participant-token",
        default=os.environ.get(
            "EVENTHUB_LOAD_PARTICIPANT_TOKEN", "synthetic-participant-001"
        ),
    )
    parser.add_argument("--baseline-users", type=int, default=2)
    parser.add_argument("--peak-multiplier", type=int, default=20)
    parser.add_argument("--requests-per-user", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=15)
    args = parser.parse_args()
    if not args.event_management_url or not args.registration_url:
        parser.error("service URLs must be provided as options or environment variables")
    if args.baseline_users < 1 or args.peak_multiplier < 1 or args.requests_per_user < 1:
        parser.error("users, multiplier, and requests per user must be positive")
    if args.peak_multiplier > 20:
        parser.error("peak multiplier must not exceed 20x")
    return args


def main() -> None:
    args = parse_args()
    run_started_at = datetime.now(timezone.utc)
    run_id, activity_ids = provision_workload(
        args.event_management_url,
        args.organizer_token,
        args.baseline_users * args.requests_per_user * (args.peak_multiplier + 1),
        args.timeout_seconds,
    )
    profiles = [
        ("baseline", args.baseline_users, 0),
        (
            "peak",
            args.baseline_users * args.peak_multiplier,
            args.baseline_users * args.requests_per_user,
        ),
    ]

    measurement_started_at = datetime.now(timezone.utc)
    results = []
    for profile, user_count, activity_offset in profiles:
        query_results = measure_requests(
            "published_event_queries",
            user_count,
            args.requests_per_user,
            measured_query(args.event_management_url, args.timeout_seconds),
        )
        query_results["profile"] = profile
        results.append(query_results)
        registration_results = measure_requests(
            "free_registrations",
            user_count,
            args.requests_per_user,
            measured_registration(
                args.registration_url,
                args.participant_token,
                run_id,
                activity_ids,
                activity_offset,
                args.timeout_seconds,
            ),
        )
        registration_results["profile"] = profile
        results.append(registration_results)

    report = {
        "started_at": run_started_at.isoformat(),
        "measurement_started_at": measurement_started_at.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "baseline_users": args.baseline_users,
            "peak_multiplier": args.peak_multiplier,
            "peak_users": args.baseline_users * args.peak_multiplier,
            "requests_per_user_per_operation": args.requests_per_user,
            "peak_requests_per_operation": (
                args.baseline_users * args.peak_multiplier * args.requests_per_user
            ),
            "provisioned_activities": len(activity_ids),
        },
        "results": results,
    }
    print(json.dumps(report, indent=2))
    if any(result["failed"] for result in results):
        raise SystemExit("Load test failed because one or more requests were unsuccessful.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Load benchmark for the presentation stack.

Runs ``scripts/cenario.sh`` (all scenarios in parallel) once per participant count,
optionally resetting the data first, while sampling container and host resources.
After each run it measures how long Reporting takes to catch up with the source
services. Results go to a timestamped folder: runs.csv (one row per run),
resources.csv (every resource sample), report.md, charts/*.png and the raw logs.

Usage: bash scripts/benchmark.sh [PARTICIPANTS ...] [options]; see --help.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PARTICIPANTS = (100, 500, 1000, 2000, 3000)
SCENARIO_KEYS = ("pico-inscricoes", "ultima-vaga", "envios-repetidos", "carga-mista")
PROJECT_PREFIX = "eventhub-"
CONFIG_KEYS = (
    "SCENARIO_MIXED_DURATION_SECONDS",
    "SCENARIO_CHECK_IN_MIN_PERCENT",
    "SCENARIO_CHECK_IN_MAX_PERCENT",
    "SCENARIO_CERTIFICATE_MIN_PERCENT",
    "SCENARIO_CERTIFICATE_MAX_PERCENT",
    "SCENARIO_CANCEL_PERCENT",
    "SCENARIO_OFFER_LAPSE_PERCENT",
    "REGISTRATION_OUTBOX_PUBLISH_INTERVAL_MS",
    "BILLING_MOCK_APPROVAL_DELAY_SECONDS",
    "BILLING_MOCK_PAYMENT_REFUSAL_PERCENT",
    "WAITLIST_OFFER_EXPIRY_MINUTES",
    "HTTP_TIMEOUT_SECONDS",
)
RUN_COLUMNS = (
    "run",
    "participants",
    "repeat",
    "started_at",
    "reset_seconds",
    "scenario_seconds",
    "verdict",
    *(key.replace("-", "_") for key in SCENARIO_KEYS),
    "requests",
    "requests_per_second",
    "failures",
    "resent",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "latency_max_ms",
    "registration_p95_ms",
    "attendance_p95_ms",
    "event_management_p95_ms",
    "catch_up_seconds",
    "mixed_confirmed",
    "mixed_pending_payment",
    "mixed_waitlisted",
    "check_ins",
    "certificates",
    "cancellations",
    "refused_payments",
    "late_offers",
    "baseline_free_cores",
    "baseline_background_cores",
    "baseline_mem_available_mib",
    "baseline_cpu_mhz",
    "containers_cpu_avg_cores",
    "containers_cpu_peak_cores",
    "containers_mem_peak_mib",
    "host_cpu_avg_percent",
    "host_cpu_peak_percent",
    "host_load1_peak",
    "host_mem_used_peak_mib",
    "host_mem_available_min_mib",
    "host_cpu_busy_share_percent",
    "background_cpu_avg_cores",
    "background_cpu_peak_cores",
    "cpu_stall_avg_percent",
    "cpu_stall_peak_percent",
    "mem_stall_peak_percent",
    "io_stall_peak_percent",
    "cpu_mhz_avg",
    "cpu_mhz_min",
    "limit",
    "top_cpu_container",
    "top_cpu_container_peak_cores",
    "top_mem_container",
    "top_mem_container_peak_mib",
)
RESOURCE_COLUMNS = ("run", "participants", "elapsed_seconds", "phase", "container", "cpu_cores", "mem_mib")
# Extra columns of the "host" rows of resources.csv (cpu_cores holds the host CPU %
# and mem_mib the used memory on those rows).
HOST_COLUMNS = (
    "load1",
    "mem_available_mib",
    "background_cores",
    "cpu_stall_percent",
    "mem_stall_percent",
    "io_stall_percent",
    "cpu_mhz",
)
HOST = "host"
# Run classification (see classify_limit).
BUSY_CPU_PERCENT = 90
SATURATED_BUSY_SHARE_PERCENT = 25
SATURATED_CPU_STALL_PERCENT = 25
SATURATED_MEM_STALL_PERCENT = 10
SATURATED_MEM_AVAILABLE_SHARE = 0.05
NOISY_BACKGROUND_SHARE = 0.15
LIMITS = {
    "architecture": "architecture (host had spare capacity)",
    "host": "host (machine saturated)",
    "noisy": "noisy (other programs competed for the CPU)",
}


# ---------------------------------------------------------------- parsing


def parse_scenario_log(text: str) -> dict[str, object]:
    """Extract the verdicts, counts and METRICS line printed by cenario.sh."""
    result: dict[str, object] = {}
    for key in SCENARIO_KEYS:
        match = re.search(rf"^  {re.escape(key)}\s+(OK|VERIFICAR)$", text, re.MULTILINE)
        result[key.replace("-", "_")] = match.group(1) if match else ""
    verdict = re.search(r"^Veredito geral: (\w+)", text, re.MULTILINE)
    result["verdict"] = verdict.group(1) if verdict else "ERRO"

    mixed = "\n".join(
        line.split("] ", 1)[1]
        for line in text.splitlines()
        if line.startswith("[carga-mista") and "] " in line
    )
    outcome = re.search(r"^Resultado: (.*)$", mixed, re.MULTILINE)
    statuses = dict(
        (status, int(count))
        for count, status in re.findall(r"(\d+) ([A-Z_]+)", outcome.group(1) if outcome else "")
    )
    result["mixed_confirmed"] = statuses.get("CONFIRMED", 0)
    result["mixed_pending_payment"] = statuses.get("PENDING_PAYMENT", 0)
    result["mixed_waitlisted"] = statuses.get("WAITLISTED", 0)
    for column, pattern in (
        ("check_ins", r"^Check-ins: (\d+)"),
        ("certificates", r"^Certificados emitidos: (\d+)"),
        ("cancellations", r"^Desistências: (\d+)"),
        ("refused_payments", r"^Pagamentos recusados: (\d+)"),
        ("late_offers", r"^Ofertas que expiraram antes do aceite[^:]*: (\d+)"),
    ):
        match = re.search(pattern, mixed, re.MULTILINE)
        result[column] = int(match.group(1)) if match else 0

    metrics_line = re.search(r"^METRICS (\{.*\})$", text, re.MULTILINE)
    metrics = json.loads(metrics_line.group(1)) if metrics_line else {}
    latency = metrics.get("latencyMs", {})
    services = metrics.get("services", {})
    result.update(
        requests=metrics.get("requests", ""),
        requests_per_second=metrics.get("requestsPerSecond", ""),
        failures=metrics.get("failures", ""),
        resent=metrics.get("resent", ""),
        latency_p50_ms=_blank(latency.get("p50")),
        latency_p95_ms=_blank(latency.get("p95")),
        latency_p99_ms=_blank(latency.get("p99")),
        latency_max_ms=_blank(latency.get("max")),
    )
    for service in ("registration", "attendance", "event-management"):
        p95 = services.get(service, {}).get("latencyMs", {}).get("p95")
        result[f"{service.replace('-', '_')}_p95_ms"] = _blank(p95)
    return result


def _blank(value: object) -> object:
    return "" if value is None else value


_UNITS = {"b": 1 / 1024**2, "kib": 1 / 1024, "kb": 1 / 1024, "mib": 1, "mb": 1, "gib": 1024, "gb": 1024}


def parse_memory_mib(text: str) -> float:
    """'512MiB / 58.7GiB' -> 512.0 (the used part)."""
    used = text.split("/", 1)[0].strip()
    match = re.fullmatch(r"([\d.]+)\s*([A-Za-z]+)", used)
    if not match:
        return 0.0
    return float(match.group(1)) * _UNITS.get(match.group(2).lower(), 0)


def parse_docker_stats(lines: Iterable[str]) -> dict[str, tuple[float, float]]:
    """docker stats JSON lines -> {service: (cpu_cores, mem_mib)}."""
    samples = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        name = item.get("Name", "")
        if not name.startswith(PROJECT_PREFIX):
            continue
        service = re.sub(r"-\d+$", "", name[len(PROJECT_PREFIX) :])
        cpu = float(item.get("CPUPerc", "0%").rstrip("%") or 0) / 100
        samples[service] = (round(cpu, 3), round(parse_memory_mib(item.get("MemUsage", "")), 1))
    return samples


def read_proc_stat(text: str) -> tuple[int, int]:
    """First line of /proc/stat -> (busy jiffies, total jiffies)."""
    values = [int(value) for value in text.splitlines()[0].split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values[:8])
    return total - idle, total


def read_meminfo_used_mib(text: str) -> float:
    fields = _meminfo(text)
    return round((fields.get("MemTotal", 0) - fields.get("MemAvailable", 0)) / 1024, 1)


def read_meminfo_available_mib(text: str) -> float:
    return round(_meminfo(text).get("MemAvailable", 0) / 1024, 1)


def _meminfo(text: str) -> dict[str, int]:
    return {line.split(":")[0]: int(line.split()[1]) for line in text.splitlines() if ":" in line}


def read_pressure_total(text: str) -> int:
    """/proc/pressure/<resource> -> total microseconds some task was stalled."""
    match = re.search(r"^some .*\btotal=(\d+)", text, re.MULTILINE)
    return int(match.group(1)) if match else 0


def read_cpu_mhz(cpu_dir: Path, cpuinfo: str = "") -> float | None:
    """Average current clock of all CPUs (cpufreq, else /proc/cpuinfo), in MHz."""
    values = []
    for path in cpu_dir.glob("cpu[0-9]*/cpufreq/scaling_cur_freq"):
        try:
            values.append(int(path.read_text()) / 1000)
        except (OSError, ValueError):
            continue
    if not values:
        values = [float(value) for value in re.findall(r"^cpu MHz\s*:\s*([\d.]+)", cpuinfo, re.MULTILINE)]
    return round(sum(values) / len(values)) if values else None


# ---------------------------------------------------------------- resources


@dataclass(frozen=True)
class HostSample:
    cpu_percent: float
    load1: float
    mem_used_mib: float
    mem_available_mib: float
    cpu_stall_percent: float | None = None
    mem_stall_percent: float | None = None
    io_stall_percent: float | None = None
    cpu_mhz: float | None = None


@dataclass
class ResourceSampler:
    """Samples docker stats plus host CPU, memory, stalls and clock until stopped.

    The phase is "baseline" (idle host before the scenario), "scenario" or "catch-up".
    """

    run: int
    participants: int
    stats: Callable[[], dict[str, tuple[float, float]]]
    host: Callable[[], HostSample]
    cpus: int = 1
    rows: list[dict[str, object]] = field(default_factory=list)
    phase: str = "scenario"
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _started: float = 0.0

    def start(self) -> None:
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def begin(self, phase: str, *, restart_clock: bool = False) -> None:
        if restart_clock:
            self._started = time.monotonic()
        self.phase = phase

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                containers = self.stats()
                host = self.host()
            except (OSError, ValueError, subprocess.SubprocessError):
                self._stop.wait(1)
                continue
            self.rows.extend(self.to_rows(containers, host))
            self._stop.wait(0.5)

    def to_rows(self, containers: Mapping[str, tuple[float, float]], host: HostSample) -> list[dict[str, object]]:
        elapsed = round(time.monotonic() - self._started, 1)
        base = {"run": self.run, "participants": self.participants, "elapsed_seconds": elapsed, "phase": self.phase}
        rows: list[dict[str, object]] = [
            {**base, "container": name, "cpu_cores": cores, "mem_mib": mem}
            for name, (cores, mem) in sorted(containers.items())
        ]
        # Host CPU not used by the EventHub containers: other programs, plus Docker
        # and this sampler. Both readings are approximate, so clamp at zero.
        used = host.cpu_percent / 100 * self.cpus
        background = max(0.0, used - sum(cores for cores, _ in containers.values()))
        rows.append(
            {
                **base,
                "container": HOST,
                "cpu_cores": host.cpu_percent,
                "mem_mib": host.mem_used_mib,
                "load1": host.load1,
                "mem_available_mib": host.mem_available_mib,
                "background_cores": _round(background),
                "cpu_stall_percent": _blank(host.cpu_stall_percent),
                "mem_stall_percent": _blank(host.mem_stall_percent),
                "io_stall_percent": _blank(host.io_stall_percent),
                "cpu_mhz": _blank(host.cpu_mhz),
            }
        )
        return rows


class HostProbe:
    """Host CPU %, load, memory, stall time (/proc/pressure, % of wall time) and clock.

    CPU % and stall time cover the interval since the previous call.
    """

    def __init__(self, proc: Path = Path("/proc"), cpu_dir: Path = Path("/sys/devices/system/cpu"),
                 clock: Callable[[], float] = time.monotonic):
        self._proc = proc
        self._cpu_dir = cpu_dir
        self._clock = clock
        self._last = read_proc_stat((proc / "stat").read_text())
        self._last_time = clock()
        self._last_stalls = self._stalls()

    def _stalls(self) -> dict[str, int | None]:
        stalls: dict[str, int | None] = {}
        for resource in ("cpu", "memory", "io"):
            try:
                stalls[resource] = read_pressure_total((self._proc / "pressure" / resource).read_text())
            except OSError:
                stalls[resource] = None
        return stalls

    def __call__(self) -> HostSample:
        busy, total = read_proc_stat((self._proc / "stat").read_text())
        last_busy, last_total = self._last
        self._last = (busy, total)
        now = self._clock()
        wall_us = max(1.0, (now - self._last_time) * 1_000_000)
        self._last_time = now
        stalls = self._stalls()
        percents = {
            resource: None if value is None or self._last_stalls.get(resource) is None
            else round(min(100.0, 100 * (value - self._last_stalls[resource]) / wall_us), 1)  # type: ignore[operator]
            for resource, value in stalls.items()
        }
        self._last_stalls = stalls
        meminfo = (self._proc / "meminfo").read_text()
        cpuinfo_path = self._proc / "cpuinfo"
        return HostSample(
            cpu_percent=round(100 * (busy - last_busy) / max(1, total - last_total), 1),
            load1=float((self._proc / "loadavg").read_text().split()[0]),
            mem_used_mib=read_meminfo_used_mib(meminfo),
            mem_available_mib=read_meminfo_available_mib(meminfo),
            cpu_stall_percent=percents["cpu"],
            mem_stall_percent=percents["memory"],
            io_stall_percent=percents["io"],
            cpu_mhz=read_cpu_mhz(self._cpu_dir, cpuinfo_path.read_text() if cpuinfo_path.exists() else ""),
        )


def summarize_resources(rows: Sequence[Mapping[str, object]], *, cpus: int = 0) -> dict[str, object]:
    """Aggregate the samples of one run into runs.csv columns.

    Baseline columns come from the "baseline" phase, the rest from the "scenario" phase.
    """
    baseline_host = [row for row in rows if row["phase"] == "baseline" and row["container"] == HOST]
    baseline: dict[str, object] = {
        "baseline_free_cores": _avg([cpus * (1 - float(row["cpu_cores"]) / 100) for row in baseline_host]) if cpus else "",
        "baseline_background_cores": _avg(_values(baseline_host, "background_cores")),
        "baseline_mem_available_mib": _avg(_values(baseline_host, "mem_available_mib")),
        "baseline_cpu_mhz": _avg(_values(baseline_host, "cpu_mhz"), digits=0),
    }
    scenario = [row for row in rows if row["phase"] == "scenario"]
    containers = [row for row in scenario if row["container"] != HOST]
    host = [row for row in scenario if row["container"] == HOST]
    by_time: dict[float, list[Mapping[str, object]]] = {}
    for row in containers:
        by_time.setdefault(float(row["elapsed_seconds"]), []).append(row)
    totals_cpu = [sum(float(row["cpu_cores"]) for row in group) for group in by_time.values()]
    totals_mem = [sum(float(row["mem_mib"]) for row in group) for group in by_time.values()]
    peak_cpu: dict[str, float] = {}
    peak_mem: dict[str, float] = {}
    for row in containers:
        name = str(row["container"])
        peak_cpu[name] = max(peak_cpu.get(name, 0.0), float(row["cpu_cores"]))
        peak_mem[name] = max(peak_mem.get(name, 0.0), float(row["mem_mib"]))
    top_cpu = max(peak_cpu, key=peak_cpu.get, default="")
    top_mem = max(peak_mem, key=peak_mem.get, default="")
    host_cpu = [float(row["cpu_cores"]) for row in host]
    cpu_mhz = _values(host, "cpu_mhz")
    return {
        **baseline,
        "containers_cpu_avg_cores": _round(sum(totals_cpu) / len(totals_cpu)) if totals_cpu else "",
        "containers_cpu_peak_cores": _round(max(totals_cpu, default=0)) if totals_cpu else "",
        "containers_mem_peak_mib": _round(max(totals_mem, default=0)) if totals_mem else "",
        "host_cpu_avg_percent": _round(sum(host_cpu) / len(host_cpu)) if host_cpu else "",
        "host_cpu_peak_percent": _round(max(host_cpu)) if host_cpu else "",
        "host_load1_peak": _round(max(float(row.get("load1", 0)) for row in host)) if host else "",
        "host_mem_used_peak_mib": _round(max(float(row["mem_mib"]) for row in host)) if host else "",
        "host_mem_available_min_mib": _min(_values(host, "mem_available_mib")),
        "host_cpu_busy_share_percent": (
            _round(100 * sum(value >= BUSY_CPU_PERCENT for value in host_cpu) / len(host_cpu)) if host_cpu else ""
        ),
        "background_cpu_avg_cores": _avg(_values(host, "background_cores")),
        "background_cpu_peak_cores": _max(_values(host, "background_cores")),
        "cpu_stall_avg_percent": _avg(_values(host, "cpu_stall_percent")),
        "cpu_stall_peak_percent": _max(_values(host, "cpu_stall_percent")),
        "mem_stall_peak_percent": _max(_values(host, "mem_stall_percent")),
        "io_stall_peak_percent": _max(_values(host, "io_stall_percent")),
        "cpu_mhz_avg": _avg(cpu_mhz, digits=0),
        "cpu_mhz_min": _min(cpu_mhz),
        "top_cpu_container": top_cpu,
        "top_cpu_container_peak_cores": _round(peak_cpu[top_cpu]) if top_cpu else "",
        "top_mem_container": top_mem,
        "top_mem_container_peak_mib": _round(peak_mem[top_mem]) if top_mem else "",
    }


def container_peaks(rows: Sequence[Mapping[str, object]]) -> dict[str, tuple[float, float, float]]:
    """{container: (avg cores, peak cores, peak MiB)} for the scenario phase."""
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        if row["phase"] == "scenario" and row["container"] != HOST:
            grouped.setdefault(str(row["container"]), []).append(row)
    return {
        name: (
            _round(sum(float(row["cpu_cores"]) for row in items) / len(items)),
            _round(max(float(row["cpu_cores"]) for row in items)),
            _round(max(float(row["mem_mib"]) for row in items)),
        )
        for name, items in grouped.items()
    }


def _round(value: float) -> float:
    return round(value, 2)


def _values(rows: Sequence[Mapping[str, object]], key: str) -> list[float]:
    """Numeric values of a column, skipping blanks (e.g. no /proc/pressure)."""
    return [value for value in (_number(row.get(key)) for row in rows) if value is not None]


def _avg(values: Sequence[float], digits: int = 2) -> object:
    return round(sum(values) / len(values), digits) if values else ""


def _max(values: Sequence[float]) -> object:
    return _round(max(values)) if values else ""


def _min(values: Sequence[float]) -> object:
    return _round(min(values)) if values else ""


def classify_limit(run: Mapping[str, object], cpus: int, memory_mib: float) -> str:
    """What limited a run: "host" (machine saturated), "noisy" (other programs
    competed for the CPU) or "architecture" (spare capacity: the stack's design)."""
    if _number(run.get("host_cpu_avg_percent")) is None:
        return ""
    # Only the idle baseline: during the run, background CPU also includes the Docker
    # engine and kernel networking driven by the load itself.
    background = _number(run.get("baseline_background_cores")) or 0.0
    if background >= max(1.0, NOISY_BACKGROUND_SHARE * cpus):
        return "noisy"
    available = _number(run.get("host_mem_available_min_mib"))
    if (
        (_number(run.get("host_cpu_busy_share_percent")) or 0) >= SATURATED_BUSY_SHARE_PERCENT
        or (_number(run.get("cpu_stall_avg_percent")) or 0) >= SATURATED_CPU_STALL_PERCENT
        or (_number(run.get("mem_stall_peak_percent")) or 0) >= SATURATED_MEM_STALL_PERCENT
        or (available is not None and memory_mib and available < SATURATED_MEM_AVAILABLE_SHARE * memory_mib)
    ):
        return "host"
    return "architecture"


# ---------------------------------------------------------------- report


@dataclass(frozen=True)
class HostInfo:
    cpu_model: str
    logical_cpus: int
    memory_mib: float
    docker_cpus: int
    docker_memory_mib: float
    os: str
    docker_version: str
    git_commit: str
    cpu_max_mhz: float = 0.0


def findings(runs: Sequence[Mapping[str, object]], host: HostInfo, peaks: Mapping[str, tuple[float, float, float]]) -> list[str]:
    """Plain-language observations derived from the results."""
    if not runs:
        return ["No run finished."]
    notes = []
    passed = [run for run in runs if run["verdict"] == "OK"]
    failed = [run for run in runs if run["verdict"] != "OK"]
    if passed:
        notes.append(f"Highest load with every scenario OK: {max(int(run['participants']) for run in passed)} participants.")
    if failed:
        first = failed[0]
        bad = [key for key in SCENARIO_KEYS if first.get(key.replace("-", "_")) != "OK"]
        notes.append(
            f"First run that needs checking: {first['participants']} participants "
            f"(scenarios: {', '.join(bad) or 'none finished'}); see its log."
        )
    largest = max(runs, key=lambda run: int(run["participants"]))
    cpu_peak = _number(largest.get("containers_cpu_peak_cores"))
    cpu_avg = _number(largest.get("containers_cpu_avg_cores"))
    if cpu_peak is not None:
        share = 100 * cpu_peak / max(1, host.docker_cpus)
        notes.append(
            f"At {largest['participants']} participants the containers used at most {cpu_peak:.1f} of "
            f"{host.docker_cpus} CPUs ({share:.0f}%), {cpu_avg or 0:.1f} on average."
        )
        if share < 50:
            notes.append(
                "The host had spare CPU: delays at this load come from contention (database "
                "locks, single-worker services, message relays, the scenario's own limit of 20 "
                "concurrent connections), not from a lack of hardware. Adding replicas or "
                "workers would use the idle cores; the stack runs one instance per service."
            )
    mem_peak = _number(largest.get("containers_mem_peak_mib"))
    if mem_peak is not None:
        notes.append(
            f"Peak container memory: {mem_peak / 1024:.1f} GiB of {host.docker_memory_mib / 1024:.1f} GiB "
            f"({100 * mem_peak / max(1.0, host.docker_memory_mib):.0f}%)."
        )
    saturated = [name for name, (_, peak, _) in peaks.items() if peak >= 0.9]
    if saturated:
        notes.append(
            "Containers that reached about one full core (a single-threaded process at its "
            f"limit is a likely bottleneck): {', '.join(sorted(saturated))}."
        )
    with_latency = [run for run in runs if _number(run.get("latency_p95_ms")) is not None]
    if len(with_latency) >= 2:
        low, high = with_latency[0], with_latency[-1]
        notes.append(
            f"Request p95 went from {low['latency_p95_ms']} ms ({low['participants']} participants) "
            f"to {high['latency_p95_ms']} ms ({high['participants']}); throughput from "
            f"{low['requests_per_second']} to {high['requests_per_second']} requests/s."
        )
    lags = [run for run in runs if _number(run.get("catch_up_seconds")) is not None]
    if lags:
        worst = max(lags, key=lambda run: float(run["catch_up_seconds"]))
        notes.append(
            f"Reports caught up at most {worst['catch_up_seconds']} s after the scenario ended "
            f"({worst['participants']} participants)."
        )
    if any(run.get("catch_up_seconds") == "timeout" for run in runs):
        notes.append("Reporting did not catch up within the timeout in at least one run.")
    resent = sum(int(run["resent"] or 0) for run in runs if str(run.get("resent", "")).isdigit())
    if resent:
        notes.append(f"Requests resent after a timeout (same Idempotency-Key): {resent} in total.")
    late = sum(int(run.get("late_offers") or 0) for run in runs)
    if late:
        notes.append(
            f"Waiting-list offers that expired before the participant accepted them: {late} in total "
            "(long runs outlast WAITLIST_OFFER_EXPIRY_MINUTES; an expected outcome, not an error)."
        )
    notes += host_findings(runs, host)
    return notes


def host_findings(runs: Sequence[Mapping[str, object]], host: HostInfo) -> list[str]:
    """Observations crossing each run with the host resources available at that moment."""
    notes = []
    labelled = [run for run in runs if run.get("limit")]
    if labelled:
        notes.append(
            "What limited each run: "
            + "; ".join(f"{run['participants']} → {run['limit']}" for run in labelled)
            + "."
        )
    saturated = [run for run in labelled if run["limit"] == "host"]
    if saturated:
        first = saturated[0]
        notes.append(
            f"The machine saturated at {first['participants']} participants: CPU at or above "
            f"{BUSY_CPU_PERCENT}% in {first.get('host_cpu_busy_share_percent')}% of the samples, tasks "
            f"waiting for a CPU {first.get('cpu_stall_avg_percent')}% of the time on average, "
            f"{_mib_text(first.get('host_mem_available_min_mib'))} of memory left at the lowest point."
        )
    elif labelled:
        notes.append(
            "The machine never saturated: the limits observed come from the stack's design "
            "(one instance per service), not from this host."
        )
    noisy = [run for run in labelled if run["limit"] == "noisy"]
    if noisy:
        notes.append(
            "Other programs were using the CPU before "
            + ", ".join(f"{run['participants']} ({run.get('baseline_background_cores')} cores)" for run in noisy)
            + " participants; compare those runs with care or rerun them with the host idle."
        )
    clocked = [run for run in runs if _number(run.get("cpu_mhz_avg")) is not None]
    if clocked:
        largest = max(clocked, key=lambda run: int(run["participants"]))
        start = _number(largest.get("baseline_cpu_mhz"))
        low = _number(largest.get("cpu_mhz_min")) or 0
        note = (
            f"CPU clock at {largest['participants']} participants: {largest['cpu_mhz_avg']} MHz on average, "
            f"{largest['cpu_mhz_min']} MHz at the lowest"
            + (f" (idle before the run: {start:.0f} MHz" if start else " (")
            + (f"; maximum {host.cpu_max_mhz:.0f} MHz)." if host.cpu_max_mhz else ").")
        )
        if start and low < 0.75 * start:
            note += (
                " The clock dropped by more than a quarter: with every core busy a laptop lowers it "
                "to stay within its power and temperature limits, so per-request work gets slower."
            )
        notes.append(note)
    return notes


def _mib_text(value: object) -> str:
    number = _number(value)
    return "unknown" if number is None else f"{number / 1024:.1f} GiB"


def _gib(value: object) -> str:
    number = _number(value)
    return "" if number is None else f"{number / 1024:.1f}"


def _number(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def render_report(
    *,
    started_at: str,
    command: str,
    host: HostInfo,
    config: Mapping[str, str],
    runs: Sequence[Mapping[str, object]],
    peaks_by_run: Mapping[int, Mapping[str, tuple[float, float, float]]],
    charts: Sequence[str],
    reset: bool,
) -> str:
    lines = [
        "# Load benchmark",
        "",
        f"Started at {started_at} · commit `{host.git_commit}` · command `{command}`",
        "",
        "Each run executes every load scenario in parallel (`bash scripts/cenario.sh`) with the given",
        "number of participants"
        + (", after restoring the initial data (`scripts/reset-demo.sh`)." if reset else ", without resetting the data between runs."),
        "",
        "## Host",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| CPU | {host.cpu_model} |",
        f"| Logical CPUs (host / Docker) | {host.logical_cpus} / {host.docker_cpus} |",
        f"| Memory (host / Docker) | {host.memory_mib / 1024:.1f} GiB / {host.docker_memory_mib / 1024:.1f} GiB |",
        f"| OS | {host.os} |",
        f"| Docker | {host.docker_version} |",
        "",
        "## Configuration",
        "",
        "| Variable | Value |",
        "|---|---|",
        *(f"| `{key}` | {value or '(default)'} |" for key, value in config.items()),
        "",
        "## Results",
        "",
        "| Participants | Verdict | Scenario (s) | Requests/s | p50 / p95 / p99 (ms) | Failures | Resent | Reports catch-up (s) | Peak CPU (cores) | Peak memory (MiB) |",
        "|---:|---|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for run in runs:
        lines.append(
            f"| {run['participants']} | {run['verdict']} | {run['scenario_seconds']} | {run['requests_per_second']} "
            f"| {run['latency_p50_ms']} / {run['latency_p95_ms']} / {run['latency_p99_ms']} | {run['failures']} "
            f"| {run['resent']} | {run['catch_up_seconds']} | {run['containers_cpu_peak_cores']} | {run['containers_mem_peak_mib']} |"
        )
    lines += [
        "",
        "Latency is the time each service took to answer the scenario's requests (local queueing",
        "excluded). Catch-up is the time, after the scenario ended, until Reporting's counts match",
        "Registration and Attendance. CPU is in cores (1.0 = one full CPU).",
        "",
        "## Host resources during each run",
        "",
        "| Participants | Free at start (cores / GiB) | Other programs at start (cores) | Host CPU avg / peak (%) "
        "| Time at ≥90% CPU (%) | Background during run avg / peak (cores) | Waiting for CPU avg / peak (%) "
        "| Memory / disk stall peak (%) | Memory left (GiB) | Clock avg / min (MHz) | Limit |",
        "|---:|---|---:|---|---:|---|---|---|---:|---|---|",
    ]
    for run in runs:
        lines.append(
            f"| {run['participants']} | {run.get('baseline_free_cores', '')} / {_gib(run.get('baseline_mem_available_mib'))} "
            f"| {run.get('baseline_background_cores', '')} "
            f"| {run.get('host_cpu_avg_percent', '')} / {run.get('host_cpu_peak_percent', '')} "
            f"| {run.get('host_cpu_busy_share_percent', '')} "
            f"| {run.get('background_cpu_avg_cores', '')} / {run.get('background_cpu_peak_cores', '')} "
            f"| {run.get('cpu_stall_avg_percent', '')} / {run.get('cpu_stall_peak_percent', '')} "
            f"| {run.get('mem_stall_peak_percent', '')} / {run.get('io_stall_peak_percent', '')} "
            f"| {_gib(run.get('host_mem_available_min_mib'))} "
            f"| {run.get('cpu_mhz_avg', '')} / {run.get('cpu_mhz_min', '')} | {run.get('limit', '')} |"
        )
    lines += [
        "",
        "*Free at start* and *other programs* come from an idle sample of the host taken just before",
        "the scenario (the stack running, no load). *Background during run* is host CPU not used by",
        "the EventHub containers: other programs plus the Docker engine and kernel networking.",
        "*Waiting for CPU* and the *stall* columns come from the kernel (`/proc/pressure`): the share",
        "of time at least one task was ready but waiting for a CPU, memory or the disk.",
        "",
        "Limit: *host* when the CPU stayed at or above "
        f"{BUSY_CPU_PERCENT}% in at least {SATURATED_BUSY_SHARE_PERCENT}% of the samples, tasks waited for a CPU "
        f"{SATURATED_CPU_STALL_PERCENT}% of the time or more on average, memory stalls reached "
        f"{SATURATED_MEM_STALL_PERCENT}%, or less than {SATURATED_MEM_AVAILABLE_SHARE:.0%} of the memory was left; "
        f"*noisy* when other programs used {NOISY_BACKGROUND_SHARE:.0%} of the CPUs or more before the run; "
        "otherwise *architecture* (the host had spare capacity, so the stack's design set the limit).",
        "",
        "## Findings",
        "",
        *(f"- {note}" for note in findings(runs, host, peaks_by_run.get(int(runs[-1]["run"]), {}) if runs else {})),
        "",
    ]
    if runs:
        last = runs[-1]
        peaks = peaks_by_run.get(int(last["run"]), {})
        lines += [
            f"## Containers at {last['participants']} participants",
            "",
            "| Container | Avg CPU (cores) | Peak CPU (cores) | Peak memory (MiB) |",
            "|---|---:|---:|---:|",
            *(
                f"| {name} | {avg} | {peak} | {mem} |"
                for name, (avg, peak, mem) in sorted(peaks.items(), key=lambda item: -item[1][1])
            ),
            "",
        ]
    if charts:
        lines += ["## Charts", ""]
        lines += [f"![{Path(chart).stem}]({chart})" for chart in charts]
        lines.append("")
    lines += [
        "## Files",
        "",
        "- `runs.csv`: one row per run (all columns of the table above and more).",
        "- `resources.csv`: every resource sample (containers and host) during each run; on the",
        "  `host` rows `cpu_cores` is the host CPU % and `mem_mib` the used memory. Phases:",
        "  `baseline` (idle, before the scenario), `scenario` and `catch-up`.",
        "- `logs/`: the full output of each scenario and reset.",
        "",
    ]
    return "\n".join(lines)


def draw_charts(
    out_dir: Path,
    runs: Sequence[Mapping[str, object]],
    resources: Sequence[Mapping[str, object]],
    cpus: int = 0,
) -> list[str]:
    """PNG charts; skipped when matplotlib is not installed."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    charts_dir = out_dir / "charts"
    charts_dir.mkdir(exist_ok=True)
    created = []
    measured = [run for run in runs if _number(run.get("latency_p95_ms")) is not None]
    x = [int(run["participants"]) for run in measured]

    def save(fig, name: str) -> None:
        fig.tight_layout()
        fig.savefig(charts_dir / name, dpi=110)
        plt.close(fig)
        created.append(f"charts/{name}")

    if measured:
        fig, ax = plt.subplots(figsize=(7, 4))
        for key, label in (("latency_p50_ms", "p50"), ("latency_p95_ms", "p95"), ("latency_p99_ms", "p99")):
            ax.plot(x, [float(run[key]) for run in measured], marker="o", label=label)
        ax.set(xlabel="Participants", ylabel="Latency (ms)", title="Request latency")
        ax.legend()
        ax.grid(alpha=0.3)
        save(fig, "latency.png")

        fig, ax = plt.subplots(figsize=(7, 4))
        ax.plot(x, [float(run["requests_per_second"]) for run in measured], marker="o", color="tab:green")
        ax.set(xlabel="Participants", ylabel="Requests/s", title="Throughput")
        ax.grid(alpha=0.3)
        save(fig, "throughput.png")

    lagged = [run for run in runs if _number(run.get("catch_up_seconds")) is not None]
    if lagged:
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.plot(
            [int(run["participants"]) for run in lagged],
            [float(run["catch_up_seconds"]) for run in lagged],
            marker="o",
            color="tab:orange",
        )
        ax.set(xlabel="Participants", ylabel="Seconds", title="Reports catch-up after the scenario")
        ax.grid(alpha=0.3)
        save(fig, "catch-up.png")

    with_cpu = [run for run in runs if _number(run.get("containers_cpu_peak_cores")) is not None]
    if with_cpu:
        fig, ax = plt.subplots(figsize=(7, 4))
        xs = [int(run["participants"]) for run in with_cpu]
        ax.plot(xs, [float(run["containers_cpu_peak_cores"]) for run in with_cpu], marker="o", label="peak")
        ax.plot(xs, [float(run["containers_cpu_avg_cores"]) for run in with_cpu], marker="o", label="average")
        ax.set(xlabel="Participants", ylabel="CPU (cores)", title="Containers CPU (sum of all containers)")
        ax.legend()
        ax.grid(alpha=0.3)
        save(fig, "cpu.png")

    split = [
        run for run in with_cpu
        if _number(run.get("background_cpu_avg_cores")) is not None and cpus
    ]
    if split:
        fig, ax = plt.subplots(figsize=(7, 4))
        labels = [str(run["participants"]) for run in split]
        eventhub = [float(run["containers_cpu_avg_cores"]) for run in split]
        background = [float(run["background_cpu_avg_cores"]) for run in split]
        free = [max(0.0, cpus - a - b) for a, b in zip(eventhub, background)]
        ax.bar(labels, eventhub, label="EventHub containers")
        ax.bar(labels, background, bottom=eventhub, label="other programs / Docker / kernel")
        ax.bar(labels, free, bottom=[a + b for a, b in zip(eventhub, background)], label="free", color="#d9d9d9")
        ax.set(xlabel="Participants", ylabel="CPU (cores, average during the scenario)", title=f"Host CPU use ({cpus} CPUs)")
        ax.legend(fontsize="small")
        save(fig, "host-cpu.png")

    if runs:
        last = runs[-1]
        samples = [
            row for row in resources if row["run"] == last["run"] and row["phase"] == "scenario" and row["container"] != HOST
        ]
        peaks = container_peaks(samples)
        top = sorted(peaks, key=lambda name: -peaks[name][1])[:6]
        if top:
            fig, ax = plt.subplots(figsize=(8, 4.5))
            for name in top:
                series = [row for row in samples if row["container"] == name]
                ax.plot(
                    [float(row["elapsed_seconds"]) for row in series],
                    [float(row["cpu_cores"]) for row in series],
                    label=name,
                )
            ax.set(
                xlabel="Seconds since the scenario started",
                ylabel="CPU (cores)",
                title=f"Busiest containers at {last['participants']} participants",
            )
            ax.legend(fontsize="small")
            ax.grid(alpha=0.3)
            save(fig, "containers-cpu-timeline.png")
    return created


# ---------------------------------------------------------------- stack access


class Stack:
    """Thin wrapper over docker compose and the repository scripts."""

    def __init__(self, root: Path = ROOT):
        self.root = root
        env_file = Path(os.environ.get("EVENTHUB_ENV_FILE", root / "infra/.env"))
        self.env_file = env_file
        self.compose = [
            "docker",
            "compose",
            "--env-file",
            str(root / "infra/.env.example"),
            "--env-file",
            str(env_file),
            "-f",
            str(root / "infra/docker-compose.yml"),
            "-f",
            str(root / "infra/docker-compose.demo-web.yml"),
            "-f",
            str(root / "infra/docker-compose.demo-lab.yml"),
            "--profile",
            "demo-web",
        ]

    def env_value(self, key: str) -> str:
        if os.environ.get(key):
            return os.environ[key]
        for path in (self.env_file, self.root / "infra/.env.example"):
            if path.exists():
                values = [
                    line.split("=", 1)[1].strip()
                    for line in path.read_text().splitlines()
                    if line.startswith(f"{key}=")
                ]
                if values:
                    return values[-1]
        return ""

    def run_logged(self, command: Sequence[str], log: Path, env: Mapping[str, str] | None = None) -> int:
        with log.open("w") as handle:
            process = subprocess.Popen(
                command,
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env={**os.environ, **(env or {})},
            )
            assert process.stdout is not None
            for line in process.stdout:
                handle.write(line)
            return process.wait()

    def docker_stats(self) -> dict[str, tuple[float, float]]:
        output = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
        return parse_docker_stats(output.splitlines())

    def counts(self) -> tuple[dict[str, int], dict[str, int]]:
        """(source counts, Reporting counts) for registrations, check-ins and certificates."""
        script = (
            'psql -U "$POSTGRES_USER" -d registration -Atc '
            '"select status, count(*) from registration group by 1"; '
            'echo ---; psql -U "$POSTGRES_USER" -d attendance_certification -Atc '
            '"select \'check_in\', count(*) from check_in union all select \'certificate\', count(*) from certificate"; '
            'echo ---; psql -U "$POSTGRES_USER" -d reporting -Atc '
            "\"select coalesce(snapshot->'data'->>'status', event_type), count(*) from projection "
            "where event_type = 'registration.changed' group by 1 "
            "union all select case event_type when 'attendance.recorded' then 'check_in' else 'certificate' end, count(*) "
            "from projection where event_type in ('attendance.recorded', 'certificate.issued') group by event_type\""
        )
        output = subprocess.run(
            [*self.compose, "exec", "-T", "postgres", "sh", "-c", script],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout
        registration, attendance, reporting = (output.split("---\n") + ["", ""])[:3]
        return _pairs(registration + attendance), _pairs(reporting)

    def host_info(self) -> HostInfo:
        cpuinfo = Path("/proc/cpuinfo").read_text() if Path("/proc/cpuinfo").exists() else ""
        model = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
        meminfo = Path("/proc/meminfo").read_text() if Path("/proc/meminfo").exists() else ""
        mem_total = re.search(r"^MemTotal:\s+(\d+)", meminfo, re.MULTILINE)
        docker = _quiet(["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}} {{.ServerVersion}}"]).split()
        commit = _quiet(["git", "-C", str(self.root), "rev-parse", "--short", "HEAD"])
        dirty = _quiet(["git", "-C", str(self.root), "status", "--porcelain"])
        return HostInfo(
            cpu_model=model.group(1).strip() if model else platform.processor() or "unknown",
            logical_cpus=os.cpu_count() or 0,
            memory_mib=round(int(mem_total.group(1)) / 1024, 1) if mem_total else 0.0,
            docker_cpus=int(docker[0]) if docker else 0,
            docker_memory_mib=round(int(docker[1]) / 1024**2, 1) if len(docker) > 1 else 0.0,
            os=f"{platform.system()} {platform.release()}",
            docker_version=docker[2] if len(docker) > 2 else "unknown",
            git_commit=commit + ("+changes" if dirty else ""),
            cpu_max_mhz=_max_mhz(),
        )


def _max_mhz(cpu_dir: Path = Path("/sys/devices/system/cpu")) -> float:
    try:
        return round(int((cpu_dir / "cpu0/cpufreq/cpuinfo_max_freq").read_text()) / 1000)
    except (OSError, ValueError):
        return 0.0


def _pairs(text: str) -> dict[str, int]:
    pairs = {}
    for line in text.splitlines():
        if "|" in line:
            key, value = line.rsplit("|", 1)
            pairs[key] = int(value)
    return pairs


def _quiet(command: Sequence[str]) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def wait_for_catch_up(
    counts: Callable[[], tuple[dict[str, int], dict[str, int]]],
    timeout: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> float | None:
    """Seconds until Reporting matches the sources, or None after the timeout."""
    started = clock()
    while True:
        try:
            source, reporting = counts()
            if source == reporting:
                return round(clock() - started, 1)
        except (subprocess.SubprocessError, ValueError):
            pass
        if clock() - started >= timeout:
            return None
        sleep(1)


# ---------------------------------------------------------------- main


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bash scripts/benchmark.sh",
        description="Run the load scenarios for several participant counts and record the results.",
    )
    parser.add_argument(
        "participants",
        nargs="*",
        type=int,
        help=f"participant counts, 1-20000 (default: {' '.join(map(str, DEFAULT_PARTICIPANTS))})",
    )
    parser.add_argument("--repeat", type=int, default=1, help="runs per participant count (default 1)")
    parser.add_argument("--duration", type=int, help="SCENARIO_MIXED_DURATION_SECONDS for every run (default: infra/.env)")
    parser.add_argument("--no-reset", action="store_true", help="keep the data between runs (faster, less comparable)")
    parser.add_argument(
        "--stop-at",
        choices=("saturation", "verificar", "never"),
        default="saturation",
        help="saturation (default): stop after a run that saturated the host (or failed to run); "
        "verificar: also stop after a run that needs checking; never: run every count",
    )
    parser.add_argument("--keep-going", action="store_true", help="same as --stop-at never")
    parser.add_argument(
        "--baseline", type=int, default=10, help="seconds of idle host sampling before each run (default 10, 0 to skip)"
    )
    parser.add_argument("--catch-up-timeout", type=int, default=300, help="seconds to wait for Reporting (default 300)")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/validation/benchmarks",
        help="parent folder of the timestamped results (default docs/validation/benchmarks)",
    )
    args = parser.parse_args(argv)
    args.participants = args.participants or list(DEFAULT_PARTICIPANTS)
    if any(not 1 <= value <= 20000 for value in args.participants):
        parser.error("participant counts must be between 1 and 20000")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    if args.duration is not None and not 10 <= args.duration <= 300:
        parser.error("--duration must be between 10 and 300")
    if args.baseline < 0:
        parser.error("--baseline must be 0 or more")
    if args.keep_going:
        args.stop_at = "never"
    return args


def stop_reason(run: Mapping[str, object], stop_at: str) -> str:
    """Why the sweep should stop after this run, or "" to continue."""
    if run["verdict"] == "ERRO":
        return "the scenarios did not run (see the log)"
    if stop_at == "never":
        return ""
    if run.get("limit") == "host":
        return "the host saturated"
    if stop_at == "verificar" and run["verdict"] != "OK":
        return "this run needs checking"
    return ""


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    stack = Stack()
    if not stack.env_file.exists():
        print("ERROR: infra/.env not found; run scripts/run-demo-web.sh first.", file=sys.stderr)
        return 2
    if stack.env_value("DEMO_LAB_ENABLED") != "true":
        print("ERROR: the scenarios require DEMO_LAB_ENABLED=true in infra/.env.", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc)
    out_dir = args.output / started.strftime("%Y%m%d-%H%M%S")
    (out_dir / "logs").mkdir(parents=True)
    host = stack.host_info()
    (out_dir / "host.json").write_text(json.dumps(host.__dict__, indent=2) + "\n")
    config = {key: stack.env_value(key) for key in CONFIG_KEYS}
    if args.duration is not None:
        config["SCENARIO_MIXED_DURATION_SECONDS"] = str(args.duration)
    command = "bash scripts/benchmark.sh " + " ".join(argv if argv is not None else sys.argv[1:])
    plan = [(value, repeat) for value in args.participants for repeat in range(1, args.repeat + 1)]
    print(f"Benchmark: {len(plan)} run(s) -> {out_dir.relative_to(ROOT) if out_dir.is_relative_to(ROOT) else out_dir}")
    print(f"Host: {host.cpu_model} · {host.docker_cpus} CPUs · {host.docker_memory_mib / 1024:.1f} GiB for Docker")

    runs: list[dict[str, object]] = []
    resources: list[dict[str, object]] = []
    peaks_by_run: dict[int, dict[str, tuple[float, float, float]]] = {}
    runs_csv = out_dir / "runs.csv"
    resources_csv = out_dir / "resources.csv"
    with runs_csv.open("w", newline="") as handle:
        csv.DictWriter(handle, RUN_COLUMNS).writeheader()
    with resources_csv.open("w", newline="") as handle:
        csv.DictWriter(handle, (*RESOURCE_COLUMNS, *HOST_COLUMNS), extrasaction="ignore").writeheader()

    for number, (participants, repeat) in enumerate(plan, start=1):
        tag = f"{number:02}-{participants}p-r{repeat}"
        print(f"[{number}/{len(plan)}] {participants} participants (repeat {repeat})")
        reset_seconds: object = ""
        if not args.no_reset:
            print("  resetting the data (scripts/reset-demo.sh)...")
            began = time.monotonic()
            code = stack.run_logged(["bash", "scripts/reset-demo.sh", "--yes"], out_dir / "logs" / f"{tag}-reset.log")
            reset_seconds = round(time.monotonic() - began, 1)
            if code != 0:
                print(f"  ERROR: reset failed (exit {code}); see logs/{tag}-reset.log", file=sys.stderr)
                break

        sampler = ResourceSampler(number, participants, stack.docker_stats, HostProbe(), cpus=host.logical_cpus)
        env = {"SCENARIO_PEAK_PARTICIPANTS": str(participants), "SCENARIO_METRICS": "1"}
        if args.duration is not None:
            env["SCENARIO_MIXED_DURATION_SECONDS"] = str(args.duration)
        if args.baseline:
            print(f"  sampling the idle host for {args.baseline} s...")
            sampler.begin("baseline")
            sampler.start()
            time.sleep(args.baseline)
        run_started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print("  running the scenarios...")
        sampler.begin("scenario", restart_clock=True)
        if not args.baseline:
            sampler.start()
        began = time.monotonic()
        stack.run_logged(["bash", "scripts/cenario.sh"], out_dir / "logs" / f"{tag}-scenario.log", env)
        scenario_seconds = round(time.monotonic() - began, 1)
        sampler.begin("catch-up")
        print("  waiting for Reports to catch up...")
        lag = wait_for_catch_up(stack.counts, args.catch_up_timeout)
        sampler.stop()

        parsed = parse_scenario_log((out_dir / "logs" / f"{tag}-scenario.log").read_text())
        row = {
            "run": number,
            "participants": participants,
            "repeat": repeat,
            "started_at": run_started,
            "reset_seconds": reset_seconds,
            "scenario_seconds": scenario_seconds,
            **parsed,
            "catch_up_seconds": "timeout" if lag is None else lag,
            **summarize_resources(sampler.rows, cpus=host.logical_cpus),
        }
        row["limit"] = classify_limit(row, host.logical_cpus, host.memory_mib)
        runs.append(row)
        resources.extend(sampler.rows)
        peaks_by_run[number] = container_peaks(sampler.rows)
        with runs_csv.open("a", newline="") as handle:
            csv.DictWriter(handle, RUN_COLUMNS, extrasaction="ignore").writerow(row)
        with resources_csv.open("a", newline="") as handle:
            csv.DictWriter(handle, (*RESOURCE_COLUMNS, *HOST_COLUMNS), extrasaction="ignore").writerows(sampler.rows)
        print(
            f"  {row['verdict']} · {scenario_seconds} s · {row['requests_per_second']} req/s · "
            f"p95 {row['latency_p95_ms']} ms · catch-up {row['catch_up_seconds']} s · "
            f"peak CPU {row['containers_cpu_peak_cores']} cores · host CPU avg {row['host_cpu_avg_percent']}% · "
            f"waiting for CPU {row['cpu_stall_avg_percent']}% · limit {row['limit'] or '?'}"
        )
        reason = stop_reason(row, args.stop_at)
        if reason:
            print(f"  stopping: {reason} (see --stop-at).")
            break

    charts = draw_charts(out_dir, runs, resources, host.logical_cpus)
    report = render_report(
        started_at=started.strftime("%Y-%m-%d %H:%M UTC"),
        command=command.strip(),
        host=host,
        config=config,
        runs=runs,
        peaks_by_run=peaks_by_run,
        charts=charts,
        reset=not args.no_reset,
    )
    (out_dir / "report.md").write_text(report)
    if not charts:
        print("Charts skipped: install matplotlib (pip install -r requirements-validation.txt).")
    print(f"Report: {out_dir / 'report.md'}")
    return 0 if runs and all(run["verdict"] == "OK" for run in runs) else 1


if __name__ == "__main__":
    sys.exit(main())

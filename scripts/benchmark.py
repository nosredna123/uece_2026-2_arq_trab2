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
    "containers_cpu_avg_cores",
    "containers_cpu_peak_cores",
    "containers_mem_peak_mib",
    "host_cpu_avg_percent",
    "host_cpu_peak_percent",
    "host_load1_peak",
    "host_mem_used_peak_mib",
    "top_cpu_container",
    "top_cpu_container_peak_cores",
    "top_mem_container",
    "top_mem_container_peak_mib",
)
RESOURCE_COLUMNS = ("run", "participants", "elapsed_seconds", "phase", "container", "cpu_cores", "mem_mib")
HOST = "host"


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
    fields = {line.split(":")[0]: int(line.split()[1]) for line in text.splitlines() if ":" in line}
    return round((fields.get("MemTotal", 0) - fields.get("MemAvailable", 0)) / 1024, 1)


# ---------------------------------------------------------------- resources


@dataclass
class ResourceSampler:
    """Samples docker stats plus host CPU, load and memory until stopped."""

    run: int
    participants: int
    stats: Callable[[], dict[str, tuple[float, float]]]
    host: Callable[[], tuple[float, float, float]]
    rows: list[dict[str, object]] = field(default_factory=list)
    phase: str = "scenario"
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _started: float = 0.0

    def start(self) -> None:
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                containers = self.stats()
                cpu_percent, load1, mem_used = self.host()
            except (OSError, ValueError, subprocess.SubprocessError):
                self._stop.wait(1)
                continue
            elapsed = round(time.monotonic() - self._started, 1)
            base = {"run": self.run, "participants": self.participants, "elapsed_seconds": elapsed, "phase": self.phase}
            for name, (cores, mem) in sorted(containers.items()):
                self.rows.append({**base, "container": name, "cpu_cores": cores, "mem_mib": mem})
            self.rows.append(
                {**base, "container": HOST, "cpu_cores": cpu_percent, "mem_mib": mem_used, "load1": load1}
            )
            self._stop.wait(0.5)


class HostProbe:
    """Host CPU % (all CPUs, between calls), 1-minute load average and used memory."""

    def __init__(self, proc: Path = Path("/proc")):
        self._proc = proc
        self._last = read_proc_stat((proc / "stat").read_text())

    def __call__(self) -> tuple[float, float, float]:
        busy, total = read_proc_stat((self._proc / "stat").read_text())
        last_busy, last_total = self._last
        self._last = (busy, total)
        percent = round(100 * (busy - last_busy) / max(1, total - last_total), 1)
        load1 = float((self._proc / "loadavg").read_text().split()[0])
        return percent, load1, read_meminfo_used_mib((self._proc / "meminfo").read_text())


def summarize_resources(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Aggregate the samples of one run (scenario phase only) into runs.csv columns."""
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
    return {
        "containers_cpu_avg_cores": _round(sum(totals_cpu) / len(totals_cpu)) if totals_cpu else "",
        "containers_cpu_peak_cores": _round(max(totals_cpu, default=0)) if totals_cpu else "",
        "containers_mem_peak_mib": _round(max(totals_mem, default=0)) if totals_mem else "",
        "host_cpu_avg_percent": _round(sum(host_cpu) / len(host_cpu)) if host_cpu else "",
        "host_cpu_peak_percent": _round(max(host_cpu)) if host_cpu else "",
        "host_load1_peak": _round(max(float(row.get("load1", 0)) for row in host)) if host else "",
        "host_mem_used_peak_mib": _round(max(float(row["mem_mib"]) for row in host)) if host else "",
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
    return notes


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
        "- `resources.csv`: every resource sample (containers and host) during each run.",
        "- `logs/`: the full output of each scenario and reset.",
        "",
    ]
    return "\n".join(lines)


def draw_charts(
    out_dir: Path,
    runs: Sequence[Mapping[str, object]],
    resources: Sequence[Mapping[str, object]],
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
        )


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
    parser.add_argument("--keep-going", action="store_true", help="continue after a run that needs checking")
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
    return args


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
        csv.DictWriter(handle, (*RESOURCE_COLUMNS, "load1"), extrasaction="ignore").writeheader()

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

        sampler = ResourceSampler(number, participants, stack.docker_stats, HostProbe())
        env = {"SCENARIO_PEAK_PARTICIPANTS": str(participants), "SCENARIO_METRICS": "1"}
        if args.duration is not None:
            env["SCENARIO_MIXED_DURATION_SECONDS"] = str(args.duration)
        run_started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print("  running the scenarios...")
        sampler.start()
        began = time.monotonic()
        stack.run_logged(["bash", "scripts/cenario.sh"], out_dir / "logs" / f"{tag}-scenario.log", env)
        scenario_seconds = round(time.monotonic() - began, 1)
        sampler.phase = "catch-up"
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
            **summarize_resources(sampler.rows),
        }
        runs.append(row)
        resources.extend(sampler.rows)
        peaks_by_run[number] = container_peaks(sampler.rows)
        with runs_csv.open("a", newline="") as handle:
            csv.DictWriter(handle, RUN_COLUMNS, extrasaction="ignore").writerow(row)
        with resources_csv.open("a", newline="") as handle:
            csv.DictWriter(handle, (*RESOURCE_COLUMNS, "load1"), extrasaction="ignore").writerows(sampler.rows)
        print(
            f"  {row['verdict']} · {scenario_seconds} s · {row['requests_per_second']} req/s · "
            f"p95 {row['latency_p95_ms']} ms · catch-up {row['catch_up_seconds']} s · "
            f"peak CPU {row['containers_cpu_peak_cores']} cores"
        )
        if row["verdict"] != "OK" and not args.keep_going:
            print("  stopping: this run needs checking (use --keep-going to continue).")
            break

    charts = draw_charts(out_dir, runs, resources)
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

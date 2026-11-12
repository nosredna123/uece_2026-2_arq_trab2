import json

from scripts.benchmark import (
    HostInfo,
    container_peaks,
    findings,
    parse_args,
    parse_docker_stats,
    parse_memory_mib,
    parse_scenario_log,
    read_meminfo_used_mib,
    read_proc_stat,
    render_report,
    summarize_resources,
    wait_for_catch_up,
)

METRICS = {
    "durationSeconds": 31.2,
    "requests": 1200,
    "requestsPerSecond": 38.5,
    "failures": 1,
    "resent": 2,
    "latencyMs": {"p50": 12, "p95": 80, "p99": 150, "max": 900},
    "services": {
        "registration": {"requests": 900, "failures": 1, "latencyMs": {"p50": 15, "p95": 95, "p99": 160, "max": 900}},
        "attendance": {"requests": 300, "failures": 0, "latencyMs": {"p50": 5, "p95": 20, "p99": 30, "max": 40}},
    },
}
LOG = f"""Todos os cenários em paralelo, durante cerca de 30 s
[carga-mista     ] Desistências: 38 · vagas repassadas da lista de espera: 8
[carga-mista     ] Resultado: 200 CONFIRMED, 106 PENDING_PAYMENT, 318 WAITLISTED
[carga-mista     ] Pagamentos recusados: 13
[carga-mista     ] Check-ins: 166 de 263 inscrições confirmadas
[carga-mista     ] Certificados emitidos: 142 de 166 check-ins
[pico-inscricoes ] Resultado: 50 CONFIRMED, 150 WAITLISTED

Resumo
  pico-inscricoes  OK
  ultima-vaga      OK
  envios-repetidos OK
  carga-mista      VERIFICAR
Veredito geral: VERIFICAR
METRICS {json.dumps(METRICS)}
"""
HOST = HostInfo("Test CPU", 16, 61440.0, 16, 61440.0, "Linux", "29", "abc123")


def test_scenario_log_gives_verdicts_counts_and_request_metrics():
    parsed = parse_scenario_log(LOG)

    assert parsed["verdict"] == "VERIFICAR"
    assert parsed["pico_inscricoes"] == "OK"
    assert parsed["carga_mista"] == "VERIFICAR"
    assert (parsed["mixed_confirmed"], parsed["mixed_pending_payment"], parsed["mixed_waitlisted"]) == (200, 106, 318)
    assert (parsed["check_ins"], parsed["certificates"]) == (166, 142)
    assert (parsed["cancellations"], parsed["refused_payments"]) == (38, 13)
    assert parsed["requests_per_second"] == 38.5
    assert (parsed["latency_p50_ms"], parsed["latency_p95_ms"], parsed["latency_max_ms"]) == (12, 80, 900)
    assert parsed["registration_p95_ms"] == 95
    assert parsed["event_management_p95_ms"] == ""


def test_scenario_log_without_summary_is_an_error():
    parsed = parse_scenario_log("ERRO: O app de apresentação não está rodando\n")
    assert parsed["verdict"] == "ERRO"
    assert parsed["requests"] == "" and parsed["check_ins"] == 0


def test_docker_stats_keep_project_containers_in_cores_and_mib():
    lines = [
        json.dumps({"Name": "eventhub-registration-1", "CPUPerc": "153.20%", "MemUsage": "512MiB / 58.7GiB"}),
        json.dumps({"Name": "eventhub-postgres-1", "CPUPerc": "40%", "MemUsage": "1.5GiB / 58.7GiB"}),
        json.dumps({"Name": "other-app", "CPUPerc": "99%", "MemUsage": "1GiB / 58.7GiB"}),
        "",
    ]
    assert parse_docker_stats(lines) == {"registration": (1.532, 512.0), "postgres": (0.4, 1536.0)}
    assert parse_memory_mib("800kB / 1GiB") == 800 / 1024
    assert parse_memory_mib("garbage") == 0.0


def test_proc_readers():
    assert read_proc_stat("cpu  10 0 10 70 10 0 0 0 0 0\ncpu0 1 2 3\n") == (20, 100)
    assert read_meminfo_used_mib("MemTotal: 4096 kB\nMemAvailable: 1024 kB\n") == 3.0


def _sample(elapsed, container, cpu, mem, phase="scenario", load1=None):
    row = {
        "run": 1,
        "participants": 100,
        "elapsed_seconds": elapsed,
        "phase": phase,
        "container": container,
        "cpu_cores": cpu,
        "mem_mib": mem,
    }
    if load1 is not None:
        row["load1"] = load1
    return row


def test_resources_are_summed_per_sample_and_catch_up_is_left_out():
    rows = [
        _sample(0, "registration", 1.0, 500),
        _sample(0, "postgres", 0.5, 1000),
        _sample(0, "host", 10, 8000, load1=2.0),
        _sample(2, "registration", 2.0, 600),
        _sample(2, "postgres", 1.0, 900),
        _sample(2, "host", 30, 9000, load1=4.0),
        _sample(4, "registration", 9.0, 9999, phase="catch-up"),
    ]

    summary = summarize_resources(rows)

    assert summary["containers_cpu_avg_cores"] == 2.25
    assert summary["containers_cpu_peak_cores"] == 3.0
    assert summary["containers_mem_peak_mib"] == 1500
    assert (summary["host_cpu_avg_percent"], summary["host_cpu_peak_percent"]) == (20, 30)
    assert summary["host_load1_peak"] == 4.0
    assert summary["host_mem_used_peak_mib"] == 9000
    assert (summary["top_cpu_container"], summary["top_cpu_container_peak_cores"]) == ("registration", 2.0)
    assert (summary["top_mem_container"], summary["top_mem_container_peak_mib"]) == ("postgres", 1000)
    assert container_peaks(rows) == {"registration": (1.5, 2.0, 600), "postgres": (0.75, 1.0, 1000)}
    assert summarize_resources([])["containers_cpu_peak_cores"] == ""


def test_catch_up_waits_until_reporting_matches_the_sources():
    now = [0.0]
    answers = iter([({"CONFIRMED": 2}, {"CONFIRMED": 1}), ({"CONFIRMED": 2}, {"CONFIRMED": 2})])

    def sleep(seconds):
        now[0] += seconds

    assert wait_for_catch_up(lambda: next(answers), 10, clock=lambda: now[0], sleep=sleep) == 1.0
    never = wait_for_catch_up(lambda: ({"a": 1}, {}), 3, clock=lambda: now[0], sleep=sleep)
    assert never is None


def _run(participants, verdict="OK", **values):
    row = {
        "run": participants // 100,
        "participants": participants,
        "verdict": verdict,
        "scenario_seconds": 35,
        "requests_per_second": 40,
        "latency_p50_ms": 10,
        "latency_p95_ms": 80,
        "latency_p99_ms": 120,
        "failures": 0,
        "resent": 0,
        "catch_up_seconds": 3.5,
        "containers_cpu_peak_cores": 3.2,
        "containers_cpu_avg_cores": 1.6,
        "containers_mem_peak_mib": 6144,
        "carga_mista": verdict,
        "pico_inscricoes": "OK",
        "ultima_vaga": "OK",
        "envios_repetidos": "OK",
    }
    row.update(values)
    return row


def test_findings_explain_capacity_headroom_and_the_first_failure():
    runs = [
        _run(100),
        _run(1000, latency_p95_ms=400, requests_per_second=90),
        _run(2000, "VERIFICAR", catch_up_seconds="timeout"),
    ]
    notes = findings(runs, HOST, {"registration": (0.6, 1.0, 700), "postgres": (0.2, 0.4, 900)})
    text = "\n".join(notes)

    assert "Highest load with every scenario OK: 1000 participants." in text
    assert "First run that needs checking: 2000 participants (scenarios: carga-mista)" in text
    assert "used at most 3.2 of 16 CPUs (20%)" in text
    assert "The host had spare CPU" in text
    assert "reached about one full core" in text and "registration" in text
    assert "Request p95 went from 80 ms (100 participants) to 80 ms (2000)" in text
    assert "did not catch up within the timeout" in text
    assert findings([], HOST, {}) == ["No run finished."]


def test_report_lists_host_configuration_results_and_charts():
    report = render_report(
        started_at="2026-11-12 17:00 UTC",
        command="bash scripts/benchmark.sh 100",
        host=HOST,
        config={"SCENARIO_MIXED_DURATION_SECONDS": "30", "WAITLIST_OFFER_EXPIRY_MINUTES": ""},
        runs=[_run(100)],
        peaks_by_run={1: {"registration": (0.6, 1.2, 700)}},
        charts=["charts/latency.png"],
        reset=True,
    )

    assert "| CPU | Test CPU |" in report
    assert "| `WAITLIST_OFFER_EXPIRY_MINUTES` | (default) |" in report
    assert "| 100 | OK | 35 | 40 | 10 / 80 / 120 | 0 | 0 | 3.5 | 3.2 | 6144 |" in report
    assert "| registration | 0.6 | 1.2 | 700 |" in report
    assert "![latency](charts/latency.png)" in report
    assert "after restoring the initial data" in report


def test_arguments_default_and_validate():
    args = parse_args([])
    assert args.participants == [100, 500, 1000, 2000, 3000]
    assert args.repeat == 1 and not args.no_reset
    assert parse_args(["30", "60", "--repeat", "2", "--no-reset"]).participants == [30, 60]
    for bad in (["0"], ["--repeat", "0"], ["--duration", "5"]):
        try:
            parse_args(bad)
        except SystemExit:
            continue
        raise AssertionError(bad)

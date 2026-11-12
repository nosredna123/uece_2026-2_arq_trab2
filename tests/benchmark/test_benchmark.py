import json

from scripts.benchmark import (
    HostInfo,
    HostProbe,
    HostSample,
    ResourceSampler,
    classify_limit,
    container_peaks,
    findings,
    parse_args,
    parse_docker_stats,
    parse_memory_mib,
    parse_scenario_log,
    read_cpu_mhz,
    read_meminfo_available_mib,
    read_meminfo_used_mib,
    read_pressure_total,
    read_proc_stat,
    render_report,
    stop_reason,
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
[carga-mista     ] Ofertas que expiraram antes do aceite (carga alta): 7
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
    assert parsed["late_offers"] == 7
    assert parsed["requests_per_second"] == 38.5
    assert (parsed["latency_p50_ms"], parsed["latency_p95_ms"], parsed["latency_max_ms"]) == (12, 80, 900)
    assert parsed["registration_p95_ms"] == 95
    assert parsed["event_management_p95_ms"] == ""


def test_peak_only_log_takes_the_single_scenario_verdict():
    log = f"""Cenário: Pico de inscrições
[  0.1s] Participante de cenário 01 → CONFIRMED

Resultado: 50 CONFIRMED, 150 WAITLISTED
Vagas ocupadas: 50 de 50 — sem overbooking.
Veredito: OK
METRICS {json.dumps(METRICS)}
"""
    parsed = parse_scenario_log(log, "pico-inscricoes")

    assert (parsed["verdict"], parsed["pico_inscricoes"], parsed["carga_mista"]) == ("OK", "OK", "")
    assert parsed["requests_per_second"] == 38.5
    assert parsed["mixed_confirmed"] == 0
    assert parse_scenario_log(log)["verdict"] == "ERRO"


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
    assert read_meminfo_available_mib("MemTotal: 4096 kB\nMemAvailable: 1024 kB\n") == 1.0
    pressure = "some avg10=0.77 avg60=0.77 avg300=0.80 total=1873\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=5\n"
    assert read_pressure_total(pressure) == 1873
    assert read_pressure_total("") == 0


def test_cpu_clock_prefers_cpufreq_and_falls_back_to_cpuinfo(tmp_path):
    assert read_cpu_mhz(tmp_path, "cpu MHz\t\t: 4000.0\ncpu MHz\t\t: 3000.0\n") == 3500
    assert read_cpu_mhz(tmp_path, "") is None
    for index, khz in enumerate((2000000, 4000000)):
        folder = tmp_path / f"cpu{index}" / "cpufreq"
        folder.mkdir(parents=True)
        (folder / "scaling_cur_freq").write_text(f"{khz}\n")
    assert read_cpu_mhz(tmp_path, "cpu MHz : 1.0\n") == 3000


def test_host_probe_turns_stall_totals_into_a_share_of_wall_time(tmp_path):
    proc = tmp_path / "proc"
    (proc / "pressure").mkdir(parents=True)
    (proc / "loadavg").write_text("2.50 2.00 1.00 1/100 1\n")
    (proc / "meminfo").write_text("MemTotal: 8388608 kB\nMemAvailable: 2097152 kB\n")
    (proc / "cpuinfo").write_text("cpu MHz : 3000.0\n")

    def write(busy, idle, cpu_us, io_us):
        (proc / "stat").write_text(f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\n")
        (proc / "pressure" / "cpu").write_text(f"some avg10=0 avg60=0 avg300=0 total={cpu_us}\n")
        (proc / "pressure" / "io").write_text(f"some avg10=0 avg60=0 avg300=0 total={io_us}\n")

    now = [0.0]
    write(0, 0, 0, 0)
    probe = HostProbe(proc, tmp_path / "nocpu", clock=lambda: now[0])
    write(75, 25, 500_000, 0)
    now[0] = 2.0

    sample = probe()

    assert sample == HostSample(
        cpu_percent=75.0,
        load1=2.5,
        mem_used_mib=6144.0,
        mem_available_mib=2048.0,
        cpu_stall_percent=25.0,
        mem_stall_percent=None,
        io_stall_percent=0.0,
        cpu_mhz=3000,
    )


def test_sampler_rows_include_background_cpu_beyond_the_containers():
    sampler = ResourceSampler(1, 100, stats=lambda: {}, host=lambda: None, cpus=16, phase="baseline")
    host = HostSample(cpu_percent=25.0, load1=1.0, mem_used_mib=4000, mem_available_mib=12000, cpu_stall_percent=3.0)

    rows = sampler.to_rows({"registration": (1.5, 500), "postgres": (0.5, 300)}, host)

    assert [row["container"] for row in rows] == ["postgres", "registration", "host"]
    assert rows[-1]["background_cores"] == 2.0
    assert rows[-1]["phase"] == "baseline"
    assert (rows[-1]["cpu_stall_percent"], rows[-1]["io_stall_percent"], rows[-1]["cpu_mhz"]) == (3.0, "", "")
    busy = sampler.to_rows({"registration": (9.0, 500)}, host)
    assert busy[-1]["background_cores"] == 0.0


def _sample(elapsed, container, cpu, mem, phase="scenario", load1=None, **extra):
    row = {
        "run": 1,
        "participants": 100,
        "elapsed_seconds": elapsed,
        "phase": phase,
        "container": container,
        "cpu_cores": cpu,
        "mem_mib": mem,
        **extra,
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
    assert summary["baseline_free_cores"] == "" and summary["cpu_stall_avg_percent"] == ""


def test_host_columns_compare_the_idle_baseline_with_the_run():
    host = {"mem_available_mib": 0, "background_cores": 0, "io_stall_percent": ""}
    rows = [
        _sample(0, "host", 12.5, 4000, phase="baseline", load1=1.0, **{**host, "mem_available_mib": 12000,
                "background_cores": 1.0, "cpu_mhz": 4000, "cpu_stall_percent": 1.0}),
        _sample(0, "host", 95, 9000, load1=8.0, **{**host, "mem_available_mib": 7000, "background_cores": 1.5,
                "cpu_mhz": 3000, "cpu_stall_percent": 30.0, "mem_stall_percent": 0.0}),
        _sample(2, "host", 60, 8000, load1=9.0, **{**host, "mem_available_mib": 8000, "background_cores": 0.5,
                "cpu_mhz": 3400, "cpu_stall_percent": 10.0, "mem_stall_percent": 2.0}),
    ]

    summary = summarize_resources(rows, cpus=16)

    assert (summary["baseline_free_cores"], summary["baseline_background_cores"]) == (14.0, 1.0)
    assert (summary["baseline_mem_available_mib"], summary["baseline_cpu_mhz"]) == (12000, 4000)
    assert (summary["host_cpu_avg_percent"], summary["host_cpu_busy_share_percent"]) == (77.5, 50)
    assert summary["host_mem_available_min_mib"] == 7000
    assert (summary["background_cpu_avg_cores"], summary["background_cpu_peak_cores"]) == (1.0, 1.5)
    assert (summary["cpu_stall_avg_percent"], summary["cpu_stall_peak_percent"]) == (20.0, 30.0)
    assert (summary["mem_stall_peak_percent"], summary["io_stall_peak_percent"]) == (2.0, "")
    assert (summary["cpu_mhz_avg"], summary["cpu_mhz_min"]) == (3200, 3000)


def test_runs_are_classified_by_what_limited_them():
    calm = {"host_cpu_avg_percent": 40, "host_cpu_busy_share_percent": 5, "cpu_stall_avg_percent": 3,
            "mem_stall_peak_percent": 0, "host_mem_available_min_mib": 50000, "baseline_background_cores": 0.3}
    assert classify_limit(calm, 16, 60000) == "architecture"
    assert classify_limit({**calm, "host_cpu_busy_share_percent": 25}, 16, 60000) == "host"
    assert classify_limit({**calm, "cpu_stall_avg_percent": 25}, 16, 60000) == "host"
    assert classify_limit({**calm, "mem_stall_peak_percent": 10}, 16, 60000) == "host"
    assert classify_limit({**calm, "host_mem_available_min_mib": 2000}, 16, 60000) == "host"
    assert classify_limit({**calm, "baseline_background_cores": 2.4}, 16, 60000) == "noisy"
    assert classify_limit({**calm, "baseline_background_cores": ""}, 16, 60000) == "architecture"
    assert classify_limit({"host_cpu_avg_percent": ""}, 16, 60000) == ""


def test_sweep_stops_at_saturation_by_default():
    assert stop_reason({"verdict": "VERIFICAR", "limit": "architecture"}, "saturation") == ""
    assert stop_reason({"verdict": "OK", "limit": "host"}, "saturation") == "the host saturated"
    assert stop_reason({"verdict": "VERIFICAR", "limit": "architecture"}, "verificar") == "this run needs checking"
    assert stop_reason({"verdict": "OK", "limit": "host"}, "never") == ""
    assert stop_reason({"verdict": "ERRO", "limit": ""}, "never").startswith("the scenarios did not run")


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


def test_findings_cross_each_run_with_the_host_resources():
    host = HostInfo("Test CPU", 16, 61440.0, 16, 61440.0, "Linux", "29", "abc123", cpu_max_mhz=4463)
    runs = [
        _run(1000, limit="noisy", baseline_background_cores=3.0, late_offers=0),
        _run(2000, "VERIFICAR", limit="architecture", late_offers=45),
        _run(3000, limit="host", host_cpu_busy_share_percent=60, cpu_stall_avg_percent=35,
             host_mem_available_min_mib=40960, baseline_cpu_mhz=4100, cpu_mhz_avg=3300, cpu_mhz_min=2900),
    ]

    text = "\n".join(findings(runs, host, {}))

    assert "What limited each run: 1000 → noisy; 2000 → architecture; 3000 → host." in text
    assert "The machine saturated at 3000 participants: CPU at or above 90% in 60% of the samples" in text
    assert "waiting for a CPU 35% of the time on average, 40.0 GiB of memory left" in text
    assert "Other programs were using the CPU before 1000 (3.0 cores) participants" in text
    assert "expired before the participant accepted them: 45 in total" in text
    assert "CPU clock at 3000 participants: 3300 MHz on average, 2900 MHz at the lowest (idle before the run: 4100 MHz; maximum 4463 MHz)." in text
    assert "The clock dropped by more than a quarter" in text
    calm = "\n".join(findings([_run(100, limit="architecture")], HOST, {}))
    assert "The machine never saturated" in calm


def test_report_lists_host_configuration_results_and_charts():
    report = render_report(
        started_at="2026-11-12 17:00 UTC",
        command="bash scripts/benchmark.sh 100",
        host=HOST,
        config={"SCENARIO_MIXED_DURATION_SECONDS": "30", "WAITLIST_OFFER_EXPIRY_MINUTES": ""},
        runs=[_run(100, baseline_free_cores=14.5, baseline_mem_available_mib=51200, baseline_background_cores=0.4,
                   host_cpu_avg_percent=30, host_cpu_peak_percent=70, host_cpu_busy_share_percent=0,
                   background_cpu_avg_cores=0.8, background_cpu_peak_cores=1.6, cpu_stall_avg_percent=4,
                   cpu_stall_peak_percent=12, mem_stall_peak_percent=0, io_stall_peak_percent=1,
                   host_mem_available_min_mib=49152, cpu_mhz_avg=3900, cpu_mhz_min=3500, limit="architecture")],
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
    assert (
        "| 100 | 14.5 / 50.0 | 0.4 | 30 / 70 | 0 | 0.8 / 1.6 | 4 / 12 | 0 / 1 | 48.0 | 3900 / 3500 | architecture |"
        in report
    )
    assert "Limit: *host* when the CPU stayed at or above 90%" in report
    assert "every load scenario in parallel" in report

    peak = render_report(
        started_at="2026-11-12 17:00 UTC", command="bash scripts/benchmark.sh --scenario pico-inscricoes",
        host=HOST, config={}, runs=[], peaks_by_run={}, charts=[], reset=False, scenario="pico-inscricoes",
    )
    assert "only the `pico-inscricoes` scenario (`bash scripts/cenario.sh pico-inscricoes`)" in peak


def test_arguments_default_and_validate():
    args = parse_args([])
    assert args.participants == [100, 500, 1000, 2000, 3000]
    assert args.repeat == 1 and not args.no_reset
    assert (args.stop_at, args.baseline) == ("saturation", 10)
    assert parse_args(["--keep-going"]).stop_at == "never"
    assert parse_args(["--stop-at", "verificar", "--baseline", "0"]).stop_at == "verificar"
    assert parse_args(["30", "60", "--repeat", "2", "--no-reset"]).participants == [30, 60]
    assert parse_args(["--connections", "100"]).connections == 100
    assert parse_args([]).connections is None
    assert parse_args([]).scenario == "all"
    assert parse_args(["--scenario", "pico-inscricoes", "--duration", "5"]).duration == 5
    for bad in (
        ["0"], ["--repeat", "0"], ["--duration", "5"], ["--baseline", "-1"], ["--stop-at", "x"],
        ["--connections", "0"], ["--connections", "201"], ["--scenario", "ultima-vaga"],
        ["--scenario", "pico-inscricoes", "--duration", "301"],
    ):
        try:
            parse_args(bad)
        except SystemExit:
            continue
        raise AssertionError(bad)

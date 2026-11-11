import os
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
BASE_COMPOSE = ROOT / "infra/docker-compose.yml"
WEB_COMPOSE = ROOT / "infra/docker-compose.demo-web.yml"
LAB_COMPOSE = ROOT / "infra/docker-compose.demo-lab.yml"
RUNNER = ROOT / "scripts/run-demo-web.sh"
TEST_RUNNER = ROOT / "scripts/test-demo-web.sh"


def _web_compose():
    return yaml.safe_load(WEB_COMPOSE.read_text(encoding="utf-8"))


def test_demo_web_is_an_opt_in_localhost_profile_with_internal_service_urls():
    base = yaml.safe_load(BASE_COMPOSE.read_text(encoding="utf-8"))
    overlay = _web_compose()
    service = overlay["services"]["demo-web"]
    environment = service["environment"]

    assert "demo-web" not in base["services"]
    assert "demo-web" in service["profiles"]
    assert service["ports"] == ["127.0.0.1:${DEMO_WEB_PORT:?Set DEMO_WEB_PORT in infra/.env}:8080"]
    assert {
        "identity-mock",
        "event-management",
        "registration",
        "attendance",
        "reporting",
        "billing-mock",
        "notification-mock",
    }.issubset(service["depends_on"])
    assert (
        environment["DEMO_WEB_ENABLED"] == "${DEMO_WEB_ENABLED:?Set DEMO_WEB_ENABLED in infra/.env}"
    )
    expected_urls = {
        "DEMO_WEB_EVENT_BASE_URL": "http://event-management:8080",
        "DEMO_WEB_REGISTRATION_BASE_URL": "http://registration:8080",
        "DEMO_WEB_ATTENDANCE_BASE_URL": "http://attendance:8080",
        "DEMO_WEB_REPORTING_BASE_URL": "http://reporting:8080",
    }
    example = (ROOT / "infra/.env.example").read_text(encoding="utf-8")
    for key, url in expected_urls.items():
        assert environment[key] == "${" + key + ":?Set " + key + " in infra/.env}"
        assert f"{key}={url}" in example


def test_scenario_pool_is_opt_in_and_fixture_mounts_are_read_only():
    base = yaml.safe_load(BASE_COMPOSE.read_text(encoding="utf-8"))
    service = _web_compose()["services"]["demo-web"]
    lab_service = yaml.safe_load(LAB_COMPOSE.read_text(encoding="utf-8"))["services"]["demo-web"]
    environment = service["environment"]
    lab_environment = lab_service["environment"]
    identity_environment = base["services"]["identity-mock"]["environment"]

    assert (
        identity_environment["IDENTITY_LAB_ENABLED"]
        == "${IDENTITY_LAB_ENABLED:?Set IDENTITY_LAB_ENABLED in infra/.env}"
    )
    assert (
        identity_environment["IDENTITY_LAB_FIXTURES_PATH"]
        == "${IDENTITY_LAB_FIXTURES_PATH:-/app/scenario-participants.json}"
    )
    assert (
        environment["IDENTITY_LAB_ENABLED"]
        == "${IDENTITY_LAB_ENABLED:?Set IDENTITY_LAB_ENABLED in infra/.env}"
    )
    assert (
        environment["DEMO_LAB_ENABLED"] == "${DEMO_LAB_ENABLED:?Set DEMO_LAB_ENABLED in infra/.env}"
    )
    assert "DEMO_LAB_FIXTURES_PATH" not in environment
    assert (
        lab_environment["DEMO_LAB_ENABLED"]
        == "${DEMO_LAB_ENABLED:?Set DEMO_LAB_ENABLED in infra/.env}"
    )
    assert (
        lab_environment["DEMO_LAB_FIXTURES_PATH"]
        == "${DEMO_LAB_FIXTURES_PATH:?Set DEMO_LAB_FIXTURES_PATH in infra/.env}"
    )
    assert (
        lab_environment["DEMO_LAB_MAX_PARTICIPANTS"]
        == "${DEMO_LAB_MAX_PARTICIPANTS:?Set DEMO_LAB_MAX_PARTICIPANTS in infra/.env}"
    )

    web_mounts = service["volumes"]
    lab_mounts = lab_service["volumes"]
    assert any(
        "identity_access_mock/fixtures.json:/run/eventhub/identity-fixtures.json:ro" in item
        for item in web_mounts
    )
    assert not any("scenario-participants.json" in item for item in web_mounts)
    assert any(
        "scenario-participants.json:/run/eventhub/scenario-participants.json:ro" in item
        for item in lab_mounts
    )
    assert all(item.endswith(":ro") for item in web_mounts + lab_mounts)


def test_demo_web_has_no_database_broker_or_docker_socket_access():
    services = [
        _web_compose()["services"]["demo-web"],
        yaml.safe_load(LAB_COMPOSE.read_text(encoding="utf-8"))["services"]["demo-web"],
    ]
    forbidden_environment = {
        "DATABASE_URL",
        "DATABASE_USER",
        "DATABASE_PASSWORD",
        "POSTGRES_PASSWORD",
        "RABBITMQ_HOST",
        "RABBITMQ_PASSWORD",
    }

    for service in services:
        assert forbidden_environment.isdisjoint(service["environment"])
        assert "/var/run/docker.sock" not in str(service)
        assert service.get("privileged", False) is False


def _runner_environment(
    tmp_path,
    *,
    demo_port=18085,
    demo_running=False,
    identity_running=False,
    port_busy=False,
    backend_ports_busy=False,
    lab_enabled=False,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case " $* " in
  *" ps --status running -q demo-web "*)
    [[ "${FAKE_DEMO_RUNNING:-false}" == true ]] && printf 'demo-web-container\\n'
    ;;
  *" ps --status running -q identity-mock "*)
    [[ "${FAKE_IDENTITY_RUNNING:-false}" == true ]] && printf 'identity-container\\n'
    ;;
esac
if [[ " $* " == *" up "* && "${FAKE_DOCKER_UP_STATUS:-0}" != 0 ]]; then
  exit "$FAKE_DOCKER_UP_STATUS"
fi
if [[ " $* " == *" inspect --format "* ]]; then
  printf 'IDENTITY_LAB_ENABLED=false\\n'
fi
if [[ -n "${FAKE_DOCKER_FAIL_MATCH:-}" && "$*" == *"$FAKE_DOCKER_FAIL_MATCH"* ]]; then
  exit "${FAKE_DOCKER_FAIL_STATUS:-19}"
fi
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    ss = bin_dir / "ss"
    ss.write_text(
        """#!/usr/bin/env bash
        set -eu
        printf '%s\\n' "$*" >> "$FAKE_SS_LOG"
        if [[ "${FAKE_PORT_BUSY:-false}" == true && "$*" == *":$DEMO_WEB_PORT"* ]]; then
          printf 'LISTEN 0 128 127.0.0.1:%s 0.0.0.0:*\\n' "$DEMO_WEB_PORT"
        fi
        if [[ "${FAKE_BACKEND_PORTS_BUSY:-false}" == true && "$*" == *":5432"* ]]; then
          printf 'LISTEN 0 128 127.0.0.1:5432 0.0.0.0:*\\n'
        fi
        """,
        encoding="utf-8",
    )
    ss.chmod(0o755)
    env_file = tmp_path / "infra.env"
    env_file.write_text(
        f"DEMO_WEB_PORT={demo_port}\n"
        "DEMO_WEB_ENABLED=true\n"
        f"IDENTITY_LAB_ENABLED={str(lab_enabled).lower()}\n"
        f"DEMO_LAB_ENABLED={str(lab_enabled).lower()}\n"
        + (
            "DEMO_LAB_FIXTURES_PATH=/run/eventhub/scenario-participants.json\n"
            "DEMO_LAB_MAX_PARTICIPANTS=50\n"
            "DEMO_LAB_MAX_CONCURRENCY=10\n"
            "DEMO_LAB_RUN_TIMEOUT_SECONDS=60\n"
            "DEMO_LAB_SETTLE_TIMEOUT_SECONDS=30\n"
            "DEMO_LAB_RUN_TTL_SECONDS=1800\n"
            "DEMO_LAB_MAX_RETAINED_RUNS=5\n"
            if lab_enabled
            else ""
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "EVENTHUB_ENV_FILE": str(env_file),
            "DEMO_WEB_PORT": str(demo_port),
            "FAKE_DEMO_RUNNING": str(demo_running).lower(),
            "FAKE_IDENTITY_RUNNING": str(identity_running).lower(),
            "FAKE_PORT_BUSY": str(port_busy).lower(),
            "FAKE_BACKEND_PORTS_BUSY": str(backend_ports_busy).lower(),
            "FAKE_DOCKER_LOG": str(tmp_path / "docker.log"),
            "FAKE_SS_LOG": str(tmp_path / "ss.log"),
        }
    )
    return env


def _run_demo_web(tmp_path, *, args=(), **options):
    environment = _runner_environment(tmp_path, **options)
    return subprocess.run(
        ["bash", str(RUNNER), *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    ), environment


def test_runner_does_not_run_backend_port_gate_or_delete_volumes_on_existing_stack(tmp_path):
    result, environment = _run_demo_web(tmp_path, demo_running=True)

    assert result.returncode == 0, result.stderr
    docker_log = Path(environment["FAKE_DOCKER_LOG"]).read_text(encoding="utf-8")
    assert "up" in docker_log and "demo-web" in docker_log
    assert "check-environment.sh" not in docker_log
    assert "down" not in docker_log
    assert "--volumes" not in docker_log
    assert not Path(environment["FAKE_SS_LOG"]).exists()
    assert "http://localhost:18085" in result.stdout


def test_runner_reports_an_unrelated_web_port_collision(tmp_path):
    result, environment = _run_demo_web(tmp_path, port_busy=True)

    assert result.returncode != 0
    assert "port 18085" in result.stderr.lower()
    docker_log = Path(environment["FAKE_DOCKER_LOG"]).read_text(encoding="utf-8")
    assert " up " not in f" {docker_log} "


def test_runner_checks_only_the_web_port_when_backend_stack_ports_are_in_use(tmp_path):
    result, environment = _run_demo_web(tmp_path, backend_ports_busy=True)

    assert result.returncode == 0, result.stderr
    port_checks = Path(environment["FAKE_SS_LOG"]).read_text(encoding="utf-8").splitlines()
    assert port_checks == ["-H -ltn sport = :18085"]
    assert "http://localhost:18085" in result.stdout


def test_runner_preserves_compose_failure_and_never_removes_volumes(tmp_path):
    result, environment = _run_demo_web(tmp_path)
    assert result.returncode == 0, result.stderr

    environment["FAKE_DOCKER_UP_STATUS"] = "23"
    failed = subprocess.run(
        ["bash", str(RUNNER)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert failed.returncode == 23
    docker_log = Path(environment["FAKE_DOCKER_LOG"]).read_text(encoding="utf-8")
    assert "down" not in docker_log
    assert "--volumes" not in docker_log


def test_runner_requires_existing_env_file_without_creating_or_overwriting_it(tmp_path):
    environment = _runner_environment(tmp_path)
    env_file = Path(environment["EVENTHUB_ENV_FILE"])
    env_file.unlink()
    environment["EVENTHUB_ENV_FILE"] = str(env_file)

    result = subprocess.run(
        ["bash", str(RUNNER)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "infra/.env" in result.stderr
    assert not env_file.exists()
    assert not Path(environment["FAKE_DOCKER_LOG"]).exists()


def test_runner_requires_explicit_identity_restart_when_enabling_lab_pool(tmp_path):
    result, environment = _run_demo_web(
        tmp_path,
        identity_running=True,
        lab_enabled=True,
    )

    assert result.returncode != 0
    assert "--restart-identity-pool" in result.stderr
    assert " up " not in f" {Path(environment['FAKE_DOCKER_LOG']).read_text()} "

    restarted, environment = _run_demo_web(
        tmp_path,
        args=("--restart-identity-pool",),
        identity_running=True,
        lab_enabled=True,
    )

    assert restarted.returncode == 0, restarted.stderr
    docker_log = Path(environment["FAKE_DOCKER_LOG"]).read_text(encoding="utf-8")
    assert "--force-recreate" in docker_log
    assert "identity-mock" in docker_log
    assert "demo-web" in docker_log


def test_test_runner_is_separate_bounded_and_never_takes_down_stack(tmp_path):
    environment = _runner_environment(tmp_path)
    result = subprocess.run(
        ["bash", str(TEST_RUNNER)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    docker_log = Path(environment["FAKE_DOCKER_LOG"]).read_text(encoding="utf-8")
    assert "build python-suite-test" in docker_log
    assert "run --rm python-suite-test timeout" in docker_log
    assert "python -m pytest -x --import-mode=importlib" in docker_log
    assert "up" not in docker_log
    assert "down" not in docker_log
    assert "--volumes" not in docker_log

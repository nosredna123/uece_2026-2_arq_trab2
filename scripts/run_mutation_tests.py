import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICES = (
    "services/attendance-certification-python",
    "services/reporting-python",
)
REQUIRED_SCORE = 95
INCOMPLETE_STATUSES = (
    "no_tests",
    "skipped",
    "suspicious",
    "timeout",
    "check_was_interrupted_by_user",
    "segfault",
)


def run_mutation_tests(service: str) -> None:
    working_directory = ROOT / service
    subprocess.run(
        [sys.executable, "-m", "mutmut", "run"],
        cwd=working_directory,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "mutmut", "export-cicd-stats"],
        cwd=working_directory,
        check=True,
    )

    stats_path = working_directory / "mutants" / "mutmut-cicd-stats.json"
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    killed = stats["killed"]
    survived = stats["survived"]
    total = stats["total"]
    incomplete = {
        status: stats.get(status, 0)
        for status in INCOMPLETE_STATUSES
        if stats.get(status, 0)
    }

    if total == 0:
        raise RuntimeError(f"{service}: mutation run generated no mutants")
    if incomplete:
        raise RuntimeError(f"{service}: mutation run was incomplete: {incomplete}")
    if killed + survived != total:
        raise RuntimeError(
            f"{service}: mutation counts do not reconcile: "
            f"killed={killed}, survived={survived}, total={total}"
        )

    score_percent = 100 * killed / total
    print(
        f"{service}: {score_percent:.2f}% "
        f"({killed} killed / {total} total; {survived} survived)",
        flush=True,
    )
    if 100 * killed < REQUIRED_SCORE * total:
        raise RuntimeError(
            f"{service}: mutation score {score_percent:.2f}% "
            f"is below the required {REQUIRED_SCORE}%"
        )


def main() -> None:
    for service in SERVICES:
        run_mutation_tests(service)


if __name__ == "__main__":
    main()

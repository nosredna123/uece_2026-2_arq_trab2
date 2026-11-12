"""Regenerate the synthetic scenario identity pool used by load scenarios.

Usage: python scripts/generate-scenario-participants.py
"""

import json
from pathlib import Path


COUNT = 20_000
TARGET = Path(__file__).resolve().parents[1] / "mocks/identity-access/scenario-participants.json"


def main() -> None:
    pool = {
        f"scenarioParticipant{index:02}": {
            "token": f"synthetic-scenario-participant-{index:02}",
            "subjectId": f"50000000-0000-4000-8000-{index:012d}",
            "role": "PARTICIPANT",
        }
        for index in range(1, COUNT + 1)
    }
    TARGET.write_text(json.dumps(pool, indent=1) + "\n", encoding="utf-8")
    print(f"{COUNT} identities written to {TARGET} ({TARGET.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

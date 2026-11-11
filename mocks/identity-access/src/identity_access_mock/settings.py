import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    internal_service_key: str = field(repr=False)
    available: bool
    scenario_pool_enabled: bool = False
    scenario_fixtures_path: Path | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        service_key = os.environ.get("INTERNAL_SERVICE_KEY")
        if service_key is None or not service_key.strip():
            raise RuntimeError("INTERNAL_SERVICE_KEY must be configured")

        available_value = os.environ.get("IDENTITY_MOCK_AVAILABLE")
        if available_value not in {"true", "false"}:
            raise RuntimeError("IDENTITY_MOCK_AVAILABLE must be exactly 'true' or 'false'")

        scenario_pool_value = os.environ.get("IDENTITY_MOCK_SCENARIO_POOL_ENABLED", "false")
        if scenario_pool_value not in {"true", "false"}:
            raise RuntimeError(
                "IDENTITY_MOCK_SCENARIO_POOL_ENABLED must be exactly 'true' or 'false'"
            )
        scenario_fixtures_value = os.environ.get("IDENTITY_MOCK_SCENARIO_FIXTURES_PATH")
        if scenario_pool_value == "true" and not scenario_fixtures_value:
            raise RuntimeError(
                "IDENTITY_MOCK_SCENARIO_FIXTURES_PATH must be configured "
                "when the scenario pool is enabled"
            )

        return cls(
            internal_service_key=service_key,
            available=available_value == "true",
            scenario_pool_enabled=scenario_pool_value == "true",
            scenario_fixtures_path=(
                Path(scenario_fixtures_value) if scenario_fixtures_value else None
            ),
        )

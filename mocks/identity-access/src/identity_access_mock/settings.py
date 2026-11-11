import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    internal_service_key: str = field(repr=False)
    available: bool
    lab_enabled: bool = False
    lab_fixtures_path: Path | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        service_key = os.environ.get("INTERNAL_SERVICE_KEY")
        if service_key is None or not service_key.strip():
            raise RuntimeError("INTERNAL_SERVICE_KEY must be configured")

        available_value = os.environ.get("IDENTITY_MOCK_AVAILABLE")
        if available_value not in {"true", "false"}:
            raise RuntimeError("IDENTITY_MOCK_AVAILABLE must be exactly 'true' or 'false'")

        lab_enabled_value = os.environ.get("IDENTITY_LAB_ENABLED", "false")
        if lab_enabled_value not in {"true", "false"}:
            raise RuntimeError("IDENTITY_LAB_ENABLED must be exactly 'true' or 'false'")
        lab_fixtures_value = os.environ.get("IDENTITY_LAB_FIXTURES_PATH")
        if lab_enabled_value == "true" and not lab_fixtures_value:
            raise RuntimeError(
                "IDENTITY_LAB_FIXTURES_PATH must be configured when the lab pool is enabled"
            )

        return cls(
            internal_service_key=service_key,
            available=available_value == "true",
            lab_enabled=lab_enabled_value == "true",
            lab_fixtures_path=(Path(lab_fixtures_value) if lab_fixtures_value else None),
        )

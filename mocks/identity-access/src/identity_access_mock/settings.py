import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    internal_service_key: str = field(repr=False)
    available: bool

    @classmethod
    def from_environment(cls) -> "Settings":
        service_key = os.environ.get("INTERNAL_SERVICE_KEY")
        if service_key is None or not service_key.strip():
            raise RuntimeError("INTERNAL_SERVICE_KEY must be configured")

        available_value = os.environ.get("IDENTITY_MOCK_AVAILABLE")
        if available_value not in {"true", "false"}:
            raise RuntimeError(
                "IDENTITY_MOCK_AVAILABLE must be exactly 'true' or 'false'"
            )

        return cls(
            internal_service_key=service_key,
            available=available_value == "true",
        )

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class NotificationSettings:
    available: bool

    def __post_init__(self) -> None:
        if not isinstance(self.available, bool):
            raise ValueError("notification availability must be a boolean")


@dataclass(frozen=True)
class WorkerSettings:
    available: bool
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_user: str
    rabbitmq_password: str = field(repr=False)
    store_path: Path
    retry_initial_seconds: int
    retry_max_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.available, bool):
            raise ValueError("notification availability must be a boolean")
        if not self.rabbitmq_host.strip() or not self.rabbitmq_user.strip():
            raise ValueError("RabbitMQ host and user must be configured")
        if not self.rabbitmq_password:
            raise ValueError("RabbitMQ password must be configured")
        if not 1 <= self.rabbitmq_port <= 65535:
            raise ValueError("RabbitMQ port must be a valid TCP port")
        if not str(self.store_path).strip():
            raise ValueError("notification store path must be configured")
        if self.retry_initial_seconds <= 0:
            raise ValueError("initial retry delay must be positive")
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError("maximum retry delay must be at least the initial delay")

    @classmethod
    def from_environment(cls) -> "WorkerSettings":
        def required(name: str) -> str:
            value = os.environ.get(name)
            if value is None or not value.strip():
                raise RuntimeError(f"{name} must be configured")
            return value

        def positive_integer(name: str) -> int:
            value = required(name)
            try:
                number = int(value)
            except ValueError:
                raise RuntimeError(f"{name} must be a positive integer") from None
            if number <= 0:
                raise RuntimeError(f"{name} must be a positive integer")
            return number

        availability = required("NOTIFICATION_MOCK_AVAILABLE")
        if availability not in {"true", "false"}:
            raise RuntimeError(
                "NOTIFICATION_MOCK_AVAILABLE must be true or false"
            )

        port = positive_integer("RABBITMQ_PORT")
        if port > 65535:
            raise RuntimeError("RABBITMQ_PORT must be a valid TCP port")

        retry_initial = positive_integer("MESSAGE_RETRY_INITIAL_SECONDS")
        retry_maximum = positive_integer("MESSAGE_RETRY_MAX_SECONDS")
        if retry_initial > retry_maximum:
            raise RuntimeError(
                "MESSAGE_RETRY_MAX_SECONDS must be at least the initial delay"
            )

        return cls(
            available=availability == "true",
            rabbitmq_host=required("RABBITMQ_HOST"),
            rabbitmq_port=port,
            rabbitmq_user=required("RABBITMQ_USER"),
            rabbitmq_password=required("RABBITMQ_PASSWORD"),
            store_path=Path(required("NOTIFICATION_STORE_PATH")),
            retry_initial_seconds=retry_initial,
            retry_max_seconds=retry_maximum,
        )

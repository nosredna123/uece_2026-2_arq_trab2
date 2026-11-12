import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast


Scenario = Literal["APPROVED", "REFUSED", "TIMEOUT"]
MAX_PAYMENT_DELAY_SECONDS = 60


def parse_delay_range(value: str) -> tuple[int, int]:
    """Parse "N" or "MIN-MAX" whole seconds, from 0 through MAX_PAYMENT_DELAY_SECONDS."""
    parts = value.strip().split("-")
    if len(parts) not in {1, 2} or not all(part.strip().isdigit() for part in parts):
        raise ValueError("delay must be N or MIN-MAX whole seconds")
    low, high = int(parts[0]), int(parts[-1])
    if low > high or high > MAX_PAYMENT_DELAY_SECONDS:
        raise ValueError(
            f"delay must be from 0 through {MAX_PAYMENT_DELAY_SECONDS} seconds, MIN <= MAX"
        )
    return low, high


@dataclass(frozen=True)
class BillingSettings:
    payment_scenario: Scenario
    refund_scenario: Scenario
    refund_percentage: int
    payment_delay_seconds: tuple[int, int] = (0, 0)

    def __post_init__(self) -> None:
        if self.payment_scenario not in {"APPROVED", "REFUSED", "TIMEOUT"}:
            raise ValueError("invalid payment scenario")
        if self.refund_scenario not in {"APPROVED", "REFUSED", "TIMEOUT"}:
            raise ValueError("invalid refund scenario")
        if not 1 <= self.refund_percentage <= 100:
            raise ValueError("refund percentage must be from 1 through 100")
        low, high = self.payment_delay_seconds
        if not 0 <= low <= high <= MAX_PAYMENT_DELAY_SECONDS:
            raise ValueError("invalid payment delay range")


@dataclass(frozen=True)
class WorkerSettings:
    billing: BillingSettings
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_user: str
    rabbitmq_password: str = field(repr=False)
    store_path: Path
    retry_initial_seconds: int
    retry_max_seconds: int

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

        def scenario(name: str) -> Scenario:
            value = required(name)
            if value not in {"APPROVED", "REFUSED", "TIMEOUT"}:
                raise RuntimeError(f"{name} has an unsupported scenario")
            return cast(Scenario, value)

        percentage = positive_integer("REFUND_PERCENTAGE")
        if percentage > 100:
            raise RuntimeError("REFUND_PERCENTAGE must be from 1 through 100")

        port = positive_integer("RABBITMQ_PORT")
        if port > 65535:
            raise RuntimeError("RABBITMQ_PORT must be a valid TCP port")

        retry_initial = positive_integer("MESSAGE_RETRY_INITIAL_SECONDS")
        retry_maximum = positive_integer("MESSAGE_RETRY_MAX_SECONDS")
        if retry_initial > retry_maximum:
            raise RuntimeError(
                "MESSAGE_RETRY_MAX_SECONDS must be at least the initial delay"
            )

        try:
            payment_delay = parse_delay_range(
                os.environ.get("BILLING_MOCK_APPROVAL_DELAY_SECONDS", "0") or "0"
            )
        except ValueError as error:
            raise RuntimeError(f"BILLING_MOCK_APPROVAL_DELAY_SECONDS: {error}") from None

        billing_settings = BillingSettings(
            payment_scenario=scenario("BILLING_MOCK_SCENARIO"),
            refund_scenario=scenario("BILLING_MOCK_REFUND_SCENARIO"),
            refund_percentage=percentage,
            payment_delay_seconds=payment_delay,
        )
        return cls(
            billing=billing_settings,
            rabbitmq_host=required("RABBITMQ_HOST"),
            rabbitmq_port=port,
            rabbitmq_user=required("RABBITMQ_USER"),
            rabbitmq_password=required("RABBITMQ_PASSWORD"),
            store_path=Path(required("BILLING_STORE_PATH")),
            retry_initial_seconds=retry_initial,
            retry_max_seconds=retry_maximum,
        )

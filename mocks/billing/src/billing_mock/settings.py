import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast


Scenario = Literal["APPROVED", "REFUSED", "TIMEOUT"]


@dataclass(frozen=True)
class BillingSettings:
    payment_scenario: Scenario
    refund_scenario: Scenario
    refund_percentage: int

    def __post_init__(self) -> None:
        if self.payment_scenario not in {"APPROVED", "REFUSED", "TIMEOUT"}:
            raise ValueError("invalid payment scenario")
        if self.refund_scenario not in {"APPROVED", "REFUSED", "TIMEOUT"}:
            raise ValueError("invalid refund scenario")
        if not 1 <= self.refund_percentage <= 100:
            raise ValueError("refund percentage must be from 1 through 100")


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

        billing_settings = BillingSettings(
            payment_scenario=scenario("BILLING_MOCK_SCENARIO"),
            refund_scenario=scenario("BILLING_MOCK_REFUND_SCENARIO"),
            refund_percentage=percentage,
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

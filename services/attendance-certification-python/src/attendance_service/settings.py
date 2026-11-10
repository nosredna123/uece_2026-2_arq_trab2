import os
from typing import Literal, Self

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    SecretStr,
    field_validator,
    model_validator,
)


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr = Field(validation_alias="DATABASE_URL", min_length=1)
    identity_base_url: AnyHttpUrl = Field(validation_alias="IDENTITY_BASE_URL")
    internal_service_key: SecretStr = Field(
        validation_alias="INTERNAL_SERVICE_KEY",
        min_length=1,
    )
    http_timeout_seconds: PositiveInt = Field(validation_alias="HTTP_TIMEOUT_SECONDS")
    retry_initial_seconds: PositiveInt = Field(
        validation_alias="MESSAGE_RETRY_INITIAL_SECONDS"
    )
    retry_max_seconds: PositiveInt = Field(
        validation_alias="MESSAGE_RETRY_MAX_SECONDS"
    )
    rabbitmq_host: str = Field(validation_alias="RABBITMQ_HOST", min_length=1)
    rabbitmq_port: PositiveInt = Field(validation_alias="RABBITMQ_PORT")
    rabbitmq_user: str = Field(validation_alias="RABBITMQ_USER", min_length=1)
    rabbitmq_password: SecretStr = Field(
        validation_alias="RABBITMQ_PASSWORD",
        min_length=1,
    )
    certificate_policy: Literal["CHECK_IN"] = Field(
        validation_alias="CERTIFICATE_ELIGIBILITY_POLICY"
    )
    certificate_after_event: Literal[True] = Field(
        validation_alias="CERTIFICATE_ISSUANCE_AFTER_EVENT"
    )

    @field_validator("rabbitmq_port")
    @classmethod
    def validate_rabbitmq_port(cls, value: int) -> int:
        if value > 65535:
            raise ValueError("RabbitMQ port must be a valid TCP port")
        return value

    @field_validator("rabbitmq_host", "rabbitmq_user")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("RabbitMQ host and user must not be empty")
        return value

    @field_validator("certificate_after_event", mode="before")
    @classmethod
    def parse_required_boolean(cls, value: object) -> bool:
        if value == "true" or value is True:
            return True
        raise ValueError("certificate issuance must remain after activity completion")

    @model_validator(mode="after")
    def validate_retry_order(self) -> Self:
        if self.retry_initial_seconds > self.retry_max_seconds:
            raise ValueError("retry maximum must not be less than initial delay")
        return self

    @classmethod
    def from_environment(cls) -> Self:
        return cls.model_validate(dict(os.environ))

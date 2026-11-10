from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class InvalidBillingRequest(ValueError):
    pass


class BillingRequestData(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID = Field(alias="operationId")
    attempt_id: UUID = Field(alias="attemptId")
    kind: Literal["PAYMENT", "REFUND"]
    registration_id: UUID = Field(alias="registrationId")
    activity_id: UUID = Field(alias="activityId")
    organization_id: UUID = Field(alias="organizationId")
    participant_id: UUID = Field(alias="participantId")
    amount_minor: int = Field(alias="amountMinor", strict=True, ge=0)
    currency: str = Field(strict=True, pattern=r"^[A-Z]{3}$")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    original_payment_id: UUID | None = Field(
        default=None, alias="originalPaymentId"
    )

    @model_validator(mode="after")
    def validate_operation(self) -> "BillingRequestData":
        if self.kind == "PAYMENT":
            if self.amount_minor <= 0 or self.expires_at is None:
                raise ValueError("payment requires a positive amount and expiry")
        elif self.original_payment_id is None:
            raise ValueError("refund requires an original payment")

        if (
            self.expires_at is not None
            and self.expires_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("expiry must be UTC")
        return self


class BillingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID = Field(alias="eventId")
    event_type: Literal["billing.requested"] = Field(alias="eventType")
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    aggregate_id: UUID = Field(alias="aggregateId")
    aggregate_version: int = Field(alias="aggregateVersion", strict=True, ge=1)
    correlation_id: UUID = Field(alias="correlationId")
    occurred_at: datetime = Field(alias="occurredAt")
    producer: Literal["registration"]
    data: BillingRequestData

    @model_validator(mode="after")
    def validate_envelope(self) -> "BillingRequest":
        if self.aggregate_id != self.data.operation_id:
            raise ValueError("billing operation must be the aggregate")
        if self.occurred_at.utcoffset() != timedelta(0):
            raise ValueError("event timestamp must be UTC")
        return self

    @classmethod
    def from_message(cls, message: bytes) -> "BillingRequest":
        try:
            return cls.model_validate_json(message)
        except (ValidationError, ValueError):
            raise InvalidBillingRequest(
                "message does not match the BillingRequest contract"
            ) from None

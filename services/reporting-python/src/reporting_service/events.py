from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    model_validator,
)

from reporting_service.errors import InvalidEvent


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Envelope(ContractModel):
    event_id: UUID = Field(alias="eventId")
    event_type: str = Field(alias="eventType", min_length=1)
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    aggregate_id: UUID = Field(alias="aggregateId")
    aggregate_version: StrictInt = Field(alias="aggregateVersion", gt=0)
    correlation_id: UUID = Field(alias="correlationId")
    occurred_at: AwareDatetime = Field(alias="occurredAt")
    producer: str
    data: dict


class ActivityData(ContractModel):
    activity_id: UUID = Field(alias="activityId")
    event_id: UUID = Field(alias="eventId")
    organization_id: UUID = Field(alias="organizationId")
    status: Literal["DRAFT", "PUBLISHED", "CANCELLED"]
    starts_at: AwareDatetime = Field(alias="startsAt")
    ends_at: AwareDatetime = Field(alias="endsAt")


class RegistrationData(ContractModel):
    registration_id: UUID = Field(alias="registrationId")
    activity_id: UUID = Field(alias="activityId")
    organization_id: UUID = Field(alias="organizationId")
    participant_id: UUID = Field(alias="participantId")
    status: Literal[
        "WAITLISTED",
        "OFFERED",
        "PENDING_PAYMENT",
        "CONFIRMED",
        "REFUSED",
        "EXPIRED",
        "CANCELLED",
    ]
    refund_status: Literal["NONE", "PENDING", "APPROVED"] = Field(alias="refundStatus")
    amount_minor: StrictInt = Field(alias="amountMinor", ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    activity_starts_at: AwareDatetime = Field(alias="activityStartsAt")
    activity_ends_at: AwareDatetime = Field(alias="activityEndsAt")
    expires_at: AwareDatetime | None = Field(default=None, alias="expiresAt")
    offer_expires_at: AwareDatetime | None = Field(
        default=None,
        alias="offerExpiresAt",
    )

    @model_validator(mode="after")
    def validate_expiry(self):
        if self.status == "PENDING_PAYMENT" and self.expires_at is None:
            raise ValueError("PENDING_PAYMENT requires expiresAt")
        if self.status == "OFFERED" and self.offer_expires_at is None:
            raise ValueError("OFFERED requires offerExpiresAt")
        return self


class BillingData(ContractModel):
    operation_id: UUID = Field(alias="operationId")
    attempt_id: UUID = Field(alias="attemptId")
    kind: Literal["PAYMENT", "REFUND"]
    registration_id: UUID = Field(alias="registrationId")
    activity_id: UUID = Field(alias="activityId")
    organization_id: UUID = Field(alias="organizationId")
    status: Literal["APPROVED", "REFUSED"]
    amount_minor: StrictInt = Field(alias="amountMinor", ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class AttendanceData(ContractModel):
    registration_id: UUID = Field(alias="registrationId")
    activity_id: UUID = Field(alias="activityId")
    organization_id: UUID = Field(alias="organizationId")
    participant_id: UUID = Field(alias="participantId")
    checked_in_at: AwareDatetime = Field(alias="checkedInAt")


class CertificateData(ContractModel):
    certificate_id: UUID = Field(alias="certificateId")
    registration_id: UUID = Field(alias="registrationId")
    activity_id: UUID = Field(alias="activityId")
    organization_id: UUID = Field(alias="organizationId")
    participant_id: UUID = Field(alias="participantId")
    issued_at: AwareDatetime = Field(alias="issuedAt")


EVENT_MODELS = {
    "activity.changed": ("event-management", ActivityData, "activity_id"),
    "registration.changed": ("registration", RegistrationData, "registration_id"),
    "billing.result": ("billing-mock", BillingData, "operation_id"),
    "attendance.recorded": (
        "attendance-certification",
        AttendanceData,
        "registration_id",
    ),
    "certificate.issued": (
        "attendance-certification",
        CertificateData,
        "registration_id",
    ),
}


def parse_event(payload: bytes | str | dict) -> dict:
    try:
        envelope = Envelope.model_validate_json(payload) if isinstance(
            payload, (bytes, str)
        ) else Envelope.model_validate(payload)
        model_spec = EVENT_MODELS.get(envelope.event_type)
        if model_spec is None:
            raise InvalidEvent(f"unsupported event type: {envelope.event_type}")
        expected_producer, data_model, aggregate_field = model_spec
        if envelope.producer != expected_producer:
            raise InvalidEvent("event producer does not match event type")
        data = data_model.model_validate(envelope.data)
        if getattr(data, aggregate_field) != envelope.aggregate_id:
            raise InvalidEvent("aggregateId does not match event data")
        parsed = envelope.model_dump(mode="json", by_alias=True)
        parsed["data"] = data.model_dump(mode="json", by_alias=True)
        return parsed
    except InvalidEvent:
        raise
    except (TypeError, ValueError) as error:
        raise InvalidEvent("event does not match the Reporting contract") from error

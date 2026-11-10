from datetime import datetime, timedelta
from typing import Annotated, Literal, Self, Union
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)


class InvalidNotificationEvent(ValueError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_timestamp_offsets(self) -> Self:
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if isinstance(value, datetime) and value.utcoffset() is None:
                raise ValueError("event timestamps must include a timezone offset")
        return self


class RegistrationData(StrictModel):
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
    refund_status: Literal["NONE", "PENDING", "APPROVED"] = Field(
        alias="refundStatus"
    )
    amount_minor: int = Field(alias="amountMinor", strict=True, ge=0)
    currency: str = Field(strict=True, pattern=r"^[A-Z]{3}$")
    activity_starts_at: datetime = Field(alias="activityStartsAt")
    activity_ends_at: datetime = Field(alias="activityEndsAt")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    offer_expires_at: datetime | None = Field(default=None, alias="offerExpiresAt")

    @model_validator(mode="after")
    def validate_deadlines(self) -> "RegistrationData":
        if self.status == "PENDING_PAYMENT" and self.expires_at is None:
            raise ValueError("pending payment registration requires expiresAt")
        if self.status == "OFFERED" and self.offer_expires_at is None:
            raise ValueError("offered registration requires offerExpiresAt")
        return self


class ActivityData(StrictModel):
    activity_id: UUID = Field(alias="activityId")
    event_id: UUID = Field(alias="eventId")
    organization_id: UUID = Field(alias="organizationId")
    status: Literal["DRAFT", "PUBLISHED", "CANCELLED"]
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime = Field(alias="endsAt")


class AttendanceData(StrictModel):
    registration_id: UUID = Field(alias="registrationId")
    activity_id: UUID = Field(alias="activityId")
    organization_id: UUID = Field(alias="organizationId")
    participant_id: UUID = Field(alias="participantId")
    checked_in_at: datetime = Field(alias="checkedInAt")


class CertificateData(StrictModel):
    certificate_id: UUID = Field(alias="certificateId")
    registration_id: UUID = Field(alias="registrationId")
    activity_id: UUID = Field(alias="activityId")
    organization_id: UUID = Field(alias="organizationId")
    participant_id: UUID = Field(alias="participantId")
    issued_at: datetime = Field(alias="issuedAt")


class NotificationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID = Field(alias="eventId")
    event_type: str = Field(alias="eventType")
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    aggregate_id: UUID = Field(alias="aggregateId")
    aggregate_version: int = Field(alias="aggregateVersion", strict=True, ge=1)
    correlation_id: UUID = Field(alias="correlationId")
    occurred_at: datetime = Field(alias="occurredAt")
    producer: str
    data: BaseModel

    @model_validator(mode="after")
    def validate_envelope(self) -> Self:
        if self.occurred_at.utcoffset() != timedelta(0):
            raise ValueError("event timestamp must use UTC")
        return self


class RegistrationChanged(NotificationEvent):
    event_type: Literal["registration.changed"] = Field(alias="eventType")
    producer: Literal["registration"]
    data: RegistrationData

    @model_validator(mode="after")
    def validate_aggregate(self) -> "RegistrationChanged":
        if self.aggregate_id != self.data.registration_id:
            raise ValueError("aggregateId must identify the registration")
        return self


class ActivityChanged(NotificationEvent):
    event_type: Literal["activity.changed"] = Field(alias="eventType")
    producer: Literal["event-management"]
    data: ActivityData

    @model_validator(mode="after")
    def validate_aggregate(self) -> "ActivityChanged":
        if self.aggregate_id != self.data.activity_id:
            raise ValueError("aggregateId must identify the activity")
        return self


class AttendanceRecorded(NotificationEvent):
    event_type: Literal["attendance.recorded"] = Field(alias="eventType")
    producer: Literal["attendance-certification"]
    data: AttendanceData

    @model_validator(mode="after")
    def validate_aggregate(self) -> "AttendanceRecorded":
        if self.aggregate_id != self.data.registration_id:
            raise ValueError("aggregateId must identify the registration")
        return self


class CertificateIssued(NotificationEvent):
    event_type: Literal["certificate.issued"] = Field(alias="eventType")
    producer: Literal["attendance-certification"]
    data: CertificateData

    @model_validator(mode="after")
    def validate_aggregate(self) -> "CertificateIssued":
        if self.aggregate_id != self.data.registration_id:
            raise ValueError("aggregateId must identify the registration")
        return self


NotificationPayload = Annotated[
    Union[
        RegistrationChanged,
        ActivityChanged,
        AttendanceRecorded,
        CertificateIssued,
    ],
    Field(discriminator="event_type"),
]
_NOTIFICATION_ADAPTER = TypeAdapter(NotificationPayload)


def parse_notification_event(
    message: bytes,
    *,
    transport_message_id: str | None = None,
    transport_correlation_id: str | None = None,
) -> NotificationPayload:
    try:
        event = _NOTIFICATION_ADAPTER.validate_json(message)
        if (
            transport_message_id is not None
            and UUID(transport_message_id) != event.event_id
        ):
            raise ValueError("transport message ID does not match eventId")
        if (
            transport_correlation_id is not None
            and UUID(transport_correlation_id) != event.correlation_id
        ):
            raise ValueError("transport correlation ID does not match correlationId")
        return event
    except (ValidationError, ValueError, UnicodeDecodeError):
        raise InvalidNotificationEvent(
            "message does not match a supported notification source contract"
        ) from None


def notice_type(event: NotificationPayload) -> str:
    if isinstance(event, RegistrationChanged):
        if event.data.refund_status != "NONE":
            return f"registration.refund_{event.data.refund_status.lower()}"
        return f"registration.{event.data.status.lower()}"
    if isinstance(event, ActivityChanged):
        return f"activity.{event.data.status.lower()}"
    if isinstance(event, AttendanceRecorded):
        return "attendance.recorded"
    if isinstance(event, CertificateIssued):
        return "certificate.issued"
    raise TypeError("unsupported notification source event")

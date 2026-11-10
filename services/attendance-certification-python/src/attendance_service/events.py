import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from attendance_service.errors import InvalidEvent


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    event_id: UUID = Field(alias="eventId")
    event_type: str = Field(alias="eventType")
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    aggregate_id: UUID = Field(alias="aggregateId")
    aggregate_version: int = Field(alias="aggregateVersion", ge=1)
    correlation_id: UUID = Field(alias="correlationId")
    occurred_at: datetime = Field(alias="occurredAt")
    producer: str = Field(min_length=1)

    @model_validator(mode="after")
    def occurred_at_has_timezone(self) -> "EventEnvelope":
        if self.occurred_at.tzinfo is None:
            raise ValueError("event timestamps must include a timezone")
        return self


class RegistrationData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

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
    amount_minor: int = Field(alias="amountMinor", ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    activity_starts_at: datetime = Field(alias="activityStartsAt")
    activity_ends_at: datetime = Field(alias="activityEndsAt")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    offer_expires_at: datetime | None = Field(default=None, alias="offerExpiresAt")

    @model_validator(mode="after")
    def validate_registration_snapshot(self) -> "RegistrationData":
        timestamps = (self.activity_starts_at, self.activity_ends_at)
        if any(value.tzinfo is None for value in timestamps):
            raise ValueError("activity timestamps must include a timezone")
        if self.status == "PENDING_PAYMENT" and self.expires_at is None:
            raise ValueError("pending payment snapshot must include expiresAt")
        if self.status == "OFFERED" and self.offer_expires_at is None:
            raise ValueError("offered snapshot must include offerExpiresAt")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expiry timestamp must include a timezone")
        if self.offer_expires_at is not None and self.offer_expires_at.tzinfo is None:
            raise ValueError("offer expiry timestamp must include a timezone")
        return self


class ActivityData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    activity_id: UUID = Field(alias="activityId")
    event_id: UUID = Field(alias="eventId")
    organization_id: UUID = Field(alias="organizationId")
    status: Literal["DRAFT", "PUBLISHED", "CANCELLED"]
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime = Field(alias="endsAt")

    @model_validator(mode="after")
    def timestamps_have_timezone(self) -> "ActivityData":
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("activity timestamps must include a timezone")
        return self


class RegistrationChanged(EventEnvelope):
    event_type: Literal["registration.changed"] = Field(alias="eventType")
    producer: Literal["registration"]
    data: RegistrationData

    @model_validator(mode="after")
    def aggregate_matches_registration(self) -> "RegistrationChanged":
        if self.aggregate_id != self.data.registration_id:
            raise ValueError("registration aggregate does not match the snapshot")
        return self


class ActivityChanged(EventEnvelope):
    event_type: Literal["activity.changed"] = Field(alias="eventType")
    producer: Literal["event-management"]
    data: ActivityData

    @model_validator(mode="after")
    def aggregate_matches_activity(self) -> "ActivityChanged":
        if self.aggregate_id != self.data.activity_id:
            raise ValueError("activity aggregate does not match the snapshot")
        return self


def parse_event(body: bytes) -> RegistrationChanged | ActivityChanged:
    try:
        envelope = json.loads(body)
        if not isinstance(envelope, dict):
            raise InvalidEvent("event envelope must be an object")
        event_type = envelope.get("eventType")
        if event_type == "registration.changed":
            return RegistrationChanged.model_validate(envelope)
        if event_type == "activity.changed":
            return ActivityChanged.model_validate(envelope)
        raise InvalidEvent("unsupported event type")
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
        raise InvalidEvent("event does not match the Attendance contract") from error

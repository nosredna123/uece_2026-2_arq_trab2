from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Money(ContractModel):
    amount_minor: int = Field(alias="amountMinor", ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class EventInput(ContractModel):
    organization_id: UUID = Field(alias="organizationId")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=10000)
    starts_at: AwareDatetime = Field(alias="startsAt")
    ends_at: AwareDatetime = Field(alias="endsAt")

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.starts_at >= self.ends_at:
            raise ValueError("Event start must precede its end.")
        return self


class Event(ContractModel):
    id: UUID
    organization_id: UUID = Field(alias="organizationId")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=10000)
    starts_at: AwareDatetime = Field(alias="startsAt")
    ends_at: AwareDatetime = Field(alias="endsAt")
    status: Literal["DRAFT", "PUBLISHED", "CANCELLED"]
    version: int = Field(ge=1)


class ActivityInput(ContractModel):
    title: str = Field(min_length=1, max_length=200)
    starts_at: AwareDatetime = Field(alias="startsAt")
    ends_at: AwareDatetime = Field(alias="endsAt")
    location: str = Field(min_length=1)
    responsible: str = Field(min_length=1)
    capacity: int = Field(ge=1)
    price: Money

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.starts_at >= self.ends_at:
            raise ValueError("Activity start must precede its end.")
        return self


class Activity(ContractModel):
    id: UUID
    event_id: UUID = Field(alias="eventId")
    organization_id: UUID = Field(alias="organizationId")
    title: str = Field(min_length=1, max_length=200)
    starts_at: AwareDatetime = Field(alias="startsAt")
    ends_at: AwareDatetime = Field(alias="endsAt")
    location: str = Field(min_length=1)
    responsible: str = Field(min_length=1)
    capacity: int = Field(ge=1)
    price: Money
    version: int = Field(ge=1)
    capacity_frozen: bool = Field(alias="capacityFrozen")


class Registration(ContractModel):
    id: UUID
    activity_id: UUID = Field(alias="activityId")
    participant_id: UUID = Field(alias="participantId")
    organization_id: UUID = Field(alias="organizationId")
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
    created_at: AwareDatetime = Field(alias="createdAt")
    version: int = Field(ge=1)
    expires_at: AwareDatetime | None = Field(default=None, alias="expiresAt")
    offer_expires_at: AwareDatetime | None = Field(default=None, alias="offerExpiresAt")
    waitlist_sequence: int | None = Field(default=None, alias="waitlistSequence", ge=1)
    price: Money | None = None


class Availability(ContractModel):
    activity_id: UUID = Field(alias="activityId")
    capacity: int = Field(ge=1)
    occupied: int = Field(ge=0)
    available: int = Field(ge=0)
    as_of: AwareDatetime = Field(alias="asOf")


class CheckIn(ContractModel):
    registration_id: UUID = Field(alias="registrationId")
    participant_id: UUID = Field(alias="participantId")
    checked_in_at: AwareDatetime = Field(alias="checkedInAt")


class Certificate(ContractModel):
    id: UUID
    registration_id: UUID = Field(alias="registrationId")
    participant_id: UUID = Field(alias="participantId")
    activity_id: UUID = Field(alias="activityId")
    issued_at: AwareDatetime = Field(alias="issuedAt")


class Report(ContractModel):
    organization_id: UUID = Field(alias="organizationId")
    activity_id: UUID = Field(alias="activityId")
    confirmed: int = Field(ge=0)
    pending_payment: int = Field(alias="pendingPayment", ge=0)
    offered: int = Field(ge=0)
    waiting: int = Field(ge=0)
    occupied: int = Field(ge=0)
    checked_in: int = Field(alias="checkedIn", ge=0)
    certificates: int = Field(ge=0)
    payments_approved: int = Field(alias="paymentsApproved", ge=0)
    payments_refused: int = Field(alias="paymentsRefused", ge=0)
    refunds_approved: int = Field(alias="refundsApproved", ge=0)
    projected_at: AwareDatetime = Field(alias="projectedAt")


class ErrorResponse(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    correlation_id: UUID = Field(alias="correlationId")

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates

from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.models import Money


@dataclass(frozen=True)
class BadgeDefinition:
    icon: str
    label: str
    description: str


@dataclass(frozen=True)
class PageMetadata:
    title: str
    eyebrow: str
    heading: str
    description: str
    implementation_status: str


@dataclass(frozen=True)
class ServiceFailure:
    service: str
    status_code: int | None
    code: str
    correlation_id: str


BADGE_DEFINITIONS = {
    "live_service": BadgeDefinition(
        icon="●",
        label="LIVE SERVICE",
        description="Observed from an existing EventHub service API.",
    ),
    "simulated_dependency": BadgeDefinition(
        icon="◐",
        label="SIMULATED DEPENDENCY",
        description="Behavior supplied by a simulator or mock dependency.",
    ),
    "demo_presentation": BadgeDefinition(
        icon="✦",
        label="DEMO PRESENTATION",
        description="Presentation-only guidance or synthetic browser state.",
    ),
}

PAGE_METADATA = {
    "home": PageMetadata(
        title="EventHub demonstration",
        eyebrow="A guided view of the EventHub prototype",
        heading="See the journey, understand the boundaries",
        description=(
            "Explore how the presentation client distinguishes service evidence, "
            "simulated dependencies, and demo-only guidance."
        ),
        implementation_status="Catalog and shell available",
    ),
    "events": PageMetadata(
        title="Published events",
        eyebrow="Explore the published program",
        heading="Find an event",
        description=(
            "Browse the public Event Management catalog. Availability is observed "
            "separately and never reserves a place."
        ),
        implementation_status="Catalog and shell available",
    ),
    "event_detail": PageMetadata(
        title="Event details",
        eyebrow="Published event and activity program",
        heading="Event details",
        description=(
            "Schedules and prices come from Event Management. Capacity is a "
            "time-stamped observation from Registration."
        ),
        implementation_status="Catalog and shell available",
    ),
    "checkout": PageMetadata(
        title="Confirm registration",
        eyebrow="One explicit service command",
        heading="Review your registration",
        description=(
            "The activity details come from Event Management. Registration is "
            "created only when you confirm below."
        ),
        implementation_status="Catalog and checkout available",
    ),
    "registrations": PageMetadata(
        title="My registrations",
        eyebrow="Your participant journey",
        heading="My registrations",
        description=(
            "Current states come from Registration. A list is scoped to the selected "
            "synthetic participant."
        ),
        implementation_status="Registration status available",
    ),
    "registration_detail": PageMetadata(
        title="Registration status",
        eyebrow="Live registration record",
        heading="Registration status",
        description=(
            "This page shows the last observed backend state. Refresh is bounded and can be paused."
        ),
        implementation_status="Registration status available",
    ),
    "organizer": PageMetadata(
        title="Organizer setup",
        eyebrow="Create a small synthetic program",
        heading="Organizer workspace",
        description=(
            "Create, extend, and publish event records through Event Management. "
            "Only references created in this browser session appear below."
        ),
        implementation_status="Organizer setup available",
    ),
    "certificate": PageMetadata(
        title="Certificate record",
        eyebrow="Attendance record and printable presentation",
        heading="Certificate of participation",
        description=(
            "The certificate identity and issue time come from Attendance. "
            "The printable layout is a presentation, not a signed credential."
        ),
        implementation_status="Certificate view available",
    ),
    "demo": PageMetadata(
        title="Guided event demonstration",
        eyebrow="Prepare synthetic service-owned records",
        heading="Follow the registration journeys",
        description=(
            "Prepare a paid and a one-seat free activity through Event Management, "
            "then follow the participant journeys using actual service responses."
        ),
        implementation_status="Guided preparation available",
    ),
    "error": PageMetadata(
        title="Request not completed",
        eyebrow="The prototype could not complete this page",
        heading="This request needs attention",
        description="The status below is the result returned by the web application.",
        implementation_status="Catalog and shell available",
    ),
}

PERSONA_LABELS = (
    ("visitor", "Visitor"),
    ("participant", "Participant 1"),
    ("participantTwo", "Participant 2"),
    ("organizer", "Organizer"),
)

TEMPLATE_DIRECTORY = Path(__file__).parent / "templates"
STATIC_DIRECTORY = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATE_DIRECTORY))
templates.env.globals["badge_definitions"] = BADGE_DEFINITIONS


def format_money(money: Money) -> str:
    if money.amount_minor == 0:
        return "Free"
    whole, minor = divmod(money.amount_minor, 100)
    return f"{money.currency} {whole:,}.{minor:02d}"


def format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%b %d, %Y %H:%M UTC")


def format_observation_age(
    as_of: datetime,
    *,
    now: datetime | None = None,
) -> tuple[str, bool]:
    current_time = now or datetime.now(timezone.utc)
    age_seconds = (current_time - as_of).total_seconds()
    if age_seconds < 0:
        return "timestamp is ahead of this browser clock", True
    age_seconds = int(age_seconds)
    if age_seconds < 60:
        return "less than one minute ago", False
    if age_seconds < 3600:
        minutes = age_seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago", True
    if age_seconds < 86400:
        hours = age_seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago", True
    days = age_seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago", True


def service_failure(
    error: ServiceError | TransportError | InvalidResponseError,
) -> ServiceFailure:
    return ServiceFailure(
        service=error.service,
        status_code=error.status_code,
        code=error.code,
        correlation_id=str(error.correlation_id),
    )

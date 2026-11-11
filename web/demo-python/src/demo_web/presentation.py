from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates

from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.models import Money


@dataclass(frozen=True)
class PageMetadata:
    title: str
    eyebrow: str
    heading: str
    description: str


@dataclass(frozen=True)
class ServiceFailure:
    service: str
    status_code: int | None
    code: str
    correlation_id: str


PAGE_METADATA = {
    "home": PageMetadata(
        title="Sign in",
        eyebrow="Simulated sign-in",
        heading="Who is using EventHub?",
        description=(
            "Choose a synthetic user. There is no password: these fictional identities "
            "only select which demo credentials the app sends to the services."
        ),
    ),
    "events": PageMetadata(
        title="Events",
        eyebrow="Participant",
        heading="Find an event",
        description="Browse published events and register for their activities.",
    ),
    "event_detail": PageMetadata(
        title="Event details",
        eyebrow="Participant",
        heading="Event details",
        description="Choose an activity and register. Seats shown are the latest availability.",
    ),
    "checkout": PageMetadata(
        title="Confirm registration",
        eyebrow="Participant",
        heading="Confirm your registration",
        description="Your registration is only created when you confirm below.",
    ),
    "registrations": PageMetadata(
        title="My registrations",
        eyebrow="Participant",
        heading="My registrations",
        description="Follow your registrations, check in and get your certificates.",
    ),
    "registration_detail": PageMetadata(
        title="Registration",
        eyebrow="Participant",
        heading="Registration",
        description="Current status of this registration. The page refreshes while it changes.",
    ),
    "organizer": PageMetadata(
        title="My events",
        eyebrow="Organizer",
        heading="My events",
        description="Create, edit, publish or cancel your organization's events and activities.",
    ),
    "organizer_new_event": PageMetadata(
        title="New event",
        eyebrow="Organizer",
        heading="New event",
        description="The event is created as a draft. Add activities and publish it next.",
    ),
    "organizer_event": PageMetadata(
        title="Manage event",
        eyebrow="Organizer",
        heading="Manage event",
        description="Edit the event and its activities. Times are in UTC.",
    ),
    "certificate": PageMetadata(
        title="Certificate",
        eyebrow="Participant",
        heading="Certificate of participation",
        description="A printable certificate for an attended activity.",
    ),
    "reports": PageMetadata(
        title="Reports",
        eyebrow="Organizer",
        heading="Reports",
        description=(
            "Registrations, check-ins and certificates of your published events. "
            "The page refreshes automatically."
        ),
    ),
    "error": PageMetadata(
        title="Request not completed",
        eyebrow="The prototype could not complete this page",
        heading="This request needs attention",
        description="The status below is the result returned by the web application.",
    ),
}

PERSONA_LABELS = (
    ("participant", "Participante simulado 01"),
    ("participantTwo", "Participante simulado 02"),
    ("organizer", "Organizador — GESAD/UECE"),
    ("organizerUece", "Organizador — UECE"),
    ("organizerAcademicSim", "Organizador — IASC (simulada)"),
)
ORGANIZER_PERSONAS = {
    "organizer": "GESAD/UECE",
    "organizerUece": "UECE",
    "organizerAcademicSim": "IASC",
}
SIGN_IN_GROUPS = (
    (
        "Organizers",
        (
            ("organizer", "GESAD/UECE", "Grupo de pesquisa GESAD — UECE"),
            ("organizerUece", "UECE", "Universidade Estadual do Ceará"),
            (
                "organizerAcademicSim",
                "IASC (simulada)",
                "Instituto Acadêmico Simulado do Ceará — fictício",
            ),
        ),
    ),
    (
        "Participants",
        (
            ("participant", "Participante simulado 01", "Synthetic participant"),
            ("participantTwo", "Participante simulado 02", "Synthetic participant"),
        ),
    ),
)

TEMPLATE_DIRECTORY = Path(__file__).parent / "templates"
STATIC_DIRECTORY = Path(__file__).parent / "static"


def _signed_in_user(request) -> dict:
    store = getattr(request.app.state, "sessions", None)
    if store is None:
        return {}
    session = store.get(request.cookies.get("demo_session"))
    if session is None or not session.signed_in:
        return {}
    return {
        "signed_in_user": {
            "label": dict(PERSONA_LABELS)[session.persona],
            "is_organizer": session.persona in ORGANIZER_PERSONAS,
            "csrf_token": session.csrf_token,
        }
    }


templates = Jinja2Templates(
    directory=str(TEMPLATE_DIRECTORY), context_processors=[_signed_in_user]
)


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

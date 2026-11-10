from dataclasses import dataclass
from pathlib import Path

from fastapi.templating import Jinja2Templates


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
        implementation_status="Prototype shell",
    ),
    "error": PageMetadata(
        title="Request not completed",
        eyebrow="The prototype could not complete this page",
        heading="This request needs attention",
        description="The status below is the result returned by the web application.",
        implementation_status="Prototype shell",
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

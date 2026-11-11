import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping

from demo_web.demo import DemoPreparation
from demo_web.models import Activity, CheckIn, Event, Report


PERSONAS = frozenset(
    {
        "visitor",
        "participant",
        "participantTwo",
        "organizer",
        "organizerUece",
        "organizerAcademicSim",
    }
)


class InvalidPersonaError(ValueError):
    """Raised when a requested persona is not part of the demo fixture set."""


@dataclass
class SessionEventDraft:
    event: Event
    activities: OrderedDict[str, Activity] = field(default_factory=OrderedDict)


@dataclass(frozen=True)
class ReportObservation:
    reports: tuple[Report, ...]
    observed_at: datetime


@dataclass
class BrowserSession:
    id: str
    csrf_token: str
    created_at: float
    last_used_at: float
    persona: str = "visitor"
    persona_version: int = 0
    commands: OrderedDict[str, object] = field(default_factory=OrderedDict)
    drafts: OrderedDict[str, SessionEventDraft] = field(default_factory=OrderedDict)
    attendance_flashes: OrderedDict[str, CheckIn] = field(default_factory=OrderedDict)
    demo_runs: OrderedDict[str, DemoPreparation] = field(default_factory=OrderedDict)
    demo_intents: OrderedDict[str, str] = field(default_factory=OrderedDict)
    demo_start_key: str | None = None
    report_observations: OrderedDict[str, ReportObservation] = field(default_factory=OrderedDict)
    lock: threading.RLock = field(default_factory=threading.RLock)


class SessionStore:
    def __init__(
        self,
        identity_fixtures: Mapping[str, Mapping[str, str]],
        *,
        ttl_seconds: int,
        max_sessions: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._identity_fixtures = {
            name: dict(fixture) for name, fixture in identity_fixtures.items()
        }
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        self._clock = clock
        self._sessions: OrderedDict[str, BrowserSession] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def create(self) -> BrowserSession:
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            while len(self._sessions) >= self._max_sessions:
                self._sessions.popitem(last=False)
            session = BrowserSession(
                id=secrets.token_urlsafe(32),
                csrf_token=secrets.token_urlsafe(32),
                created_at=now,
                last_used_at=now,
            )
            self._sessions[session.id] = session
            return session

    def get(self, session_id: str | None) -> BrowserSession | None:
        if not session_id:
            return None
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            session = self._sessions.get(session_id)
            if session is None:
                return None
            session.last_used_at = now
            self._sessions.move_to_end(session_id)
            return session

    def is_active(self, session_id: str) -> bool:
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            return session_id in self._sessions

    def identity_for(self, session: BrowserSession) -> Mapping[str, str] | None:
        if session.persona == "visitor":
            return None
        fixture_name = {
            "participant": "participant",
            "participantTwo": "participantTwo",
            "organizer": "organizer",
            "organizerUece": "organizerUece",
            "organizerAcademicSim": "organizerAcademicSim",
        }[session.persona]
        return self._identity_fixtures[fixture_name]

    def set_persona(self, session: BrowserSession, persona: str) -> None:
        if persona not in PERSONAS:
            raise InvalidPersonaError("The selected persona is not available.")
        with session.lock:
            if session.persona == persona:
                return
            session.persona = persona
            session.persona_version += 1
            session.csrf_token = secrets.token_urlsafe(32)
            session.attendance_flashes.clear()
            session.report_observations.clear()

    def _remove_expired(self, now: float) -> None:
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_used_at >= self._ttl_seconds
        ]
        for session_id in expired:
            del self._sessions[session_id]

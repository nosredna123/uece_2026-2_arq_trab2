import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from demo_web.errors import ServiceError
from demo_web.models import Activity, Certificate, CheckIn, Event, Registration
from demo_web.seed import (
    SeedApplier,
    SeedDataError,
    load_seed_file,
    parse_seed_data,
    run_seed,
    seed_key,
)
from demo_web.settings import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SEED_FILE = REPOSITORY_ROOT / "web" / "demo-python" / "seed-data.json"
REAL_FIXTURES = (
    REPOSITORY_ROOT / "mocks" / "identity-access" / "src" / "identity_access_mock" / "fixtures.json"
)
START = datetime(2026, 11, 11, 14, 3, 27, tzinfo=timezone.utc)


def _fixtures(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _error(status_code, code):
    return ServiceError(
        service="fake",
        method="POST",
        status_code=status_code,
        code=code,
        message=code,
        correlation_id=uuid4(),
    )


def _minimal(**registration):
    return {
        "organizations": [
            {
                "organizer": "organizer",
                "label": "GESAD/UECE",
                "events": [
                    {
                        "title": "Seminário",
                        "description": "Evento fictício.",
                        "activities": [
                            {
                                "title": "Palestra",
                                "startsInMinutes": 2,
                                "durationMinutes": 2,
                                "location": "Auditório",
                                "responsible": "Equipe simulada",
                                "capacity": 1,
                                "priceMinor": 0,
                                "registrations": [{"participant": "participant", **registration}],
                            }
                        ],
                    }
                ],
            }
        ]
    }


class Clock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class FakeServices:
    """In-memory services with durable idempotency receipts and real time rules."""

    def __init__(self, fixtures, clock):
        self.fixtures = fixtures
        self.clock = clock
        self.subjects = {item["token"]: item for item in fixtures.values()}
        self.receipts = {}
        self.events = {}
        self.activities = {}
        self.registrations = {}
        self.check_ins = {}
        self.certificates = {}
        self.pending_polls = {}

    def _replay(self, token, operation, key, create):
        receipt = (token, operation, key)
        if receipt not in self.receipts:
            self.receipts[receipt] = create()
        return copy.deepcopy(self.receipts[receipt])

    async def list_events(self, *, limit, offset, search=None, **_):
        published = [
            event
            for event in self.events.values()
            if event.status == "PUBLISHED" and (search is None or search in event.title.lower())
        ]
        return published[offset : offset + limit]

    async def create_event(self, event, *, token, idempotency_key, **_):
        def create():
            created = Event(id=uuid4(), status="DRAFT", version=1, **event.model_dump(by_alias=True))
            self.events[created.id] = created
            return created

        return self._replay(token, "create-event", idempotency_key, create)

    async def create_activity(self, event_id, activity, *, token, idempotency_key, **_):
        def create():
            created = Activity(
                id=uuid4(),
                eventId=event_id,
                organizationId=self.events[event_id].organization_id,
                version=1,
                capacityFrozen=False,
                **activity.model_dump(by_alias=True),
            )
            self.activities[created.id] = created
            return created

        return self._replay(token, f"create-activity:{event_id}", idempotency_key, create)

    async def publish_event(self, event_id, *, token, idempotency_key, **_):
        def publish():
            assert any(item.event_id == event_id for item in self.activities.values())
            self.events[event_id] = self.events[event_id].model_copy(update={"status": "PUBLISHED"})
            return self.events[event_id]

        return self._replay(token, "publish", idempotency_key, publish)

    async def request_registration(self, activity_id, *, token, idempotency_key, **_):
        receipt = (token, "register", idempotency_key)
        if receipt in self.receipts:
            return copy.deepcopy(self.receipts[receipt])
        activity = self.activities[activity_id]
        if activity.starts_at <= self.clock():
            raise _error(422, "ACTIVITY_INELIGIBLE")
        subject = self.subjects[token]["subjectId"]
        if any(
            str(item.participant_id) == subject
            and item.activity_id == activity_id
            and item.status not in {"CANCELLED", "EXPIRED", "REFUSED"}
            for item in self.registrations.values()
        ):
            raise _error(409, "ALREADY_REGISTERED")
        occupied = sum(
            1
            for item in self.registrations.values()
            if item.activity_id == activity_id and item.status in {"CONFIRMED", "PENDING_PAYMENT"}
        )
        if occupied >= activity.capacity:
            status = "WAITLISTED"
        elif activity.price.amount_minor:
            status = "PENDING_PAYMENT"
        else:
            status = "CONFIRMED"
        created = Registration(
            id=uuid4(),
            activityId=activity_id,
            participantId=subject,
            organizationId=activity.organization_id,
            status=status,
            refundStatus="NONE",
            createdAt=self.clock(),
            version=1,
        )
        self.registrations[created.id] = created
        self.pending_polls[created.id] = 1
        self.receipts[receipt] = created
        return copy.deepcopy(created)

    async def get_registration(self, registration_id, *, token, **_):
        registration = self.registrations[registration_id]
        if registration.status == "PENDING_PAYMENT":
            if self.pending_polls[registration_id] <= 0:
                registration = registration.model_copy(update={"status": "CONFIRMED"})
                self.registrations[registration_id] = registration
            self.pending_polls[registration_id] -= 1
        return registration

    async def list_registrations(self, *, token, limit, offset, **_):
        subject = self.subjects[token]["subjectId"]
        owned = [item for item in self.registrations.values() if str(item.participant_id) == subject]
        return owned[offset : offset + limit]

    async def cancel_registration(self, registration_id, *, token, idempotency_key, **_):
        def cancel():
            self.registrations[registration_id] = self.registrations[registration_id].model_copy(
                update={"status": "CANCELLED"}
            )
            return self.registrations[registration_id]

        return self._replay(token, "cancel", idempotency_key, cancel)

    async def check_in(self, registration_id, *, token, idempotency_key, **_):
        registration = self.registrations[registration_id]
        if registration.status != "CONFIRMED":
            raise _error(422, "REGISTRATION_NOT_CONFIRMED")
        self.check_ins.setdefault(
            registration_id,
            CheckIn(
                registrationId=registration_id,
                participantId=registration.participant_id,
                checkedInAt=self.clock(),
            ),
        )
        return self.check_ins[registration_id]

    async def issue_certificate(self, registration_id, *, token, idempotency_key, **_):
        registration = self.registrations[registration_id]
        if self.activities[registration.activity_id].ends_at > self.clock():
            raise _error(422, "ACTIVITY_NOT_ENDED")
        self.certificates.setdefault(
            registration_id,
            Certificate(
                id=uuid4(),
                registrationId=registration_id,
                participantId=registration.participant_id,
                activityId=registration.activity_id,
                issuedAt=self.clock(),
            ),
        )
        return self.certificates[registration_id]

    async def aclose(self):
        return None

    def counts(self):
        return (
            len(self.events),
            len(self.activities),
            len(self.registrations),
            len(self.check_ins),
            len(self.certificates),
        )


def _apply(services, organizations):
    applier = SeedApplier(
        services, services.fixtures, now=services.clock, sleep=services.clock.sleep
    )
    return asyncio.run(applier.apply(organizations))


def test_repository_seed_file_matches_real_identity_fixtures():
    organizations = load_seed_file(SEED_FILE, _fixtures(REAL_FIXTURES))

    assert [item.label for item in organizations] == ["GESAD/UECE", "UECE", "IASC"]
    titles = [event.title for org in organizations for event in org.events]
    assert all(title.startswith("[DADOS INICIAIS][SIMULADO][") for title in titles)
    assert any(not event.publish for org in organizations for event in org.events)
    registrations = [
        registration
        for org in organizations
        for event in org.events
        for activity in event.activities
        for registration in activity.registrations
    ]
    assert any(item.certificate for item in registrations)
    assert any(item.cancel for item in registrations)


def test_seed_covers_every_entity_and_state(identity_fixtures):
    fixtures = _fixtures(identity_fixtures)
    services = FakeServices(fixtures, Clock())

    summary = _apply(services, load_seed_file(SEED_FILE, fixtures))

    assert summary.warnings == []
    assert summary.anchor == START.replace(second=0)
    assert services.counts() == (7, 10, 13, 6, 4)
    statuses = [item.status for item in services.registrations.values()]
    assert statuses.count("WAITLISTED") == 1
    assert statuses.count("CANCELLED") == 1
    assert statuses.count("CONFIRMED") == 11
    assert [event.title for event in services.events.values() if event.status == "DRAFT"] == [
        "[DADOS INICIAIS][SIMULADO][IASC] Jornada Simulada de Extensão (rascunho)"
    ]
    for event in services.events.values():
        assert event.description.endswith("Lote de dados iniciais: 2026-11-11T14:03:00Z")


def test_seed_is_applied_once_across_restarts(identity_fixtures):
    fixtures = _fixtures(identity_fixtures)
    organizations = load_seed_file(SEED_FILE, fixtures)
    services = FakeServices(fixtures, Clock())
    first = _apply(services, organizations)
    counts = services.counts()

    services.clock.now += timedelta(days=3, minutes=17)
    second = _apply(services, organizations)

    assert services.counts() == counts
    assert second.anchor == first.anchor
    assert second.warnings == []
    assert second.cancellations == first.cancellations == 1


def test_reset_database_receives_exactly_one_new_batch(identity_fixtures):
    fixtures = _fixtures(identity_fixtures)
    organizations = load_seed_file(SEED_FILE, fixtures)
    _apply(FakeServices(fixtures, Clock()), organizations)

    fresh = FakeServices(fixtures, Clock(START + timedelta(days=1)))
    summary = _apply(fresh, organizations)
    _apply(fresh, organizations)

    assert summary.anchor == (START + timedelta(days=1)).replace(second=0)
    assert fresh.counts() == (7, 10, 13, 6, 4)


def test_seed_reuses_registration_created_outside_the_seed(identity_fixtures):
    fixtures = _fixtures(identity_fixtures)
    organizations = parse_seed_data(_minimal(), fixtures)
    services = FakeServices(fixtures, Clock())
    original = services.request_registration

    async def register_manually_first(activity_id, *, token, idempotency_key, **kwargs):
        if not services.registrations:
            await original(activity_id, token=token, idempotency_key="manual-ui-key")
        return await original(activity_id, token=token, idempotency_key=idempotency_key)

    services.request_registration = register_manually_first

    summary = _apply(services, organizations)

    assert summary.registrations == 1
    assert len(services.registrations) == 1


def test_new_activity_added_after_start_window_is_skipped_with_warning(identity_fixtures):
    fixtures = _fixtures(identity_fixtures)
    services = FakeServices(fixtures, Clock())
    _apply(services, parse_seed_data(_minimal(), fixtures))

    edited = _minimal()
    edited["organizations"][0]["events"][0]["activities"].append(
        {
            "title": "Nova palestra",
            "startsInMinutes": 5,
            "durationMinutes": 2,
            "location": "Auditório",
            "responsible": "Equipe simulada",
            "capacity": 5,
            "priceMinor": 0,
            "registrations": [{"participant": "participantTwo"}],
        }
    )
    services.clock.now += timedelta(hours=1)

    summary = _apply(services, parse_seed_data(edited, fixtures))

    assert "already started" in summary.warnings[0]
    assert len(services.registrations) == 1


def test_seed_keys_are_deterministic_and_bounded():
    assert seed_key("event", {"a": 1, "b": 2}) == seed_key("event", {"b": 2, "a": 1})
    assert seed_key("event", "x") != seed_key("event", "y")
    assert len(seed_key("event", "x" * 1000)) <= 128


def _activity(data):
    return data["organizations"][0]["events"][0]["activities"][0]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data["organizations"][0].update(organizer="participant"), "ORGANIZER"),
        (lambda data: data["organizations"][0].update(unknown=True), "unknown fields"),
        (lambda data: _activity(data).update(startsInDays=3), "either startsInMinutes"),
        (
            lambda data: (
                _activity(data).pop("startsInMinutes"),
                _activity(data).update(startsInDays=3),
            ),
            "both startsInDays and startTime",
        ),
        (
            lambda data: (
                _activity(data).pop("startsInMinutes"),
                _activity(data).update(startsInDays=3, startTime="9h"),
            ),
            "HH:MM",
        ),
        (lambda data: _activity(data).update(startsInMinutes=1), "at least 2"),
        (
            lambda data: (
                _activity(data).pop("startsInMinutes"),
                _activity(data).update(startsInDays=3, startTime="09:00"),
                _activity(data)["registrations"][0].update(checkIn=True),
            ),
            "checkIn requires an activity scheduled with startsInMinutes",
        ),
        (
            lambda data: (
                _activity(data).update(durationMinutes=60),
                _activity(data)["registrations"][0].update(checkIn=True, certificate=True),
            ),
            "within 30 minutes",
        ),
        (
            lambda data: _activity(data)["registrations"][0].update(participant="organizer"),
            "PARTICIPANT",
        ),
        (lambda data: _activity(data)["registrations"][0].update(certificate=True), "requires checkIn"),
        (
            lambda data: _activity(data)["registrations"][0].update(cancel=True, checkIn=True),
            "cannot combine",
        ),
        (lambda data: data["organizations"][0]["events"][0].update(publish=False), "not published"),
        (lambda data: _activity(data).update(priceMinor="10"), "priceMinor"),
        (lambda data: data["organizations"][0]["events"][0].update(activities=[]), "at least one"),
        (lambda data: _activity(data).update(title="x" * 200), "invalid"),
    ],
)
def test_invalid_seed_data_is_rejected(identity_fixtures, mutate, message):
    data = _minimal()
    mutate(data)

    with pytest.raises(SeedDataError, match=message):
        parse_seed_data(data, _fixtures(identity_fixtures))


def test_comment_fields_are_ignored(identity_fixtures):
    data = _minimal()
    data["_leia-me"] = ["comentário"]
    data["organizations"][0]["_nota"] = "ignorado"

    assert parse_seed_data(data, _fixtures(identity_fixtures))[0].label == "GESAD/UECE"


def test_settings_seed_path_is_optional(web_environment, tmp_path):
    assert Settings(web_environment).seed_data_path is None
    configured = {**web_environment, "DEMO_WEB_SEED_DATA_PATH": str(tmp_path / "seed.json")}
    assert Settings(configured).seed_data_path == tmp_path / "seed.json"


def test_run_seed_logs_and_skips_invalid_file(web_environment, identity_fixtures, tmp_path):
    seed_file = tmp_path / "seed.json"
    seed_file.write_text("{not json", encoding="utf-8")
    settings = Settings({**web_environment, "DEMO_WEB_SEED_DATA_PATH": str(seed_file)})

    assert asyncio.run(run_seed(settings, _fixtures(identity_fixtures))) is None


def test_run_seed_retries_transient_failures(web_environment, identity_fixtures, tmp_path):
    fixtures = _fixtures(identity_fixtures)
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(json.dumps(_minimal()), encoding="utf-8")
    settings = Settings({**web_environment, "DEMO_WEB_SEED_DATA_PATH": str(seed_file)})
    services = FakeServices(fixtures, Clock(datetime.now(timezone.utc)))
    attempts = []
    original = services.create_event

    async def flaky_create_event(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise _error(503, "UNAVAILABLE")
        return await original(*args, **kwargs)

    services.create_event = flaky_create_event

    async def no_sleep(_seconds):
        return None

    summary = asyncio.run(
        run_seed(settings, fixtures, clients_factory=lambda _settings: services, sleep=no_sleep)
    )

    assert summary is not None and summary.registrations == 1
    assert len(attempts) == 2
    assert len(services.events) == 1

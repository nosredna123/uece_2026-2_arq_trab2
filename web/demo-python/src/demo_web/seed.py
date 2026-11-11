"""Initial presentation data loaded once from an editable JSON file.

Activity times are relative to a seed anchor: the moment the data was first
applied. The anchor is written in each seeded event description, so later
startups recover it from the published catalog, rebuild byte-identical
requests, and replay the services' durable idempotency receipts instead of
creating duplicates. An empty database yields a fresh anchor and one new batch.
"""

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from demo_web.clients import DemoClients
from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.models import ActivityInput, EventInput, Money, Registration

logger = logging.getLogger("uvicorn.error")

TITLE_PREFIX = "[DADOS INICIAIS][SIMULADO][{label}] "
SEARCH_MARKER = "[dados iniciais]"
ANCHOR_LINE = "Lote de dados iniciais: {anchor}"
_ANCHOR_PATTERN = re.compile(r"Lote de dados iniciais: (\S+)")
LOCAL_TIMEZONE = timezone(timedelta(hours=-3), "America/Fortaleza")
MAX_SEED_BYTES = 262_144
MAX_CERTIFICATE_WAIT_MINUTES = 30
_EVENT_KEYS = {"title", "description", "publish", "activities"}
_ACTIVITY_KEYS = {
    "title",
    "startsInMinutes",
    "startsInDays",
    "startTime",
    "durationMinutes",
    "location",
    "responsible",
    "capacity",
    "priceMinor",
    "registrations",
}
_REGISTRATION_KEYS = {"participant", "checkIn", "certificate", "cancel"}
_PLACEHOLDER_ANCHOR = datetime(2026, 1, 1, tzinfo=timezone.utc)


class SeedDataError(ValueError):
    """Raised when the seed file is malformed."""


@dataclass(frozen=True)
class SeedRegistration:
    participant: str
    check_in: bool = False
    certificate: bool = False
    cancel: bool = False


@dataclass(frozen=True)
class SeedActivity:
    title: str
    starts_in_minutes: int | None
    starts_in_days: int | None
    start_time: time | None
    duration_minutes: int
    location: str
    responsible: str
    capacity: int
    price_minor: int
    registrations: tuple[SeedRegistration, ...]

    def payload(self, anchor: datetime) -> ActivityInput:
        if self.starts_in_minutes is not None:
            starts_at = anchor + timedelta(minutes=self.starts_in_minutes)
        else:
            local_day = anchor.astimezone(LOCAL_TIMEZONE).date() + timedelta(
                days=self.starts_in_days
            )
            starts_at = datetime.combine(local_day, self.start_time, LOCAL_TIMEZONE)
        return ActivityInput(
            title=self.title,
            startsAt=starts_at,
            endsAt=starts_at + timedelta(minutes=self.duration_minutes),
            location=self.location,
            responsible=self.responsible,
            capacity=self.capacity,
            price=Money(amountMinor=self.price_minor, currency="BRL"),
        )


@dataclass(frozen=True)
class SeedEvent:
    title: str
    description: str
    publish: bool
    activities: tuple[SeedActivity, ...]

    def payload(self, organization_id: str, anchor: datetime) -> EventInput:
        activities = [activity.payload(anchor) for activity in self.activities]
        return EventInput(
            organizationId=organization_id,
            title=self.title,
            description=f"{self.description}\n\n{ANCHOR_LINE.format(anchor=_iso(anchor))}",
            startsAt=min(activity.starts_at for activity in activities),
            endsAt=max(activity.ends_at for activity in activities),
        )


@dataclass(frozen=True)
class SeedOrganization:
    organizer: str
    label: str
    events: tuple[SeedEvent, ...]


@dataclass
class SeedSummary:
    anchor: datetime | None = None
    events: int = 0
    activities: int = 0
    registrations: int = 0
    check_ins: int = 0
    certificates: int = 0
    cancellations: int = 0
    warnings: list[str] = field(default_factory=list)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _visible(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if not key.startswith("_")}


def _require_object(value: Any, where: str, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(value, dict):
        raise SeedDataError(f"{where} must be a JSON object.")
    visible = _visible(value)
    unknown = set(visible) - allowed
    if unknown:
        raise SeedDataError(f"{where} has unknown fields: {', '.join(sorted(unknown))}.")
    missing = required - set(visible)
    if missing:
        raise SeedDataError(f"{where} is missing fields: {', '.join(sorted(missing))}.")
    return visible


def _require_list(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise SeedDataError(f"{where} must be a JSON array.")
    return value


def _require_bool(entry: Mapping[str, Any], key: str, where: str, default: bool) -> bool:
    value = entry.get(key, default)
    if not isinstance(value, bool):
        raise SeedDataError(f"{where}.{key} must be true or false.")
    return value


def _require_int(entry: Mapping[str, Any], key: str, where: str, minimum: int) -> int:
    value = entry[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SeedDataError(f"{where}.{key} must be an integer of at least {minimum}.")
    return value


def _require_text(entry: Mapping[str, Any], key: str, where: str) -> str:
    value = entry[key]
    if not isinstance(value, str) or not value.strip():
        raise SeedDataError(f"{where}.{key} must be a non-empty string.")
    return value.strip()


def parse_seed_data(
    raw: Any, fixtures: Mapping[str, Mapping[str, Any]]
) -> tuple[SeedOrganization, ...]:
    root = _require_object(raw, "seed file", {"organizations"}, {"organizations"})
    organizations: list[SeedOrganization] = []
    for org_index, org_raw in enumerate(_require_list(root["organizations"], "organizations")):
        where = f"organizations[{org_index}]"
        org = _require_object(
            org_raw, where, {"organizer", "label", "events"}, {"organizer", "label", "events"}
        )
        organizer = org["organizer"]
        fixture = fixtures.get(organizer) if isinstance(organizer, str) else None
        if fixture is None or fixture.get("role") != "ORGANIZER":
            raise SeedDataError(f"{where}.organizer must name an ORGANIZER identity fixture.")
        label = _require_text(org, "label", where)
        prefix = TITLE_PREFIX.format(label=label)
        events: list[SeedEvent] = []
        for event_index, event_raw in enumerate(_require_list(org["events"], f"{where}.events")):
            event_where = f"{where}.events[{event_index}]"
            event = _require_object(
                event_raw, event_where, _EVENT_KEYS, {"title", "description", "activities"}
            )
            publish = _require_bool(event, "publish", event_where, True)
            activities = tuple(
                _parse_activity(item, f"{event_where}.activities[{index}]", prefix, publish, fixtures)
                for index, item in enumerate(
                    _require_list(event["activities"], f"{event_where}.activities")
                )
            )
            if not activities:
                raise SeedDataError(f"{event_where}.activities must contain at least one activity.")
            description = event["description"]
            if not isinstance(description, str):
                raise SeedDataError(f"{event_where}.description must be a string.")
            seed_event = SeedEvent(
                prefix + _require_text(event, "title", event_where),
                description.strip(),
                publish,
                activities,
            )
            try:
                seed_event.payload(fixture["organizationId"], _PLACEHOLDER_ANCHOR)
            except ValueError as error:
                raise SeedDataError(f"{event_where} is invalid: {error}") from None
            events.append(seed_event)
        organizations.append(SeedOrganization(organizer, label, tuple(events)))
    return tuple(organizations)


def _parse_activity(
    raw: Any,
    where: str,
    prefix: str,
    publish: bool,
    fixtures: Mapping[str, Mapping[str, Any]],
) -> SeedActivity:
    activity = _require_object(
        raw,
        where,
        _ACTIVITY_KEYS,
        {"title", "durationMinutes", "location", "responsible", "capacity", "priceMinor"},
    )
    relative_minutes = "startsInMinutes" in activity
    relative_days = "startsInDays" in activity or "startTime" in activity
    if relative_minutes == relative_days:
        raise SeedDataError(
            f"{where} must define either startsInMinutes or startsInDays with startTime."
        )
    starts_in_minutes = starts_in_days = start_time = None
    if relative_minutes:
        starts_in_minutes = _require_int(activity, "startsInMinutes", where, 2)
    else:
        if "startsInDays" not in activity or "startTime" not in activity:
            raise SeedDataError(f"{where} must define both startsInDays and startTime.")
        starts_in_days = _require_int(activity, "startsInDays", where, 1)
        try:
            start_time = time.fromisoformat(activity["startTime"])
        except (TypeError, ValueError):
            raise SeedDataError(f"{where}.startTime must use the HH:MM format.") from None
    duration_minutes = _require_int(activity, "durationMinutes", where, 1)

    registrations: list[SeedRegistration] = []
    seen: set[str] = set()
    for index, registration_raw in enumerate(
        _require_list(activity.get("registrations", []), f"{where}.registrations")
    ):
        registration_where = f"{where}.registrations[{index}]"
        registration = _require_object(
            registration_raw, registration_where, _REGISTRATION_KEYS, {"participant"}
        )
        participant = registration["participant"]
        fixture = fixtures.get(participant) if isinstance(participant, str) else None
        if fixture is None or fixture.get("role") != "PARTICIPANT":
            raise SeedDataError(
                f"{registration_where}.participant must name a PARTICIPANT identity fixture."
            )
        if participant in seen:
            raise SeedDataError(f"{registration_where} repeats participant {participant}.")
        seen.add(participant)
        check_in = _require_bool(registration, "checkIn", registration_where, False)
        certificate = _require_bool(registration, "certificate", registration_where, False)
        cancel = _require_bool(registration, "cancel", registration_where, False)
        if certificate and not check_in:
            raise SeedDataError(f"{registration_where}.certificate requires checkIn.")
        if cancel and check_in:
            raise SeedDataError(f"{registration_where} cannot combine cancel and checkIn.")
        if check_in and not relative_minutes:
            raise SeedDataError(
                f"{registration_where}.checkIn requires an activity scheduled with startsInMinutes."
            )
        if certificate and starts_in_minutes + duration_minutes > MAX_CERTIFICATE_WAIT_MINUTES:
            raise SeedDataError(
                f"{registration_where}.certificate requires the activity to end within "
                f"{MAX_CERTIFICATE_WAIT_MINUTES} minutes."
            )
        registrations.append(SeedRegistration(participant, check_in, certificate, cancel))
    if registrations and not publish:
        raise SeedDataError(f"{where} has registrations but its event is not published.")

    seed_activity = SeedActivity(
        title=prefix + _require_text(activity, "title", where),
        starts_in_minutes=starts_in_minutes,
        starts_in_days=starts_in_days,
        start_time=start_time,
        duration_minutes=duration_minutes,
        location=_require_text(activity, "location", where),
        responsible=_require_text(activity, "responsible", where),
        capacity=_require_int(activity, "capacity", where, 1),
        price_minor=_require_int(activity, "priceMinor", where, 0),
        registrations=tuple(registrations),
    )
    try:
        seed_activity.payload(_PLACEHOLDER_ANCHOR)
    except ValueError as error:
        raise SeedDataError(f"{where} is invalid: {error}") from None
    return seed_activity


def load_seed_file(
    path: Path, fixtures: Mapping[str, Mapping[str, Any]]
) -> tuple[SeedOrganization, ...]:
    try:
        if path.stat().st_size > MAX_SEED_BYTES:
            raise SeedDataError("Seed file exceeds the size limit.")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SeedDataError(f"Seed file could not be read: {error.strerror}.") from None
    except json.JSONDecodeError as error:
        raise SeedDataError(
            f"Seed file is not valid JSON: {error.msg} (line {error.lineno})."
        ) from None
    return parse_seed_data(raw, fixtures)


def seed_key(*parts: Any) -> str:
    canonical = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return "seed-v1-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SeedApplier:
    def __init__(
        self,
        clients: DemoClients,
        fixtures: Mapping[str, Mapping[str, Any]],
        *,
        settle_timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 1.0,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], Any] = asyncio.sleep,
    ):
        self._clients = clients
        self._fixtures = fixtures
        self._settle_timeout = settle_timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._now = now
        self._sleep = sleep

    def _token(self, fixture_name: str) -> str:
        return self._fixtures[fixture_name]["token"]

    async def find_anchor(self) -> datetime | None:
        anchors: list[datetime] = []
        offset = 0
        while True:
            page = await self._clients.list_events(limit=100, offset=offset, search=SEARCH_MARKER)
            for event in page:
                match = _ANCHOR_PATTERN.search(event.description)
                if match is None:
                    continue
                try:
                    anchors.append(datetime.fromisoformat(match.group(1).replace("Z", "+00:00")))
                except ValueError:
                    continue
            if len(page) < 100:
                break
            offset += 100
        return min(anchors) if anchors else None

    async def apply(self, organizations: tuple[SeedOrganization, ...]) -> SeedSummary:
        anchor = await self.find_anchor()
        if anchor is None:
            anchor = self._now().astimezone(timezone.utc).replace(second=0, microsecond=0)
        summary = SeedSummary(anchor=anchor)
        certificates: list[tuple[Any, SeedRegistration, Registration]] = []
        for organization in organizations:
            fixture = self._fixtures[organization.organizer]
            for seed_event in organization.events:
                await self._apply_event(
                    fixture, organization.organizer, seed_event, anchor, summary, certificates
                )
        for activity, seed_registration, registration in sorted(
            certificates, key=lambda item: item[0].ends_at
        ):
            await self._issue_certificate(activity, seed_registration, registration, summary)
        return summary

    async def _apply_event(
        self,
        fixture: Mapping[str, Any],
        organizer: str,
        seed_event: SeedEvent,
        anchor: datetime,
        summary: SeedSummary,
        certificates: list,
    ) -> None:
        token = fixture["token"]
        event_input = seed_event.payload(fixture["organizationId"], anchor)
        event = await self._clients.create_event(
            event_input,
            token=token,
            idempotency_key=seed_key(
                "event", organizer, event_input.model_dump(mode="json", by_alias=True)
            ),
        )
        summary.events += 1
        created = []
        for index, seed_activity in enumerate(seed_event.activities):
            activity_input = seed_activity.payload(anchor)
            activity = await self._clients.create_activity(
                event.id,
                activity_input,
                token=token,
                idempotency_key=seed_key(
                    "activity", str(event.id), activity_input.model_dump(mode="json", by_alias=True)
                ),
            )
            summary.activities += 1
            created.append((seed_activity, activity))
            if index == 0 and seed_event.publish:
                # Publishing right after the first activity exposes the anchor quickly.
                await self._clients.publish_event(
                    event.id, token=token, idempotency_key=seed_key("publish", str(event.id))
                )
        for seed_activity, activity in created:
            await self._apply_registrations(seed_activity, activity, summary, certificates)

    async def _apply_registrations(
        self, seed_activity: SeedActivity, activity, summary: SeedSummary, certificates: list
    ) -> None:
        registrations: list[tuple[SeedRegistration, Registration]] = []
        for seed_registration in seed_activity.registrations:
            token = self._token(seed_registration.participant)
            try:
                registration = await self._retry_not_ready(
                    lambda token=token: self._clients.request_registration(
                        activity.id,
                        token=token,
                        idempotency_key=seed_key(
                            "register", str(activity.id), seed_registration.participant
                        ),
                    )
                )
            except ServiceError as error:
                if error.code == "ACTIVITY_INELIGIBLE":
                    summary.warnings.append(
                        f"Skipped registration of {seed_registration.participant} on "
                        f"{activity.title!r}: the activity has already started."
                    )
                    continue
                if error.code != "ALREADY_REGISTERED":
                    raise
                registration = await self._find_registration(token, activity.id)
                if registration is None:
                    raise
            summary.registrations += 1
            registrations.append((seed_registration, registration))

        settled = [
            (seed_registration, await self._settle(seed_registration, registration))
            for seed_registration, registration in registrations
        ]

        for seed_registration, registration in settled:
            if not seed_registration.cancel:
                continue
            if registration.status != "CANCELLED":
                await self._clients.cancel_registration(
                    registration.id,
                    token=self._token(seed_registration.participant),
                    idempotency_key=seed_key("cancel", str(registration.id)),
                )
            summary.cancellations += 1

        for seed_registration, registration in settled:
            if not seed_registration.check_in:
                continue
            if registration.status != "CONFIRMED":
                summary.warnings.append(
                    f"Skipped check-in for {seed_registration.participant} on "
                    f"{activity.title!r}: registration is {registration.status}."
                )
                continue
            token = self._token(seed_registration.participant)
            await self._retry_not_ready(
                lambda token=token, registration=registration: self._clients.check_in(
                    registration.id,
                    token=token,
                    idempotency_key=seed_key("check-in", str(registration.id)),
                )
            )
            summary.check_ins += 1
            if seed_registration.certificate:
                certificates.append((activity, seed_registration, registration))

    async def _issue_certificate(
        self, activity, seed_registration: SeedRegistration, registration, summary
    ) -> None:
        remaining = (activity.ends_at - self._now()).total_seconds()
        if remaining > MAX_CERTIFICATE_WAIT_MINUTES * 60:
            summary.warnings.append(
                f"Skipped certificate for {seed_registration.participant} on "
                f"{activity.title!r}: the activity has not ended."
            )
            return
        if remaining > 0:
            await self._sleep(remaining + 2)
        token = self._token(seed_registration.participant)
        await self._retry_not_ready(
            lambda: self._clients.issue_certificate(
                registration.id,
                token=token,
                idempotency_key=seed_key("certificate", str(registration.id)),
            )
        )
        summary.certificates += 1

    async def _find_registration(self, token: str, activity_id) -> Registration | None:
        offset = 0
        while True:
            page = await self._clients.list_registrations(token=token, limit=100, offset=offset)
            for registration in page:
                if registration.activity_id == activity_id and registration.status not in {
                    "CANCELLED",
                    "EXPIRED",
                    "REFUSED",
                }:
                    return registration
            if len(page) < 100:
                return None
            offset += 100

    async def _settle(
        self, seed_registration: SeedRegistration, registration: Registration
    ) -> Registration:
        token = self._token(seed_registration.participant)
        current = await self._clients.get_registration(registration.id, token=token)
        waited = 0.0
        while current.status == "PENDING_PAYMENT" and waited < self._settle_timeout:
            await self._sleep(self._poll_interval)
            waited += self._poll_interval
            current = await self._clients.get_registration(registration.id, token=token)
        return current

    async def _retry_not_ready(self, operation):
        waited = 0.0
        while True:
            try:
                return await operation()
            except ServiceError as error:
                retryable = error.code in {
                    "ELIGIBILITY_NOT_READY",
                    "REGISTRATION_NOT_CONFIRMED",
                    "ACTIVITY_NOT_ENDED",
                    "EVENT_MANAGEMENT_UNAVAILABLE",
                } or error.status_code == 404
                if not retryable or waited >= self._settle_timeout:
                    raise
            await self._sleep(self._poll_interval)
            waited += self._poll_interval


async def run_seed(
    settings,
    fixtures: Mapping[str, Mapping[str, Any]],
    *,
    max_attempts: int = 10,
    retry_delay_seconds: float = 5.0,
    clients_factory: Callable[[Any], DemoClients] = DemoClients,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> SeedSummary | None:
    path = settings.seed_data_path
    if path is None:
        return None
    try:
        organizations = load_seed_file(path, fixtures)
    except SeedDataError as error:
        logger.error("Initial presentation data was not applied: %s", error)
        return None

    for attempt in range(1, max_attempts + 1):
        clients = clients_factory(settings)
        try:
            summary = await SeedApplier(clients, fixtures, sleep=sleep).apply(organizations)
        except (TransportError, InvalidResponseError) as error:
            logger.warning("Initial data attempt %s/%s failed: %s", attempt, max_attempts, error)
        except ServiceError as error:
            if error.status_code < 500 and error.status_code not in {404, 409}:
                logger.error("Initial presentation data was not applied: %s", error)
                return None
            logger.warning("Initial data attempt %s/%s failed: %s", attempt, max_attempts, error)
        else:
            for warning in summary.warnings:
                logger.warning("Initial data: %s", warning)
            logger.info(
                "Initial presentation data ready (batch %s): %s events, %s activities, "
                "%s registrations, %s check-ins, %s certificates, %s cancellations.",
                _iso(summary.anchor),
                summary.events,
                summary.activities,
                summary.registrations,
                summary.check_ins,
                summary.certificates,
                summary.cancellations,
            )
            return summary
        finally:
            await clients.aclose()
        await sleep(retry_delay_seconds * attempt)
    logger.error("Initial presentation data was not applied after %s attempts.", max_attempts)
    return None

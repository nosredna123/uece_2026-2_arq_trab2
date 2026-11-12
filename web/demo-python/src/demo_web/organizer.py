"""Organizer pages: manage the organization's events and follow simple reports."""

import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from demo_web.commands import CommandConflictError
from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.models import Activity, ActivityInput, Event, EventInput, Money
from demo_web.presentation import (
    ORGANIZER_PERSONAS,
    PAGE_METADATA,
    format_datetime,
    format_money,
    templates,
)
from demo_web.routes import (
    ORGANIZER_PAGE,
    SERVICE_ERRORS,
    _command_key,
    _existing_session,
    _organizer_identity,
    _service_error_page,
    _validate_csrf,
    _validated_form_fields,
)
from demo_web.sessions import BrowserSession


router = APIRouter()
EVENT_FIELDS = frozenset(
    {"csrf_token", "idempotency_key", "title", "description", "starts_at", "ends_at"}
)
ACTIVITY_FIELDS = frozenset(
    {
        "csrf_token",
        "idempotency_key",
        "title",
        "starts_at",
        "ends_at",
        "location",
        "responsible",
        "capacity",
        "price",
    }
)
COMMAND_FIELDS = frozenset({"csrf_token", "idempotency_key"})
NOTICES = {
    "created": "Event created as a draft. Add activities, then publish it.",
    "saved": "Event saved.",
    "activity-added": "Activity added.",
    "activity-saved": "Activity saved.",
    "published": "Event published: participants can now register.",
    "cancelled": "Event cancelled.",
}
DOMAIN_ERROR_STATUSES = frozenset({400, 409, 412, 422})
REPORT_EVENT_STATUSES = frozenset({"PUBLISHED"})


def _organizer(request: Request) -> tuple[BrowserSession, dict[str, str]]:
    session = _existing_session(request)
    return session, _organizer_identity(request, session)


def _form_datetime(value: str) -> str:
    """Accept HTML datetime-local values (interpreted as UTC) and full ISO 8601 values."""
    value = value.strip()
    if not value:
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _input_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _price_minor(value: str) -> int:
    try:
        amount = Decimal(value.strip().replace(",", ".") or "0")
    except InvalidOperation:
        raise ValueError("Invalid price.") from None
    if amount < 0 or amount != amount.quantize(Decimal("0.01")):
        raise ValueError("Invalid price.")
    return int(amount * 100)


def _event_values(event: Event) -> dict[str, str]:
    return {
        "title": event.title,
        "description": event.description,
        "starts_at": _input_datetime(event.starts_at),
        "ends_at": _input_datetime(event.ends_at),
    }


def _activity_values(activity: Activity) -> dict[str, str]:
    whole, minor = divmod(activity.price.amount_minor, 100)
    return {
        "title": activity.title,
        "starts_at": _input_datetime(activity.starts_at),
        "ends_at": _input_datetime(activity.ends_at),
        "location": activity.location,
        "responsible": activity.responsible,
        "capacity": str(activity.capacity),
        "price": f"{whole}.{minor:02d}",
    }


def _event_input(identity: dict[str, str], fields: dict[str, str]) -> EventInput:
    return EventInput(
        organizationId=identity["organizationId"],
        title=fields.get("title", "").strip(),
        description=fields.get("description", "").strip(),
        startsAt=_form_datetime(fields.get("starts_at", "")),
        endsAt=_form_datetime(fields.get("ends_at", "")),
    )


def _activity_input(fields: dict[str, str]) -> ActivityInput:
    return ActivityInput(
        title=fields.get("title", "").strip(),
        startsAt=_form_datetime(fields.get("starts_at", "")),
        endsAt=_form_datetime(fields.get("ends_at", "")),
        location=fields.get("location", "").strip(),
        responsible=fields.get("responsible", "").strip(),
        capacity=int(fields.get("capacity", "")),
        price=Money(amountMinor=_price_minor(fields.get("price", "")), currency="BRL"),
    )


async def _organization_events(
    request: Request, session: BrowserSession, identity: dict[str, str]
) -> list[Event]:
    return await request.app.state.clients.list_organization_events(
        identity["organizationId"],
        token=identity["token"],
        limit=100,
        session_id=session.id,
    )


async def _event_and_activities(
    request: Request,
    session: BrowserSession,
    identity: dict[str, str],
    event_id: UUID,
) -> tuple[Event, list[Activity]]:
    events = await _organization_events(request, session, identity)
    event = next((item for item in events if item.id == event_id), None)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found for this organization.")
    activities = await request.app.state.clients.list_organization_activities(
        identity["organizationId"],
        event_id,
        token=identity["token"],
        limit=100,
        session_id=session.id,
    )
    return event, activities


def _page(
    request: Request,
    name: str,
    page: str,
    context: dict[str, Any],
    *,
    status_code: int = 200,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name=name,
        context={"page": PAGE_METADATA[page], **context},
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _redirect(location: str) -> Response:
    return Response(status_code=303, headers={"Location": location})


@router.get("/organizer", response_class=HTMLResponse, dependencies=ORGANIZER_PAGE)
async def organizer_events(request: Request) -> HTMLResponse:
    session, identity = _organizer(request)
    try:
        events = await _organization_events(request, session, identity)
    except SERVICE_ERRORS as error:
        return _service_error_page(request, error=error, message="Events could not be loaded.")
    return _page(
        request,
        "organizer.html",
        "organizer",
        {
            "organization_label": ORGANIZER_PERSONAS[session.persona],
            "events": [
                {
                    "event": event,
                    "starts_at": format_datetime(event.starts_at),
                    "ends_at": format_datetime(event.ends_at),
                }
                for event in events
            ],
        },
    )


def _new_event_page(
    request: Request,
    session: BrowserSession,
    *,
    values: dict[str, str] | None = None,
    key: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return _page(
        request,
        "organizer_new_event.html",
        "organizer_new_event",
        {
            "csrf_token": session.csrf_token,
            "organization_label": ORGANIZER_PERSONAS[session.persona],
            "values": values or {},
            "idempotency_key": key or secrets.token_urlsafe(32),
            "form_error": error,
        },
        status_code=status_code,
    )


@router.get("/organizer/events/new", response_class=HTMLResponse, dependencies=ORGANIZER_PAGE)
async def organizer_new_event(request: Request) -> HTMLResponse:
    session, _identity = _organizer(request)
    return _new_event_page(request, session)


async def _run_command(
    request: Request,
    session: BrowserSession,
    *,
    key: str,
    payload: dict[str, Any],
    operation: Callable[[str, UUID], Awaitable[Any]],
) -> Any:
    """Run a write once per idempotency key; return the result or an error message string."""
    try:
        outcome = await request.app.state.command_ledger.execute(
            session, idempotency_key=key, payload=payload, operation=operation
        )
    except CommandConflictError:
        raise HTTPException(
            status_code=409, detail="This form was already submitted with other values."
        ) from None
    except OverflowError:
        raise HTTPException(
            status_code=429, detail="This browser session has reached its command limit."
        ) from None
    except ServiceError as error:
        if error.status_code in DOMAIN_ERROR_STATUSES:
            return _CommandRejected(f"{error.message} ({error.code})", error.status_code)
        raise
    except (TransportError, InvalidResponseError) as error:
        if error.outcome_unknown:
            return _CommandRejected(
                "The result is unknown. Submit the same form again to retry safely.", 503
            )
        raise
    return outcome.result


class _CommandRejected:
    def __init__(self, message: str, status_code: int):
        self.message = message
        self.status_code = status_code


@router.post("/organizer/events")
async def organizer_create_event(request: Request) -> Response:
    session, identity = _organizer(request)
    await _validate_csrf(request, session)
    fields = await _validated_form_fields(request, allowed=EVENT_FIELDS)
    key = _command_key(fields)
    try:
        event_input = _event_input(identity, fields)
    except ValidationError:
        return _new_event_page(
            request,
            session,
            values=fields,
            key=key,
            error="Check the title and make sure the event starts before it ends.",
            status_code=400,
        )
    clients = request.app.state.clients

    async def create(command_key: str, correlation_id: UUID):
        return await clients.create_event(
            event_input,
            token=identity["token"],
            idempotency_key=command_key,
            session_id=session.id,
            correlation_id=correlation_id,
        )

    try:
        result = await _run_command(
            request,
            session,
            key=key,
            payload=event_input.model_dump(mode="json", by_alias=True),
            operation=create,
        )
    except SERVICE_ERRORS as error:
        return _service_error_page(request, error=error, message="The event was not created.")
    if isinstance(result, _CommandRejected):
        return _new_event_page(
            request,
            session,
            values=fields,
            key=key,
            error=result.message,
            status_code=result.status_code,
        )
    return _redirect(f"/organizer/events/{result.id}?notice=created")


async def _event_page(
    request: Request,
    session: BrowserSession,
    identity: dict[str, str],
    event_id: UUID,
    *,
    notice: str | None = None,
    error: str | None = None,
    error_form: str | None = None,
    values: dict[str, str] | None = None,
    key: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    try:
        event, activities = await _event_and_activities(request, session, identity, event_id)
    except SERVICE_ERRORS as service_error:
        return _service_error_page(
            request, error=service_error, message="The event could not be loaded."
        )

    def form(form_id: str, defaults: dict[str, str]) -> dict[str, Any]:
        failed = error_form == form_id
        return {
            "id": form_id,
            "values": values if failed and values is not None else defaults,
            "key": key if failed and key else secrets.token_urlsafe(32),
            "error": error if failed else None,
        }

    return _page(
        request,
        "organizer_event.html",
        "organizer_event",
        {
            "csrf_token": session.csrf_token,
            "event": event,
            "editable": event.status != "CANCELLED",
            "starts_at": format_datetime(event.starts_at),
            "ends_at": format_datetime(event.ends_at),
            "notice": NOTICES.get(notice or ""),
            "page_error": error if error_form == "page" else None,
            "event_form": form("event", _event_values(event)),
            "activities": [
                {
                    "activity": activity,
                    "price": format_money(activity.price),
                    "starts_at": format_datetime(activity.starts_at),
                    "form": form(str(activity.id), _activity_values(activity)),
                }
                for activity in activities
            ],
            "new_activity_form": form(
                "new-activity",
                {
                    "starts_at": _input_datetime(event.starts_at),
                    "ends_at": _input_datetime(event.ends_at),
                    "price": "0.00",
                },
            ),
            "publish_key": secrets.token_urlsafe(32),
            "cancel_key": secrets.token_urlsafe(32),
        },
        status_code=status_code,
    )


@router.get(
    "/organizer/events/{event_id}", response_class=HTMLResponse, dependencies=ORGANIZER_PAGE
)
async def organizer_event(request: Request, event_id: UUID, notice: str | None = None):
    session, identity = _organizer(request)
    return await _event_page(request, session, identity, event_id, notice=notice)


async def _event_command(
    request: Request,
    event_id: UUID,
    *,
    allowed: frozenset[str],
    form_id: str,
    build_input: Callable[[dict[str, str]], Any] | None,
    invalid_message: str,
    call: Callable[[Any, dict[str, str], str, UUID], Awaitable[Any]],
    payload: Callable[[Any], dict[str, Any]],
    notice: str,
    failure_message: str,
) -> Response:
    session, identity = _organizer(request)
    await _validate_csrf(request, session)
    fields = await _validated_form_fields(request, allowed=allowed)
    key = _command_key(fields)
    retry = {"error_form": form_id, "values": fields, "key": key}
    try:
        command_input = build_input(fields) if build_input is not None else None
    except (ValidationError, ValueError):
        return await _event_page(
            request, session, identity, event_id, error=invalid_message, status_code=400, **retry
        )

    async def operation(command_key: str, correlation_id: UUID):
        return await call(command_input, identity, command_key, correlation_id)

    try:
        result = await _run_command(
            request, session, key=key, payload=payload(command_input), operation=operation
        )
    except SERVICE_ERRORS as error:
        return _service_error_page(request, error=error, message=failure_message)
    if isinstance(result, _CommandRejected):
        return await _event_page(
            request,
            session,
            identity,
            event_id,
            error=result.message,
            status_code=result.status_code,
            **retry,
        )
    return _redirect(f"/organizer/events/{event_id}?notice={notice}")


def _session_id(request: Request) -> str:
    return _existing_session(request).id


@router.post("/organizer/events/{event_id}")
async def organizer_update_event(request: Request, event_id: UUID) -> Response:
    clients = request.app.state.clients
    session_id = _session_id(request)

    async def call(event_input, identity, key, correlation_id):
        return await clients.update_event(
            event_id,
            event_input,
            token=identity["token"],
            idempotency_key=key,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    session, identity = _organizer(request)
    return await _event_command(
        request,
        event_id,
        allowed=EVENT_FIELDS,
        form_id="event",
        build_input=lambda fields: _event_input(identity, fields),
        invalid_message="Check the title and make sure the event starts before it ends.",
        call=call,
        payload=lambda event_input: {
            "eventId": str(event_id),
            "event": event_input.model_dump(mode="json", by_alias=True),
        },
        notice="saved",
        failure_message="The event was not saved.",
    )


ACTIVITY_INVALID = "Check every field: capacity of at least 1, a valid price, and start before end."


@router.post("/organizer/events/{event_id}/activities")
async def organizer_create_activity(request: Request, event_id: UUID) -> Response:
    clients = request.app.state.clients
    session_id = _session_id(request)

    async def call(activity_input, identity, key, correlation_id):
        return await clients.create_activity(
            event_id,
            activity_input,
            token=identity["token"],
            idempotency_key=key,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    return await _event_command(
        request,
        event_id,
        allowed=ACTIVITY_FIELDS,
        form_id="new-activity",
        build_input=_activity_input,
        invalid_message=ACTIVITY_INVALID,
        call=call,
        payload=lambda activity_input: {
            "eventId": str(event_id),
            "activity": activity_input.model_dump(mode="json", by_alias=True),
        },
        notice="activity-added",
        failure_message="The activity was not added.",
    )


@router.post("/organizer/events/{event_id}/activities/{activity_id}")
async def organizer_update_activity(
    request: Request, event_id: UUID, activity_id: UUID
) -> Response:
    clients = request.app.state.clients
    session_id = _session_id(request)

    async def call(activity_input, identity, key, correlation_id):
        return await clients.update_activity(
            activity_id,
            activity_input,
            token=identity["token"],
            idempotency_key=key,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    return await _event_command(
        request,
        event_id,
        allowed=ACTIVITY_FIELDS,
        form_id=str(activity_id),
        build_input=_activity_input,
        invalid_message=ACTIVITY_INVALID,
        call=call,
        payload=lambda activity_input: {
            "activityId": str(activity_id),
            "activity": activity_input.model_dump(mode="json", by_alias=True),
        },
        notice="activity-saved",
        failure_message="The activity was not saved.",
    )


@router.post("/organizer/events/{event_id}/publish")
async def organizer_publish_event(request: Request, event_id: UUID) -> Response:
    clients = request.app.state.clients
    session_id = _session_id(request)

    async def call(_input, identity, key, correlation_id):
        return await clients.publish_event(
            event_id,
            token=identity["token"],
            idempotency_key=key,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    return await _event_command(
        request,
        event_id,
        allowed=COMMAND_FIELDS,
        form_id="page",
        build_input=None,
        invalid_message="",
        call=call,
        payload=lambda _input: {"publishEventId": str(event_id)},
        notice="published",
        failure_message="The event was not published.",
    )


@router.post("/organizer/events/{event_id}/cancel")
async def organizer_cancel_event(request: Request, event_id: UUID) -> Response:
    clients = request.app.state.clients
    session_id = _session_id(request)

    async def call(_input, identity, key, correlation_id):
        return await clients.cancel_event(
            event_id,
            token=identity["token"],
            idempotency_key=key,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    return await _event_command(
        request,
        event_id,
        allowed=COMMAND_FIELDS,
        form_id="page",
        build_input=None,
        invalid_message="",
        call=call,
        payload=lambda _input: {"cancelEventId": str(event_id)},
        notice="cancelled",
        failure_message="The event was not cancelled.",
    )


async def _report_sections(
    request: Request, session: BrowserSession, identity: dict[str, str]
) -> list[dict[str, Any]]:
    clients = request.app.state.clients
    events = [
        event
        for event in await _organization_events(request, session, identity)
        if event.status in REPORT_EVENT_STATUSES
    ]
    reports = await clients.get_reports(
        identity["organizationId"], token=identity["token"], limit=100, session_id=session.id
    )
    by_activity = {report.activity_id: report for report in reports}
    sections = []
    for event in events:
        activities = await clients.list_organization_activities(
            identity["organizationId"],
            event.id,
            token=identity["token"],
            limit=100,
            session_id=session.id,
        )
        rows = []
        for activity in activities:
            report = by_activity.get(activity.id)
            rows.append(
                {
                    "title": activity.title,
                    "starts_at": format_datetime(activity.starts_at),
                    "capacity": activity.capacity,
                    "confirmed": report.confirmed if report else 0,
                    "pending_payment": report.pending_payment if report else 0,
                    "waiting": (report.waiting + report.offered) if report else 0,
                    "checked_in": report.checked_in if report else 0,
                    "certificates": report.certificates if report else 0,
                }
            )
        totals = {
            name: sum(row[name] for row in rows)
            for name in (
                "capacity",
                "confirmed",
                "pending_payment",
                "waiting",
                "checked_in",
                "certificates",
            )
        }
        sections.append(
            {
                "event": event,
                "starts_at": format_datetime(event.starts_at),
                "rows": rows,
                "totals": totals,
            }
        )
    return sections


def _percent(part: int, whole: int) -> str:
    return f"{round(part * 100 / whole)}%" if whole else "—"


def _report_summary(sections: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        name: sum(section["totals"][name] for section in sections)
        for name in (
            "capacity",
            "confirmed",
            "pending_payment",
            "waiting",
            "checked_in",
            "certificates",
        )
    }
    return {
        "events": len(sections),
        "activities": sum(len(section["rows"]) for section in sections),
        **totals,
        "free_seats": max(0, totals["capacity"] - totals["confirmed"] - totals["pending_payment"]),
        "occupancy": _percent(totals["confirmed"], totals["capacity"]),
        "attendance": _percent(totals["checked_in"], totals["confirmed"]),
    }


async def _reports(request: Request, template: str) -> HTMLResponse:
    session, identity = _organizer(request)
    try:
        sections = await _report_sections(request, session, identity)
    except SERVICE_ERRORS as error:
        return _service_error_page(request, error=error, message="Reports could not be loaded.")
    return _page(
        request,
        template,
        "reports",
        {
            "organization_label": ORGANIZER_PERSONAS[session.persona],
            "sections": sections,
            "summary": _report_summary(sections),
            "updated_at": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            "refresh_ms": request.app.state.settings.poll_interval_seconds * 1000,
        },
    )


@router.get("/reports", response_class=HTMLResponse, dependencies=ORGANIZER_PAGE)
async def organizer_reports(request: Request) -> HTMLResponse:
    return await _reports(request, "reports.html")


@router.get("/reports/table", response_class=HTMLResponse, dependencies=ORGANIZER_PAGE)
async def organizer_reports_table(request: Request) -> HTMLResponse:
    return await _reports(request, "partials/report_tables.html")

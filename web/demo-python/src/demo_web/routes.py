import secrets
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from demo_web.commands import CommandConflictError
from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.models import ActivityInput, EventInput, Money
from demo_web.presentation import (
    PAGE_METADATA,
    PERSONA_LABELS,
    format_datetime,
    format_money,
    format_observation_age,
    service_failure,
    templates,
)
from demo_web.sessions import (
    BrowserSession,
    InvalidPersonaError,
    SessionEventDraft,
    SessionStore,
)


router = APIRouter()
SESSION_COOKIE = "demo_session"
PERSONA_LABEL_BY_ID = dict(PERSONA_LABELS)
MAX_CATALOG_LIMIT = 50
MAX_ACTIVITY_LIMIT = 20
MAX_REGISTRATION_LIMIT = 50
MAX_PAGE_OFFSET = 10000
SERVICE_ERRORS = (ServiceError, TransportError, InvalidResponseError)
POLLABLE_STATUSES = frozenset({"WAITLISTED", "OFFERED", "PENDING_PAYMENT"})


def _session_store(request: Request) -> SessionStore:
    return request.app.state.sessions


def _existing_session(request: Request) -> BrowserSession:
    session = _session_store(request).get(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=403, detail="A valid demo session is required.")
    return session


def _session_or_create(request: Request) -> tuple[BrowserSession, bool]:
    store = _session_store(request)
    session = store.get(request.cookies.get(SESSION_COOKIE))
    if session is not None:
        return session, False
    return store.create(), True


def _set_session_cookie(response: Response, session: BrowserSession, store: SessionStore) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session.id,
        max_age=store.ttl_seconds,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _set_cookie_if_new(
    response: Response,
    *,
    is_new: bool,
    session: BrowserSession,
    store: SessionStore,
) -> None:
    if is_new:
        _set_session_cookie(response, session, store)


def _service_error_page(
    request: Request,
    *,
    error: ServiceError | TransportError | InvalidResponseError,
    message: str,
) -> HTMLResponse:
    failure = service_failure(error)
    status_code = failure.status_code
    if status_code is None or not 400 <= status_code <= 599:
        status_code = 503
    response = templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "page": PAGE_METADATA["error"],
            "operational_status": "Service request could not be completed.",
            "status_code": status_code,
            "message": message,
            "error_detail": failure,
        },
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def _form_csrf_token(request: Request) -> str:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type != "application/x-www-form-urlencoded":
        return ""
    body = await request.body()
    if len(body) > 4096:
        return ""
    try:
        fields = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=16,
        )
    except (UnicodeDecodeError, ValueError):
        return ""
    tokens = fields.get("csrf_token", [])
    return tokens[0] if len(tokens) == 1 else ""


async def _validate_csrf(request: Request, session: BrowserSession) -> None:
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    origin = request.headers.get("origin", "")
    token = request.headers.get("x-csrf-token", "") or await _form_csrf_token(request)
    if origin != expected_origin or not secrets.compare_digest(token, session.csrf_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")


def _participant_identity(request: Request, session: BrowserSession) -> dict[str, str]:
    identity = _session_store(request).identity_for(session)
    if identity is None or identity.get("role") != "PARTICIPANT":
        raise HTTPException(status_code=403, detail="Select a participant persona to continue.")
    return dict(identity)


def _organizer_identity(request: Request, session: BrowserSession) -> dict[str, str]:
    identity = _session_store(request).identity_for(session)
    if identity is None or identity.get("role") != "ORGANIZER":
        raise HTTPException(status_code=403, detail="Select the organizer persona to continue.")
    return dict(identity)


async def _validated_form_fields(
    request: Request,
    *,
    allowed: frozenset[str],
) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=400, detail="A valid form is required.")
    body = await request.body()
    if len(body) > 8192:
        raise HTTPException(status_code=400, detail="The form is too large.")
    try:
        parsed = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=16,
        )
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="A valid form is required.") from None
    if set(parsed) - allowed or any(len(values) != 1 for values in parsed.values()):
        raise HTTPException(status_code=400, detail="The form contains unexpected fields.")
    return {key: values[0] for key, values in parsed.items()}


def _command_key(fields: dict[str, str]) -> str:
    key = fields.get("idempotency_key", "")
    if not 1 <= len(key) <= 200:
        raise HTTPException(status_code=400, detail="A valid command key is required.")
    return key


def _session_event_draft(
    session: BrowserSession,
    event_id: UUID,
) -> SessionEventDraft | None:
    with session.lock:
        return session.drafts.get(str(event_id))


def _record_session_event(session: BrowserSession, event) -> SessionEventDraft:
    with session.lock:
        return session.drafts.setdefault(str(event.id), SessionEventDraft(event=event))


def _organizer_response(
    request: Request,
    *,
    session: BrowserSession,
    identity: dict[str, str],
    form_error: str | None = None,
    message: str | None = None,
    retry_action: str | None = None,
    retry_event_id: UUID | None = None,
    retry_key: str | None = None,
    form_values: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    drafts = []
    with session.lock:
        current_drafts = tuple(session.drafts.values())
    for draft in current_drafts:
        event = draft.event
        is_activity_retry = retry_action == "create-activity" and str(event.id) == str(
            retry_event_id
        )
        is_publish_retry = retry_action == "publish-event" and str(event.id) == str(retry_event_id)
        drafts.append(
            {
                "event": event,
                "starts_at": format_datetime(event.starts_at),
                "ends_at": format_datetime(event.ends_at),
                "activities": [
                    {
                        "activity": activity,
                        "starts_at": format_datetime(activity.starts_at),
                        "ends_at": format_datetime(activity.ends_at),
                        "price": format_money(activity.price),
                    }
                    for activity in draft.activities.values()
                ],
                "activity_key": (retry_key if is_activity_retry else secrets.token_urlsafe(32)),
                "publish_key": retry_key if is_publish_retry else secrets.token_urlsafe(32),
                "activity_values": form_values if is_activity_retry else {},
            }
        )
    response = templates.TemplateResponse(
        request=request,
        name="organizer.html",
        context={
            "page": PAGE_METADATA["organizer"],
            "operational_status": "Organizer actions use existing Event Management APIs.",
            "csrf_token": session.csrf_token,
            "organization_id": identity["organizationId"],
            "drafts": drafts,
            "create_event_key": retry_key
            if retry_action == "create-event"
            else secrets.token_urlsafe(32),
            "create_event_values": form_values if retry_action == "create-event" else {},
            "form_error": form_error,
            "message": message,
            "retry_action": retry_action,
            "retry_event_id": retry_event_id,
            "entries": request.app.state.clients.history_for(session.id),
        },
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _registration_is_pollable(registration) -> bool:
    return registration.status in POLLABLE_STATUSES or (
        registration.status == "CONFIRMED" and registration.refund_status == "PENDING"
    )


def _registration_detail_response(
    request: Request,
    *,
    session: BrowserSession,
    registration_id: UUID,
    registration=None,
    retry_action: str | None = None,
    idempotency_key: str | None = None,
    action_error: ServiceError | TransportError | InvalidResponseError | None = None,
    message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    has_mutable_status = registration is not None and registration.status in {
        "WAITLISTED",
        "OFFERED",
        "PENDING_PAYMENT",
        "CONFIRMED",
    }
    cancel_key = (
        idempotency_key
        if retry_action == "cancel"
        else secrets.token_urlsafe(32)
        if has_mutable_status
        else None
    )
    accept_key = (
        idempotency_key
        if retry_action == "accept-offer"
        else secrets.token_urlsafe(32)
        if has_mutable_status
        else None
    )
    response = templates.TemplateResponse(
        request=request,
        name="registration_detail.html",
        context={
            "page": PAGE_METADATA["registration_detail"],
            "operational_status": (
                "Showing the last observed registration; refresh may be paused or unavailable."
            ),
            "registration": registration,
            "registration_id": registration_id,
            "created_at": (
                format_datetime(registration.created_at) if registration is not None else None
            ),
            "expires_at": (
                format_datetime(registration.expires_at)
                if registration is not None and registration.expires_at is not None
                else None
            ),
            "offer_expires_at": (
                format_datetime(registration.offer_expires_at)
                if registration is not None and registration.offer_expires_at is not None
                else None
            ),
            "price": (
                format_money(registration.price)
                if registration is not None and registration.price is not None
                else None
            ),
            "observed_at": format_datetime(datetime.now(timezone.utc)),
            "pollable": registration is not None and _registration_is_pollable(registration),
            "poll_interval_ms": request.app.state.settings.poll_interval_seconds * 1000,
            "poll_timeout_ms": request.app.state.settings.poll_timeout_seconds * 1000,
            "persona_version": session.persona_version,
            "csrf_token": session.csrf_token,
            "cancel_key": cancel_key,
            "accept_key": accept_key,
            "can_cancel": (
                retry_action == "cancel"
                or registration is not None
                and registration.status in {"WAITLISTED", "OFFERED", "PENDING_PAYMENT", "CONFIRMED"}
            ),
            "can_accept_offer": retry_action == "accept-offer"
            or registration is not None
            and registration.status == "OFFERED",
            "retry_action": retry_action,
            "action_error": service_failure(action_error) if action_error else None,
            "message": message,
            "status_code": status_code,
        },
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _checkout_response(
    request: Request,
    *,
    session: BrowserSession,
    activity=None,
    event=None,
    idempotency_key: str,
    retry: bool = False,
    service_error: ServiceError | TransportError | InvalidResponseError | None = None,
    message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    failure = service_failure(service_error) if service_error is not None else None
    response = templates.TemplateResponse(
        request=request,
        name="checkout.html",
        context={
            "page": PAGE_METADATA["checkout"],
            "operational_status": (
                "Registration result is unknown; retry uses the same command."
                if retry
                else "Activity details loaded; no registration has been created."
                if activity is not None
                else "Registration command could not be confirmed."
            ),
            "activity": activity,
            "event": event,
            "price": format_money(activity.price) if activity is not None else None,
            "activity_starts_at": (
                format_datetime(activity.starts_at) if activity is not None else None
            ),
            "activity_ends_at": (
                format_datetime(activity.ends_at) if activity is not None else None
            ),
            "csrf_token": session.csrf_token,
            "idempotency_key": idempotency_key,
            "retry": retry,
            "service_failure": failure,
            "message": message,
        },
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    store = _session_store(request)
    session, is_new = _session_or_create(request)
    entries = request.app.state.clients.history_for(session.id)
    response = templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "page": PAGE_METADATA["home"],
            "operational_status": "Browser session active; backend services not queried.",
            "persona_options": PERSONA_LABELS,
            "current_persona": session.persona,
            "persona_label": PERSONA_LABEL_BY_ID[session.persona],
            "csrf_token": session.csrf_token,
            "entries": entries,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    _set_cookie_if_new(response, is_new=is_new, session=session, store=store)
    return response


@router.get("/events", response_class=HTMLResponse)
async def published_events(
    request: Request,
    search: Annotated[str | None, Query(alias="q", max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_CATALOG_LIMIT)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> HTMLResponse:
    store = _session_store(request)
    session, is_new = _session_or_create(request)
    try:
        returned_events = await request.app.state.clients.list_events(
            limit=limit,
            offset=offset,
            search=search,
            session_id=session.id,
        )
    except SERVICE_ERRORS as error:
        failure = service_failure(error)
        status_code = failure.status_code
        if status_code is None or not 400 <= status_code <= 599:
            status_code = 503
        response = templates.TemplateResponse(
            request=request,
            name="events.html",
            context={
                "page": PAGE_METADATA["events"],
                "operational_status": "Catalog service unavailable.",
                "events": [],
                "service_failure": failure,
                "search": search or "",
                "limit": limit,
                "offset": offset,
                "has_previous": offset > 0,
                "has_next": False,
            },
            status_code=status_code,
        )
        response.headers["Cache-Control"] = "no-store"
        _set_cookie_if_new(response, is_new=is_new, session=session, store=store)
        return response

    events = [
        {
            "event": event,
            "starts_at": format_datetime(event.starts_at),
            "ends_at": format_datetime(event.ends_at),
        }
        for event in returned_events
        if event.status == "PUBLISHED"
    ]
    response = templates.TemplateResponse(
        request=request,
        name="events.html",
        context={
            "page": PAGE_METADATA["events"],
            "operational_status": "Catalog service responded.",
            "events": events,
            "service_failure": None,
            "search": search or "",
            "limit": limit,
            "offset": offset,
            "has_previous": offset > 0,
            "has_next": len(returned_events) == limit,
            "previous_offset": max(0, offset - limit),
            "next_offset": offset + limit,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    _set_cookie_if_new(response, is_new=is_new, session=session, store=store)
    return response


@router.get("/events/{event_id}", response_class=HTMLResponse)
async def published_event_detail(
    request: Request,
    event_id: UUID,
    limit: Annotated[int, Query(ge=1, le=MAX_ACTIVITY_LIMIT)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> HTMLResponse:
    store = _session_store(request)
    session, is_new = _session_or_create(request)
    clients = request.app.state.clients
    try:
        event = await clients.get_event(event_id, session_id=session.id)
    except SERVICE_ERRORS as error:
        message = (
            "The requested page was not found."
            if isinstance(error, ServiceError) and error.status_code == 404
            else "Event details are temporarily unavailable."
        )
        response = _service_error_page(request, error=error, message=message)
        _set_cookie_if_new(response, is_new=is_new, session=session, store=store)
        return response

    if event.status != "PUBLISHED":
        response = templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "page": PAGE_METADATA["error"],
                "operational_status": "The requested event is not published.",
                "status_code": 404,
                "message": "The requested page was not found.",
            },
            status_code=404,
        )
        response.headers["Cache-Control"] = "no-store"
        _set_cookie_if_new(response, is_new=is_new, session=session, store=store)
        return response

    activities_error = None
    try:
        returned_activities = await clients.list_activities(
            event_id,
            limit=limit,
            offset=offset,
            session_id=session.id,
        )
    except SERVICE_ERRORS as error:
        returned_activities = []
        activities_error = service_failure(error)

    activity_cards = []
    for activity in returned_activities:
        availability = None
        availability_error = None
        age_label = None
        stale = False
        try:
            availability = await clients.get_availability(
                activity.id,
                session_id=session.id,
            )
            age_label, stale = format_observation_age(availability.as_of)
        except SERVICE_ERRORS as error:
            availability_error = service_failure(error)
        activity_cards.append(
            {
                "activity": activity,
                "starts_at": format_datetime(activity.starts_at),
                "ends_at": format_datetime(activity.ends_at),
                "price": format_money(activity.price),
                "availability": availability,
                "availability_error": availability_error,
                "age_label": age_label,
                "stale": stale,
            }
        )

    has_partial_failure = activities_error is not None or any(
        card["availability_error"] is not None for card in activity_cards
    )
    response = templates.TemplateResponse(
        request=request,
        name="event_detail.html",
        context={
            "page": PAGE_METADATA["event_detail"],
            "operational_status": (
                "Event loaded; activity or availability data may be partial."
                if has_partial_failure
                else "Event and activity program loaded."
            ),
            "event": event,
            "event_starts_at": format_datetime(event.starts_at),
            "event_ends_at": format_datetime(event.ends_at),
            "activities": activity_cards,
            "activities_error": activities_error,
            "limit": limit,
            "offset": offset,
            "has_previous": offset > 0,
            "has_next": len(returned_activities) == limit,
            "previous_offset": max(0, offset - limit),
            "next_offset": offset + limit,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    _set_cookie_if_new(response, is_new=is_new, session=session, store=store)
    return response


@router.get("/activities/{activity_id}/checkout", response_class=HTMLResponse)
async def checkout(request: Request, activity_id: UUID) -> HTMLResponse:
    session = _existing_session(request)
    _participant_identity(request, session)
    clients = request.app.state.clients
    try:
        activity = await clients.get_activity(activity_id, session_id=session.id)
        event = await clients.get_event(activity.event_id, session_id=session.id)
    except SERVICE_ERRORS as error:
        message = (
            "The requested activity is not available."
            if isinstance(error, ServiceError) and error.status_code == 404
            else "Activity details are temporarily unavailable."
        )
        return _service_error_page(request, error=error, message=message)

    if event.status != "PUBLISHED":
        raise HTTPException(status_code=404, detail="The requested activity is not available.")
    return _checkout_response(
        request,
        session=session,
        activity=activity,
        event=event,
        idempotency_key=secrets.token_urlsafe(32),
    )


@router.post("/activities/{activity_id}/checkout")
async def confirm_checkout(request: Request, activity_id: UUID) -> Response:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    identity = _participant_identity(request, session)

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=400, detail="A valid checkout form is required.")
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(status_code=400, detail="The checkout form is too large.")
    try:
        fields = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=8,
        )
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="A valid checkout form is required.") from None
    keys = fields.get("idempotency_key", [])
    if len(keys) != 1 or not 1 <= len(keys[0]) <= 200:
        raise HTTPException(status_code=400, detail="A valid checkout command is required.")
    idempotency_key = keys[0]
    clients = request.app.state.clients

    async def create_registration(key, correlation_id):
        return await clients.request_registration(
            activity_id,
            token=identity["token"],
            idempotency_key=key,
            session_id=session.id,
            correlation_id=correlation_id,
        )

    try:
        outcome = await request.app.state.command_ledger.execute(
            session,
            idempotency_key=idempotency_key,
            payload={"activityId": str(activity_id)},
            operation=create_registration,
        )
    except CommandConflictError:
        raise HTTPException(
            status_code=409,
            detail="This checkout command was already used for a different intent.",
        ) from None
    except OverflowError:
        raise HTTPException(
            status_code=429,
            detail="This browser session has reached its command limit.",
        ) from None
    except (TransportError, InvalidResponseError) as error:
        if error.outcome_unknown:
            return _checkout_response(
                request,
                session=session,
                idempotency_key=idempotency_key,
                retry=True,
                service_error=error,
                message=(
                    "The registration result is unknown. Retry this same command; "
                    "do not start a new registration."
                ),
                status_code=503,
            )
        return _service_error_page(
            request,
            error=error,
            message="Registration could not be completed.",
        )
    except ServiceError as error:
        return _service_error_page(
            request,
            error=error,
            message="Registration could not be completed.",
        )

    registration = outcome.result
    return Response(
        status_code=303,
        headers={"Location": f"/registrations/{registration.id}"},
    )


@router.get("/registrations", response_class=HTMLResponse)
async def registrations(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_REGISTRATION_LIMIT)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> HTMLResponse:
    session = _existing_session(request)
    identity = _participant_identity(request, session)
    try:
        returned_registrations = await request.app.state.clients.list_registrations(
            token=identity["token"],
            limit=limit,
            offset=offset,
            session_id=session.id,
        )
    except SERVICE_ERRORS as error:
        return _service_error_page(
            request,
            error=error,
            message="Your registrations are temporarily unavailable.",
        )

    entries = [
        {
            "registration": registration,
            "created_at": format_datetime(registration.created_at),
            "price": format_money(registration.price) if registration.price else None,
        }
        for registration in returned_registrations
    ]
    response = templates.TemplateResponse(
        request=request,
        name="registrations.html",
        context={
            "page": PAGE_METADATA["registrations"],
            "operational_status": "Registration service responded.",
            "registrations": entries,
            "limit": limit,
            "offset": offset,
            "has_previous": offset > 0,
            "has_next": len(returned_registrations) == limit,
            "previous_offset": max(0, offset - limit),
            "next_offset": offset + limit,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/registrations/{registration_id}", response_class=HTMLResponse)
async def registration_detail(request: Request, registration_id: UUID) -> HTMLResponse:
    session = _existing_session(request)
    identity = _participant_identity(request, session)
    try:
        registration = await request.app.state.clients.get_registration(
            registration_id,
            token=identity["token"],
            session_id=session.id,
        )
    except SERVICE_ERRORS as error:
        message = (
            "The requested registration was not found."
            if isinstance(error, ServiceError) and error.status_code == 404
            else "Registration details are temporarily unavailable."
        )
        return _service_error_page(request, error=error, message=message)
    return _registration_detail_response(
        request,
        session=session,
        registration_id=registration_id,
        registration=registration,
    )


@router.get("/registrations/{registration_id}/poll")
async def poll_registration(
    request: Request,
    registration_id: UUID,
    persona_version: Annotated[int, Query(ge=0)],
) -> JSONResponse:
    session = _existing_session(request)
    identity = _participant_identity(request, session)
    if persona_version != session.persona_version:
        response = JSONResponse(
            {"state": "persona_changed", "polling": False},
            status_code=409,
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    try:
        registration = await request.app.state.clients.get_registration(
            registration_id,
            token=identity["token"],
            session_id=session.id,
        )
    except SERVICE_ERRORS as error:
        failure = service_failure(error)
        status_code = failure.status_code
        if status_code is None or not 400 <= status_code <= 599:
            status_code = 503
        response = JSONResponse(
            {
                "state": "unavailable",
                "error": {
                    "service": failure.service,
                    "statusCode": failure.status_code,
                    "code": failure.code,
                    "correlationId": failure.correlation_id,
                },
            },
            status_code=status_code,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    response = JSONResponse(
        {
            "state": "observed",
            "registration": registration.model_dump(mode="json", by_alias=True),
            "observedAt": datetime.now(timezone.utc).isoformat(),
            "polling": _registration_is_pollable(registration),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def _registration_action(
    request: Request,
    registration_id: UUID,
    *,
    action: str,
) -> Response:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    identity = _participant_identity(request, session)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=400, detail="A valid registration form is required.")
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(status_code=400, detail="The registration form is too large.")
    try:
        fields = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=8,
        )
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(
            status_code=400, detail="A valid registration form is required."
        ) from None
    keys = fields.get("idempotency_key", [])
    if len(keys) != 1 or not 1 <= len(keys[0]) <= 200:
        raise HTTPException(status_code=400, detail="A valid registration command is required.")
    idempotency_key = keys[0]
    clients = request.app.state.clients

    async def execute_action(key, correlation_id):
        method = clients.cancel_registration if action == "cancel" else clients.accept_offer
        return await method(
            registration_id,
            token=identity["token"],
            idempotency_key=key,
            session_id=session.id,
            correlation_id=correlation_id,
        )

    try:
        outcome = await request.app.state.command_ledger.execute(
            session,
            idempotency_key=idempotency_key,
            payload={"action": action, "registrationId": str(registration_id)},
            operation=execute_action,
        )
    except CommandConflictError:
        raise HTTPException(
            status_code=409,
            detail="This command key belongs to a different registration action.",
        ) from None
    except OverflowError:
        raise HTTPException(
            status_code=429,
            detail="This browser session has reached its command limit.",
        ) from None
    except (TransportError, InvalidResponseError) as error:
        if error.outcome_unknown:
            return _registration_detail_response(
                request,
                session=session,
                registration_id=registration_id,
                retry_action=action,
                idempotency_key=idempotency_key,
                action_error=error,
                message=(
                    "The action result is unknown. Retry the same command; "
                    "the service idempotency key is retained."
                ),
                status_code=503,
            )
        return _service_error_page(
            request,
            error=error,
            message="The registration action could not be completed.",
        )
    except ServiceError as error:
        return _service_error_page(
            request,
            error=error,
            message="The registration action could not be completed.",
        )

    registration = outcome.result
    return Response(
        status_code=303,
        headers={"Location": f"/registrations/{registration.id}"},
    )


@router.post("/registrations/{registration_id}/cancel")
async def cancel_registration(request: Request, registration_id: UUID) -> Response:
    return await _registration_action(request, registration_id, action="cancel")


@router.post("/registrations/{registration_id}/accept-offer")
async def accept_registration_offer(request: Request, registration_id: UUID) -> Response:
    return await _registration_action(request, registration_id, action="accept-offer")


@router.get("/organizer", response_class=HTMLResponse)
async def organizer_page(request: Request) -> HTMLResponse:
    session = _existing_session(request)
    identity = _organizer_identity(request, session)
    return _organizer_response(request, session=session, identity=identity)


@router.post("/organizer/events")
async def organizer_create_event(request: Request) -> Response:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    identity = _organizer_identity(request, session)
    fields = await _validated_form_fields(
        request,
        allowed=frozenset(
            {"csrf_token", "idempotency_key", "title", "description", "starts_at", "ends_at"}
        ),
    )
    idempotency_key = _command_key(fields)
    try:
        event_input = EventInput(
            organizationId=identity["organizationId"],
            title=fields.get("title", ""),
            description=fields.get("description", ""),
            startsAt=fields.get("starts_at", ""),
            endsAt=fields.get("ends_at", ""),
        )
    except ValidationError:
        return _organizer_response(
            request,
            session=session,
            identity=identity,
            form_error="Event details are invalid; check required fields and the event schedule.",
            retry_action="create-event",
            retry_key=idempotency_key,
            form_values=fields,
            status_code=400,
        )

    clients = request.app.state.clients

    async def create_event(key: str, correlation_id: UUID):
        return await clients.create_event(
            event_input,
            token=identity["token"],
            idempotency_key=key,
            session_id=session.id,
            correlation_id=correlation_id,
        )

    try:
        outcome = await request.app.state.command_ledger.execute(
            session,
            idempotency_key=idempotency_key,
            payload=event_input.model_dump(mode="json", by_alias=True),
            operation=create_event,
        )
    except CommandConflictError:
        raise HTTPException(
            status_code=409,
            detail="This key belongs to a different event creation intent.",
        ) from None
    except OverflowError:
        raise HTTPException(
            status_code=429,
            detail="This browser session has reached its command limit.",
        ) from None
    except (TransportError, InvalidResponseError) as error:
        if error.outcome_unknown:
            return _organizer_response(
                request,
                session=session,
                identity=identity,
                message=(
                    "The event creation result is unknown. Retry the same step; "
                    "the command key and form values are retained."
                ),
                retry_action="create-event",
                retry_key=idempotency_key,
                form_values=fields,
                status_code=503,
            )
        return _service_error_page(
            request,
            error=error,
            message="Event creation could not be completed.",
        )
    except ServiceError as error:
        return _service_error_page(
            request,
            error=error,
            message="Event creation could not be completed.",
        )

    _record_session_event(session, outcome.result)
    return Response(status_code=303, headers={"Location": "/organizer"})


@router.post("/organizer/events/{event_id}/activities")
async def organizer_create_activity(request: Request, event_id: UUID) -> Response:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    identity = _organizer_identity(request, session)
    draft = _session_event_draft(session, event_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="The session event draft was not found.")
    fields = await _validated_form_fields(
        request,
        allowed=frozenset(
            {
                "csrf_token",
                "idempotency_key",
                "title",
                "starts_at",
                "ends_at",
                "location",
                "responsible",
                "capacity",
                "amount_minor",
                "currency",
            }
        ),
    )
    idempotency_key = _command_key(fields)
    try:
        activity_input = ActivityInput(
            title=fields.get("title", ""),
            startsAt=fields.get("starts_at", ""),
            endsAt=fields.get("ends_at", ""),
            location=fields.get("location", ""),
            responsible=fields.get("responsible", ""),
            capacity=int(fields.get("capacity", "")),
            price=Money(
                amountMinor=int(fields.get("amount_minor", "")),
                currency=fields.get("currency", ""),
            ),
        )
    except (ValidationError, ValueError):
        return _organizer_response(
            request,
            session=session,
            identity=identity,
            form_error=(
                "Activity details are invalid; check required fields, capacity, price, "
                "and the activity schedule."
            ),
            retry_action="create-activity",
            retry_event_id=event_id,
            retry_key=idempotency_key,
            form_values=fields,
            status_code=400,
        )

    clients = request.app.state.clients

    async def create_activity(key: str, correlation_id: UUID):
        return await clients.create_activity(
            event_id,
            activity_input,
            token=identity["token"],
            idempotency_key=key,
            session_id=session.id,
            correlation_id=correlation_id,
        )

    try:
        outcome = await request.app.state.command_ledger.execute(
            session,
            idempotency_key=idempotency_key,
            payload={
                "eventId": str(event_id),
                "activity": activity_input.model_dump(mode="json", by_alias=True),
            },
            operation=create_activity,
        )
    except CommandConflictError:
        raise HTTPException(
            status_code=409,
            detail="This key belongs to a different activity creation intent.",
        ) from None
    except OverflowError:
        raise HTTPException(
            status_code=429,
            detail="This browser session has reached its command limit.",
        ) from None
    except (TransportError, InvalidResponseError) as error:
        if error.outcome_unknown:
            return _organizer_response(
                request,
                session=session,
                identity=identity,
                message=(
                    "The activity creation result is unknown. Retry the same step; "
                    "the command key and form values are retained."
                ),
                retry_action="create-activity",
                retry_event_id=event_id,
                retry_key=idempotency_key,
                form_values=fields,
                status_code=503,
            )
        return _service_error_page(
            request,
            error=error,
            message="Activity creation could not be completed.",
        )
    except ServiceError as error:
        return _service_error_page(
            request,
            error=error,
            message="Activity creation could not be completed.",
        )

    with session.lock:
        draft.activities.setdefault(str(outcome.result.id), outcome.result)
    return Response(status_code=303, headers={"Location": "/organizer"})


@router.post("/organizer/events/{event_id}/publish")
async def organizer_publish_event(request: Request, event_id: UUID) -> Response:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    identity = _organizer_identity(request, session)
    draft = _session_event_draft(session, event_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="The session event draft was not found.")
    fields = await _validated_form_fields(
        request,
        allowed=frozenset({"csrf_token", "idempotency_key"}),
    )
    idempotency_key = _command_key(fields)
    clients = request.app.state.clients

    async def publish_event(key: str, correlation_id: UUID):
        return await clients.publish_event(
            event_id,
            token=identity["token"],
            idempotency_key=key,
            session_id=session.id,
            correlation_id=correlation_id,
        )

    try:
        outcome = await request.app.state.command_ledger.execute(
            session,
            idempotency_key=idempotency_key,
            payload={"action": "publish", "eventId": str(event_id)},
            operation=publish_event,
        )
    except CommandConflictError:
        raise HTTPException(
            status_code=409,
            detail="This key belongs to a different publish intent.",
        ) from None
    except OverflowError:
        raise HTTPException(
            status_code=429,
            detail="This browser session has reached its command limit.",
        ) from None
    except (TransportError, InvalidResponseError) as error:
        if error.outcome_unknown:
            return _organizer_response(
                request,
                session=session,
                identity=identity,
                message=(
                    "The publish result is unknown. Retry the same step; "
                    "the command key is retained."
                ),
                retry_action="publish-event",
                retry_event_id=event_id,
                retry_key=idempotency_key,
                status_code=503,
            )
        return _service_error_page(
            request,
            error=error,
            message="The event could not be published.",
        )
    except ServiceError as error:
        return _service_error_page(
            request,
            error=error,
            message="The event could not be published.",
        )

    with session.lock:
        draft.event = outcome.result
    return Response(status_code=303, headers={"Location": "/organizer"})


@router.get("/session")
async def session_info(request: Request, response: Response) -> dict[str, str]:
    store = _session_store(request)
    session, is_new = _session_or_create(request)
    _set_cookie_if_new(response, is_new=is_new, session=session, store=store)
    identity = store.identity_for(session)
    result = {
        "persona": session.persona,
        "csrf_token": session.csrf_token,
    }
    response.headers["Cache-Control"] = "no-store"
    if identity is not None:
        result["role"] = identity["role"]
    return result


@router.post("/persona/{persona}")
async def select_persona(request: Request, persona: str) -> Response:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    try:
        _session_store(request).set_persona(session, persona)
    except InvalidPersonaError:
        raise HTTPException(status_code=400, detail="The selected persona is invalid.") from None
    return Response(status_code=303, headers={"Location": "/"})

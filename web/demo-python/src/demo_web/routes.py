import asyncio
import secrets
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from demo_web.commands import CommandConflictError
from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.models import (
    Activity,
    Certificate,
    CheckIn,
)
from demo_web.presentation import (
    PAGE_METADATA,
    PERSONA_LABELS,
    SIGN_IN_GROUPS,
    format_datetime,
    format_money,
    format_observation_age,
    service_failure,
    templates,
)
from demo_web.sessions import (
    BrowserSession,
    InvalidPersonaError,
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
ROLE_HOMES = {"ORGANIZER": "/organizer", "PARTICIPANT": "/events"}


class SignInRequired(Exception):
    """Raised when a page needs a different (or any) synthetic user; redirects to location."""

    def __init__(self, location: str = "/"):
        super().__init__(location)
        self.location = location


def _session_store(request: Request) -> SessionStore:
    return request.app.state.sessions


def _existing_session(request: Request) -> BrowserSession:
    session = _session_store(request).get(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=403, detail="A valid demo session is required.")
    return session


def role_home(request: Request, session: BrowserSession) -> str:
    identity = _session_store(request).identity_for(session)
    return ROLE_HOMES.get(identity.get("role", "") if identity else "", "/")


def _require_role(role: str):
    def dependency(request: Request) -> None:
        session = _session_store(request).get(request.cookies.get(SESSION_COOKIE))
        if session is None or not session.signed_in:
            raise SignInRequired()
        identity = _session_store(request).identity_for(session)
        if identity is None or identity.get("role") != role:
            raise SignInRequired(role_home(request, session))

    return dependency


ORGANIZER_PAGE = [Depends(_require_role("ORGANIZER"))]
PARTICIPANT_PAGE = [Depends(_require_role("PARTICIPANT"))]


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
    check_in_observation: CheckIn | None = None,
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
    check_in_key = (
        idempotency_key
        if retry_action == "check-in"
        else secrets.token_urlsafe(32)
        if has_mutable_status and registration.status == "CONFIRMED"
        else None
    )
    certificate_key = (
        idempotency_key
        if retry_action == "certificate"
        else secrets.token_urlsafe(32)
        if has_mutable_status and registration.status == "CONFIRMED"
        else None
    )
    response = templates.TemplateResponse(
        request=request,
        name="registration_detail.html",
        context={
            "page": PAGE_METADATA["registration_detail"],
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
            "check_in_key": check_in_key,
            "certificate_key": certificate_key,
            "check_in_observation": check_in_observation,
            "check_in_observed_at": (
                format_datetime(check_in_observation.checked_in_at)
                if check_in_observation is not None
                else None
            ),
            "can_check_in": retry_action == "check-in"
            or registration is not None
            and registration.status == "CONFIRMED",
            "can_issue_certificate": retry_action == "certificate"
            or registration is not None
            and registration.status == "CONFIRMED",
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
async def home(request: Request) -> Response:
    store = _session_store(request)
    session, is_new = _session_or_create(request)
    if session.signed_in:
        return Response(
            status_code=303,
            headers={"Location": role_home(request, session), "Cache-Control": "no-store"},
        )
    response = templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "page": PAGE_METADATA["home"],
            "sign_in_groups": SIGN_IN_GROUPS,
            "persona_labels": PERSONA_LABEL_BY_ID,
            "csrf_token": session.csrf_token,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    _set_cookie_if_new(response, is_new=is_new, session=session, store=store)
    return response


@router.get("/events", response_class=HTMLResponse, dependencies=PARTICIPANT_PAGE)
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


@router.get("/events/{event_id}", response_class=HTMLResponse, dependencies=PARTICIPANT_PAGE)
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

    response = templates.TemplateResponse(
        request=request,
        name="event_detail.html",
        context={
            "page": PAGE_METADATA["event_detail"],
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


@router.get(
    "/activities/{activity_id}/checkout",
    response_class=HTMLResponse,
    dependencies=PARTICIPANT_PAGE,
)
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


@router.get("/registrations", response_class=HTMLResponse, dependencies=PARTICIPANT_PAGE)
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

    activity_ids = list(dict.fromkeys(item.activity_id for item in returned_registrations))
    activities = await asyncio.gather(
        *(
            request.app.state.clients.get_activity(activity_id, session_id=session.id)
            for activity_id in activity_ids
        ),
        return_exceptions=True,
    )
    titles = {
        activity_id: activity.title
        for activity_id, activity in zip(activity_ids, activities)
        if isinstance(activity, Activity)
    }
    entries = [
        {
            "registration": registration,
            "activity_title": titles.get(registration.activity_id),
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


@router.get(
    "/registrations/{registration_id}",
    response_class=HTMLResponse,
    dependencies=PARTICIPANT_PAGE,
)
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
    with session.lock:
        check_in_observation = session.attendance_flashes.pop(str(registration_id), None)
    return _registration_detail_response(
        request,
        session=session,
        registration_id=registration_id,
        registration=registration,
        check_in_observation=check_in_observation,
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


async def _attendance_action(
    request: Request,
    registration_id: UUID,
    *,
    action: str,
) -> Response:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    identity = _participant_identity(request, session)
    fields = await _validated_form_fields(
        request,
        allowed=frozenset({"csrf_token", "idempotency_key"}),
    )
    idempotency_key = _command_key(fields)
    clients = request.app.state.clients

    async def execute_action(key: str, correlation_id: UUID):
        if action == "check-in":
            return await clients.check_in(
                registration_id,
                token=identity["token"],
                idempotency_key=key,
                session_id=session.id,
                correlation_id=correlation_id,
            )
        return await clients.issue_certificate(
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
            detail="This key belongs to a different Attendance action.",
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
                    "The Attendance command result is unknown. Retry the same command; "
                    "do not infer eligibility or success."
                ),
                status_code=503,
            )
        return _service_error_page(
            request,
            error=error,
            message="The Attendance action could not be completed.",
        )
    except ServiceError as error:
        return _service_error_page(
            request,
            error=error,
            message="The Attendance action could not be completed.",
        )

    result = outcome.result
    if action == "check-in":
        with session.lock:
            session.attendance_flashes[str(registration_id)] = result
            while (
                len(session.attendance_flashes)
                > request.app.state.settings.max_commands_per_session
            ):
                session.attendance_flashes.popitem(last=False)
        location = f"/registrations/{registration_id}"
    else:
        location = f"/registrations/{registration_id}/certificate"
    return Response(status_code=303, headers={"Location": location})


@router.post("/registrations/{registration_id}/check-in")
async def check_in_registration(request: Request, registration_id: UUID) -> Response:
    return await _attendance_action(request, registration_id, action="check-in")


@router.post("/registrations/{registration_id}/certificate")
async def issue_registration_certificate(
    request: Request,
    registration_id: UUID,
) -> Response:
    return await _attendance_action(request, registration_id, action="certificate")


@router.get(
    "/registrations/{registration_id}/certificate",
    response_class=HTMLResponse,
    dependencies=PARTICIPANT_PAGE,
)
async def registration_certificate(request: Request, registration_id: UUID) -> HTMLResponse:
    session = _existing_session(request)
    identity = _participant_identity(request, session)
    clients = request.app.state.clients
    try:
        certificate: Certificate = await clients.get_certificate(
            registration_id,
            token=identity["token"],
            session_id=session.id,
        )
    except SERVICE_ERRORS as error:
        message = (
            "The requested certificate was not found."
            if isinstance(error, ServiceError) and error.status_code == 404
            else "The certificate record is temporarily unavailable."
        )
        return _service_error_page(request, error=error, message=message)

    activity = None
    activity_error = None
    try:
        activity = await clients.get_activity(
            certificate.activity_id,
            session_id=session.id,
        )
    except SERVICE_ERRORS as error:
        activity_error = service_failure(error)
    response = templates.TemplateResponse(
        request=request,
        name="certificate.html",
        context={
            "page": PAGE_METADATA["certificate"],
            "certificate": certificate,
            "issued_at": format_datetime(certificate.issued_at),
            "activity": activity,
            "activity_error": activity_error,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


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
    return Response(status_code=303, headers={"Location": role_home(request, session)})


@router.post("/logout")
async def sign_out(request: Request) -> Response:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    _session_store(request).sign_out(session)
    return Response(status_code=303, headers={"Location": "/"})

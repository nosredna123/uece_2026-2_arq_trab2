import secrets
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import parse_qs
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from demo_web.commands import CommandConflictError
from demo_web.demo import DemoPreparation, countdown_label, create_preparation
from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.models import (
    Activity,
    Certificate,
    CheckIn,
    Event,
)
from demo_web.presentation import (
    ORGANIZER_PERSONAS,
    PAGE_METADATA,
    PERSONA_LABELS,
    SIGN_IN_GROUPS,
    format_datetime,
    format_money,
    format_observation_age,
    service_failure,
    templates,
)
from demo_web.scenarios import (
    PRESETS,
    PresetUnavailableError,
    ScenarioAccessError,
    ScenarioConflictError,
    ScenarioDisabledError,
    ScenarioManager,
    ScenarioStateError,
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


def _require_sign_in(request: Request) -> None:
    session = _session_store(request).get(request.cookies.get(SESSION_COOKIE))
    if session is None or not session.signed_in:
        raise SignInRequired()


SIGNED_IN_PAGE = [Depends(_require_sign_in)]


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


def _scenario_manager(request: Request, session: BrowserSession) -> ScenarioManager:
    _organizer_identity(request, session)
    if session.persona != "organizer":
        raise HTTPException(
            status_code=403,
            detail="Scenario Lab records are scoped to the GESAD/UECE synthetic organization.",
        )
    manager = getattr(request.app.state, "scenario_manager", None)
    if manager is None:
        raise HTTPException(status_code=404, detail="Scenario Lab is not available.")
    return manager


def _scenario_error(error: Exception) -> HTTPException:
    if isinstance(error, ScenarioAccessError):
        return HTTPException(status_code=404, detail="The requested run is not available.")
    if isinstance(
        error,
        (ScenarioConflictError, PresetUnavailableError, ScenarioStateError),
    ):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, ScenarioDisabledError):
        return HTTPException(status_code=404, detail="Scenario Lab is not available.")
    raise error


def _demo_response(
    request: Request,
    *,
    session: BrowserSession,
    run: DemoPreparation | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    now = datetime.now(timezone.utc)
    with session.lock:
        if session.demo_start_key is None:
            session.demo_start_key = secrets.token_urlsafe(32)
        new_run_key = session.demo_start_key
        runs = list(session.demo_runs.values())

    activity_cards = []
    stale_warnings = []
    if run is not None:
        for label, configured_activity, returned_activity in (
            ("Paid activity", run.paid_activity_input, run.paid_activity),
            ("Free waiting-list activity", run.free_activity_input, run.free_activity),
        ):
            activity = returned_activity or configured_activity
            activity_cards.append(
                {
                    "label": label,
                    "activity": activity,
                    "returned": returned_activity is not None,
                    "starts_at": format_datetime(activity.starts_at),
                    "ends_at": format_datetime(activity.ends_at),
                    "price": format_money(activity.price),
                    "countdown": countdown_label(
                        activity.starts_at,
                        activity.ends_at,
                        now=now,
                    ),
                }
            )
            if returned_activity is not None and now >= returned_activity.ends_at:
                stale_warnings.append(f"{label} window has closed.")

    steps = []
    if run is not None:
        steps = [
            ("Event created", run.event is not None),
            ("Paid activity created", run.paid_activity is not None),
            ("Free waiting-list activity created", run.free_activity is not None),
            ("Event published", run.published_event is not None),
        ]
    response = templates.TemplateResponse(
        request=request,
        name="demo.html",
        context={
            "page": PAGE_METADATA["demo"],
            "session": session,
            "csrf_token": session.csrf_token,
            "can_prepare": session.persona in ORGANIZER_PERSONAS,
            "new_run_key": new_run_key,
            "run": run,
            "runs": runs,
            "steps": steps,
            "activity_cards": activity_cards,
            "stale_warnings": stale_warnings,
            "failure": run.failure if run is not None else None,
            "failure_step": run.failure_step if run is not None else None,
            "failure_retryable": run.failure_retryable if run is not None else False,
            "now": now,
        },
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


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


@router.get("/demo", response_class=HTMLResponse, dependencies=SIGNED_IN_PAGE)
async def guided_demo(
    request: Request,
    run_id: Annotated[UUID | None, Query()] = None,
) -> HTMLResponse:
    store = _session_store(request)
    session, is_new = _session_or_create(request)
    run = None
    with session.lock:
        if run_id is not None:
            run = session.demo_runs.get(str(run_id))
            if run is None:
                raise HTTPException(status_code=404, detail="The preparation run was not found.")
        elif session.demo_runs:
            run = next(reversed(session.demo_runs.values()))
    response = _demo_response(request, session=session, run=run)
    _set_cookie_if_new(response, is_new=is_new, session=session, store=store)
    return response


@router.post("/demo/prepare")
async def prepare_demo(request: Request) -> Response:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    identity = _organizer_identity(request, session)
    fields = await _validated_form_fields(
        request,
        allowed=frozenset({"csrf_token", "idempotency_key", "intent", "confirm_new_run", "run_id"}),
    )
    intent = fields.get("intent")

    if intent == "new":
        if fields.get("confirm_new_run") != "yes":
            raise HTTPException(
                status_code=400,
                detail="Confirm explicitly before preparing a new run.",
            )
        intent_key = _command_key(fields)
        with session.lock:
            run_id_value = session.demo_intents.get(intent_key)
            run = session.demo_runs.get(run_id_value) if run_id_value is not None else None
            if run is None:
                pending_demo_steps = sum(
                    sum(
                        result is None
                        for result in (
                            preparation.event,
                            preparation.paid_activity,
                            preparation.free_activity,
                            preparation.published_event,
                        )
                    )
                    for preparation in session.demo_runs.values()
                )
                if (
                    intent_key != session.demo_start_key
                    or len(session.commands) + pending_demo_steps + 4
                    > request.app.state.settings.max_commands_per_session
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Refresh the guide and explicitly confirm a new preparation.",
                    )
                run = create_preparation(
                    run_id=uuid4(),
                    organization_id=UUID(identity["organizationId"]),
                    organization_label=ORGANIZER_PERSONAS[session.persona],
                    settings=request.app.state.settings,
                    now=datetime.now(timezone.utc),
                )
                run_id_value = str(run.run_id)
                session.demo_runs[run_id_value] = run
                session.demo_intents[intent_key] = run_id_value
                session.demo_intents.move_to_end(intent_key)
                session.demo_start_key = None
    elif intent == "resume":
        try:
            run_id_value = str(UUID(fields.get("run_id", "")))
        except ValueError:
            raise HTTPException(
                status_code=400, detail="A valid preparation run is required."
            ) from None
        with session.lock:
            run = session.demo_runs.get(run_id_value)
        if run is None:
            raise HTTPException(status_code=404, detail="The preparation run was not found.")
    else:
        raise HTTPException(status_code=400, detail="Choose a valid preparation action.")

    if run is None:
        raise RuntimeError("The preparation intent was not retained in the browser session.")
    if run.complete:
        return Response(
            status_code=303,
            headers={"Location": f"/demo?run_id={run.run_id}"},
        )

    clients = request.app.state.clients
    run.failure = None
    run.failure_step = None
    run.failure_retryable = False

    try:
        if run.event is None:

            async def create_event(key: str, correlation_id: UUID):
                return await clients.create_event(
                    run.event_input,
                    token=identity["token"],
                    idempotency_key=key,
                    session_id=session.id,
                    correlation_id=correlation_id,
                )

            outcome = await request.app.state.command_ledger.execute(
                session,
                idempotency_key=run.command_keys["event"],
                payload=run.event_input.model_dump(mode="json", by_alias=True),
                operation=create_event,
            )
            if not isinstance(outcome.result, Event):
                raise RuntimeError("The event client returned an invalid preparation result.")
            with session.lock:
                run.event = outcome.result

        if run.paid_activity is None:

            async def create_paid_activity(key: str, correlation_id: UUID):
                return await clients.create_activity(
                    run.event.id,
                    run.paid_activity_input,
                    token=identity["token"],
                    idempotency_key=key,
                    session_id=session.id,
                    correlation_id=correlation_id,
                )

            outcome = await request.app.state.command_ledger.execute(
                session,
                idempotency_key=run.command_keys["paid_activity"],
                payload={
                    "eventId": str(run.event.id),
                    "activity": run.paid_activity_input.model_dump(mode="json", by_alias=True),
                },
                operation=create_paid_activity,
            )
            if not isinstance(outcome.result, Activity):
                raise RuntimeError("The activity client returned an invalid preparation result.")
            with session.lock:
                run.paid_activity = outcome.result

        if run.free_activity is None:

            async def create_free_activity(key: str, correlation_id: UUID):
                return await clients.create_activity(
                    run.event.id,
                    run.free_activity_input,
                    token=identity["token"],
                    idempotency_key=key,
                    session_id=session.id,
                    correlation_id=correlation_id,
                )

            outcome = await request.app.state.command_ledger.execute(
                session,
                idempotency_key=run.command_keys["free_activity"],
                payload={
                    "eventId": str(run.event.id),
                    "activity": run.free_activity_input.model_dump(mode="json", by_alias=True),
                },
                operation=create_free_activity,
            )
            if not isinstance(outcome.result, Activity):
                raise RuntimeError("The activity client returned an invalid preparation result.")
            with session.lock:
                run.free_activity = outcome.result

        if run.published_event is None:

            async def publish_event(key: str, correlation_id: UUID):
                return await clients.publish_event(
                    run.event.id,
                    token=identity["token"],
                    idempotency_key=key,
                    session_id=session.id,
                    correlation_id=correlation_id,
                )

            outcome = await request.app.state.command_ledger.execute(
                session,
                idempotency_key=run.command_keys["publish"],
                payload={"eventId": str(run.event.id), "action": "publish"},
                operation=publish_event,
            )
            if not isinstance(outcome.result, Event):
                raise RuntimeError("The publish client returned an invalid preparation result.")
            with session.lock:
                run.event = outcome.result
                run.published_event = outcome.result
    except CommandConflictError:
        raise HTTPException(
            status_code=409,
            detail="This preparation command was invalidated; start a fresh run explicitly.",
        ) from None
    except OverflowError:
        raise HTTPException(
            status_code=429,
            detail="This browser session has reached its command limit.",
        ) from None
    except SERVICE_ERRORS as error:
        run.failure = service_failure(error)
        run.failure_step = (
            "event creation"
            if run.event is None
            else "paid activity creation"
            if run.paid_activity is None
            else "free activity creation"
            if run.free_activity is None
            else "event publication"
        )
        run.failure_retryable = error.outcome_unknown
        status_code = 503 if error.outcome_unknown else error.status_code or 502
        return _demo_response(
            request,
            session=session,
            run=run,
            status_code=status_code,
        )

    return Response(
        status_code=303,
        headers={"Location": f"/demo?run_id={run.run_id}"},
    )


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


@router.get("/scenario-lab", response_class=HTMLResponse, dependencies=SIGNED_IN_PAGE)
async def scenario_lab_page(request: Request) -> HTMLResponse:
    store = _session_store(request)
    session, is_new = _session_or_create(request)
    manager = getattr(request.app.state, "scenario_manager", None)
    identity = store.identity_for(session)
    configured = manager is not None
    tenant_allowed = session.persona == "organizer"
    enabled = configured and tenant_allowed
    unavailable_reason = (
        "Scenario Lab is disabled by configuration."
        if not configured
        else "Scenario Lab requires the GESAD/UECE organizer persona."
    )
    presets = [
        manager.preview(preset_key)
        if enabled
        else {
            "preset": preset.key,
            "title": preset.title,
            "participants": preset.participants,
            "capacity": preset.capacity,
            "submissionsPerParticipant": preset.submissions_per_participant,
            "concurrency": preset.required_concurrency,
            "priceMinor": preset.price_minor,
            "available": False,
            "unavailableReason": unavailable_reason,
        }
        for preset_key, preset in PRESETS.items()
    ]
    presets.append(
        {
            "preset": "seat-opens",
            "title": "A seat opens",
            "participants": 0,
            "capacity": 0,
            "submissionsPerParticipant": 0,
            "concurrency": 0,
            "priceMinor": 0,
            "available": enabled,
            "unavailableReason": None if enabled else unavailable_reason,
        }
    )
    timeout = getattr(request.app.state.settings, "lab_run_timeout_seconds", None) or 60
    response = templates.TemplateResponse(
        request=request,
        name="lab.html",
        context={
            "page": PAGE_METADATA["lab"],
            "lab_unavailable_reason": unavailable_reason,
            "lab_enabled": enabled,
            "can_run": enabled and identity is not None and identity["role"] == "ORGANIZER",
            "csrf_token": session.csrf_token,
            "presets": presets,
            "runs": manager.snapshots_for(session.id) if enabled else [],
            "max_poll_attempts": min(90, timeout + 10),
        },
    )
    response.headers["Cache-Control"] = "no-store"
    _set_cookie_if_new(response, is_new=is_new, session=session, store=store)
    return response


@router.get("/scenario-lab/presets/{preset}")
async def scenario_preview(request: Request, preset: str) -> JSONResponse:
    session = _existing_session(request)
    manager = _scenario_manager(request, session)
    try:
        result = manager.preview(preset)
    except (
        ScenarioAccessError,
        ScenarioConflictError,
        PresetUnavailableError,
        ScenarioDisabledError,
        ScenarioStateError,
    ) as error:
        raise _scenario_error(error) from None
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/scenario-lab/runs")
async def scenario_runs(request: Request) -> JSONResponse:
    session = _existing_session(request)
    manager = _scenario_manager(request, session)
    response = JSONResponse({"runs": manager.snapshots_for(session.id)})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/scenario-lab/runs/{preset}")
async def start_scenario(request: Request, preset: str) -> JSONResponse:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    manager = _scenario_manager(request, session)
    try:
        result = await manager.start(session.id, preset)
    except (
        ScenarioAccessError,
        ScenarioConflictError,
        PresetUnavailableError,
        ScenarioDisabledError,
        ScenarioStateError,
    ) as error:
        raise _scenario_error(error) from None
    response = JSONResponse(result, status_code=202)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/scenario-lab/runs/{run_id:uuid}")
async def scenario_snapshot(request: Request, run_id: UUID) -> JSONResponse:
    session = _existing_session(request)
    manager = _scenario_manager(request, session)
    result = manager.snapshot(session.id, str(run_id))
    if result is None:
        raise HTTPException(status_code=404, detail="The requested run is not available.")
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/scenario-lab/runs/{run_id:uuid}/export")
async def export_scenario(request: Request, run_id: UUID) -> JSONResponse:
    session = _existing_session(request)
    manager = _scenario_manager(request, session)
    result = manager.export(session.id, str(run_id))
    if result is None:
        raise HTTPException(status_code=404, detail="The requested run is not available.")
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/scenario-lab/runs/{run_id:uuid}/stop")
async def stop_scenario(request: Request, run_id: UUID) -> JSONResponse:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    manager = _scenario_manager(request, session)
    try:
        result = await manager.stop(session.id, str(run_id))
    except (
        ScenarioAccessError,
        ScenarioConflictError,
        PresetUnavailableError,
        ScenarioDisabledError,
        ScenarioStateError,
    ) as error:
        raise _scenario_error(error) from None
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/scenario-lab/runs/{run_id:uuid}/cancel-confirmed")
async def cancel_scenario_registration(request: Request, run_id: UUID) -> JSONResponse:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    manager = _scenario_manager(request, session)
    try:
        result = await manager.cancel_confirmed(session.id, str(run_id))
    except (
        ScenarioAccessError,
        ScenarioConflictError,
        PresetUnavailableError,
        ScenarioDisabledError,
        ScenarioStateError,
    ) as error:
        raise _scenario_error(error) from None
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/scenario-lab/runs/{run_id:uuid}/accept-offer")
async def accept_scenario_offer(request: Request, run_id: UUID) -> JSONResponse:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    manager = _scenario_manager(request, session)
    try:
        result = await manager.accept_offer(session.id, str(run_id))
    except (
        ScenarioAccessError,
        ScenarioConflictError,
        PresetUnavailableError,
        ScenarioDisabledError,
        ScenarioStateError,
    ) as error:
        raise _scenario_error(error) from None
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    return response


def _reject_if_scenario_running(request: Request, session: BrowserSession, detail: str) -> None:
    manager = getattr(request.app.state, "scenario_manager", None)
    if manager is not None and manager.is_running_for(session.id):
        raise HTTPException(status_code=409, detail=detail)


@router.post("/persona/{persona}")
async def select_persona(request: Request, persona: str) -> Response:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    _reject_if_scenario_running(
        request,
        session,
        "Persona cannot change while this session owns an active Scenario Lab run.",
    )
    try:
        _session_store(request).set_persona(session, persona)
    except InvalidPersonaError:
        raise HTTPException(status_code=400, detail="The selected persona is invalid.") from None
    return Response(status_code=303, headers={"Location": role_home(request, session)})


@router.post("/logout")
async def sign_out(request: Request) -> Response:
    session = _existing_session(request)
    await _validate_csrf(request, session)
    _reject_if_scenario_running(
        request,
        session,
        "Sign out is unavailable while this session owns an active Scenario Lab run.",
    )
    _session_store(request).sign_out(session)
    return Response(status_code=303, headers={"Location": "/"})

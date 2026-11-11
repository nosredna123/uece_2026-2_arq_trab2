import secrets
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import parse_qs
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from demo_web.commands import CommandConflictError
from demo_web.demo import DemoPreparation, countdown_label, create_preparation
from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.models import (
    Activity,
    ActivityInput,
    Certificate,
    CheckIn,
    Event,
    EventInput,
    Money,
    Report,
)
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
    ReportObservation,
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
            "operational_status": (
                "Preview only; no service mutation has been requested."
                if run is None
                else "Event Management published this preparation."
                if run.complete
                else "Preparation progress is retained in this browser session."
            ),
            "session": session,
            "csrf_token": session.csrf_token,
            "can_prepare": session.persona == "organizer",
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


def _report_cache_key(
    organization_id: str,
    *,
    activity_id: UUID | None,
    limit: int,
    offset: int,
) -> str:
    return f"{organization_id}:{activity_id or ''}:{limit}:{offset}"


def _remember_report_observation(
    session: BrowserSession,
    *,
    cache_key: str,
    reports: list[Report],
    observed_at: datetime,
    max_observations: int,
) -> ReportObservation:
    observation = ReportObservation(tuple(reports), observed_at)
    with session.lock:
        session.report_observations[cache_key] = observation
        session.report_observations.move_to_end(cache_key)
        while len(session.report_observations) > max_observations:
            session.report_observations.popitem(last=False)
    return observation


def _report_response(
    request: Request,
    *,
    session: BrowserSession,
    identity: dict[str, str],
    limit: int,
    offset: int,
    activity_id: UUID | None,
    observation: ReportObservation | None,
    failure: ServiceError | TransportError | InvalidResponseError | None = None,
) -> HTMLResponse:
    now = datetime.now(timezone.utc)
    reports = list(observation.reports) if observation is not None else []
    report_rows = []
    for report in reports:
        age_label, age_stale = format_observation_age(report.projected_at, now=now)
        report_rows.append(
            {
                "report": report,
                "projected_at": format_datetime(report.projected_at),
                "age_label": age_label,
                "stale": failure is not None or age_stale,
            }
        )
    service_error = service_failure(failure) if failure is not None else None
    has_projection = observation is not None
    operational_status = (
        "Reporting unavailable; showing the last observed projection."
        if failure is not None and has_projection
        else "Reporting projection unavailable."
        if failure is not None
        else "Reporting responded with an empty projection."
        if not reports
        else "Reporting responded; the event-fed projection may lag."
    )
    response = templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={
            "page": PAGE_METADATA["reports"],
            "operational_status": operational_status,
            "csrf_token": session.csrf_token,
            "persona_version": session.persona_version,
            "poll_interval_ms": request.app.state.settings.poll_interval_seconds * 1000,
            "poll_timeout_ms": request.app.state.settings.poll_timeout_seconds * 1000,
            "organization_id": identity["organizationId"],
            "limit": limit,
            "offset": offset,
            "activity_id": str(activity_id) if activity_id is not None else "",
            "entries": request.app.state.clients.history_for(session.id),
            "report_rows": report_rows,
            "has_projection": has_projection,
            "empty_projection": has_projection and not reports and failure is None,
            "observation_age": (
                format_datetime(observation.observed_at) if observation is not None else None
            ),
            "service_failure": service_error,
            "has_previous": offset > 0,
            "has_next": len(reports) == limit,
            "previous_offset": max(0, offset - limit),
            "next_offset": offset + limit,
        },
        status_code=(
            200
            if has_projection
            else failure.status_code
            if failure is not None
            and failure.status_code is not None
            and 400 <= failure.status_code <= 599
            else 503
        ),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _report_json(report: Report) -> dict[str, object]:
    return report.model_dump(mode="json", by_alias=True)


def _report_failure_json(error) -> dict[str, object]:
    failure = service_failure(error)
    return {
        "service": failure.service,
        "statusCode": failure.status_code,
        "code": failure.code,
        "correlationId": failure.correlation_id,
    }


@router.get("/reports", response_class=HTMLResponse)
async def organization_reports(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    activity_id: Annotated[UUID | None, Query(alias="activityId")] = None,
) -> HTMLResponse:
    session = _existing_session(request)
    identity = _organizer_identity(request, session)
    organization_id = identity["organizationId"]
    cache_key = _report_cache_key(
        organization_id,
        activity_id=activity_id,
        limit=limit,
        offset=offset,
    )
    try:
        reports = await request.app.state.clients.get_reports(
            organization_id,
            token=identity["token"],
            activity_id=activity_id,
            limit=limit,
            offset=offset,
            session_id=session.id,
        )
    except SERVICE_ERRORS as error:
        with session.lock:
            observation = session.report_observations.get(cache_key)
        return _report_response(
            request,
            session=session,
            identity=identity,
            limit=limit,
            offset=offset,
            activity_id=activity_id,
            observation=observation,
            failure=error,
        )

    observation = _remember_report_observation(
        session,
        cache_key=cache_key,
        reports=reports,
        observed_at=datetime.now(timezone.utc),
        max_observations=request.app.state.settings.max_commands_per_session,
    )
    return _report_response(
        request,
        session=session,
        identity=identity,
        limit=limit,
        offset=offset,
        activity_id=activity_id,
        observation=observation,
    )


@router.get("/reports/poll")
async def poll_organization_reports(
    request: Request,
    persona_version: Annotated[int, Query(ge=0)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    activity_id: Annotated[UUID | None, Query(alias="activityId")] = None,
) -> JSONResponse:
    session = _existing_session(request)
    identity = _organizer_identity(request, session)
    if persona_version != session.persona_version:
        return JSONResponse(
            {"state": "persona_changed"},
            status_code=409,
            headers={"Cache-Control": "no-store"},
        )

    organization_id = identity["organizationId"]
    cache_key = _report_cache_key(
        organization_id,
        activity_id=activity_id,
        limit=limit,
        offset=offset,
    )
    try:
        reports = await request.app.state.clients.get_reports(
            organization_id,
            token=identity["token"],
            activity_id=activity_id,
            limit=limit,
            offset=offset,
            session_id=session.id,
        )
    except SERVICE_ERRORS as error:
        with session.lock:
            observation = session.report_observations.get(cache_key)
        failure = service_failure(error)
        status_code = (
            error.status_code
            if error.status_code is not None and 400 <= error.status_code <= 599
            else 503
        )
        return JSONResponse(
            {
                "state": "unavailable",
                "stale": observation is not None,
                "reports": (
                    [_report_json(report) for report in observation.reports]
                    if observation is not None
                    else []
                ),
                "observedAt": (
                    observation.observed_at.isoformat() if observation is not None else None
                ),
                "error": {
                    "service": failure.service,
                    "statusCode": failure.status_code,
                    "code": failure.code,
                    "correlationId": failure.correlation_id,
                },
            },
            status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )

    observation = _remember_report_observation(
        session,
        cache_key=cache_key,
        reports=reports,
        observed_at=datetime.now(timezone.utc),
        max_observations=request.app.state.settings.max_commands_per_session,
    )
    return JSONResponse(
        {
            "state": "observed",
            "stale": False,
            "reports": [_report_json(report) for report in observation.reports],
            "observedAt": observation.observed_at.isoformat(),
        },
        headers={"Cache-Control": "no-store"},
    )


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


@router.get("/demo", response_class=HTMLResponse)
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
            "operational_status": "Attendance returned an issued certificate record.",
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
    return Response(status_code=303, headers={"Location": "/"})

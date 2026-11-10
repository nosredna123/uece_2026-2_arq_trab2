import secrets
from typing import Annotated
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse

from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.presentation import (
    PAGE_METADATA,
    PERSONA_LABELS,
    format_datetime,
    format_money,
    format_observation_age,
    service_failure,
    templates,
)
from demo_web.sessions import BrowserSession, InvalidPersonaError, SessionStore


router = APIRouter()
SESSION_COOKIE = "demo_session"
PERSONA_LABEL_BY_ID = dict(PERSONA_LABELS)
MAX_CATALOG_LIMIT = 50
MAX_ACTIVITY_LIMIT = 20
MAX_PAGE_OFFSET = 10000
SERVICE_ERRORS = (ServiceError, TransportError, InvalidResponseError)


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
            max_num_fields=8,
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

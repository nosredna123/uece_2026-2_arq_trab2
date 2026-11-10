import secrets
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from demo_web.presentation import PAGE_METADATA, PERSONA_LABELS, templates
from demo_web.sessions import BrowserSession, InvalidPersonaError, SessionStore


router = APIRouter()
SESSION_COOKIE = "demo_session"
PERSONA_LABEL_BY_ID = dict(PERSONA_LABELS)


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
    if is_new:
        _set_session_cookie(response, session, store)
    return response


@router.get("/session")
async def session_info(request: Request, response: Response) -> dict[str, str]:
    store = _session_store(request)
    session, is_new = _session_or_create(request)
    if is_new:
        _set_session_cookie(response, session, store)
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

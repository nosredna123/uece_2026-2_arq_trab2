import asyncio
import json
from contextlib import asynccontextmanager, suppress
from typing import Mapping

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response as StarletteResponse
from starlette.staticfiles import StaticFiles

from demo_web.clients import DemoClients
from demo_web.commands import CommandLedger
from demo_web.presentation import PAGE_METADATA, STATIC_DIRECTORY, templates
from demo_web.organizer import router as organizer_router
from demo_web.routes import SignInRequired, router
from demo_web.seed import run_seed
from demo_web.sessions import SessionStore
from demo_web.settings import Settings


def create_app(environ: Mapping[str, str] | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = Settings.from_environment(environ)
        fixtures = json.loads(app.state.settings.identity_fixtures_path.read_text(encoding="utf-8"))
        app.state.sessions = SessionStore(
            fixtures,
            ttl_seconds=app.state.settings.session_ttl_seconds,
            max_sessions=app.state.settings.max_sessions,
        )
        app.state.clients = DemoClients(app.state.settings)
        try:
            app.state.command_ledger = CommandLedger(
                max_commands_per_session=app.state.settings.max_commands_per_session
            )
            if app.state.settings.seed_data_path is not None:
                app.state.seed_task = asyncio.create_task(
                    run_seed(app.state.settings, fixtures), name="initial-presentation-data"
                )
            yield
        finally:
            seed_task = getattr(app.state, "seed_task", None)
            if seed_task is not None and not seed_task.done():
                seed_task.cancel()
                with suppress(asyncio.CancelledError):
                    await seed_task
            await app.state.clients.aclose()

    app = FastAPI(title="EventHub Demo", lifespan=lifespan)
    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIRECTORY)),
        name="static",
    )
    app.include_router(router)
    app.include_router(organizer_router)

    @app.exception_handler(SignInRequired)
    async def redirect_to_sign_in(request: Request, exception: SignInRequired) -> StarletteResponse:
        return StarletteResponse(
            status_code=303,
            headers={"Location": exception.location, "Cache-Control": "no-store"},
        )

    @app.exception_handler(StarletteHTTPException)
    async def render_html_http_error(
        request: Request, exception: StarletteHTTPException
    ) -> StarletteResponse:
        if "text/html" not in request.headers.get("accept", "").lower():
            return await http_exception_handler(request, exception)

        messages = {
            400: "The request could not be completed.",
            403: "The request was not authorized.",
            404: "The requested page was not found.",
            405: "This action is not available here.",
        }
        response = templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "page": PAGE_METADATA["error"],
                "status_code": exception.status_code,
                "message": messages.get(
                    exception.status_code, "The request could not be completed."
                ),
            },
            status_code=exception.status_code,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    async def health():
        return {"service": "demo-web", "status": "ok"}

    return app


app = create_app()

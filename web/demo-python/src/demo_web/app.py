import json
from contextlib import asynccontextmanager
from typing import Mapping

from fastapi import FastAPI

from demo_web.routes import router
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
        yield

    app = FastAPI(title="EventHub Demo", lifespan=lifespan)
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"service": "demo-web", "status": "ok"}

    return app


app = create_app()

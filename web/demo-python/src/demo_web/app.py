from contextlib import asynccontextmanager
from typing import Mapping

from fastapi import FastAPI

from demo_web.settings import Settings


def create_app(environ: Mapping[str, str] | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = Settings.from_environment(environ)
        yield

    app = FastAPI(title="EventHub Demo", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"service": "demo-web", "status": "ok"}

    return app


app = create_app()

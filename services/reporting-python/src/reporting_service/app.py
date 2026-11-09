from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from reporting_service.settings import Settings


LOGGER = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = Settings.from_environment()
    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="EventHub Reporting", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as error:
            LOGGER.error("Database health check failed: %s", type(error).__name__)
            raise HTTPException(status_code=503, detail="Database unavailable") from error
        return {"status": "UP"}

    return app

from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


LOGGER = logging.getLogger(__name__)


def create_app() -> FastAPI:
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="EventHub Attendance & Certification", lifespan=lifespan)

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

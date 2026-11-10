from contextlib import asynccontextmanager
import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from attendance_service.errors import ApiError
from attendance_service.identity_client import IdentityClient
from attendance_service.repository import AttendanceRepository
from attendance_service.settings import Settings
from attendance_service.worker import AttendanceWorker


LOGGER = logging.getLogger(__name__)


class CheckInResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    registration_id: UUID = Field(alias="registrationId")
    participant_id: UUID = Field(alias="participantId")
    checked_in_at: str = Field(alias="checkedInAt")


class CertificateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    registration_id: UUID = Field(alias="registrationId")
    participant_id: UUID = Field(alias="participantId")
    activity_id: UUID = Field(alias="activityId")
    issued_at: str = Field(alias="issuedAt")


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    code: str
    message: str
    correlation_id: UUID = Field(alias="correlationId")


ERROR_RESPONSES = {
    status: {
        "model": ErrorResponse,
        "description": "Explicit error with correlation identifier.",
        "headers": {
            "X-Correlation-ID": {
                "schema": {"type": "string", "format": "uuid"},
            },
        },
    }
    for status in (400, 401, 403, 404, 409, 422, 503)
}
ERROR_RESPONSES["default"] = ERROR_RESPONSES[503]


def create_app(
    settings: Settings | None = None,
    identity_client: IdentityClient | None = None,
    start_worker: bool = True,
) -> FastAPI:
    runtime_settings = settings or Settings.from_environment()
    engine = create_engine(
        runtime_settings.database_url.get_secret_value(),
        pool_pre_ping=True,
    )
    repository = AttendanceRepository(engine)
    identity = identity_client or IdentityClient(runtime_settings)
    worker = AttendanceWorker(runtime_settings, repository) if start_worker else None
    bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            if worker is not None:
                worker.start()
            yield
        finally:
            if worker is not None:
                worker.stop()
            engine.dispose()

    app = FastAPI(title="EventHub Attendance & Certification", lifespan=lifespan)
    app.state.engine = engine
    app.state.repository = repository
    app.state.identity_client = identity
    app.state.worker = worker

    @app.middleware("http")
    async def attach_correlation_id(request: Request, call_next):
        supplied_id = request.headers.get("X-Correlation-ID")
        try:
            correlation_id = UUID(supplied_id) if supplied_id is not None else uuid4()
        except ValueError:
            correlation_id = uuid4()
            request.state.correlation_id = str(correlation_id)
            return _error_response(
                request,
                400,
                "INVALID_CORRELATION_ID",
                "X-Correlation-ID must be a UUID.",
            )

        request.state.correlation_id = str(correlation_id)
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return _error_response(
            request,
            error.status_code,
            error.code,
            str(error),
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request,
        _: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            request,
            400,
            "INVALID_REQUEST",
            "Request does not match the Attendance contract.",
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        LOGGER.error(
            "Attendance database operation failed error_type=%s correlation_id=%s",
            type(error).__name__,
            getattr(request.state, "correlation_id", "unknown"),
        )
        return _error_response(
            request,
            503,
            "DATABASE_UNAVAILABLE",
            "The Attendance data store is unavailable.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        code, message = {
            404: ("NOT_FOUND", "Resource not found."),
            405: ("METHOD_NOT_ALLOWED", "HTTP method is not allowed."),
        }.get(error.status_code, ("HTTP_ERROR", "Request could not be completed."))
        return _error_response(request, error.status_code, code, message)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, error: Exception) -> JSONResponse:
        LOGGER.error(
            "Unhandled Attendance error type=%s correlation_id=%s",
            type(error).__name__,
            getattr(request.state, "correlation_id", "unknown"),
        )
        return _error_response(
            request,
            500,
            "INTERNAL_ERROR",
            "An internal error occurred.",
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as error:
            LOGGER.error("Database health check failed: %s", type(error).__name__)
            raise ApiError(503, "DATABASE_UNAVAILABLE", "Database unavailable.") from error
        return {"status": "UP"}

    @app.post(
        "/registrations/{id}/check-in",
        operation_id="checkInOwnRegistration",
        response_model=CheckInResponse,
        responses=ERROR_RESPONSES,
    )
    def check_in_own_registration(
        id: UUID,
        request: Request,
        _idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
        _correlation_id: Annotated[
            str | None, Header(alias="X-Correlation-ID")
        ] = None,
        authorization: Annotated[
            HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
        ] = None,
    ) -> dict[str, str]:
        principal_id = _require_participant(
            request,
            authorization,
            identity,
        )
        return repository.record_check_in(
            id,
            principal_id,
            UUID(request.state.correlation_id),
        )

    @app.post(
        "/registrations/{id}/certificate",
        operation_id="issueOwnCertificate",
        response_model=CertificateResponse,
        responses=ERROR_RESPONSES,
    )
    def issue_own_certificate(
        id: UUID,
        request: Request,
        _idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
        authorization: Annotated[
            HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
        ] = None,
    ) -> dict[str, str]:
        principal_id = _require_participant(
            request,
            authorization,
            identity,
        )
        return repository.issue_certificate(
            id,
            principal_id,
            UUID(request.state.correlation_id),
        )

    @app.get(
        "/registrations/{id}/certificate",
        operation_id="getOwnCertificate",
        response_model=CertificateResponse,
        responses=ERROR_RESPONSES,
    )
    def get_own_certificate(
        id: UUID,
        request: Request,
        authorization: Annotated[
            HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
        ] = None,
    ) -> dict[str, str]:
        principal_id = _require_participant(
            request,
            authorization,
            identity,
        )
        return repository.get_certificate(id, principal_id)

    return app


def _require_participant(
    request: Request,
    authorization: HTTPAuthorizationCredentials | None,
    identity: IdentityClient,
) -> UUID:
    if authorization is None or not authorization.credentials.strip():
        raise ApiError(401, "UNAUTHORIZED", "A bearer credential is required.")

    principal = identity.validate(
        authorization.credentials,
        request.state.correlation_id,
    )
    if isinstance(principal, dict):
        role = principal.get("role")
        subject_id = principal.get("subject_id")
    else:
        role = getattr(principal, "role", None)
        subject_id = getattr(principal, "subject_id", None)
    if role != "PARTICIPANT":
        raise ApiError(403, "FORBIDDEN", "A participant identity is required.")
    if not isinstance(subject_id, UUID):
        raise ApiError(
            503,
            "IDENTITY_UNAVAILABLE",
            "Identity validation returned an invalid principal.",
        )
    return subject_id


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", str(uuid4()))
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "correlationId": correlation_id,
        },
        headers={"X-Correlation-ID": correlation_id},
    )

from contextlib import asynccontextmanager
import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from reporting_service.errors import ApiError
from reporting_service.identity_client import IdentityClient
from reporting_service.observability import (
    bind_context,
    configure_logging,
    reset_context,
)
from reporting_service.repository import ReportingRepository
from reporting_service.settings import Settings
from reporting_service.worker import ReportingWorker


LOGGER = logging.getLogger(__name__)


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    organization_id: UUID = Field(alias="organizationId")
    activity_id: UUID = Field(alias="activityId")
    confirmed: int = Field(ge=0)
    pending_payment: int = Field(alias="pendingPayment", ge=0)
    offered: int = Field(ge=0)
    waiting: int = Field(ge=0)
    occupied: int = Field(ge=0)
    checked_in: int = Field(alias="checkedIn", ge=0)
    certificates: int = Field(ge=0)
    payments_approved: int = Field(alias="paymentsApproved", ge=0)
    payments_refused: int = Field(alias="paymentsRefused", ge=0)
    refunds_approved: int = Field(alias="refundsApproved", ge=0)
    projected_at: AwareDatetime = Field(alias="projectedAt")


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
    for status in (400, 401, 403, 422, 503)
}


def create_app(
    settings: Settings | None = None,
    identity_client: IdentityClient | None = None,
    start_worker: bool = True,
) -> FastAPI:
    configure_logging()
    runtime_settings = settings or Settings.from_environment()
    engine = create_engine(
        runtime_settings.database_url.get_secret_value(),
        pool_pre_ping=True,
    )
    repository = ReportingRepository(engine)
    identity = identity_client or IdentityClient(runtime_settings)
    worker = ReportingWorker(runtime_settings, repository) if start_worker else None
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

    app = FastAPI(title="EventHub Reporting", lifespan=lifespan)
    app.state.engine = engine
    app.state.repository = repository
    app.state.projector = repository
    app.state.identity_client = identity
    app.state.worker = worker

    @app.middleware("http")
    async def attach_correlation_id(request: Request, call_next):
        supplied_id = request.headers.get("X-Correlation-ID")
        try:
            correlation_id = UUID(supplied_id) if supplied_id is not None else uuid4()
        except ValueError:
            request.state.correlation_id = str(uuid4())
            context_tokens = bind_context(request.state.correlation_id)
            try:
                response = _error_response(
                    request,
                    400,
                    "INVALID_CORRELATION_ID",
                    "X-Correlation-ID must be a UUID.",
                )
                LOGGER.info(
                    "http.request.completed",
                    extra={
                        "http_method": request.method,
                        "http_path": request.url.path,
                        "correlation_id": request.state.correlation_id,
                        "status_code": response.status_code,
                    },
                )
                return response
            finally:
                reset_context(context_tokens)
        request.state.correlation_id = str(correlation_id)
        context_tokens = bind_context(request.state.correlation_id)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = request.state.correlation_id
            LOGGER.info(
                "http.request.completed",
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "correlation_id": request.state.correlation_id,
                    "registration_id": request.path_params.get("registration_id")
                    or request.path_params.get("id"),
                    "status_code": response.status_code,
                },
            )
            return response
        finally:
            reset_context(context_tokens)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return _error_response(request, error.status_code, error.code, str(error))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request,
        _: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            request,
            400,
            "INVALID_REQUEST",
            "Request does not match the Reporting contract.",
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        LOGGER.error(
            "database.operation.failed",
            extra={
                "correlation_id": getattr(request.state, "correlation_id", None),
                "error_type": type(error).__name__,
                "registration_id": request.path_params.get("registration_id")
                or request.path_params.get("id"),
            },
        )
        return _error_response(
            request,
            503,
            "DATABASE_UNAVAILABLE",
            "The Reporting data store is unavailable.",
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
            "request.unhandled_error",
            extra={
                "correlation_id": getattr(request.state, "correlation_id", None),
                "error_type": type(error).__name__,
                "registration_id": request.path_params.get("registration_id")
                or request.path_params.get("id"),
            },
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
            LOGGER.error(
                "database.health_check.failed",
                extra={"error_type": type(error).__name__},
            )
            raise ApiError(
                503,
                "DATABASE_UNAVAILABLE",
                "Reporting database unavailable.",
            ) from error
        return {"status": "UP"}

    @app.get(
        "/organizations/{organizationId}/reports",
        operation_id="getOrganizationReports",
        response_model=list[Report],
        responses=ERROR_RESPONSES,
    )
    def get_organization_reports(
        organizationId: UUID,
        request: Request,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(bearer_scheme),
        ],
        _correlation_id: Annotated[
            str | None,
            Header(alias="X-Correlation-ID"),
        ] = None,
        activity_id: Annotated[UUID | None, Query(alias="activityId")] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict]:
        if credentials is None:
            raise ApiError(401, "UNAUTHORIZED", "A bearer credential is required.")
        principal = identity.validate(
            credentials.credentials,
            _correlation_id or request.state.correlation_id,
        )
        if principal["role"] not in {"ORGANIZER", "GLOBAL_ADMIN"}:
            raise ApiError(403, "FORBIDDEN", "This role cannot view reports.")
        if (
            principal["role"] == "ORGANIZER"
            and principal["organization_id"] != organizationId
        ):
            raise ApiError(403, "FORBIDDEN", "Reports are limited to your organization.")
        return repository.get_reports(organizationId, activity_id, limit, offset)

    return app


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", str(uuid4()))
    return JSONResponse(
        status_code=status_code,
        headers={"X-Correlation-ID": correlation_id},
        content={
            "code": code,
            "message": message,
            "correlationId": correlation_id,
        },
    )

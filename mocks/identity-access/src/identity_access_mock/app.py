from contextlib import asynccontextmanager
import hmac
import json
import logging
from importlib.resources import files
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)
from starlette.middleware.base import RequestResponseEndpoint
from starlette.exceptions import HTTPException as StarletteHTTPException

from identity_access_mock.observability import (
    bind_context,
    configure_logging,
    reset_context,
)
from identity_access_mock.settings import Settings


EXPECTED_ROLES = {
    "participant": "PARTICIPANT",
    "participantTwo": "PARTICIPANT",
    "organizer": "ORGANIZER",
    "globalAdmin": "GLOBAL_ADMIN",
}
LOGGER = logging.getLogger(__name__)
SCENARIO_PARTICIPANT_COUNT = 20
MAX_SCENARIO_FIXTURE_BYTES = 32_768


def _scenario_names() -> set[str]:
    return {f"scenarioParticipant{index:02}" for index in range(1, SCENARIO_PARTICIPANT_COUNT + 1)}


class IdentityFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: SecretStr = Field(min_length=1, max_length=4096)
    subject_id: UUID = Field(alias="subjectId")
    role: Literal["PARTICIPANT", "ORGANIZER", "GLOBAL_ADMIN"]
    organization_id: UUID | None = Field(default=None, alias="organizationId")

    @model_validator(mode="after")
    def validate_role_scope(self) -> "IdentityFixture":
        if (self.role == "ORGANIZER") != (self.organization_id is not None):
            raise ValueError("organizer fixtures require organization scope")
        return self


class ValidateIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str = Field(min_length=1, max_length=4096)


class PrincipalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: UUID = Field(alias="subjectId")
    role: Literal["PARTICIPANT", "ORGANIZER", "GLOBAL_ADMIN"]
    organization_id: UUID | None = Field(alias="organizationId")


def load_fixtures() -> dict[str, IdentityFixture]:
    fixture_path = files("identity_access_mock").joinpath("fixtures.json")
    with fixture_path.open("r", encoding="utf-8") as fixture_file:
        fixture_data = json.load(fixture_file)

    if not isinstance(fixture_data, dict) or set(fixture_data) != set(EXPECTED_ROLES):
        raise ValueError("identity fixture names do not match the approved roles")

    try:
        fixtures = {
            name: IdentityFixture.model_validate(fixture_data[name]) for name in EXPECTED_ROLES
        }
    except ValidationError:
        raise ValueError("identity fixture data is invalid") from None
    if any(fixtures[name].role != role for name, role in EXPECTED_ROLES.items()):
        raise ValueError("identity fixtures do not match their approved roles")

    tokens = [fixture.token.get_secret_value() for fixture in fixtures.values()]
    if len(tokens) != len(set(tokens)):
        raise ValueError("identity fixture tokens must be unique")

    return fixtures


def load_scenario_fixtures(
    fixture_path: Path,
    baseline_fixtures: dict[str, IdentityFixture],
) -> dict[str, IdentityFixture]:
    try:
        if fixture_path.stat().st_size > MAX_SCENARIO_FIXTURE_BYTES:
            raise ValueError("scenario identity fixture exceeds its size limit")
        with fixture_path.open("r", encoding="utf-8") as fixture_file:
            fixture_data = json.load(fixture_file)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("scenario identity fixture is invalid") from None

    if not isinstance(fixture_data, dict) or set(fixture_data) != _scenario_names():
        raise ValueError("scenario identity fixture must contain twenty actors")

    try:
        fixtures = {
            name: IdentityFixture.model_validate(fixture_data[name])
            for name in sorted(_scenario_names())
        }
    except ValidationError:
        raise ValueError("scenario identity fixture is invalid") from None

    if any(
        fixture.role != "PARTICIPANT" or fixture.organization_id is not None
        for fixture in fixtures.values()
    ):
        raise ValueError("scenario identities must be unscoped participants")

    combined_fixtures = [*baseline_fixtures.values(), *fixtures.values()]
    tokens = [fixture.token.get_secret_value() for fixture in combined_fixtures]
    subjects = [fixture.subject_id for fixture in combined_fixtures]
    if len(tokens) != len(set(tokens)) or len(subjects) != len(set(subjects)):
        raise ValueError("identity fixture tokens and subjects must be unique")

    return fixtures


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "correlationId": request.state.correlation_id,
        },
        headers={"X-Correlation-ID": request.state.correlation_id},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    runtime_settings = settings or Settings.from_environment()
    fixtures = load_fixtures()
    if runtime_settings.scenario_pool_enabled:
        if runtime_settings.scenario_fixtures_path is None:
            raise ValueError("scenario identity fixture path is required when the pool is enabled")
        fixtures.update(
            load_scenario_fixtures(
                runtime_settings.scenario_fixtures_path,
                fixtures,
            )
        )
    fixtures_by_token = {fixture.token.get_secret_value(): fixture for fixture in fixtures.values()}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(
        title="EventHub Identity and Access MOCK",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def attach_correlation_id(
        request: Request,
        call_next: RequestResponseEndpoint,
    ):
        supplied_id = request.headers.get("X-Correlation-ID")
        try:
            correlation_id = UUID(supplied_id) if supplied_id is not None else uuid4()
        except ValueError:
            request.state.correlation_id = str(uuid4())
            context_tokens = bind_context(request.state.correlation_id)
            try:
                response = error_response(
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

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, _: RequestValidationError) -> JSONResponse:
        return error_response(
            request,
            400,
            "INVALID_REQUEST",
            "Request does not match the identity contract.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code, message = {
            404: ("NOT_FOUND", "Resource not found."),
            405: ("METHOD_NOT_ALLOWED", "HTTP method is not allowed."),
        }.get(error.status_code, ("HTTP_ERROR", "Request could not be completed."))
        return error_response(request, error.status_code, code, message)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, error: Exception) -> JSONResponse:
        LOGGER.error(
            "request.unhandled_error",
            extra={
                "correlation_id": request.state.correlation_id,
                "error_type": type(error).__name__,
            },
        )
        return error_response(
            request,
            500,
            "INTERNAL_ERROR",
            "An internal error occurred.",
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "UP"}

    @app.post("/internal/identity/validate", response_model=PrincipalResponse)
    async def validate_identity(
        body: ValidateIdentityRequest,
        request: Request,
    ) -> PrincipalResponse | JSONResponse:
        supplied_key = request.headers.get("X-Service-Key", "")
        if not hmac.compare_digest(
            supplied_key.encode("utf-8"),
            runtime_settings.internal_service_key.encode("utf-8"),
        ):
            return error_response(
                request,
                401,
                "UNAUTHORIZED",
                "A valid internal service credential is required.",
            )

        if not runtime_settings.available:
            return error_response(
                request,
                503,
                "IDENTITY_UNAVAILABLE",
                "Identity validation is unavailable.",
            )

        fixture = fixtures_by_token.get(body.token)
        if fixture is None:
            return error_response(
                request,
                401,
                "INVALID_IDENTITY",
                "The synthetic identity token is invalid.",
            )

        return PrincipalResponse(
            subjectId=fixture.subject_id,
            role=fixture.role,
            organizationId=fixture.organization_id,
        )

    return app

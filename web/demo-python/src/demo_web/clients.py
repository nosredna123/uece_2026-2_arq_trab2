import json
from collections import OrderedDict, deque
from dataclasses import dataclass
from time import perf_counter
from typing import TypeVar
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ValidationError

from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.models import (
    Activity,
    ActivityInput,
    Availability,
    Certificate,
    CheckIn,
    ErrorResponse,
    Event,
    EventInput,
    Report,
    Registration,
)
from demo_web.settings import Settings


ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class RequestHistoryEntry:
    service: str
    method: str
    status: int | None
    elapsed_ms: int
    correlation_id: UUID


class DemoClients:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._settings = settings
        self._base_urls = {
            "event-management": settings.event_base_url,
            "registration": settings.registration_base_url,
            "attendance": settings.attendance_base_url,
            "reporting": settings.reporting_base_url,
        }
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
        )
        self._history: OrderedDict[str | None, deque[RequestHistoryEntry]] = OrderedDict()

    @property
    def history(self) -> tuple[RequestHistoryEntry, ...]:
        return self.history_for(None)

    def history_for(self, session_id: str | None) -> tuple[RequestHistoryEntry, ...]:
        key = _history_key(session_id)
        return tuple(self._history.get(key, ()))

    async def __aenter__(self) -> "DemoClients":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_events(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        search: str | None = None,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> list[Event]:
        params = _page_params(limit, offset)
        if search is not None:
            if len(search) > 200:
                raise ValueError("search must not exceed 200 characters.")
            params["q"] = search
        return await self._request(
            "event-management",
            "GET",
            "/events",
            Event,
            session_id=session_id,
            correlation_id=correlation_id,
            params=params,
            many=True,
        )

    async def get_event(
        self,
        event_id: UUID | str,
        *,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> Event:
        return await self._request(
            "event-management",
            "GET",
            f"/events/{_identifier(event_id)}",
            Event,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    async def list_activities(
        self,
        event_id: UUID | str,
        *,
        limit: int = 20,
        offset: int = 0,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> list[Activity]:
        return await self._request(
            "event-management",
            "GET",
            f"/events/{_identifier(event_id)}/activities",
            Activity,
            session_id=session_id,
            correlation_id=correlation_id,
            params=_page_params(limit, offset),
            many=True,
        )

    async def get_activity(
        self,
        activity_id: UUID | str,
        *,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> Activity:
        return await self._request(
            "event-management",
            "GET",
            f"/activities/{_identifier(activity_id)}",
            Activity,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    async def create_event(
        self,
        event: EventInput,
        *,
        token: str,
        idempotency_key: str,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> Event:
        return await self._request(
            "event-management",
            "POST",
            "/events",
            Event,
            token=token,
            idempotency_key=idempotency_key,
            session_id=session_id,
            correlation_id=correlation_id,
            json_body=event.model_dump(mode="json", by_alias=True),
            expected_statuses=(201,),
        )

    async def create_activity(
        self,
        event_id: UUID | str,
        activity: ActivityInput,
        *,
        token: str,
        idempotency_key: str,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> Activity:
        return await self._request(
            "event-management",
            "POST",
            f"/events/{_identifier(event_id)}/activities",
            Activity,
            token=token,
            idempotency_key=idempotency_key,
            session_id=session_id,
            correlation_id=correlation_id,
            json_body=activity.model_dump(mode="json", by_alias=True),
            expected_statuses=(201,),
        )

    async def publish_event(
        self,
        event_id: UUID | str,
        *,
        token: str,
        idempotency_key: str,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> Event:
        return await self._request(
            "event-management",
            "POST",
            f"/events/{_identifier(event_id)}/publish",
            Event,
            token=token,
            idempotency_key=idempotency_key,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    async def get_availability(
        self,
        activity_id: UUID | str,
        *,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> Availability:
        return await self._request(
            "registration",
            "GET",
            f"/activities/{_identifier(activity_id)}/availability",
            Availability,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    async def request_registration(
        self,
        activity_id: UUID | str,
        *,
        token: str,
        idempotency_key: str,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> Registration:
        return await self._request(
            "registration",
            "POST",
            "/registrations",
            Registration,
            token=token,
            idempotency_key=idempotency_key,
            session_id=session_id,
            correlation_id=correlation_id,
            json_body={"activityId": str(_identifier(activity_id))},
            expected_statuses=(201,),
        )

    async def list_registrations(
        self,
        *,
        token: str,
        limit: int = 20,
        offset: int = 0,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> list[Registration]:
        return await self._request(
            "registration",
            "GET",
            "/registrations",
            Registration,
            token=token,
            session_id=session_id,
            correlation_id=correlation_id,
            params=_page_params(limit, offset),
            many=True,
        )

    async def get_registration(
        self,
        registration_id: UUID | str,
        *,
        token: str,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> Registration:
        return await self._request(
            "registration",
            "GET",
            f"/registrations/{_identifier(registration_id)}",
            Registration,
            token=token,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    async def cancel_registration(
        self,
        registration_id: UUID | str,
        *,
        token: str,
        idempotency_key: str,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> Registration:
        return await self._request(
            "registration",
            "POST",
            f"/registrations/{_identifier(registration_id)}/cancel",
            Registration,
            token=token,
            idempotency_key=idempotency_key,
            session_id=session_id,
            correlation_id=correlation_id,
            expected_statuses=(200, 202),
        )

    async def accept_offer(
        self,
        registration_id: UUID | str,
        *,
        token: str,
        idempotency_key: str,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> Registration:
        return await self._request(
            "registration",
            "POST",
            f"/registrations/{_identifier(registration_id)}/accept-offer",
            Registration,
            token=token,
            idempotency_key=idempotency_key,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    async def check_in(
        self,
        registration_id: UUID | str,
        *,
        token: str,
        idempotency_key: str,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> CheckIn:
        return await self._request(
            "attendance",
            "POST",
            f"/registrations/{_identifier(registration_id)}/check-in",
            CheckIn,
            token=token,
            idempotency_key=idempotency_key,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    async def issue_certificate(
        self,
        registration_id: UUID | str,
        *,
        token: str,
        idempotency_key: str,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> Certificate:
        return await self._request(
            "attendance",
            "POST",
            f"/registrations/{_identifier(registration_id)}/certificate",
            Certificate,
            token=token,
            idempotency_key=idempotency_key,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    async def get_certificate(
        self,
        registration_id: UUID | str,
        *,
        token: str,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> Certificate:
        return await self._request(
            "attendance",
            "GET",
            f"/registrations/{_identifier(registration_id)}/certificate",
            Certificate,
            token=token,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    async def get_reports(
        self,
        organization_id: UUID | str,
        *,
        token: str,
        activity_id: UUID | str | None = None,
        limit: int = 20,
        offset: int = 0,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
    ) -> list[Report]:
        params = _page_params(limit, offset)
        if activity_id is not None:
            params["activityId"] = str(_identifier(activity_id))
        return await self._request(
            "reporting",
            "GET",
            f"/organizations/{_identifier(organization_id)}/reports",
            Report,
            token=token,
            session_id=session_id,
            correlation_id=correlation_id,
            params=params,
            many=True,
        )

    async def _request(
        self,
        service: str,
        method: str,
        path: str,
        response_model: type[ModelT],
        *,
        token: str | None = None,
        idempotency_key: str | None = None,
        session_id: str | None = None,
        correlation_id: UUID | str | None = None,
        params: dict[str, str | int] | None = None,
        json_body: dict[str, object] | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        many: bool = False,
    ) -> ModelT | list[ModelT]:
        history_key = _history_key(session_id)
        correlation = _correlation_uuid(correlation_id)
        headers = {"X-Correlation-ID": str(correlation)}
        if token is not None:
            headers["Authorization"] = f"Bearer {_validate_token(token)}"
        if method != "GET":
            headers["Idempotency-Key"] = _validate_idempotency_key(idempotency_key)

        started = perf_counter()
        try:
            response = await self._client.request(
                method,
                f"{self._base_urls[service]}{path}",
                params=params,
                json=json_body,
                headers=headers,
            )
        except httpx.TimeoutException:
            self._record(service, method, None, started, correlation, history_key)
            raise TransportError(
                service=service,
                method=method,
                code="HTTP_TIMEOUT",
                message="The service request timed out.",
                correlation_id=correlation,
                outcome_unknown=method != "GET",
            ) from None
        except httpx.RequestError:
            self._record(service, method, None, started, correlation, history_key)
            raise TransportError(
                service=service,
                method=method,
                code="SERVICE_UNAVAILABLE",
                message="The service could not be reached.",
                correlation_id=correlation,
                outcome_unknown=method != "GET",
            ) from None
        self._record(service, method, response.status_code, started, correlation, history_key)

        if response.status_code not in expected_statuses:
            if response.status_code >= 400:
                raise _service_error(
                    service,
                    method,
                    response.status_code,
                    response,
                    correlation,
                    token,
                    idempotency_key,
                )
            raise InvalidResponseError(
                service=service,
                method=method,
                status_code=response.status_code,
                correlation_id=correlation,
            )
        try:
            payload = json.loads(response.content)
            if many:
                if not isinstance(payload, list):
                    raise ValueError("Expected a JSON array.")
                return [response_model.model_validate(item) for item in payload]
            return response_model.model_validate(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, ValidationError):
            raise InvalidResponseError(
                service=service,
                method=method,
                status_code=response.status_code,
                correlation_id=correlation,
            ) from None

    def _record(
        self,
        service: str,
        method: str,
        status: int | None,
        started: float,
        correlation: UUID,
        history_key: str | None,
    ) -> None:
        elapsed_ms = max(0, round((perf_counter() - started) * 1000))
        if history_key not in self._history:
            if len(self._history) >= self._settings.max_sessions:
                self._history.popitem(last=False)
            self._history[history_key] = deque(maxlen=self._settings.history_limit)
        self._history.move_to_end(history_key)
        self._history[history_key].append(
            RequestHistoryEntry(service, method, status, elapsed_ms, correlation)
        )


def _service_error(
    service: str,
    method: str,
    status_code: int,
    response: httpx.Response,
    correlation_id: UUID,
    token: str | None,
    idempotency_key: str | None,
) -> ServiceError:
    code = "HTTP_ERROR"
    message = f"The service returned HTTP {status_code}."
    try:
        payload = json.loads(response.content)
        error = ErrorResponse.model_validate(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, ValidationError):
        error = None
    if error is not None:
        code = _redact(error.code, token, idempotency_key)
        message = _redact(error.message, token, idempotency_key)
        correlation_id = error.correlation_id
    return ServiceError(
        service=service,
        method=method,
        status_code=status_code,
        code=code,
        message=message,
        correlation_id=correlation_id,
    )


def _redact(value: str, *secrets: str | None) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[redacted]")
    return value


def _identifier(value: UUID | str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Resource identifier must be a UUID.") from None


def _correlation_uuid(value: UUID | str | None) -> UUID:
    if value is None:
        return uuid4()
    return _identifier(value)


def _validate_token(token: str) -> str:
    if not token.strip() or "\r" in token or "\n" in token:
        raise ValueError("A valid synthetic bearer token is required.")
    return token


def _validate_idempotency_key(value: str | None) -> str:
    if not value or len(value) > 128 or not value.strip() or "\r" in value or "\n" in value:
        raise ValueError("A valid idempotency key is required.")
    return value


def _page_params(limit: int, offset: int) -> dict[str, int]:
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be nonnegative.")
    return {"limit": limit, "offset": offset}


def _history_key(session_id: str | None) -> str | None:
    if session_id is not None and (
        not session_id or len(session_id) > 128 or "\r" in session_id or "\n" in session_id
    ):
        raise ValueError("Invalid server session identifier.")
    return session_id

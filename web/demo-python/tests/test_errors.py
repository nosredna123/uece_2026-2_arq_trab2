import asyncio

import httpx
import pytest

from demo_web.clients import DemoClients
from demo_web.errors import InvalidResponseError, ServiceError, TransportError


TOKEN = "synthetic-error-token-must-not-leak"
IDEMPOTENCY_KEY = "synthetic-command-key-must-not-leak"
ERROR_STATUSES = (401, 403, 404, 409, 422, 503)


@pytest.mark.parametrize("status", ERROR_STATUSES)
def test_preserves_contract_error_status_and_code_without_credentials(web_environment, status):
    def respond(request):
        return httpx.Response(
            status,
            json={
                "code": f"ERROR_{status}",
                "message": f"Rejected credential: {TOKEN}; key={IDEMPOTENCY_KEY}",
                "correlationId": request.headers["X-Correlation-ID"],
            },
            headers={"X-Correlation-ID": request.headers["X-Correlation-ID"]},
        )

    async def exercise_clients():
        async with DemoClients(
            _settings(web_environment), transport=httpx.MockTransport(respond)
        ) as clients:
            with pytest.raises(ServiceError) as error:
                await clients.cancel_registration(
                    "10000000-0000-4000-8000-000000000003",
                    token=TOKEN,
                    idempotency_key=IDEMPOTENCY_KEY,
                )
        return error.value, clients.history

    error, history = asyncio.run(exercise_clients())

    assert error.status_code == status
    assert error.code == f"ERROR_{status}"
    assert error.correlation_id == history[0].correlation_id
    assert TOKEN not in str(error)
    assert IDEMPOTENCY_KEY not in str(error)
    assert TOKEN not in repr(history)
    assert IDEMPOTENCY_KEY not in repr(history)
    assert history[0].status == status


def test_timeout_on_mutation_is_explicitly_unknown_and_never_retried(web_environment):
    requests = []

    def timeout(request):
        requests.append(request)
        raise httpx.ReadTimeout("request contains no credential details", request=request)

    async def exercise_clients():
        async with DemoClients(
            _settings(web_environment), transport=httpx.MockTransport(timeout)
        ) as clients:
            with pytest.raises(TransportError) as error:
                await clients.request_registration(
                    "10000000-0000-4000-8000-000000000002",
                    token=TOKEN,
                    idempotency_key="same-command-key",
                )
        return error.value, clients.history

    error, history = asyncio.run(exercise_clients())

    assert len(requests) == 1
    assert error.outcome_unknown is True
    assert error.code == "HTTP_TIMEOUT"
    assert TOKEN not in str(error)
    assert history[0].status is None


def test_connection_failure_is_explicit_and_sanitized(web_environment):
    def refuse(request):
        raise httpx.ConnectError(f"failed with {TOKEN}", request=request)

    async def exercise_clients():
        async with DemoClients(
            _settings(web_environment), transport=httpx.MockTransport(refuse)
        ) as clients:
            with pytest.raises(TransportError) as error:
                await clients.list_events()
        return error.value

    error = asyncio.run(exercise_clients())

    assert error.code == "SERVICE_UNAVAILABLE"
    assert error.outcome_unknown is False
    assert TOKEN not in str(error)


def test_malformed_success_response_is_not_returned_as_success(web_environment):
    def malformed(request):
        return httpx.Response(200, json={"not": "an event"})

    async def exercise_clients():
        async with DemoClients(
            _settings(web_environment), transport=httpx.MockTransport(malformed)
        ) as clients:
            with pytest.raises(InvalidResponseError) as error:
                await clients.list_events()
        return error.value, clients.history

    error, history = asyncio.run(exercise_clients())

    assert error.service == "event-management"
    assert error.status_code == 200
    assert history[0].status == 200


def test_malformed_error_body_keeps_http_status_and_safe_message(web_environment):
    def malformed_error(request):
        return httpx.Response(503, content=b"upstream details include " + TOKEN.encode())

    async def exercise_clients():
        async with DemoClients(
            _settings(web_environment), transport=httpx.MockTransport(malformed_error)
        ) as clients:
            with pytest.raises(ServiceError) as error:
                await clients.list_events()
        return error.value

    error = asyncio.run(exercise_clients())

    assert error.status_code == 503
    assert error.code == "HTTP_ERROR"
    assert TOKEN not in str(error)


def test_redirect_is_not_followed_to_an_arbitrary_origin(web_environment):
    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(
            302,
            headers={"Location": "http://attacker.example/collect"},
        )

    async def exercise_clients():
        async with DemoClients(
            _settings(web_environment), transport=httpx.MockTransport(redirect)
        ) as clients:
            with pytest.raises(InvalidResponseError) as error:
                await clients.list_events()
        return error.value

    error = asyncio.run(exercise_clients())

    assert error.status_code == 302
    assert len(requests) == 1


def _settings(environment):
    from demo_web.settings import Settings

    return Settings.from_environment(environment)

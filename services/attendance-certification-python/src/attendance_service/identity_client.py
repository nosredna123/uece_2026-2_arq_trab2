import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from attendance_service.errors import ApiError
from attendance_service.settings import Settings


class IdentityClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = str(settings.identity_base_url).rstrip("/")
        self.service_key = settings.internal_service_key.get_secret_value()
        self.timeout = settings.http_timeout_seconds

    def validate(self, token: str, correlation_id: str) -> dict:
        request = Request(
            f"{self.base_url}/internal/identity/validate",
            data=json.dumps({"token": token}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Service-Key": self.service_key,
                "X-Correlation-ID": correlation_id,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read())
        except HTTPError as error:
            if error.code == 401:
                raise ApiError(401, "INVALID_IDENTITY", "The bearer credential is invalid.") from error
            if error.code == 403:
                raise ApiError(403, "FORBIDDEN", "The identity service denied this request.") from error
            raise ApiError(
                503,
                "IDENTITY_UNAVAILABLE",
                "Identity validation could not be completed.",
            ) from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ApiError(
                503,
                "IDENTITY_UNAVAILABLE",
                "Identity validation could not be completed.",
            ) from error

        try:
            subject_id = UUID(body["subjectId"])
            role = body["role"]
            organization_id = (
                UUID(body["organizationId"])
                if body.get("organizationId") is not None
                else None
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ApiError(
                503,
                "IDENTITY_UNAVAILABLE",
                "Identity validation returned an invalid principal.",
            ) from error
        if role not in {"PARTICIPANT", "ORGANIZER", "GLOBAL_ADMIN"} or (
            (role == "ORGANIZER") != (organization_id is not None)
        ):
            raise ApiError(
                503,
                "IDENTITY_UNAVAILABLE",
                "Identity validation returned an invalid principal.",
            )
        return {
            "subject_id": subject_id,
            "role": role,
            "organization_id": organization_id,
        }

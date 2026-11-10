from uuid import UUID


class ServiceError(RuntimeError):
    def __init__(
        self,
        *,
        service: str,
        method: str,
        status_code: int,
        code: str,
        message: str,
        correlation_id: UUID,
    ):
        self.service = service
        self.method = method
        self.status_code = status_code
        self.code = code
        self.message = message
        self.correlation_id = correlation_id
        self.outcome_unknown = False
        super().__init__(
            f"{service} {method} failed with HTTP {status_code} "
            f"({code}; correlation {correlation_id}): {message}"
        )


class TransportError(RuntimeError):
    def __init__(
        self,
        *,
        service: str,
        method: str,
        code: str,
        message: str,
        correlation_id: UUID,
        outcome_unknown: bool,
    ):
        self.service = service
        self.method = method
        self.status_code = None
        self.code = code
        self.message = message
        self.correlation_id = correlation_id
        self.outcome_unknown = outcome_unknown
        super().__init__(
            f"{service} {method} failed ({code}; correlation {correlation_id}): {message}"
        )


class InvalidResponseError(RuntimeError):
    def __init__(
        self,
        *,
        service: str,
        method: str,
        status_code: int,
        correlation_id: UUID,
    ):
        self.service = service
        self.method = method
        self.status_code = status_code
        self.code = "INVALID_RESPONSE"
        self.correlation_id = correlation_id
        self.outcome_unknown = method != "GET"
        super().__init__(
            f"{service} returned an invalid response to {method} "
            f"(HTTP {status_code}; correlation {correlation_id})."
        )

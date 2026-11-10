import contextvars
from contextvars import Token
from datetime import datetime, timezone
import json
import logging
import sys


_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "notification_correlation_id", default=None
)
_registration_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "notification_registration_id", default=None
)


class StructuredFormatter(logging.Formatter):
    eventhub_structured = True

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = {
            "correlation_id": "correlationId",
            "registration_id": "registrationId",
            "event_id": "eventId",
            "event_type": "eventType",
            "http_method": "httpMethod",
            "http_path": "httpPath",
            "status_code": "statusCode",
            "error_type": "errorType",
            "retry_seconds": "retrySeconds",
        }
        for attribute, name in fields.items():
            value = getattr(record, attribute, None)
            if value is not None:
                entry[name] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, separators=(",", ":"), default=str)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        correlation_id = _correlation_id.get()
        registration_id = _registration_id.get()
        if correlation_id is not None:
            record.correlation_id = correlation_id
        if registration_id is not None:
            record.registration_id = registration_id
        return True


def configure_logging() -> None:
    root_logger = logging.getLogger()
    handler = next(
        (
            candidate
            for candidate in root_logger.handlers
            if getattr(candidate.formatter, "eventhub_structured", False)
        ),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        root_logger.addHandler(handler)
    if not any(
        getattr(item, "eventhub_context_module", None) == __name__
        for item in handler.filters
    ):
        handler.addFilter(ContextFilter())
        handler.filters[-1].eventhub_context_module = __name__
    root_logger.setLevel(logging.INFO)


def bind_context(
    correlation_id: str | None,
    registration_id: str | None = None,
) -> tuple[Token[str | None], Token[str | None]]:
    return (
        _correlation_id.set(correlation_id),
        _registration_id.set(registration_id),
    )


def reset_context(
    tokens: tuple[Token[str | None], Token[str | None]],
) -> None:
    _registration_id.reset(tokens[1])
    _correlation_id.reset(tokens[0])

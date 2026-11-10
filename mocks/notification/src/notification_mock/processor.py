from dataclasses import dataclass
from hashlib import sha256

from notification_mock.models import (
    InvalidNotificationEvent,
    NotificationEvent,
    notice_type,
    parse_notification_event,
)
from notification_mock.settings import NotificationSettings
from notification_mock.store import (
    ConflictingNotificationEvent,
    NotificationStore,
)


@dataclass(frozen=True)
class ProcessingResult:
    event_id: str
    correlation_id: str
    status: str
    attempt_count: int


class NotificationProcessor:
    def __init__(
        self,
        settings: NotificationSettings,
        store: NotificationStore,
    ) -> None:
        self.settings = settings
        self.store = store

    def process_message(
        self,
        message: bytes,
        *,
        transport_message_id: str | None = None,
        transport_correlation_id: str | None = None,
    ) -> ProcessingResult:
        event = parse_notification_event(
            message,
            transport_message_id=transport_message_id,
            transport_correlation_id=transport_correlation_id,
        )
        payload = event.model_dump_json(by_alias=True, exclude_none=True)
        payload_hash = sha256(payload.encode("utf-8")).hexdigest()
        try:
            state = self.store.record_event(
                source_event_id=str(event.event_id),
                payload_hash=payload_hash,
                correlation_id=str(event.correlation_id),
                event_type=event.event_type,
                notice_type=notice_type(event),
                available=self.settings.available,
            )
        except ConflictingNotificationEvent:
            raise InvalidNotificationEvent(
                "source event ID conflicts with stored content"
            ) from None
        return ProcessingResult(
            event_id=str(event.event_id),
            correlation_id=str(event.correlation_id),
            status=state.status,
            attempt_count=state.attempts,
        )

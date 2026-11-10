from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class InboxRecord:
    source_event_id: str
    correlation_id: str
    event_type: str
    notice_type: str
    status: str
    attempts: int
    last_error: str | None


@dataclass(frozen=True)
class DeliveryRecord:
    source_event_id: str
    correlation_id: str
    event_type: str
    notice_type: str
    delivered_at: str


@dataclass(frozen=True)
class ProcessingState:
    status: str
    attempts: int


class ConflictingNotificationEvent(ValueError):
    pass


class NotificationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS notification_inbox (
                    source_event_id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    notice_type TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('RETRYABLE', 'DELIVERED')),
                    attempts INTEGER NOT NULL,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS simulated_deliveries (
                    source_event_id TEXT PRIMARY KEY
                        REFERENCES notification_inbox(source_event_id),
                    correlation_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    notice_type TEXT NOT NULL,
                    delivered_at TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection

    def record_event(
        self,
        *,
        source_event_id: str,
        payload_hash: str,
        correlation_id: str,
        event_type: str,
        notice_type: str,
        available: bool,
    ) -> ProcessingState:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT payload_hash, status, attempts FROM notification_inbox "
                "WHERE source_event_id=?",
                (source_event_id,),
            ).fetchone()
            if existing is not None and existing["payload_hash"] != payload_hash:
                raise ConflictingNotificationEvent(
                    "source event ID was reused with different content"
                )
            if existing is not None and existing["status"] == "DELIVERED":
                return ProcessingState("DELIVERED", existing["attempts"])

            attempts = 1 if existing is None else existing["attempts"] + 1
            status = "DELIVERED" if available else "RETRYABLE"
            last_error = None if available else "NOTIFICATION_MOCK_UNAVAILABLE"
            connection.execute(
                """
                INSERT INTO notification_inbox (
                    source_event_id, payload_hash, correlation_id, event_type,
                    notice_type, status, attempts, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_event_id) DO UPDATE SET
                    status=excluded.status,
                    attempts=excluded.attempts,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (
                    source_event_id,
                    payload_hash,
                    correlation_id,
                    event_type,
                    notice_type,
                    status,
                    attempts,
                    last_error,
                    timestamp,
                ),
            )
            if available:
                connection.execute(
                    """
                    INSERT INTO simulated_deliveries (
                        source_event_id, correlation_id, event_type,
                        notice_type, delivered_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        source_event_id,
                        correlation_id,
                        event_type,
                        notice_type,
                        timestamp,
                    ),
                )
        return ProcessingState(status, attempts)

    def get_inbox(self, source_event_id: str) -> InboxRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT source_event_id, correlation_id, event_type,
                    notice_type, status, attempts, last_error
                FROM notification_inbox WHERE source_event_id=?
                """,
                (source_event_id,),
            ).fetchone()
        if row is None:
            return None
        return InboxRecord(**dict(row))

    def get_delivery(self, source_event_id: str) -> DeliveryRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT source_event_id, correlation_id, event_type,
                    notice_type, delivered_at
                FROM simulated_deliveries WHERE source_event_id=?
                """,
                (source_event_id,),
            ).fetchone()
        if row is None:
            return None
        return DeliveryRecord(**dict(row))

    def count_deliveries(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM simulated_deliveries"
            ).fetchone()
        return row["count"]

    def count_retryable(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM notification_inbox "
                "WHERE status='RETRYABLE'"
            ).fetchone()
        return row["count"]

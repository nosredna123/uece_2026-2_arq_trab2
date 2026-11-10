from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from collections.abc import Generator


@dataclass(frozen=True)
class OutboxMessage:
    event_id: str
    body: bytes
    correlation_id: str


class BillingStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    business_fingerprint TEXT NOT NULL,
                    registration_id TEXT NOT NULL,
                    activity_id TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    participant_id TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    original_payment_id TEXT,
                    approved INTEGER NOT NULL DEFAULT 0,
                    result_version INTEGER NOT NULL DEFAULT 0,
                    last_request_version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                    source_event_id TEXT NOT NULL UNIQUE,
                    request_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    event_id TEXT PRIMARY KEY,
                    body TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    published_at TEXT
                );
                CREATE INDEX IF NOT EXISTS outbox_pending
                    ON outbox(published_at, event_id);
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

    def pending_outbox(self) -> list[OutboxMessage]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT event_id, body, correlation_id FROM outbox "
                "WHERE published_at IS NULL ORDER BY rowid"
            ).fetchall()
        return [
            OutboxMessage(
                event_id=row["event_id"],
                body=row["body"].encode("utf-8"),
                correlation_id=row["correlation_id"],
            )
            for row in rows
        ]

    def mark_published(self, event_id: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self.transaction() as connection:
            connection.execute(
                "UPDATE outbox SET published_at=? "
                "WHERE event_id=? AND published_at IS NULL",
                (timestamp, event_id),
            )

    def count_attempts(self) -> int:
        return self._count("attempts")

    def count_operations(self) -> int:
        return self._count("operations")

    def count_unpublished(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM outbox WHERE published_at IS NULL"
            ).fetchone()
        return row["count"]

    def _count(self, table_name: str) -> int:
        if table_name not in {"attempts", "operations"}:
            raise ValueError("unsupported billing record table")
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table_name}"
            ).fetchone()
        return row["count"]

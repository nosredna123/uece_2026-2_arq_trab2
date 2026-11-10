from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
)


metadata = MetaData()

registration_projection = Table(
    "registration_projection",
    metadata,
    Column("registration_id", Uuid(as_uuid=True), primary_key=True),
    Column("participant_id", Uuid(as_uuid=True), nullable=False),
    Column("activity_id", Uuid(as_uuid=True), nullable=False),
    Column("organization_id", Uuid(as_uuid=True), nullable=False),
    Column("version", BigInteger, nullable=False),
    Column("snapshot", JSON, nullable=False),
)

activity_projection = Table(
    "activity_projection",
    metadata,
    Column("activity_id", Uuid(as_uuid=True), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("snapshot", JSON, nullable=False),
)

processed_event = Table(
    "processed_event",
    metadata,
    Column("event_id", Uuid(as_uuid=True), primary_key=True),
    Column("event_type", String(80), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("processed_at", DateTime(timezone=True), nullable=False),
)

check_in = Table(
    "check_in",
    metadata,
    Column(
        "registration_id",
        Uuid(as_uuid=True),
        ForeignKey("registration_projection.registration_id"),
        primary_key=True,
    ),
    Column("checked_in_at", DateTime(timezone=True), nullable=False),
)

certificate = Table(
    "certificate",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column(
        "registration_id",
        Uuid(as_uuid=True),
        ForeignKey("check_in.registration_id"),
        nullable=False,
        unique=True,
    ),
    Column("issued_at", DateTime(timezone=True), nullable=False),
)

message_outbox = Table(
    "message_outbox",
    metadata,
    Column("event_id", Uuid(as_uuid=True), primary_key=True),
    Column("event_type", String(80), nullable=False),
    Column("routing_key", String(120), nullable=False),
    Column("correlation_id", Uuid(as_uuid=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True)),
    Column("attempts", Integer, nullable=False, default=0),
    Column("last_error", Text),
)

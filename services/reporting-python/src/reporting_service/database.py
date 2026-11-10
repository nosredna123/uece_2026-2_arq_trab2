from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    JSON,
    MetaData,
    String,
    Table,
    Uuid,
)


metadata = MetaData()

projection = Table(
    "projection",
    metadata,
    Column("producer", String(64), primary_key=True),
    Column("aggregate_id", Uuid(as_uuid=True), primary_key=True),
    Column("event_type", String(64), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("organization_id", Uuid(as_uuid=True), nullable=False),
    Column("activity_id", Uuid(as_uuid=True), nullable=False),
    Column("snapshot", JSON, nullable=False),
    Column("projected_at", DateTime(timezone=True), nullable=False),
)
Index("projection_organization", projection.c.organization_id, projection.c.activity_id)

inbox = Table(
    "inbox",
    metadata,
    Column("event_id", Uuid(as_uuid=True), primary_key=True),
    Column("payload_hash", String(64), nullable=False),
    Column("processed_at", DateTime(timezone=True), nullable=False),
)

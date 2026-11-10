from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE processed_event (
            event_id uuid PRIMARY KEY,
            event_type varchar(80) NOT NULL,
            payload_hash char(64) NOT NULL,
            processed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE message_outbox (
            event_id uuid PRIMARY KEY,
            event_type varchar(80) NOT NULL,
            routing_key varchar(120) NOT NULL,
            correlation_id uuid NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
            published_at timestamptz,
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            last_error text
        );
        CREATE INDEX message_outbox_pending_idx
            ON message_outbox (created_at, event_id)
            WHERE published_at IS NULL;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE message_outbox, processed_event")

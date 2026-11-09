from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE projection (
            producer varchar(64) NOT NULL,
            aggregate_id uuid NOT NULL,
            event_type varchar(64) NOT NULL,
            version bigint NOT NULL CHECK (version > 0),
            organization_id uuid NOT NULL,
            activity_id uuid NOT NULL,
            snapshot jsonb NOT NULL,
            projected_at timestamptz NOT NULL,
            PRIMARY KEY (producer, aggregate_id, event_type)
        );
        CREATE INDEX projection_organization ON projection(organization_id, activity_id);
        CREATE TABLE inbox (
            event_id uuid PRIMARY KEY,
            payload_hash varchar(64) NOT NULL,
            processed_at timestamptz NOT NULL
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE inbox, projection")

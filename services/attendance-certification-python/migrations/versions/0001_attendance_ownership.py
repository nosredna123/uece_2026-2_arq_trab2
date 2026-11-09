from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE registration_projection (
            registration_id uuid PRIMARY KEY,
            participant_id uuid NOT NULL,
            activity_id uuid NOT NULL,
            organization_id uuid NOT NULL,
            version bigint NOT NULL CHECK (version > 0),
            snapshot jsonb NOT NULL
        );
        CREATE TABLE activity_projection (
            activity_id uuid PRIMARY KEY,
            version bigint NOT NULL CHECK (version > 0),
            snapshot jsonb NOT NULL
        );
        CREATE TABLE check_in (
            registration_id uuid PRIMARY KEY REFERENCES registration_projection(registration_id),
            checked_in_at timestamptz NOT NULL
        );
        CREATE TABLE certificate (
            id uuid PRIMARY KEY,
            registration_id uuid NOT NULL UNIQUE REFERENCES check_in(registration_id),
            issued_at timestamptz NOT NULL
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE certificate, check_in, activity_projection, registration_projection")

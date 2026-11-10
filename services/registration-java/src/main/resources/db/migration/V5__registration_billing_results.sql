ALTER TABLE billing_outbox
    ADD COLUMN suppressed_at timestamptz;

CREATE INDEX billing_outbox_unsuppressed
    ON billing_outbox (available_at, created_at, event_id)
    WHERE published_at IS NULL AND suppressed_at IS NULL;

CREATE TABLE billing_result_inbox (
    event_id uuid PRIMARY KEY,
    operation_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    registration_id uuid NOT NULL REFERENCES registration(id),
    payload_hash char(64) NOT NULL,
    disposition varchar(32) NOT NULL,
    received_at timestamptz NOT NULL
);

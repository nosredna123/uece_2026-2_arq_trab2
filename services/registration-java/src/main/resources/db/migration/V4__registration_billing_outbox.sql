CREATE TABLE billing_outbox (
    event_id uuid PRIMARY KEY,
    registration_id uuid NOT NULL REFERENCES registration(id),
    routing_key varchar(128) NOT NULL,
    correlation_id uuid NOT NULL,
    body text NOT NULL,
    created_at timestamptz NOT NULL,
    available_at timestamptz NOT NULL,
    published_at timestamptz,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    claim_token uuid,
    claim_expires_at timestamptz,
    last_error varchar(512),
    CHECK ((claim_token IS NULL) = (claim_expires_at IS NULL))
);

CREATE INDEX billing_outbox_pending
    ON billing_outbox (available_at, created_at, event_id)
    WHERE published_at IS NULL;

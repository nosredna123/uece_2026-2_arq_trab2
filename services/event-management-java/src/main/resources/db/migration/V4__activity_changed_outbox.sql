CREATE TABLE event_outbox (
    event_id uuid PRIMARY KEY,
    aggregate_id uuid NOT NULL,
    aggregate_version bigint NOT NULL CHECK (aggregate_version > 0),
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

CREATE INDEX event_outbox_pending
    ON event_outbox (available_at, created_at, event_id)
    WHERE published_at IS NULL;

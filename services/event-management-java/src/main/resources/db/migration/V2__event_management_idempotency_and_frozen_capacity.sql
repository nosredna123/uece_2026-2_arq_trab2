ALTER TABLE activity
    ADD COLUMN capacity_frozen boolean NOT NULL DEFAULT false;

CREATE TABLE command_receipt (
    actor_id uuid NOT NULL,
    operation varchar(80) NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    request_hash char(64),
    response_status integer,
    response_body text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_id, operation, idempotency_key),
    CHECK (
        (response_status IS NULL AND response_body IS NULL)
        OR (response_status BETWEEN 200 AND 299 AND response_body IS NOT NULL)
    )
);

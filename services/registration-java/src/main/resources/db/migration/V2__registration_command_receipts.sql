CREATE TABLE command_receipt (
    principal_id uuid NOT NULL,
    operation varchar(64) NOT NULL,
    idempotency_key varchar(128) NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    request_hash varchar(64) NOT NULL CHECK (length(request_hash) = 64),
    response_status integer NOT NULL CHECK (response_status BETWEEN 200 AND 299),
    response_body text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (principal_id, operation, idempotency_key)
);

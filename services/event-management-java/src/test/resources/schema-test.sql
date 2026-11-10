CREATE TABLE IF NOT EXISTS event (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    title varchar(200) NOT NULL,
    description text NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('DRAFT', 'PUBLISHED', 'CANCELLED')),
    version bigint NOT NULL CHECK (version > 0),
    CHECK (starts_at < ends_at)
);

CREATE TABLE IF NOT EXISTS activity (
    id uuid PRIMARY KEY,
    event_id uuid NOT NULL REFERENCES event(id),
    title varchar(200) NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    location text NOT NULL,
    responsible text NOT NULL,
    capacity integer NOT NULL CHECK (capacity > 0),
    amount_minor bigint NOT NULL CHECK (amount_minor >= 0),
    currency char(3) NOT NULL CHECK (currency REGEXP '^[A-Z]{3}$'),
    version bigint NOT NULL CHECK (version > 0),
    capacity_frozen boolean NOT NULL DEFAULT false,
    CHECK (starts_at < ends_at)
);

CREATE TABLE IF NOT EXISTS admission_snapshot (
    activity_id uuid PRIMARY KEY REFERENCES activity(id),
    snapshot_id uuid NOT NULL UNIQUE,
    snapshot_version bigint NOT NULL CHECK (snapshot_version > 0),
    frozen_capacity integer NOT NULL CHECK (frozen_capacity > 0)
);

CREATE TABLE IF NOT EXISTS command_receipt (
    actor_id uuid NOT NULL,
    operation varchar(80) NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    request_hash char(64),
    response_status integer,
    response_body text,
    created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (actor_id, operation, idempotency_key),
    CHECK (
        (response_status IS NULL AND response_body IS NULL)
        OR (response_status BETWEEN 200 AND 299 AND response_body IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS event_outbox (
  event_id UUID PRIMARY KEY,
  aggregate_id UUID NOT NULL,
  aggregate_version BIGINT NOT NULL CHECK (aggregate_version > 0),
  routing_key VARCHAR(128) NOT NULL,
  correlation_id UUID NOT NULL,
  body CLOB NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  available_at TIMESTAMP WITH TIME ZONE NOT NULL,
  published_at TIMESTAMP WITH TIME ZONE,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  claim_token UUID,
  claim_expires_at TIMESTAMP WITH TIME ZONE,
  last_error VARCHAR(512),
  CHECK ((claim_token IS NULL) = (claim_expires_at IS NULL))
);

CREATE TABLE IF NOT EXISTS command_lock (
    stripe_id smallint PRIMARY KEY CHECK (stripe_id BETWEEN 0 AND 63)
);

MERGE INTO command_lock KEY(stripe_id)
VALUES
    (0), (1), (2), (3), (4), (5), (6), (7),
    (8), (9), (10), (11), (12), (13), (14), (15),
    (16), (17), (18), (19), (20), (21), (22), (23),
    (24), (25), (26), (27), (28), (29), (30), (31),
    (32), (33), (34), (35), (36), (37), (38), (39),
    (40), (41), (42), (43), (44), (45), (46), (47),
    (48), (49), (50), (51), (52), (53), (54), (55),
    (56), (57), (58), (59), (60), (61), (62), (63);

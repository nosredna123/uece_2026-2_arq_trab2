CREATE TABLE event (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    title varchar(200) NOT NULL,
    description text NOT NULL,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('DRAFT', 'PUBLISHED', 'CANCELLED')),
    version bigint NOT NULL CHECK (version > 0),
    CHECK (starts_at < ends_at)
);

CREATE TABLE activity (
    id uuid PRIMARY KEY,
    event_id uuid NOT NULL REFERENCES event(id),
    title varchar(200) NOT NULL,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    location text NOT NULL,
    responsible text NOT NULL,
    capacity integer NOT NULL CHECK (capacity > 0),
    amount_minor bigint NOT NULL CHECK (amount_minor >= 0),
    currency char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    version bigint NOT NULL CHECK (version > 0),
    CHECK (starts_at < ends_at)
);

CREATE TABLE admission_snapshot (
    activity_id uuid PRIMARY KEY REFERENCES activity(id),
    snapshot_id uuid NOT NULL UNIQUE,
    snapshot_version bigint NOT NULL CHECK (snapshot_version > 0),
    frozen_capacity integer NOT NULL CHECK (frozen_capacity > 0)
);

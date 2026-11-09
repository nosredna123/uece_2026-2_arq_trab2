CREATE TABLE activity_ledger (
    activity_id uuid PRIMARY KEY,
    snapshot_id uuid NOT NULL UNIQUE,
    snapshot_version bigint NOT NULL CHECK (snapshot_version > 0),
    frozen_capacity integer NOT NULL CHECK (frozen_capacity > 0),
    occupied integer NOT NULL DEFAULT 0,
    CHECK (occupied >= 0 AND occupied <= frozen_capacity)
);

CREATE TABLE registration (
    id uuid PRIMARY KEY,
    activity_id uuid NOT NULL REFERENCES activity_ledger(activity_id),
    organization_id uuid NOT NULL,
    participant_id uuid NOT NULL,
    status varchar(24) NOT NULL CHECK (status IN (
        'WAITLISTED', 'OFFERED', 'PENDING_PAYMENT', 'CONFIRMED', 'REFUSED', 'EXPIRED', 'CANCELLED'
    )),
    refund_status varchar(16) NOT NULL CHECK (refund_status IN ('NONE', 'PENDING', 'APPROVED')),
    created_at timestamptz NOT NULL,
    expires_at timestamptz,
    offer_expires_at timestamptz,
    amount_minor bigint NOT NULL CHECK (amount_minor >= 0),
    currency char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    version bigint NOT NULL CHECK (version > 0),
    CHECK (status <> 'PENDING_PAYMENT' OR expires_at IS NOT NULL),
    CHECK (status <> 'OFFERED' OR offer_expires_at IS NOT NULL)
);

CREATE UNIQUE INDEX one_active_enrollment ON registration(activity_id, participant_id)
WHERE status IN ('WAITLISTED', 'OFFERED', 'PENDING_PAYMENT', 'CONFIRMED');

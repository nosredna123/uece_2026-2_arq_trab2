CREATE TABLE waitlist_entry (
    registration_id uuid PRIMARY KEY REFERENCES registration(id),
    activity_id uuid NOT NULL REFERENCES activity_ledger(activity_id),
    sequence bigint NOT NULL CHECK (sequence > 0),
    UNIQUE (activity_id, sequence)
);

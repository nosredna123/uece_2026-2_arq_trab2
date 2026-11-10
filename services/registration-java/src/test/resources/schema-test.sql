CREATE TABLE IF NOT EXISTS activity_ledger (
  activity_id UUID PRIMARY KEY,
  snapshot_id UUID NOT NULL UNIQUE,
  snapshot_version BIGINT NOT NULL CHECK (snapshot_version > 0),
  frozen_capacity INTEGER NOT NULL CHECK (frozen_capacity > 0),
  occupied INTEGER NOT NULL DEFAULT 0,
  CHECK (occupied >= 0 AND occupied <= frozen_capacity)
);

CREATE TABLE IF NOT EXISTS registration (
  id UUID PRIMARY KEY,
  activity_id UUID NOT NULL,
  organization_id UUID NOT NULL,
  participant_id UUID NOT NULL,
  status VARCHAR(24) NOT NULL,
  refund_status VARCHAR(16) NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  expires_at TIMESTAMP WITH TIME ZONE,
  offer_expires_at TIMESTAMP WITH TIME ZONE,
  amount_minor BIGINT NOT NULL CHECK (amount_minor >= 0),
  currency CHAR(3) NOT NULL,
  version BIGINT NOT NULL CHECK (version > 0)
);

CREATE TABLE IF NOT EXISTS command_receipt (
  principal_id UUID NOT NULL,
  operation VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(128) NOT NULL,
  request_hash VARCHAR(64) NOT NULL,
  response_status INTEGER NOT NULL,
  response_body CLOB NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (principal_id, operation, idempotency_key)
);

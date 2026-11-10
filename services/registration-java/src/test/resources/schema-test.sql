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

CREATE TABLE IF NOT EXISTS waitlist_entry (
  registration_id UUID PRIMARY KEY,
  activity_id UUID NOT NULL,
  sequence BIGINT NOT NULL CHECK (sequence > 0),
  UNIQUE (activity_id, sequence)
);

CREATE TABLE IF NOT EXISTS billing_outbox (
  event_id UUID PRIMARY KEY,
  registration_id UUID NOT NULL,
  routing_key VARCHAR(128) NOT NULL,
  correlation_id UUID NOT NULL,
  body CLOB NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  available_at TIMESTAMP WITH TIME ZONE NOT NULL,
  published_at TIMESTAMP WITH TIME ZONE,
  suppressed_at TIMESTAMP WITH TIME ZONE,
  attempts INTEGER NOT NULL DEFAULT 0,
  claim_token UUID,
  claim_expires_at TIMESTAMP WITH TIME ZONE,
  last_error VARCHAR(512)
);

CREATE TABLE IF NOT EXISTS billing_result_inbox (
  event_id UUID PRIMARY KEY,
  operation_id UUID NOT NULL,
  attempt_id UUID NOT NULL,
  registration_id UUID NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  body CLOB NOT NULL,
  disposition VARCHAR(32) NOT NULL,
  received_at TIMESTAMP WITH TIME ZONE NOT NULL
);

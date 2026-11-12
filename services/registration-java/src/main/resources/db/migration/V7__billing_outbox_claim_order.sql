-- The publisher claims the oldest due message with ORDER BY created_at, event_id.
-- The earlier indexes lead with available_at, so every claim scanned and sorted
-- the whole backlog; this index lets the claim read the oldest rows in order.
CREATE INDEX billing_outbox_claim_order
  ON billing_outbox (created_at, event_id)
  WHERE published_at IS NULL AND suppressed_at IS NULL;

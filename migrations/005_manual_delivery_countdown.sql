-- Live customer countdown + SLA deadline columns for PENDING_MANUAL_DELIVERY.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS status_chat_id BIGINT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS status_message_id BIGINT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS manual_deadline_at TIMESTAMPTZ;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS status_countdown_closed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS status_overdue_notified BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_orders_manual_deadline_at ON orders (manual_deadline_at);

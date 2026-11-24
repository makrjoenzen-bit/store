-- Sham Cash QR / proof-channel support
ALTER TABLE orders ADD COLUMN IF NOT EXISTS proof_posted_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_orders_proof_posted_at ON orders (proof_posted_at);

-- Phase 11B / Suite 5: critical composite indexes for hot query paths.
-- Safe to re-run: PostgreSQL ignores duplicates with IF NOT EXISTS.
-- Mirrors the Index(...) declarations in src/core/models/entities.py.

CREATE INDEX IF NOT EXISTS idx_orders_customer_state
    ON orders (customer_id, state);

CREATE INDEX IF NOT EXISTS idx_payment_invoices_status_expires
    ON payment_invoices (status, expires_at);

CREATE INDEX IF NOT EXISTS idx_wallet_ledger_customer_created
    ON wallet_transactions (customer_id, created_at);

-- Hot listing / supplier-link paths + nullable-unique tx_hash dedup
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders (created_at);

CREATE INDEX IF NOT EXISTS idx_product_supplier_links_supplier_id
    ON product_supplier_links (supplier_id);
CREATE INDEX IF NOT EXISTS idx_product_supplier_links_master_product_id
    ON product_supplier_links (master_product_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_invoices_tx_hash
    ON payment_invoices (tx_hash)
    WHERE tx_hash IS NOT NULL;

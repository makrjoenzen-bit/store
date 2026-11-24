-- Security / concurrency hardening indexes (safe to re-run).

-- Order listing / treasury windows
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders (created_at);

-- Supplier link lookups
CREATE INDEX IF NOT EXISTS idx_product_supplier_links_supplier_id
    ON product_supplier_links (supplier_id);
CREATE INDEX IF NOT EXISTS idx_product_supplier_links_master_product_id
    ON product_supplier_links (master_product_id);

-- Atomic TX dedup (nullable unique: multiple NULLs allowed)
CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_invoices_tx_hash
    ON payment_invoices (tx_hash)
    WHERE tx_hash IS NOT NULL;

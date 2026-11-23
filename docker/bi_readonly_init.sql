-- Phase 17 — read-only BI role + analytical views (safe for Metabase).
-- Apply manually or via: psql $DATABASE_URL_SYNC -f docker/bi_readonly_init.sql

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bi_readonly') THEN
    CREATE ROLE bi_readonly LOGIN PASSWORD 'bi_readonly_change_me';
  END IF;
END
$$;

GRANT CONNECT ON DATABASE aggregator_db TO bi_readonly;
GRANT USAGE ON SCHEMA public TO bi_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bi_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bi_readonly;

CREATE OR REPLACE VIEW v_daily_net_profit AS
SELECT
  DATE(created_at AT TIME ZONE 'UTC') AS day,
  COUNT(*) FILTER (WHERE state IN ('completed', 'order_fulfilled', 'delivered')) AS completed_orders,
  COALESCE(SUM(our_price) FILTER (WHERE state IN ('completed', 'order_fulfilled', 'delivered')), 0) AS revenue,
  COALESCE(SUM(supplier_cost) FILTER (WHERE state IN ('completed', 'order_fulfilled', 'delivered')), 0) AS cogs,
  COALESCE(SUM(our_price - COALESCE(supplier_cost, 0))
    FILTER (WHERE state IN ('completed', 'order_fulfilled', 'delivered')), 0) AS net_profit
FROM orders
GROUP BY 1
ORDER BY 1 DESC;

CREATE OR REPLACE VIEW v_supplier_reliability AS
SELECT
  s.id AS supplier_id,
  s.name,
  s.priority_weight,
  COALESCE(s.quality_penalty_score, 0) AS quality_penalty_score,
  (s.priority_weight - COALESCE(s.quality_penalty_score, 0)) AS effective_priority,
  s.warranty_claim_rate_30d,
  s.api_failure_rate_30d,
  s.status::text AS status
FROM suppliers s;

CREATE OR REPLACE VIEW v_customer_retention AS
SELECT
  c.id AS customer_id,
  c.telegram_id,
  c.total_orders,
  c.total_spent,
  c.vip_tier::text AS vip_tier,
  c.created_at AS first_seen,
  (
    SELECT MAX(o.created_at) FROM orders o WHERE o.customer_id = c.id
  ) AS last_order_at
FROM customers c;

GRANT SELECT ON v_daily_net_profit, v_supplier_reliability, v_customer_retention TO bi_readonly;

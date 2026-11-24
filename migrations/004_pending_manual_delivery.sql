-- Add PENDING_MANUAL_DELIVERY to PostgreSQL OrderState enum (safe to re-run).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_enum e
        JOIN pg_type t ON e.enumtypid = t.oid
        WHERE t.typname = 'orderstate' AND e.enumlabel = 'pending_manual_delivery'
    ) THEN
        ALTER TYPE orderstate ADD VALUE 'pending_manual_delivery';
    END IF;
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN undefined_object THEN NULL;
END $$;

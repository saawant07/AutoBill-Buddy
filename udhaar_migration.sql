-- ============================================================================
-- AutoBill Buddy: Udhaar (Credit) Management Migration
-- Run this script in the Supabase SQL Editor
-- ============================================================================

-- ============================================================================
-- STEP 1: Add payment_mode and is_settled columns to sales table
-- ============================================================================

ALTER TABLE sales
ADD COLUMN IF NOT EXISTS payment_mode TEXT DEFAULT 'Cash';

ALTER TABLE sales
ADD COLUMN IF NOT EXISTS is_settled BOOLEAN DEFAULT true;

-- ============================================================================
-- STEP 2: Create dues table
-- ============================================================================

CREATE TABLE IF NOT EXISTS dues (
    id BIGSERIAL PRIMARY KEY,
    customer_name TEXT NOT NULL,
    total_due NUMERIC DEFAULT 0,
    last_updated TIMESTAMPTZ DEFAULT now(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE
);

-- Create index for faster user lookups
CREATE INDEX IF NOT EXISTS idx_dues_user_id ON dues(user_id);

-- Add unique constraint for upsert support (user_id + customer_name must be unique)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'dues_user_customer_unique'
    ) THEN
        ALTER TABLE dues ADD CONSTRAINT dues_user_customer_unique UNIQUE (user_id, customer_name);
    END IF;
END $$;

-- ============================================================================
-- STEP 3: Enable RLS on dues table
-- ============================================================================

ALTER TABLE dues ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- STEP 4: Create RLS policies for dues table
-- ============================================================================

DROP POLICY IF EXISTS "Users can view their own dues" ON dues;
DROP POLICY IF EXISTS "Users can insert their own dues" ON dues;
DROP POLICY IF EXISTS "Users can update their own dues" ON dues;
DROP POLICY IF EXISTS "Users can delete their own dues" ON dues;

CREATE POLICY "Users can view their own dues"
ON dues FOR SELECT
USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own dues"
ON dues FOR INSERT
WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own dues"
ON dues FOR UPDATE
USING (auth.uid() = user_id)
WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete their own dues"
ON dues FOR DELETE
USING (auth.uid() = user_id);

-- ============================================================================
-- NOTES:
-- All existing sales rows will default to payment_mode='Cash', is_settled=true
-- No existing data is affected.
-- ============================================================================

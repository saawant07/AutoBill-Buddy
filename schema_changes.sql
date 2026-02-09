-- ============================================================================
-- AutoBill Buddy: Multi-Tenant Schema Migration
-- Run this script in the Supabase SQL Editor
-- ============================================================================

-- ============================================================================
-- STEP 1: Add user_id and price columns to inventory table
-- ============================================================================

ALTER TABLE inventory 
ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE;

ALTER TABLE inventory 
ADD COLUMN IF NOT EXISTS price DECIMAL(10, 2) DEFAULT 0;

-- Create index for faster user lookups
CREATE INDEX IF NOT EXISTS idx_inventory_user_id ON inventory(user_id);

-- Add unique constraint for upsert support (user_id + item_name must be unique)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'inventory_user_item_unique'
    ) THEN
        ALTER TABLE inventory ADD CONSTRAINT inventory_user_item_unique UNIQUE (user_id, item_name);
    END IF;
END $$;

-- ============================================================================
-- STEP 2: Add user_id column to sales table
-- ============================================================================

ALTER TABLE sales 
ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE;

-- Create index for faster user lookups
CREATE INDEX IF NOT EXISTS idx_sales_user_id ON sales(user_id);

-- ============================================================================
-- STEP 3: Enable Row Level Security (RLS)
-- ============================================================================

ALTER TABLE inventory ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- STEP 4: Create RLS policies for inventory table
-- ============================================================================

-- Drop existing policies if they exist (for re-running this script)
DROP POLICY IF EXISTS "Users can view their own inventory" ON inventory;
DROP POLICY IF EXISTS "Users can insert their own inventory" ON inventory;
DROP POLICY IF EXISTS "Users can update their own inventory" ON inventory;
DROP POLICY IF EXISTS "Users can delete their own inventory" ON inventory;

-- SELECT: Users can only view their own inventory
CREATE POLICY "Users can view their own inventory"
ON inventory FOR SELECT
USING (auth.uid() = user_id);

-- INSERT: Users can only insert into their own inventory
CREATE POLICY "Users can insert their own inventory"
ON inventory FOR INSERT
WITH CHECK (auth.uid() = user_id);

-- UPDATE: Users can only update their own inventory
CREATE POLICY "Users can update their own inventory"
ON inventory FOR UPDATE
USING (auth.uid() = user_id)
WITH CHECK (auth.uid() = user_id);

-- DELETE: Users can only delete their own inventory
CREATE POLICY "Users can delete their own inventory"
ON inventory FOR DELETE
USING (auth.uid() = user_id);

-- ============================================================================
-- STEP 5: Create RLS policies for sales table
-- ============================================================================

-- Drop existing policies if they exist (for re-running this script)
DROP POLICY IF EXISTS "Users can view their own sales" ON sales;
DROP POLICY IF EXISTS "Users can insert their own sales" ON sales;
DROP POLICY IF EXISTS "Users can update their own sales" ON sales;
DROP POLICY IF EXISTS "Users can delete their own sales" ON sales;

-- SELECT: Users can only view their own sales
CREATE POLICY "Users can view their own sales"
ON sales FOR SELECT
USING (auth.uid() = user_id);

-- INSERT: Users can only insert their own sales
CREATE POLICY "Users can insert their own sales"
ON sales FOR INSERT
WITH CHECK (auth.uid() = user_id);

-- UPDATE: Users can only update their own sales
CREATE POLICY "Users can update their own sales"
ON sales FOR UPDATE
USING (auth.uid() = user_id)
WITH CHECK (auth.uid() = user_id);

-- DELETE: Users can only delete their own sales
CREATE POLICY "Users can delete their own sales"
ON sales FOR DELETE
USING (auth.uid() = user_id);

-- ============================================================================
-- NOTES:
-- 
-- 1. After running this script, existing data will NOT be visible to any user
--    because user_id will be NULL. You have two options:
--    
--    Option A: Delete existing data and let each user re-seed
--      TRUNCATE inventory CASCADE;
--      TRUNCATE sales CASCADE;
--    
--    Option B: Migrate existing data to a specific user
--      UPDATE inventory SET user_id = 'YOUR-USER-UUID-HERE' WHERE user_id IS NULL;
--      UPDATE sales SET user_id = 'YOUR-USER-UUID-HERE' WHERE user_id IS NULL;
--
-- 2. The service role key bypasses RLS. The refactored main.py uses
--    the user's JWT token for authenticated requests, so RLS applies.
-- ============================================================================

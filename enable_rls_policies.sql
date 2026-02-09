-- ============================================================================
-- AutoBill Buddy: Enable Row Level Security (RLS) Policies
-- Run this script in the Supabase SQL Editor AFTER running schema_changes.sql
-- ============================================================================

-- Step 1: Enable RLS on tables
ALTER TABLE inventory ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales ENABLE ROW LEVEL SECURITY;

-- Step 2: Drop any existing policies (clean slate)
DROP POLICY IF EXISTS "Users see own inventory" ON inventory;
DROP POLICY IF EXISTS "Users insert own inventory" ON inventory;
DROP POLICY IF EXISTS "Users update own inventory" ON inventory;
DROP POLICY IF EXISTS "Users delete own inventory" ON inventory;

DROP POLICY IF EXISTS "Users see own sales" ON sales;
DROP POLICY IF EXISTS "Users insert own sales" ON sales;
DROP POLICY IF EXISTS "Users update own sales" ON sales;
DROP POLICY IF EXISTS "Users delete own sales" ON sales;

-- Step 3: Create Inventory Policies
CREATE POLICY "Users see own inventory" ON inventory 
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users insert own inventory" ON inventory 
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users update own inventory" ON inventory 
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users delete own inventory" ON inventory 
    FOR DELETE USING (auth.uid() = user_id);

-- Step 4: Create Sales Policies
CREATE POLICY "Users see own sales" ON sales 
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users insert own sales" ON sales 
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users update own sales" ON sales 
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users delete own sales" ON sales 
    FOR DELETE USING (auth.uid() = user_id);

-- ============================================================================
-- IMPORTANT: After running this script, you should:
-- 1. Clear any orphan data: DELETE FROM sales WHERE user_id IS NULL;
-- 2. Clear any orphan inventory: DELETE FROM inventory WHERE user_id IS NULL;
-- ============================================================================

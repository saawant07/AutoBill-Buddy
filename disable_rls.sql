-- ============================================================================
-- DISABLE ROW LEVEL SECURITY (RLS)
-- Run this to restore access for the "Old UI" (No Authentication)
-- ============================================================================

-- Disable RLS on inventory table to allow public read/write
ALTER TABLE inventory DISABLE ROW LEVEL SECURITY;

-- Disable RLS on sales table to allow public read/write
ALTER TABLE sales DISABLE ROW LEVEL SECURITY;

-- Optional: If you prefer to keep RLS but allow public access, use these instead:
-- CREATE POLICY "Public Access" ON inventory FOR ALL USING (true);
-- CREATE POLICY "Public Access" ON sales FOR ALL USING (true);

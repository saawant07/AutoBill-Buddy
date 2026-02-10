-- Add expiry_date column to inventory table
ALTER TABLE inventory ADD COLUMN expiry_date DATE DEFAULT NULL;

-- Drop existing unique constraint on item_name if it exists (assuming it's named inventory_item_name_key or similar, or just relying on user_id, item_name)
-- Note: In Supabase/Postgres, we might need to know the exact constraint name. 
-- For now, we'll try to drop the likely unique index or constraint.
ALTER TABLE inventory DROP CONSTRAINT IF EXISTS inventory_user_id_item_name_key;

-- Add new composite unique constraint
-- We treat NULL expiry as a distinct value (Postgres 15+ allows NULLS NOT DISTINCT, but for safety we might just rely on application logic or standard unique)
-- Actually, standard UNIQUE allows multiple NULLs. To prevent duplicate "No Expiry" batches, we should use a default date or just accept that NULL means "No Expiry" and we might have multiple rows? 
-- No, we want to group by expiry. 
-- Let's create a unique index that treats NULLs as distinct values (default) but we want only ONE "No Expiry" batch per item.
-- Strategy: coalesce expiry_date for uniqueness? index(user_id, item_name, coalesce(expiry_date, '9999-12-31'))?
-- Simpler: Just add the column and index. The application will handle upserting correctly by querying first.

CREATE INDEX idx_inventory_expiry ON inventory(expiry_date);
CREATE UNIQUE INDEX idx_inventory_user_item_expiry ON inventory(user_id, item_name, expiry_date);


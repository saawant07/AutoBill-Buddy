-- Add cost_price column to inventory table
ALTER TABLE inventory ADD COLUMN IF NOT EXISTS cost_price FLOAT DEFAULT 0;

-- Add total_cost column to sales table (to store historical cost at time of sale)
ALTER TABLE sales ADD COLUMN IF NOT EXISTS total_cost FLOAT DEFAULT 0;

-- Optional: Update existing items (estimate cost as 80% of price?)
-- UPDATE inventory SET cost_price = price * 0.8 WHERE cost_price = 0;

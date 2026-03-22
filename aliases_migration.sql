-- Add aliases column to inventory table for multilingual search
ALTER TABLE inventory ADD COLUMN IF NOT EXISTS aliases TEXT;

-- Enable pg_trgm extension for fuzzy/substring matching (required for GIN on TEXT)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Create GIN index with trigram operator class for fuzzy search on aliases
CREATE INDEX IF NOT EXISTS idx_inventory_aliases ON inventory USING GIN (aliases gin_trgm_ops);
-- FileMoon columns: add to the existing DoodStream tables.
-- Run in Supabase SQL Editor. Safe to re-run (IF NOT EXISTS).

ALTER TABLE movies      ADD COLUMN IF NOT EXISTS filemoon_url TEXT;
ALTER TABLE movies      ADD COLUMN IF NOT EXISTS filemoon_download_url TEXT;

ALTER TABLE tv_episodes ADD COLUMN IF NOT EXISTS filemoon_url TEXT;
ALTER TABLE tv_episodes ADD COLUMN IF NOT EXISTS filemoon_download_url TEXT;
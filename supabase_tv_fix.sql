-- Fix TV episodes support for the live Supabase 'movies' table
-- Run this in your Supabase SQL Editor (Dashboard > SQL Editor)

-- 1) Fast lookup on TV episodes stored in the movies table
CREATE INDEX IF NOT EXISTS idx_movies_media_type
    ON movies(media_type);

CREATE INDEX IF NOT EXISTS idx_movies_season_episode
    ON movies(media_type, season_number, episode_number);

-- 2) Allow deletions (the current policy set only allows SELECT/INSERT/UPDATE,
--    so DELETE queries are silently ignored by the API).
CREATE POLICY "Allow public delete" ON movies FOR DELETE USING (true);

-- 3) OPTIONAL: if you prefer a dedicated tv_episodes table instead of storing
--    episodes inside movies, run create_supabase_table.sql and then delete the
--    TV rows from movies:
-- DELETE FROM movies WHERE media_type = 'tv';
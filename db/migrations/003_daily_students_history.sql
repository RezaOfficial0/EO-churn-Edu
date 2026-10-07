-- Keep one snapshot per student per day instead of overwriting (see db/schema.sql).
--
-- Idempotent on purpose: init_db.py also adopts an untracked database built
-- directly from db/schema.sql, which already has the new shape.
--
-- The FK relied on the single-column PK, which no longer exists.
-- Existing rows get the date they were loaded: updated_at is set on every
-- upsert, so it is the load date of the data each row currently holds.

ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_student_id_fkey;

ALTER TABLE daily_students ADD COLUMN IF NOT EXISTS as_of_date DATE;
UPDATE daily_students SET as_of_date = updated_at::date WHERE as_of_date IS NULL;
ALTER TABLE daily_students ALTER COLUMN as_of_date SET NOT NULL;

ALTER TABLE daily_students DROP CONSTRAINT IF EXISTS daily_students_pkey;
ALTER TABLE daily_students ADD PRIMARY KEY (student_id, as_of_date);

CREATE INDEX IF NOT EXISTS daily_students_as_of_idx ON daily_students (as_of_date);
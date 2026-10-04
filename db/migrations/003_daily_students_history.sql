-- Keep one snapshot per student per day instead of overwriting (see db/schema.sql).
--
-- alerts -> daily_students FK relied on the single-column PK, which no longer exists.
-- Existing rows are carried over under today's date.

ALTER TABLE alerts DROP CONSTRAINT alerts_student_id_fkey;

ALTER TABLE daily_students ADD COLUMN as_of_date DATE;
UPDATE daily_students SET as_of_date = CURRENT_DATE;
ALTER TABLE daily_students ALTER COLUMN as_of_date SET NOT NULL;

ALTER TABLE daily_students DROP CONSTRAINT daily_students_pkey;
ALTER TABLE daily_students ADD PRIMARY KEY (student_id, as_of_date);

CREATE INDEX IF NOT EXISTS daily_students_as_of_idx ON daily_students (as_of_date);
-- Bring a database created before this change in line with db/schema.sql.
--
-- Adds the (run_at, student_id) uniqueness guarantee to `alerts` and drops the
-- now-redundant run_at-only index, which the new index supersedes because
-- run_at is its leading column.
--
--     psql -d eo_churn -f db/migrations/002_alerts_unique_run_at_student_id.sql
--
-- If the CREATE fails with "could not create unique index", the table already
-- contains a student listed twice inside one run. Find them with:
--
--     SELECT run_at, student_id, count(*)
--     FROM alerts GROUP BY 1, 2 HAVING count(*) > 1;
--
-- and delete the extra rows (keep the lowest id of each group) before re-running:
--
--     DELETE FROM alerts a USING alerts b
--     WHERE a.run_at = b.run_at AND a.student_id = b.student_id AND a.id > b.id;

CREATE UNIQUE INDEX IF NOT EXISTS alerts_run_at_student_id_uidx
    ON alerts (run_at, student_id);

DROP INDEX IF EXISTS alerts_run_at_idx;

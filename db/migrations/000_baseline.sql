-- 000 - the schema as it stood when migrations started being tracked (B-16).
--
-- A FROZEN copy of db/schema.sql at that point. Never edit this file: a database
-- that already recorded 000 will not run it again, so an edit here only changes
-- what NEW databases get, and the two would drift apart silently. Every schema
-- change is a new NNN_*.sql file, plus the same change in db/schema.sql (the
-- readable "current shape" document). tests/test_migrations.py checks that a
-- database built from these files matches one built from db/schema.sql.
--
-- Everything is IF NOT EXISTS, so this also runs cleanly against a database that
-- was created before tracking existed - that is how such a database is adopted.

CREATE TABLE IF NOT EXISTS daily_students (
    student_id      TEXT PRIMARY KEY,
    enrollment_date DATE,
    features        JSONB       NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- One row per at-risk student per run. Append-only: history is the point, since
-- `status` is defined against the previous run.
--
-- churn_probability is DOUBLE PRECISION, not NUMERIC(5,4): the whole point of the
-- CSV/DB switch is that both backends produce the same numbers, and NUMERIC(5,4)
-- would silently round 0.648649 to 0.6486. It also reaches Python as a float rather
-- than a Decimal, which is what the API and the dashboard expect.
CREATE TABLE IF NOT EXISTS alerts (
    id                 SERIAL PRIMARY KEY,
    student_id         TEXT        NOT NULL REFERENCES daily_students(student_id),
    run_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    churn_probability  DOUBLE PRECISION NOT NULL,
    status             TEXT        NOT NULL CHECK (status IN ('new', 'still_at_risk')),
    top_reasons        TEXT,
    top_reasons_detail JSONB
);

-- "this student's alert history" (student detail view).
CREATE INDEX IF NOT EXISTS alerts_student_id_idx ON alerts (student_id);

-- Two jobs in one index.
--
-- 1. Uniqueness. A run writes each at-risk student exactly once - the pipeline
--    builds one dataframe per run, so student_id is unique within it by
--    construction. Nothing enforced that, though: a row inserted by hand, a
--    restored dump, or the pipeline called twice with the same explicit run_at
--    could put a student in a run twice and quietly double-count them in the
--    daily message. The database now refuses it.
-- 2. Lookup. Every run write asks "which students were flagged in the run with
--    the largest run_at?" - that is what decides new vs. still_at_risk. run_at
--    leads this index, so it answers that query too and a separate
--    alerts_run_at_idx would be redundant.
CREATE UNIQUE INDEX IF NOT EXISTS alerts_run_at_student_id_uidx
    ON alerts (run_at, student_id);

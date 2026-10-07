-- EO-Churn-Edu — operational schema for the DAILY pipeline (DATA_SOURCE=db).
--
-- Scope: only the daily pipeline's operational data lives here. Training data and
-- the training pipeline stay CSV-based and are not represented in this schema.
--
-- Create a database from it with:
--
--     createdb eo_churn
--     python scripts/init_db.py
--
-- This file is the CURRENT SHAPE of the schema, kept readable in one place.
-- `python scripts/init_db.py` does not apply it: it runs db/migrations/ (tracked
-- in schema_migrations, each file once). Changing the schema means a new
-- db/migrations/NNN_*.sql AND the same edit here; tests/test_migrations.py fails
-- when the two disagree. Applying this file directly still works for a scratch
-- database (every statement is IF NOT EXISTS), but that database is untracked.


-- Snapshot of students to score on a given date.
-- student_id + as_of_date form the primary key so the same student can appear
-- on multiple scoring dates without overwriting previous rows.
--
-- Features are stored as JSONB rather than typed columns because the feature
-- list is client-specific (see config.FEATURES). Re-pointing EO-Churn at a new
-- dataset should not require a migration. The trade-off is that the database
-- does not enforce feature types or presence — src/data/loader.py does that
-- by coercing values back to numeric on read.
CREATE TABLE IF NOT EXISTS daily_students (
    student_id      TEXT        NOT NULL,
    enrollment_date DATE,
    features        JSONB       NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    as_of_date      DATE        NOT NULL,
    PRIMARY KEY (student_id, as_of_date)
);

CREATE INDEX IF NOT EXISTS daily_students_as_of_idx ON daily_students (as_of_date);


-- One row per daily run - including the runs that flagged nobody and the ones that
-- failed. "Ran, nobody at risk" and "did not run" used to look identical (nothing
-- was written either way); a missing row here now means the run did not happen.
--
-- status:
--   ok          at least one student at or above the threshold
--   no_alerts   ran to the end, nobody at or above the threshold
--   failed      stopped before writing alerts (bad export, unreachable database...)
--   superseded  only written by migration 004, for an older run on a day that
--               had several before this table existed; the pipeline never writes it
--
-- One successful run per run_date (the day in config.SCHEDULER_TIMEZONE). A second
-- run that day - a manual re-run, or two schedulers racing - is refused rather
-- than recorded, so a student cannot be counted twice in a day. Failed runs are
-- outside the index, so a retry after a failure still goes through.
CREATE TABLE IF NOT EXISTS runs (
    run_id        BIGSERIAL        PRIMARY KEY,
    run_date      DATE             NOT NULL,
    started_at    TIMESTAMPTZ      NOT NULL,
    finished_at   TIMESTAMPTZ      NOT NULL DEFAULT now(),
    model_version TEXT,
    threshold     DOUBLE PRECISION,
    entity_count  INTEGER,
    at_risk_count INTEGER,
    status        TEXT             NOT NULL
        CHECK (status IN ('ok', 'no_alerts', 'failed', 'superseded'))
);

CREATE UNIQUE INDEX IF NOT EXISTS runs_run_date_uidx
    ON runs (run_date) WHERE status IN ('ok', 'no_alerts');


-- One row per at-risk student per run. Append-only: history is the point, since
-- `status` is defined against the previous run.
--
-- churn_probability is DOUBLE PRECISION, not NUMERIC(5,4): the whole point of the
-- CSV/DB switch is that both backends produce the same numbers, and NUMERIC(5,4)
-- would silently round 0.648649 to 0.6486. It also reaches Python as a float rather
-- than a Decimal, which is what the API and the dashboard expect.
--
-- run_at is the run's started_at, copied onto every row so the alert log reads on
-- its own; run_id is what groups a run.

-- NOTE: student_id is no longer a foreign key to daily_students. In daily_students
-- student_id is now only half of the primary key (student_id, as_of_date), so the
-- old single-column FK cannot exist. The database therefore no longer guarantees that
-- an alert's student was ever scored. A composite FK (student_id, as_of_date) would
-- need alerts.as_of_date and is tracked as a follow-up.
CREATE TABLE IF NOT EXISTS alerts (
    id                 SERIAL PRIMARY KEY,
    student_id         TEXT        NOT NULL,
    run_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    churn_probability  DOUBLE PRECISION NOT NULL,
    status             TEXT        NOT NULL CHECK (status IN ('new', 'still_at_risk')),
    top_reasons        TEXT,
    top_reasons_detail JSONB,
    run_id             BIGINT      NOT NULL REFERENCES runs (run_id)
);

-- "this student's alert history" (student detail view).
CREATE INDEX IF NOT EXISTS alerts_student_id_idx ON alerts (student_id);

-- A run writes each at-risk student exactly once. The old (run_at, student_id)
-- index did not stop two concurrent runs - each took its own microsecond and both
-- were accepted. It also serves "which students were flagged in run N?", which is
-- what decides new vs. still_at_risk.
CREATE UNIQUE INDEX IF NOT EXISTS alerts_run_id_student_id_uidx
    ON alerts (run_id, student_id);

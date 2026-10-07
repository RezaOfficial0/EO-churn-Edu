-- One row per daily run, including the runs that flagged nobody and the ones that
-- failed (see db/schema.sql). alerts.run_id points at the run that wrote it.
--
-- Idempotent on purpose, like 003: init_db.py also adopts an untracked database
-- built directly from db/schema.sql, which already has the new shape.
--
-- Existing alerts get one legacy run per distinct run_at - that is how the old
-- code grouped a run. Legacy runs carry no model_version, threshold or
-- entity_count: that was never recorded. When a day holds more than one legacy
-- run, only the last one stays 'ok' and the earlier ones become 'superseded', so
-- the one-run-per-day index below can be created. run_date is taken in
-- Europe/Istanbul, the default config.SCHEDULER_TIMEZONE.

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

ALTER TABLE alerts ADD COLUMN IF NOT EXISTS run_id BIGINT REFERENCES runs (run_id);

INSERT INTO runs (run_date, started_at, finished_at, at_risk_count, status)
SELECT
    (run_at AT TIME ZONE 'Europe/Istanbul')::date,
    run_at,
    run_at,
    count(*),
    CASE
        WHEN row_number() OVER (
            PARTITION BY (run_at AT TIME ZONE 'Europe/Istanbul')::date
            ORDER BY run_at DESC
        ) = 1 THEN 'ok'
        ELSE 'superseded'
    END
FROM alerts
WHERE run_id IS NULL
GROUP BY run_at;

UPDATE alerts a
SET run_id = r.run_id
FROM runs r
WHERE a.run_id IS NULL AND r.started_at = a.run_at;

ALTER TABLE alerts ALTER COLUMN run_id SET NOT NULL;

-- (run_at, student_id) did not stop two concurrent runs: each took its own
-- microsecond and both were accepted. A run is now a row, so uniqueness is per run.
DROP INDEX IF EXISTS alerts_run_at_student_id_uidx;
CREATE UNIQUE INDEX IF NOT EXISTS alerts_run_id_student_id_uidx
    ON alerts (run_id, student_id);

CREATE UNIQUE INDEX IF NOT EXISTS runs_run_date_uidx
    ON runs (run_date) WHERE status IN ('ok', 'no_alerts');

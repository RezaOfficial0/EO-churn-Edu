-- Bring a database created before this change in line with db/schema.sql.
--
-- Only needed if your `alerts` table was created with the original
-- NUMERIC(5,4) column, which rounded 0.648649 to 0.6486 and handed Python a
-- Decimal instead of a float. New databases get DOUBLE PRECISION directly.
--
-- Guarded: the ALTER runs only while the column is still NUMERIC. Unguarded, it
-- took an ACCESS EXCLUSIVE lock on `alerts` on every init and failed outright as
-- soon as a view depended on the column (B-16). init_db.py now also records it in
-- schema_migrations so it runs once, but the guard keeps a manual
-- `psql -f` of this file safe too.
--
-- Already-stored values are converted, but the precision they lost when they
-- were written is gone - it is not recovered by widening the column.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'alerts'
          AND column_name = 'churn_probability'
          AND data_type = 'numeric'
    ) THEN
        ALTER TABLE alerts ALTER COLUMN churn_probability TYPE DOUBLE PRECISION;
    END IF;
END
$$;

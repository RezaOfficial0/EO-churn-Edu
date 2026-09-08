-- Bring a database created before this change in line with db/schema.sql.
--
-- Only needed if your `alerts` table was created with the original
-- NUMERIC(5,4) column, which rounded 0.648649 to 0.6486 and handed Python a
-- Decimal instead of a float. New databases get DOUBLE PRECISION from
-- db/schema.sql directly and can skip this file.
--
--     psql -d eo_churn -f db/migrations/001_churn_probability_to_double.sql
--
-- Already-stored values are converted, but the precision they lost when they
-- were written is gone - it is not recovered by widening the column.

ALTER TABLE alerts
    ALTER COLUMN churn_probability TYPE DOUBLE PRECISION;

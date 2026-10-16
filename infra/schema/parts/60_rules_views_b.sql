-- Materialized views of the business views B (analytics, ext_network).
-- CockroachDB v26.2 does NOT allow REFRESH MATERIALIZED VIEW inside a function/procedure ("unimplemented: REFRESH MATERIALIZED VIEW
-- usage inside a function definition is not supported"), so no fn_refresh_*() exists. The refresh statements are published as data
-- (vw_mv_refresh_plan) and the external scheduler executes them one by one:
--   for each row: cockroach sql -e "<refresh_sql>"
-- With an enterprise licence the same statements can be scheduled natively, e.g.
--   CREATE SCHEDULE IF NOT EXISTS sch_refresh_mv_kpi FOR SQL 'REFRESH MATERIALIZED VIEW mv_kpi_summary_monthly' RECURRING '@hourly' WITH SCHEDULE OPTIONS ignore_existing = true;
--   CREATE SCHEDULE IF NOT EXISTS sch_refresh_mv_funnel FOR SQL 'REFRESH MATERIALIZED VIEW mv_funnel_last_90_days' RECURRING '0 3 * * *' WITH SCHEDULE OPTIONS ignore_existing = true;
-- (the local cluster has no licence: throttling starts 2026-10-22, so the external scheduler is the documented path).
CREATE OR REPLACE VIEW vw_mv_refresh_plan AS
SELECT v.mv_name, 'REFRESH MATERIALIZED VIEW ' || v.mv_name AS refresh_sql, v.cadence, v.sort_order
  FROM (VALUES
    ('mv_event_rsvp_headcount', 'every 5 minutes', 10),
    ('mv_kpi_summary_monthly', 'hourly', 20),
    ('mv_funnel_last_90_days', 'daily after ETL publish gate', 30),
    ('mv_recruiter_leaderboard_30d', 'daily after ETL publish gate', 40),
    ('mv_mrr_trend', 'daily after ETL publish gate', 50),
    ('mv_wallet_cover_risk', 'daily after ETL publish gate', 60)
  ) AS v(mv_name, cadence, sort_order);

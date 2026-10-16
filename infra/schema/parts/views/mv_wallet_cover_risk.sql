-- Organisations with fewer than 7 days of wallet cover (upsell / churn-risk list).
-- OVERRIDE reason: the sketch was prose. agg_wallet_burn_daily has no organisation grain, so cover is computed from
-- fact_wallet_transaction: latest balance_after_credits / average daily debit over the last 7 days.
-- Refresh: fn_refresh_mv_wallet_cover_risk().
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_wallet_cover_risk AS
WITH last_bal AS (
  SELECT DISTINCT ON (dim_organisation_id) dim_organisation_id, balance_after_credits AS closing_balance_credits, event_at
    FROM fact_wallet_transaction
   ORDER BY dim_organisation_id, event_at DESC, created_at DESC
), burn AS (
  SELECT dim_organisation_id, sum(-credits_delta)::DECIMAL / 7 AS avg_daily_burn_credits
    FROM fact_wallet_transaction
   WHERE event_at >= now() - INTERVAL '7 days' AND credits_delta < 0 AND NOT is_reversal
   GROUP BY dim_organisation_id
)
SELECT b.dim_organisation_id, l.closing_balance_credits, b.avg_daily_burn_credits,
       l.closing_balance_credits / b.avg_daily_burn_credits AS days_of_cover
  FROM burn b JOIN last_bal l USING (dim_organisation_id)
 WHERE b.avg_daily_burn_credits > 0 AND l.closing_balance_credits / b.avg_daily_burn_credits < 7;

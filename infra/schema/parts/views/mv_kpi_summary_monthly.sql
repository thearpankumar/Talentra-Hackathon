-- Executive KPI sheet per month: K01 time to fill, K05 offer acceptance, K06 cost per hire, K14 net revenue.
-- OVERRIDE reason: the sketch had a "..." placeholder. Each source is first rolled up to (month[, currency]) so the
-- joins do not fan out across category/plan grains. Money measures keep their currency (never summed across currencies).
-- Refresh: fn_refresh_mv_kpi_summary_monthly() in parts/60_rules_views_b.sql (hourly).
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_kpi_summary_monthly AS
WITH tf AS (
  SELECT period_date,
         sum(requirements_filled) AS requirements_filled,
         sum(avg_days_to_fill * requirements_filled) / nullif(sum(requirements_filled), 0) AS k01_avg_days_to_fill
    FROM agg_time_to_fill_monthly GROUP BY period_date
), oa AS (
  SELECT period_date,
         sum(offers_extended) AS offers_extended, sum(offers_accepted) AS offers_accepted,
         sum(offers_accepted)::DECIMAL / nullif(sum(offers_extended), 0) AS k05_offer_acceptance_rate
    FROM agg_offer_acceptance_monthly GROUP BY period_date
), cph AS (
  SELECT period_date, currency_id, sum(hires) AS hires, sum(spend_amount) AS spend_amount,
         sum(spend_amount) / nullif(sum(hires), 0) AS k06_cost_per_hire
    FROM agg_cost_per_hire_monthly GROUP BY period_date, currency_id
), rev AS (
  SELECT period_date, currency_id, sum(net_revenue_amount) AS k14_net_revenue, sum(closing_mrr_amount) AS closing_mrr
    FROM agg_revenue_monthly GROUP BY period_date, currency_id
), money AS (
  SELECT coalesce(c.period_date, r.period_date) AS period_date, coalesce(c.currency_id, r.currency_id) AS currency_id,
         c.hires, c.spend_amount, c.k06_cost_per_hire, r.k14_net_revenue, r.closing_mrr
    FROM cph c FULL JOIN rev r ON r.period_date = c.period_date AND r.currency_id = c.currency_id
), periods AS (
  SELECT period_date FROM tf UNION SELECT period_date FROM oa UNION SELECT period_date FROM money
)
SELECT p.period_date, tf.requirements_filled, tf.k01_avg_days_to_fill,
       oa.offers_extended, oa.offers_accepted, oa.k05_offer_acceptance_rate,
       m.currency_id, m.hires, m.spend_amount, m.k06_cost_per_hire, m.k14_net_revenue, m.closing_mrr
  FROM periods p
  LEFT JOIN tf ON tf.period_date = p.period_date
  LEFT JOIN oa ON oa.period_date = p.period_date
  LEFT JOIN money m ON m.period_date = p.period_date;

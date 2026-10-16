-- Advertiser campaign report (CTR, cost per conversion).
-- OVERRIDE reason: sketch had "GROUP BY ..." and a bare "campaign". ad_account_id is exposed so the data-access layer
-- filters by ad_account membership; soft-deleted campaigns hidden.
CREATE OR REPLACE VIEW vw_ad_campaign_report AS
SELECT c.ad_account_id, c.id AS ad_campaign_id, c.name AS campaign_name, p.performance_date,
       sum(p.impressions) AS impressions, sum(p.clicks) AS clicks, sum(p.conversions) AS conversions,
       sum(p.spend_amount) AS spend_amount,
       sum(p.invalid_clicks) AS invalid_clicks, sum(p.invalid_spend_amount) AS invalid_spend_amount,
       sum(p.clicks)::DECIMAL / nullif(sum(p.impressions), 0) AS ctr,
       sum(p.spend_amount) / nullif(sum(p.conversions), 0) AS cost_per_conversion
  FROM ad_performance_daily p
  JOIN ad_campaign c ON c.id = p.ad_campaign_id
 WHERE c.deleted_at IS NULL
 GROUP BY c.ad_account_id, c.id, c.name, p.performance_date;

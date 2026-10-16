-- Serving candidates: running sponsored jobs of active accounts with live jobs.
-- OVERRIDE reason: the sketch did not exclude suspended/restricted accounts, paused/non-active entities, deleted
-- campaigns/accounts or non-live jobs, which the security text requires.
CREATE OR REPLACE VIEW vw_sponsored_job_serving AS
SELECT sj.*
  FROM sponsored_job sj
  JOIN ad_campaign c ON c.id = sj.ad_campaign_id
  JOIN ad_account a ON a.id = sj.ad_account_id
  JOIN ad_account_status ast ON ast.id = a.ad_account_status_id AND ast.code = 'active'
  JOIN ad_entity_status ses ON ses.id = sj.ad_entity_status_id AND ses.code = 'active'
  JOIN ad_entity_status ces ON ces.id = c.ad_entity_status_id AND ces.code = 'active'
  JOIN job_requirement j ON j.id = sj.job_requirement_id AND j.deleted_at IS NULL AND j.is_live
 WHERE sj.deleted_at IS NULL AND c.deleted_at IS NULL AND a.deleted_at IS NULL
   AND now() BETWEEN sj.starts_at AND coalesce(sj.ends_at, 'infinity')
   AND a.verified_at IS NOT NULL
   AND (a.restricted_until_at IS NULL OR a.restricted_until_at < now())
   AND c.spent_amount < c.budget_amount;

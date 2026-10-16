-- Unexpired, undismissed recommendations joined to live jobs for the feed API.
-- OVERRIDE reason: the sketch checked only j.deleted_at; "live jobs" also requires j.is_live.
CREATE OR REPLACE VIEW vw_candidate_job_recommendation_live AS
SELECT r.*
  FROM job_recommendation r
  JOIN job_requirement j ON j.id = r.job_requirement_id
 WHERE r.expires_at > now() AND r.dismissed_at IS NULL AND j.deleted_at IS NULL AND j.is_live;

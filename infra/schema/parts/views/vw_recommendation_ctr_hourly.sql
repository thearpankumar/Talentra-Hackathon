-- 90-day operational CTR per surface/model/hour.
-- OVERRIDE reason: sketch described the click join as prose. Clicks are attributed to the impression they came from
-- (request_ref + rank_position + job), so CTR = clicks of those impressions / impressions.
CREATE OR REPLACE VIEW vw_recommendation_ctr_hourly AS
SELECT i.recommendation_surface_id, i.recommendation_model_version_id, date_trunc('hour', i.impressed_at) AS impression_hour,
       count(DISTINCT i.id) AS impressions, count(DISTINCT c.id) AS clicks,
       count(DISTINCT c.id)::DECIMAL / nullif(count(DISTINCT i.id), 0) AS ctr
  FROM recommendation_impression i
  LEFT JOIN recommendation_click_event c
         ON c.request_ref = i.request_ref AND c.rank_position = i.rank_position AND c.job_requirement_id = i.job_requirement_id
 WHERE i.impressed_at >= now() - INTERVAL '90 days'
 GROUP BY 1, 2, 3;

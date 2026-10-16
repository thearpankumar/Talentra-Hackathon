-- Open, public, unexpired, non-deleted gig requests for browse.
-- OVERRIDE reason: sketch was "SELECT ...". Public visibility only (invite/favourites-only requests never appear);
-- requester id not exposed and coordinates rounded to 2 dp.
CREATE OR REPLACE VIEW vw_gig_open_request AS
SELECT r.id, r.organisation_id, r.title, r.description, r.job_category_id, r.gig_work_mode_id, r.gig_experience_level_id,
       r.city_id, r.country_id, round(r.latitude, 2) AS approx_latitude, round(r.longitude, 2) AS approx_longitude,
       r.window_start_at, r.window_end_at, r.gig_price_type_id, r.is_rate_based, r.fixed_price, r.rate_amount,
       r.gig_rate_unit_id, r.budget_max, r.currency_id, r.headcount, r.accepted_count, r.application_deadline_at, r.published_at
  FROM gig_request r
  JOIN gig_request_status s ON s.id = r.gig_request_status_id AND s.code = 'open'
  JOIN gig_request_visibility v ON v.id = r.gig_request_visibility_id AND v.code = 'public'
 WHERE r.deleted_at IS NULL
   AND (r.application_deadline_at IS NULL OR r.application_deadline_at > now())
   AND (r.window_end_at IS NULL OR r.window_end_at > now());

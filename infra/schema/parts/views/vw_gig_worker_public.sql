-- Requester-facing worker card.
-- OVERRIDE reason: duplicate output names (unnamed round(..), rating_count in both tables) are aliased. Coordinates
-- rounded to 2 dp (~1 km); no KYC columns (kyc_token, kyc_verified_at, verification status), no app_user_id; listed and
-- not-deleted workers only.
CREATE OR REPLACE VIEW vw_gig_worker_public AS
SELECT p.id, p.headline, p.hourly_rate, p.daily_rate, p.currency_id, p.service_radius_km, p.home_city_id,
       round(p.home_latitude, 2) AS approx_latitude, round(p.home_longitude, 2) AS approx_longitude,
       p.rating_avg, coalesce(s.rating_count, p.rating_count) AS rating_count,
       p.completed_gig_count, p.gig_availability_status_id
  FROM gig_worker_profile p
  LEFT JOIN gig_worker_rating_summary s ON s.gig_worker_profile_id = p.id
 WHERE p.is_listed AND p.deleted_at IS NULL;

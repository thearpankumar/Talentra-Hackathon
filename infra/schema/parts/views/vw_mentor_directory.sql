-- Searchable mentors accepting requests.
-- OVERRIDE reason: sketch was "SELECT ...". Only approved/active mentors (applied, paused, suspended excluded),
-- soft-deleted hidden, no candidate_profile_id / time_zone (private fields).
CREATE OR REPLACE VIEW vw_mentor_directory AS
SELECT m.id, m.headline, m.bio, m.industry_id, m.years_experience, m.is_pro_bono, m.hourly_rate, m.currency_id,
       m.language_id, m.average_rating, m.completed_session_count,
       (SELECT array_agg(ms.skill_id) FROM mentor_skill ms WHERE ms.mentor_profile_id = m.id) AS skill_ids,
       (SELECT array_agg(mf.mentorship_focus_area_id) FROM mentor_focus mf WHERE mf.mentor_profile_id = m.id) AS focus_area_ids
  FROM mentor_profile m
  JOIN mentor_status s ON s.id = m.mentor_status_id AND s.code IN ('approved', 'active')
 WHERE m.deleted_at IS NULL AND m.is_accepting_requests;

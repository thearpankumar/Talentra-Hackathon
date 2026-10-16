-- Latest snapshot per (scope, period, scope_key) with anonymisation applied.
-- OVERRIDE reason: sketch was prose. Anonymised entries lose candidate_profile_id (no identity leaves the view).
CREATE OR REPLACE VIEW vw_learning_leaderboard_latest AS
SELECT s.id AS leaderboard_snapshot_id, s.leaderboard_scope_id, s.leaderboard_period_id, s.scope_key, s.skill_id, s.country_id,
       s.organisation_id, s.period_start_date, s.period_end_date, s.computed_at,
       e.rank_no, e.score_points, e.is_anonymised,
       CASE WHEN e.is_anonymised THEN NULL ELSE e.candidate_profile_id END AS candidate_profile_id
  FROM (SELECT DISTINCT ON (leaderboard_scope_id, leaderboard_period_id, scope_key) *
          FROM leaderboard_snapshot
         ORDER BY leaderboard_scope_id, leaderboard_period_id, scope_key, computed_at DESC, id) s
  JOIN leaderboard_entry e ON e.leaderboard_snapshot_id = s.id;

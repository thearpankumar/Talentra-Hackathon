-- Per-learner transcript: completed courses, completed paths, certificates, badges.
-- OVERRIDE reason: sketch was prose; built as UNION ALL (branches disjoint by entry_kind). Revoked certificates/badges are
-- flagged (is_revoked), not hidden. The application filters by candidate_profile_id (owner or consented recruiter).
CREATE OR REPLACE VIEW vw_candidate_learning_transcript AS
SELECT e.candidate_profile_id, 'course' AS entry_kind, e.id AS entry_id, c.title AS title,
       e.completed_at AS occurred_at, e.final_score_percent AS score_percent, false AS is_revoked
  FROM course_enrolment e
  JOIN enrolment_status s ON s.id = e.enrolment_status_id AND s.code = 'completed'
  JOIN course c ON c.id = e.course_id
 WHERE e.deleted_at IS NULL
UNION ALL
SELECT pe.candidate_profile_id, 'learning_path', pe.id, lp.title, pe.completed_at, NULL::DECIMAL(5,2), false
  FROM learning_path_enrolment pe
  JOIN learning_path lp ON lp.id = pe.learning_path_id
 WHERE pe.deleted_at IS NULL AND pe.completed_at IS NOT NULL
UNION ALL
SELECT ce.candidate_profile_id, 'certificate', ce.id, ce.title, ce.issued_at, ce.score_percent, (ce.revoked_at IS NOT NULL)
  FROM certificate ce
 WHERE ce.deleted_at IS NULL
UNION ALL
SELECT b.candidate_profile_id, 'badge', b.id, sb.name, b.awarded_at, NULL::DECIMAL(5,2), (b.revoked_at IS NOT NULL)
  FROM candidate_skill_badge b
  JOIN skill_badge sb ON sb.id = b.skill_badge_id
 WHERE b.deleted_at IS NULL;

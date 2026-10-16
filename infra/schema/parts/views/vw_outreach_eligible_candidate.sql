-- Override: the sketch used the parameters $sender/$org and a non-existent user_block.candidate_profile_id. Pair view over
-- vw_candidate_visible_to_user (user + organisation blocks, both directions, effective-dated): one row per (sender, candidate)
-- that may be contacted: not blocked, consent not withdrawn/expired (latest profile_visible_to_employers / share_with_consultants
-- event), searchable, and not contacted by the same sender in the last 7 days. id = candidate_profile_id (BR-SRC-06).
-- Use WHERE sender_app_user_id = $1 or fn_outreach_eligible_candidate(sender).
CREATE OR REPLACE VIEW vw_outreach_eligible_candidate AS
SELECT vis.viewer_app_user_id AS sender_app_user_id, vis.candidate_profile_id AS id, vis.candidate_app_user_id
FROM vw_candidate_visible_to_user vis
JOIN candidate_profile cp ON cp.id = vis.candidate_profile_id AND cp.is_searchable AND cp.anonymised_at IS NULL
WHERE NOT EXISTS (SELECT 1 FROM vw_user_consent_current cc
                  JOIN consent_type ct ON ct.id = cc.consent_type_id AND ct.code IN ('profile_visible_to_employers','share_with_consultants')
                  JOIN consent_action ca ON ca.id = cc.consent_action_id AND ca.code IN ('withdrawn','expired')
                  WHERE cc.app_user_id = vis.candidate_app_user_id)
  AND NOT EXISTS (SELECT 1 FROM candidate_contact_attempt a
                  WHERE a.candidate_profile_id = vis.candidate_profile_id AND a.contacter_app_user_id = vis.viewer_app_user_id
                    AND a.attempted_at > now() - INTERVAL '7 days');

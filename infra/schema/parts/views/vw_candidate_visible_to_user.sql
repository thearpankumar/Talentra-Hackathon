-- Override: the dictionary sketch used rom.app_user_id (the column is recruiter_app_user_id), ignored employer role scoping,
-- effective-dating windows and block direction. Pair view: ALWAYS filter on viewer_app_user_id (or use fn_candidate_visible_to_user).
-- Hidden when: open user_block in either direction between viewer and candidate user; open organisation_block (either direction)
-- of the candidate against any organisation the viewer acts for (open recruiter_organisation_mapping incl. with_descendants,
-- or open app_user_role organisation).
CREATE OR REPLACE VIEW vw_candidate_visible_to_user AS
SELECT v.id AS viewer_app_user_id, cp.id AS candidate_profile_id, cp.app_user_id AS candidate_app_user_id
FROM app_user v
JOIN actor_type vt ON vt.id = v.actor_type_id AND vt.code IN ('employer','recruiter','consultant','placement_officer')
CROSS JOIN candidate_profile cp
WHERE v.deleted_at IS NULL AND cp.deleted_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM user_block ub
                  WHERE ((ub.blocker_app_user_id = cp.app_user_id AND ub.blocked_app_user_id = v.id)
                      OR (ub.blocker_app_user_id = v.id AND ub.blocked_app_user_id = cp.app_user_id))
                    AND ub.effective_from <= now() AND (ub.effective_to IS NULL OR ub.effective_to > now()))
  AND NOT EXISTS (SELECT 1 FROM organisation_block ob
                  WHERE ob.candidate_profile_id = cp.id
                    AND ob.effective_from <= now() AND (ob.effective_to IS NULL OR ob.effective_to > now())
                    AND (EXISTS (SELECT 1 FROM recruiter_organisation_mapping rom
                                 JOIN organisation_hierarchy_closure c ON c.ancestor_organisation_id = rom.organisation_id
                                 WHERE rom.recruiter_app_user_id = v.id
                                   AND rom.effective_from <= now() AND (rom.effective_to IS NULL OR rom.effective_to > now())
                                   AND c.descendant_organisation_id = ob.organisation_id
                                   AND (c.depth = 0 OR rom.mapping_scope_id = (SELECT id FROM mapping_scope WHERE code = 'with_descendants')))
                         OR EXISTS (SELECT 1 FROM app_user_role aur
                                    WHERE aur.app_user_id = v.id AND aur.organisation_id = ob.organisation_id
                                      AND aur.effective_from <= now() AND (aur.effective_to IS NULL OR aur.effective_to > now()))));

-- Override: the sketch was not viewer-aware (AT-05 requires blocked parties to get 0 rows from this card too) and its INNER JOIN on
-- salary_disclosure dropped candidates without a disclosure. Now a pair view over vw_candidate_visible_to_user (filter on
-- viewer_app_user_id or use fn_candidate_profile_recruiter); salary and contact are NULL (not 0/empty) unless disclosed / visible;
-- birth_date, address and the other Sensitive columns are never exposed.
CREATE OR REPLACE VIEW vw_candidate_profile_recruiter AS
SELECT vis.viewer_app_user_id, p.id, p.display_name, p.headline,
       CASE WHEN d.code = 'disclosed' THEN p.current_salary_amount END AS current_salary_amount,
       CASE WHEN p.is_contact_visible_to_recruiters THEN p.contact_email END AS contact_email
FROM vw_candidate_visible_to_user vis
JOIN candidate_profile p ON p.id = vis.candidate_profile_id AND p.deleted_at IS NULL AND p.is_searchable
LEFT JOIN salary_disclosure d ON d.id = p.current_salary_disclosure_id;

-- Viewer-scoped access functions for the core modules (docs/13_rbac_matrix.xlsx E-02, GR-23, AT-25).
-- The viewer is an explicit ARGUMENT bound by the data-access layer from the authenticated session (no session variable).
-- All functions are LANGUAGE SQL STABLE and run with the caller's privileges: the caller needs SELECT on the pair view (see 75_post_grants_core.sql).
CREATE OR REPLACE FUNCTION fn_candidate_visible_to_user(p_viewer_app_user_id UUID) RETURNS SETOF vw_candidate_visible_to_user
LANGUAGE SQL STABLE AS $$ SELECT * FROM vw_candidate_visible_to_user WHERE viewer_app_user_id = p_viewer_app_user_id $$;
-- @@
CREATE OR REPLACE FUNCTION fn_recruiter_job_requirement(p_viewer_app_user_id UUID) RETURNS SETOF vw_recruiter_job_requirement
LANGUAGE SQL STABLE AS $$ SELECT * FROM vw_recruiter_job_requirement WHERE viewer_app_user_id = p_viewer_app_user_id $$;
-- @@
CREATE OR REPLACE FUNCTION fn_consultant_open_requirement(p_consultant_profile_id UUID) RETURNS SETOF vw_consultant_open_requirement
LANGUAGE SQL STABLE AS $$ SELECT * FROM vw_consultant_open_requirement WHERE viewer_consultant_profile_id = p_consultant_profile_id $$;
-- @@
CREATE OR REPLACE FUNCTION fn_outreach_eligible_candidate(p_sender_app_user_id UUID) RETURNS SETOF vw_outreach_eligible_candidate
LANGUAGE SQL STABLE AS $$ SELECT * FROM vw_outreach_eligible_candidate WHERE sender_app_user_id = p_sender_app_user_id $$;
-- @@
-- Point check: may this viewer see this candidate profile? (false for blocked parties and unknown ids)
CREATE OR REPLACE FUNCTION fn_candidate_is_visible(p_viewer_app_user_id UUID, p_candidate_profile_id UUID) RETURNS BOOL
LANGUAGE SQL STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM vw_candidate_visible_to_user
                 WHERE viewer_app_user_id = p_viewer_app_user_id AND candidate_profile_id = p_candidate_profile_id) $$;
-- @@
-- Point check: may this recruiter see requirements of this organisation (open mapping, scope, active organisation)?
CREATE OR REPLACE FUNCTION fn_recruiter_can_see_organisation(p_viewer_app_user_id UUID, p_organisation_id UUID) RETURNS BOOL
LANGUAGE SQL STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM vw_recruiter_job_requirement
                 WHERE viewer_app_user_id = p_viewer_app_user_id AND organisation_id = p_organisation_id) $$;
-- @@
-- Consultant openness: is there currently a window through which this consultant may work on this requirement?
CREATE OR REPLACE FUNCTION fn_consultant_can_work_requirement(p_consultant_profile_id UUID, p_job_requirement_id UUID) RETURNS BOOL
LANGUAGE SQL STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM vw_consultant_open_requirement
                 WHERE viewer_consultant_profile_id = p_consultant_profile_id AND job_requirement_id = p_job_requirement_id) $$;
-- @@
CREATE OR REPLACE FUNCTION fn_candidate_profile_recruiter(p_viewer_app_user_id UUID) RETURNS SETOF vw_candidate_profile_recruiter
LANGUAGE SQL STABLE AS $$ SELECT * FROM vw_candidate_profile_recruiter WHERE viewer_app_user_id = p_viewer_app_user_id $$;

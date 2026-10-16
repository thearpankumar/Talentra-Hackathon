-- Grants for the viewer-scoped views/functions of the core modules (RoleMatrix views_over_table, GR-10/GR-22/GR-23, E-06/E-07/E-08).
-- 70_grants.sql grants every view to every persona that has a V cell on one of its tables; the pair views below expose
-- other people's identifiers and must be reachable only by the personas the RBAC docs name, so first REVOKE then GRANT.
-- Base tables (job_requirement, consultant_job_openness, user_block, organisation_block, recruiter_organisation_mapping,
-- candidate_profile) are intentionally NOT granted to recruiter/consultant personas.
REVOKE SELECT ON TABLE vw_recruiter_job_requirement FROM app_candidate, app_consultant, app_employer, app_guest, app_tpo, app_recruiter;
-- @@
REVOKE SELECT ON TABLE vw_consultant_open_requirement FROM app_candidate, app_employer, app_guest, app_recruiter, app_tpo, app_consultant;
-- @@
REVOKE SELECT ON TABLE vw_consultant_job_requirement FROM app_candidate, app_guest, app_employer, app_tpo;
-- @@
REVOKE SELECT ON TABLE vw_outreach_eligible_candidate FROM app_candidate, app_employer, app_recruiter, app_consultant, app_tpo;
-- @@
REVOKE SELECT ON TABLE vw_candidate_visible_to_user FROM app_candidate, app_guest;
-- @@
REVOKE SELECT ON TABLE vw_live_job_requirement FROM app_consultant, app_recruiter, app_tpo;
-- @@
GRANT SELECT ON TABLE vw_recruiter_job_requirement TO app_recruiter, app_staff_admin;
-- @@
GRANT SELECT ON TABLE vw_consultant_open_requirement, vw_consultant_job_requirement TO app_consultant, app_staff_admin;
-- @@
GRANT SELECT ON TABLE vw_candidate_visible_to_user TO app_employer, app_recruiter, app_consultant, app_tpo, app_staff_admin, app_staff_super;
-- @@
GRANT SELECT ON TABLE vw_outreach_eligible_candidate TO app_employer, app_recruiter, app_consultant, app_staff_admin, app_service;
-- @@
GRANT SELECT ON TABLE vw_live_job_requirement TO app_guest, app_candidate, app_tpo, app_recruiter, app_consultant, app_employer, app_staff_admin, app_service;
-- @@
GRANT SELECT ON TABLE vw_salary_benchmark_published TO app_candidate, app_recruiter, app_employer, app_consultant, app_tpo, app_account_admin, app_staff_admin, app_service;
-- @@
GRANT EXECUTE ON FUNCTION fn_candidate_visible_to_user(UUID) TO app_employer, app_recruiter, app_consultant, app_tpo, app_service, app_staff_admin, app_staff_super;
-- @@
GRANT EXECUTE ON FUNCTION fn_candidate_is_visible(UUID, UUID) TO app_employer, app_recruiter, app_consultant, app_tpo, app_service, app_staff_admin, app_staff_super;
-- @@
GRANT EXECUTE ON FUNCTION fn_recruiter_job_requirement(UUID) TO app_recruiter, app_service, app_staff_admin;
-- @@
GRANT EXECUTE ON FUNCTION fn_recruiter_can_see_organisation(UUID, UUID) TO app_recruiter, app_service, app_staff_admin;
-- @@
GRANT EXECUTE ON FUNCTION fn_consultant_open_requirement(UUID) TO app_consultant, app_service, app_staff_admin;
-- @@
GRANT EXECUTE ON FUNCTION fn_consultant_can_work_requirement(UUID, UUID) TO app_consultant, app_service, app_staff_admin;
-- @@
GRANT EXECUTE ON FUNCTION fn_outreach_eligible_candidate(UUID) TO app_employer, app_recruiter, app_consultant, app_service, app_staff_admin;
-- @@
REVOKE SELECT ON TABLE vw_candidate_profile_recruiter FROM app_candidate, app_guest;
-- @@
GRANT SELECT ON TABLE vw_candidate_profile_recruiter TO app_employer, app_recruiter, app_consultant, app_tpo, app_staff_admin, app_service;
-- @@
GRANT EXECUTE ON FUNCTION fn_candidate_profile_recruiter(UUID) TO app_employer, app_recruiter, app_consultant, app_tpo, app_service, app_staff_admin;

-- Override: the sketch called current_consultant_profile() (a session function that does not exist; the viewer is an explicit
-- argument by design). Pair view (viewer_consultant_profile_id, window); read through fn_consultant_open_requirement(p_consultant_profile_id)
-- or WHERE viewer_consultant_profile_id = $1. Also requires the requirement itself to be live and open to consultants (AT-03).
CREATE OR REPLACE VIEW vw_consultant_open_requirement AS
SELECT cp.id AS viewer_consultant_profile_id, o.*, jr.title AS job_title, jr.reference_code AS job_reference_code
FROM consultant_job_openness o
JOIN job_requirement jr ON jr.id = o.job_requirement_id AND jr.is_live AND jr.is_open_to_consultants AND jr.deleted_at IS NULL
JOIN consultant_profile cp ON cp.deleted_at IS NULL AND (o.consultant_profile_id IS NULL OR o.consultant_profile_id = cp.id)
WHERE o.closed_at IS NULL AND o.deleted_at IS NULL AND o.valid_from <= now() AND (o.valid_to IS NULL OR o.valid_to > now());

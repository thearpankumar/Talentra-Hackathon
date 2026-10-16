-- Override: the sketch joined job_consultant_openness.deleted_at, a column that table does not have. consultant_job_openness (sourcing)
-- is authoritative (Findings F-04), so this view is a thin alias: live requirements open to consultants with a valid general window
-- (jobs-side scope all_consultants, or sourcing-side window with no personal consultant). Personal invitations are only visible
-- through vw_consultant_open_requirement / fn_consultant_open_requirement.
CREATE OR REPLACE VIEW vw_consultant_job_requirement AS
SELECT r.*
FROM job_requirement r
WHERE r.is_open_to_consultants AND r.is_live AND r.deleted_at IS NULL
  AND (EXISTS (SELECT 1 FROM job_consultant_openness o
               JOIN consultant_access_scope sc ON sc.id = o.consultant_access_scope_id AND sc.code = 'all_consultants'
               WHERE o.job_requirement_id = r.id AND o.valid_from <= now() AND (o.valid_to IS NULL OR o.valid_to > now()))
    OR EXISTS (SELECT 1 FROM consultant_job_openness co
               WHERE co.job_requirement_id = r.id AND co.consultant_profile_id IS NULL AND co.closed_at IS NULL AND co.deleted_at IS NULL
                 AND co.valid_from <= now() AND (co.valid_to IS NULL OR co.valid_to > now())));

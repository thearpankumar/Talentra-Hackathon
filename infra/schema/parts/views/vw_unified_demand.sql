-- Single demand feed across jobs and gigs (D-15).
-- OVERRIDE reason: sketch had <category>/<city>/<status>/<opened_at> placeholders. Identifiers only (no titles, no personal
-- data); soft-deleted rows hidden on both branches.
CREATE OR REPLACE VIEW vw_unified_demand AS
SELECT 'job' AS demand_type, j.id, j.organisation_id, j.job_category_id, j.primary_city_id AS city_id,
       s.code AS status_code, j.published_at AS opened_at
  FROM job_requirement j JOIN job_requirement_status s ON s.id = j.job_requirement_status_id
 WHERE j.deleted_at IS NULL
UNION ALL
SELECT 'gig', g.id, g.organisation_id, g.job_category_id, g.city_id, s.code, g.published_at
  FROM gig_request g JOIN gig_request_status s ON s.id = g.gig_request_status_id
 WHERE g.deleted_at IS NULL;

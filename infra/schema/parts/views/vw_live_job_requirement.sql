-- Override: the sketch had the placeholders "SELECT ..." and "<public>". Public browse surface; confidential employers are hidden
-- (organisation_id / organisation_name NULL); internal columns (posted_by, created_by, legacy refs) are not exposed.
CREATE OR REPLACE VIEW vw_live_job_requirement AS
SELECT r.id, r.reference_code, r.title, r.short_summary, r.description, r.description_language_id,
       r.job_role_id, r.job_category_id, r.employment_type_id, r.work_type_id, r.seniority_level_id, r.industry_id,
       r.shift_type_id, r.hiring_urgency_id, r.apply_mode_id, r.external_apply_url,
       r.primary_country_id, r.primary_city_id,
       r.salary_min, r.salary_max, r.currency_id, r.salary_period_id, r.salary_disclosure_id,
       r.experience_min_months, r.experience_max_months, r.openings_count,
       r.is_open_to_freshers, r.is_walk_in, r.is_confidential_employer,
       CASE WHEN r.is_confidential_employer THEN NULL ELSE r.organisation_id END AS organisation_id,
       CASE WHEN r.is_confidential_employer THEN NULL ELSE o.display_name END AS organisation_name,
       r.published_at, r.expires_at, r.closing_date,
       s.application_count, s.view_count,
       (SELECT count(*) FROM job_requirement_location l WHERE l.job_requirement_id = r.id) AS location_count
FROM job_requirement r
JOIN visibility_level vl ON vl.id = r.visibility_level_id AND vl.code = 'public'
LEFT JOIN organisation o ON o.id = r.organisation_id AND o.deleted_at IS NULL
LEFT JOIN job_requirement_stat s ON s.job_requirement_id = r.id
WHERE r.is_live AND r.deleted_at IS NULL AND r.closed_at IS NULL
  AND (r.expires_at IS NULL OR r.expires_at > now());

-- Current version of each candidate excluding erased rows.
-- OVERRIDE reason (security): the sketch used SELECT *, which exposes the Personal key candidate_profile_id (and CockroachDB
-- has no column privileges to hide it from the BI role). Explicit non-personal column list instead.
CREATE OR REPLACE VIEW vw_dim_candidate_current AS
SELECT id, version_no, valid_from, valid_to, is_current, gender_code, age_band_code, experience_band_code,
       highest_qualification_code, current_job_role_code, current_dim_industry_id, home_dim_location_id,
       acquisition_dim_source_id, signup_dim_date_id, preferred_language_code, profile_completion_band_code,
       expected_salary_band_code, is_open_to_work, is_active_candidate
  FROM dim_candidate
 WHERE is_current AND NOT is_erased;

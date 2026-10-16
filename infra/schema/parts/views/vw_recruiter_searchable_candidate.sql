-- Candidates a recruiter organisation may see in search.
-- OVERRIDE reason (privacy bug): the sketch LEFT JOINed privacy_hidden_organisation, giving one row per hidden org, so a
-- candidate hidden from orgs A and B still showed up as the row (B) to a recruiter of org A. Hidden orgs are now an array
-- (hidden_from_organisation_ids); the caller filters `NOT (:org = ANY(hidden_from_organisation_ids))`. Also requires
-- an existing, non-deleted, non-anonymised, searchable profile.
CREATE OR REPLACE VIEW vw_recruiter_searchable_candidate AS
SELECT p.app_user_id, c.id AS candidate_profile_id, p.profile_visibility_level_id,
       coalesce((SELECT array_agg(h.organisation_id) FROM privacy_hidden_organisation h WHERE h.app_user_id = p.app_user_id),
                ARRAY[]::UUID[]) AS hidden_from_organisation_ids
  FROM privacy_setting p
  JOIN candidate_profile c ON c.app_user_id = p.app_user_id AND c.deleted_at IS NULL AND c.anonymised_at IS NULL AND c.is_searchable
 WHERE p.is_searchable_by_recruiters;

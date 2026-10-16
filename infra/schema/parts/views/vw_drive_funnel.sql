-- Registrations, decided, selected per drive and institution for TPO reports.
-- OVERRIDE reason: the security text requires small counts (< 5 registrations) to be suppressed; the sketch exposed them.
-- Suppressed cells show NULL counts with is_suppressed = true; withdrawn/deleted registrations excluded.
CREATE OR REPLACE VIEW vw_drive_funnel AS
SELECT placement_drive_id, institution_id,
       CASE WHEN registered < 5 THEN NULL ELSE registered END AS registered,
       CASE WHEN registered < 5 THEN NULL ELSE decided END AS decided,
       CASE WHEN registered < 5 THEN NULL ELSE selected END AS selected,
       (registered < 5) AS is_suppressed
  FROM (SELECT r.placement_drive_id, r.institution_id, count(*) AS registered,
               count(d.id) FILTER (WHERE d.drive_result_decision_id IS NOT NULL) AS decided,
               count(d.id) FILTER (WHERE dd.code IN ('selected', 'ppo_issued')) AS selected
          FROM drive_registration r
          LEFT JOIN drive_result d ON d.drive_registration_id = r.id AND d.deleted_at IS NULL
          LEFT JOIN drive_result_decision dd ON dd.id = d.drive_result_decision_id
         WHERE r.deleted_at IS NULL AND r.withdrawn_at IS NULL
         GROUP BY r.placement_drive_id, r.institution_id) x;

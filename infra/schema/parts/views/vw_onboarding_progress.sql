-- Per-plan required/completed/overdue task counts for HR dashboards (tenant filter on organisation_id by the application).
-- OVERRIDE reason: the sketch lacked organisation_id, the overdue count named in the purpose, and soft-delete filters.
CREATE OR REPLACE VIEW vw_onboarding_progress AS
SELECT p.id, p.organisation_id,
       count(*) FILTER (WHERE t.is_required) AS required_tasks,
       count(*) FILTER (WHERE t.completed_at IS NOT NULL) AS completed_tasks,
       count(*) FILTER (WHERE t.completed_at IS NULL AND t.due_date < current_date) AS overdue_tasks
  FROM onboarding_plan p
  JOIN onboarding_plan_task t ON t.onboarding_plan_id = p.id AND t.deleted_at IS NULL
 WHERE p.deleted_at IS NULL
 GROUP BY p.id, p.organisation_id;

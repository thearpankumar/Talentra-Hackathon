-- Mandatory-assignment compliance per organisation; the application filters by organisation_id (tenant isolation).
-- OVERRIDE reason: column "status" does not exist; the real column is learning_assignment_status_id (lookup
-- learning_assignment_status). overdue_count = open mandatory assignments past due_date.
CREATE OR REPLACE VIEW vw_employer_learning_compliance AS
SELECT a.organisation_id, s.code AS assignment_status_code, count(*) AS assignment_count,
       count(*) FILTER (WHERE a.completed_at IS NULL AND a.due_date < current_date
                          AND s.code NOT IN ('completed', 'waived', 'cancelled')) AS overdue_count
  FROM learning_assignment a
  JOIN learning_assignment_status s ON s.id = a.learning_assignment_status_id
 WHERE a.is_mandatory AND a.deleted_at IS NULL
 GROUP BY a.organisation_id, s.code;

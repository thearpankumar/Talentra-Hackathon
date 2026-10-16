-- Item analysis per question (assessment authors).
-- OVERRIDE reason: duplicate unnamed output columns (count, avg, avg) are aliased.
CREATE OR REPLACE VIEW vw_assessment_item_statistics AS
SELECT a.quiz_question_id, count(*) AS answer_count,
       avg(a.is_correct::INT) AS facility_index,
       avg(a.time_spent_seconds) AS avg_time_spent_seconds,
       q.discrimination_index
  FROM assessment_answer a JOIN quiz_question q ON q.id = a.quiz_question_id
 WHERE a.graded_at IS NOT NULL
 GROUP BY a.quiz_question_id, q.discrimination_index;

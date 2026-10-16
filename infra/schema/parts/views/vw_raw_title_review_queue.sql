-- Titles needing human mapping by frequency.
-- OVERRIDE reason: sketch had a <needs_review> placeholder.
CREATE OR REPLACE VIEW vw_raw_title_review_queue AS
SELECT r.* FROM raw_title_normalisation r
  JOIN normalisation_status s ON s.id = r.normalisation_status_id AND s.code = 'needs_review'
 ORDER BY r.seen_count DESC;

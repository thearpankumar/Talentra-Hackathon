-- Override: the sketch used a plain join which duplicated each benchmark once per source, and kept sources that were inactive/deleted.
-- One row per publishable benchmark with at least one fresh active source; attribution texts aggregated; age exposed.
CREATE OR REPLACE VIEW vw_salary_benchmark_published AS
SELECT b.*, string_agg(s.attribution_text, '; ' ORDER BY s.attribution_text) AS attribution_text,
       count(s.id) AS fresh_source_count, now() - b.refreshed_at AS benchmark_age
FROM salary_benchmark b
JOIN salary_benchmark_source x ON x.salary_benchmark_id = b.id
JOIN reference_data_source s ON s.id = x.reference_data_source_id AND s.deleted_at IS NULL AND s.is_active
WHERE b.is_publishable AND now() - b.refreshed_at <= coalesce(s.max_staleness_days, 36500) * INTERVAL '1 day'
GROUP BY b.id;

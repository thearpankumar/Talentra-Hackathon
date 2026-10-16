-- Public catalogue with current price and average approved rating.
-- OVERRIDE reason: sketch had an <approved> placeholder and fanned out reviews x prices. Reviews are pre-aggregated;
-- only approved, non-deleted reviews; only current non-deleted list prices (one row per course per price country);
-- soft-deleted / inactive courses hidden.
CREATE OR REPLACE VIEW vw_course_catalogue_public AS
SELECT c.id, c.title, c.course_provider_id, c.primary_skill_id, c.proficiency_level_id, c.language_id,
       c.duration_minutes, c.is_free,
       p.country_id AS price_country_id, p.currency_id AS price_currency_id, p.list_amount, p.credit_cost,
       r.avg_rating, coalesce(r.review_count, 0) AS review_count
  FROM course c
  LEFT JOIN course_list_price p
         ON p.course_id = c.id AND p.deleted_at IS NULL AND p.valid_from <= now() AND (p.valid_to IS NULL OR p.valid_to > now())
  LEFT JOIN (SELECT rv.course_id, avg(rv.rating)::DECIMAL(3,2) AS avg_rating, count(*) AS review_count
               FROM course_review rv JOIN content_review_status s ON s.id = rv.content_review_status_id AND s.code = 'approved'
              WHERE rv.deleted_at IS NULL GROUP BY rv.course_id) r ON r.course_id = c.id
 WHERE c.deleted_at IS NULL AND c.is_active;

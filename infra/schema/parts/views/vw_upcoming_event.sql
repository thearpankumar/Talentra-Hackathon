-- Published future events for discovery; public columns only (no join_url).
-- OVERRIDE reason: the sketch did not restrict to published status (drafts/cancelled events were visible).
CREATE OR REPLACE VIEW vw_upcoming_event AS
SELECT e.id, e.title, e.starts_at, e.ends_at, e.event_format_id, e.city_id
  FROM event e
  JOIN event_status s ON s.id = e.event_status_id AND s.code = 'published'
 WHERE e.deleted_at IS NULL AND e.starts_at > now();

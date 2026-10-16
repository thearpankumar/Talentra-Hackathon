-- Minimal public verification result by verification_code.
-- OVERRIDE reason: adds status_code and is_valid (issued, not revoked, not expired) so verifiers need no lookup access; still
-- exposes no holder/contact data.
CREATE OR REPLACE VIEW vw_certificate_public_verification AS
SELECT c.verification_code, c.title, c.issued_at, c.expires_at, c.certificate_status_id, s.code AS status_code,
       (s.code = 'issued' AND c.revoked_at IS NULL AND (c.expires_at IS NULL OR c.expires_at > now())) AS is_valid
  FROM certificate c
  JOIN certificate_status s ON s.id = c.certificate_status_id
 WHERE c.deleted_at IS NULL;

-- Security helpers: password-hash guards (BR-ADM-04), credential history trigger (BR-ADM-03, AuditCoverage row 1),
-- audit hash chain (BR-ADM-09): fn_audit_append / fn_audit_verify_chain, chain-head seed rows.
-- Idempotent; statements separated by '-- @@'.

-- PHC string of argon2id: $argon2id$v=19$m=..,t=..,p=..$<salt>$<hash>   (BR-ADM-04: argon2id hashes only)
ALTER TABLE user_credential ADD CONSTRAINT IF NOT EXISTS ck_user_credential_password_hash_argon2id
  CHECK (password_hash ~ '^\$argon2id\$v=[0-9]+\$m=[0-9]+,t=[0-9]+,p=[0-9]+\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+$');
-- @@
-- argon2id parameters are recorded with every hash (needed for rehash decisions)
ALTER TABLE user_credential ADD CONSTRAINT IF NOT EXISTS ck_user_credential_argon_params_set
  CHECK (argon2_memory_kib IS NOT NULL AND argon2_iterations IS NOT NULL AND argon2_parallelism IS NOT NULL);
-- @@
CREATE OR REPLACE FUNCTION fn_user_credential_guard() RETURNS TRIGGER LANGUAGE PLpgSQL AS $$
DECLARE v_code STRING;
BEGIN
  SELECT code INTO v_code FROM hash_algorithm WHERE id = (NEW).hash_algorithm_id;
  IF v_code IS DISTINCT FROM 'argon2id' THEN
    RAISE EXCEPTION 'user_credential: only argon2id hashes are stored (hash_algorithm=%)', v_code USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
-- @@
CREATE OR REPLACE TRIGGER trg_user_credential_guard BEFORE INSERT OR UPDATE ON user_credential
  FOR EACH ROW EXECUTE FUNCTION fn_user_credential_guard();
-- @@
-- every credential insert / change / flag writes user_credential_history in the same transaction
CREATE OR REPLACE FUNCTION fn_user_credential_history() RETURNS TRIGGER LANGUAGE PLpgSQL AS $$
DECLARE v_event STRING; v_actor UUID; v_ev UUID;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF EXISTS (SELECT 1 FROM user_credential WHERE app_user_id = (NEW).app_user_id AND id <> (NEW).id) THEN
      v_event := 'changed';
    ELSE
      v_event := 'set';
    END IF;
    v_actor := (NEW).created_by;
  ELSE
    v_actor := (NEW).updated_by;
    IF (NEW).password_hash IS DISTINCT FROM (OLD).password_hash THEN
      IF (NEW).hash_algorithm_id IS DISTINCT FROM (OLD).hash_algorithm_id
         OR (NEW).argon2_memory_kib IS DISTINCT FROM (OLD).argon2_memory_kib THEN v_event := 'rehashed';
      ELSE v_event := 'changed'; END IF;
    ELSIF (NEW).breach_detected_at IS NOT NULL AND (OLD).breach_detected_at IS NULL THEN v_event := 'breach_flagged';
    ELSIF (NEW).must_change AND NOT (OLD).must_change THEN v_event := 'forced_rotation';
    ELSE RETURN NEW;
    END IF;
  END IF;
  SELECT id INTO v_ev FROM credential_event_type WHERE code = v_event;
  INSERT INTO user_credential_history (created_by, app_user_id, user_credential_id, credential_event_type_id, ip_address, user_agent, request_ref)
  VALUES (v_actor, (NEW).app_user_id, (NEW).id, v_ev,
          NULLIF(current_setting('app.client_ip', true), '')::INET,
          NULLIF(current_setting('app.user_agent', true), ''),
          NULLIF(current_setting('app.request_ref', true), '')::UUID);
  RETURN NEW;
END $$;
-- @@
CREATE OR REPLACE TRIGGER trg_user_credential_history AFTER INSERT OR UPDATE ON user_credential
  FOR EACH ROW EXECUTE FUNCTION fn_user_credential_history();
-- @@
-- ------------------------------------------------------------------ audit hash chain (BR-ADM-09)
-- 32 chains (chain_no 0..31); a request id maps deterministically to a chain
CREATE OR REPLACE FUNCTION fn_audit_chain_no(p_request UUID) RETURNS INT2 LANGUAGE SQL IMMUTABLE AS $$
SELECT (get_byte(decode(replace(p_request::STRING, '-', ''), 'hex'), 0) % 32)::INT2 $$;
-- @@
-- row_hash = SHA-256(prev_hash || canonical field list)
CREATE OR REPLACE FUNCTION fn_audit_row_hash(p_prev BYTES, p_id UUID, p_occurred TIMESTAMPTZ, p_actor UUID, p_actor_type UUID,
  p_action UUID, p_entity_type UUID, p_entity_ref UUID, p_outcome UUID, p_request UUID, p_chain INT2, p_seq INT8)
RETURNS BYTES LANGUAGE SQL IMMUTABLE AS $$
SELECT digest(
  coalesce(p_prev, '\x'::BYTES) || convert_to(concat_ws('|', p_id::STRING,
     (extract(epoch FROM p_occurred) * 1000000)::INT8::STRING, coalesce(p_actor::STRING, ''), p_actor_type::STRING,
     p_action::STRING, p_entity_type::STRING, coalesce(p_entity_ref::STRING, ''), p_outcome::STRING, p_request::STRING,
     p_chain::STRING, p_seq::STRING), 'utf8'),
  'sha256') $$;
-- @@
-- append one audit row to its chain inside the caller's (business) transaction: head row locked FOR UPDATE.
CREATE OR REPLACE FUNCTION fn_audit_append(p_actor_type_code STRING, p_actor UUID, p_action_code STRING,
  p_entity_type_code STRING, p_entity_ref UUID, p_outcome_code STRING, p_request_ref UUID,
  p_organisation_ref UUID DEFAULT NULL, p_before JSONB DEFAULT NULL, p_after JSONB DEFAULT NULL,
  p_reason STRING DEFAULT NULL, p_session UUID DEFAULT NULL, p_ip INET DEFAULT NULL, p_user_agent STRING DEFAULT NULL,
  p_impersonator UUID DEFAULT NULL, p_created_by UUID DEFAULT '00000000-0000-0000-0000-000000000001')
RETURNS UUID LANGUAGE PLpgSQL AS $$
DECLARE
  v_chain INT2; v_seq INT8; v_prev BYTES; v_hash BYTES; v_id UUID := gen_random_uuid(); v_occ TIMESTAMPTZ := clock_timestamp();
  v_at UUID; v_act UUID; v_et UUID; v_out UUID; v_head UUID;
BEGIN
  SELECT id INTO v_at FROM actor_type WHERE code = p_actor_type_code;
  SELECT id INTO v_act FROM audit_action WHERE code = p_action_code;
  SELECT id INTO v_et FROM audit_entity_type WHERE code = p_entity_type_code;
  SELECT id INTO v_out FROM audit_outcome WHERE code = p_outcome_code;
  IF v_at IS NULL OR v_act IS NULL OR v_et IS NULL OR v_out IS NULL THEN
    RAISE EXCEPTION 'fn_audit_append: unknown vocabulary code (actor_type=% action=% entity_type=% outcome=%)',
      p_actor_type_code, p_action_code, p_entity_type_code, p_outcome_code USING ERRCODE = '23503';
  END IF;
  v_chain := fn_audit_chain_no(p_request_ref);
  SELECT id, last_chain_seq, last_row_hash INTO v_head, v_seq, v_prev FROM audit_chain_head WHERE chain_no = v_chain FOR UPDATE;
  IF v_head IS NULL THEN
    RAISE EXCEPTION 'fn_audit_append: audit_chain_head % missing', v_chain USING ERRCODE = '23503';
  END IF;
  v_seq := v_seq + 1;
  v_hash := fn_audit_row_hash(v_prev, v_id, v_occ, p_actor, v_at, v_act, v_et, p_entity_ref, v_out, p_request_ref, v_chain, v_seq);
  INSERT INTO audit_event (id, created_by, occurred_at, actor_app_user_id, actor_type_id, impersonator_app_user_id,
      audit_action_id, audit_entity_type_id, entity_ref, organisation_ref, audit_outcome_id, before_state, after_state,
      reason_text, request_ref, identity_session_ref, ip_address, user_agent, chain_no, chain_seq, prev_hash, row_hash)
  VALUES (v_id, p_created_by, v_occ, p_actor, v_at, p_impersonator, v_act, v_et, p_entity_ref, p_organisation_ref, v_out,
      p_before, p_after, p_reason, p_request_ref, p_session, p_ip, p_user_agent, v_chain, v_seq, v_prev, v_hash);
  UPDATE audit_chain_head SET last_chain_seq = v_seq, last_row_hash = v_hash, updated_at = clock_timestamp(),
      updated_by = p_created_by WHERE id = v_head;
  RETURN v_id;
END $$;
-- @@
-- chain verifier (AT-40): returns one row per defect found in the chain
CREATE OR REPLACE FUNCTION fn_audit_verify_chain(p_chain INT2) RETURNS TABLE (chain_seq INT8, problem STRING)
LANGUAGE SQL STABLE AS $$
WITH e AS (
  SELECT a.chain_seq AS s, a.prev_hash AS ph, a.row_hash AS rh,
         fn_audit_row_hash(a.prev_hash, a.id, a.occurred_at, a.actor_app_user_id, a.actor_type_id, a.audit_action_id,
            a.audit_entity_type_id, a.entity_ref, a.audit_outcome_id, a.request_ref, a.chain_no, a.chain_seq) AS calc,
         lag(a.row_hash) OVER (ORDER BY a.chain_seq) AS lag_rh,
         lag(a.chain_seq) OVER (ORDER BY a.chain_seq) AS lag_s,
         last_value(a.row_hash) OVER (ORDER BY a.chain_seq ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS tip_rh,
         last_value(a.chain_seq) OVER (ORDER BY a.chain_seq ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS tip_s
  FROM audit_event a WHERE a.chain_no = p_chain)
SELECT s, 'row_hash_mismatch' FROM e WHERE calc IS DISTINCT FROM rh
UNION ALL SELECT s, 'prev_hash_mismatch' FROM e WHERE lag_s IS NOT NULL AND ph IS DISTINCT FROM lag_rh
UNION ALL SELECT s, 'sequence_gap' FROM e WHERE lag_s IS NOT NULL AND s <> lag_s + 1
UNION ALL SELECT e.tip_s, 'head_mismatch' FROM e JOIN audit_chain_head h ON h.chain_no = p_chain
  WHERE e.s = e.tip_s AND (h.last_chain_seq <> e.tip_s OR h.last_row_hash IS DISTINCT FROM e.tip_rh)
$$;

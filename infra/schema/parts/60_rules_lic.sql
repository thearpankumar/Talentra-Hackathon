-- 60_rules_lic.sql : procedural business rules of modules licences_devices, organisation_account, administration
-- (BR-LIC-02/03/05/06/07/08/09/14/15, BR-ADM-07/16, BR-ORG-04/06/08, BR-LGS-03; see the header of each function).
-- Conventions
--  * every function is SECURITY INVOKER: the calling service role needs the table privileges (see 75_post_grants_lic.sql);
--  * functions never COMMIT: a "denial" is a normal return value (not an exception), so the evidence rows (login_attempt,
--    audit_event, device_limit_violation) are committed together with the transaction the caller ends with COMMIT;
--  * the database runs SERIALIZABLE: callers MUST retry the whole transaction on SQLSTATE 40001 (tests use tests/conftest.py retry());
--  * all locks are SELECT ... FOR UPDATE on the rows named in the rule text (seat / licence / account / organisation).
-- CockroachDB PL/pgSQL limits honoured (v26.2): no FOUND, no RECORD variables, no FOR..LOOP over a query, no GET DIAGNOSTICS,
-- no PERFORM ... FOR UPDATE, no named-argument calls (=>): scalar SELECT .. INTO, set-based statements, INT * INTERVAL instead.
-- The audit chain writer (BR-ADM-09) is fn_lic_audit below.

-- BR-ADM-09 writer used by every function below. Self-contained (60_security.sql fn_audit_append uses IF NOT FOUND, which CockroachDB
-- v26.2 rejects) but byte-compatible with it: chain = first byte of the request uuid mod 32, head row locked FOR UPDATE, row_hash =
-- SHA-256(prev_hash || canonical field list) exactly as fn_audit_row_hash, so fn_audit_verify_chain validates these rows too.
-- The actor type is derived from the actor's account ('service' when the actor is NULL / unknown).
INSERT INTO audit_chain_head (chain_no, created_by, updated_by)
SELECT g::INT2, '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001' FROM generate_series(0, 31) AS g
ON CONFLICT (chain_no) DO NOTHING;
-- @@
CREATE OR REPLACE FUNCTION fn_lic_audit(p_actor UUID, p_action STRING, p_entity STRING, p_entity_ref UUID, p_outcome STRING,
  p_request UUID, p_reason STRING DEFAULT NULL, p_ip INET DEFAULT NULL, p_after JSONB DEFAULT NULL) RETURNS UUID LANGUAGE PLpgSQL AS $$
DECLARE
  v_chain INT2; v_seq INT8; v_prev BYTES; v_hash BYTES; v_id UUID := gen_random_uuid(); v_occ TIMESTAMPTZ := clock_timestamp();
  v_at UUID; v_act UUID; v_et UUID; v_out UUID; v_head UUID;
BEGIN
  v_at := COALESCE((SELECT u.actor_type_id FROM app_user u WHERE u.id = p_actor), (SELECT t.id FROM actor_type t WHERE t.code = 'service'));
  v_act := (SELECT id FROM audit_action WHERE code = p_action);
  v_et := (SELECT id FROM audit_entity_type WHERE code = p_entity);
  v_out := (SELECT id FROM audit_outcome WHERE code = p_outcome);
  IF v_at IS NULL OR v_act IS NULL OR v_et IS NULL OR v_out IS NULL THEN
    RAISE EXCEPTION 'fn_lic_audit: unknown vocabulary code (action=% entity=% outcome=%)', p_action, p_entity, p_outcome USING ERRCODE = '23503';
  END IF;
  v_chain := (get_byte(decode(replace(p_request::STRING, '-', ''), 'hex'), 0) % 32)::INT2;
  SELECT h.id, h.last_chain_seq, h.last_row_hash INTO v_head, v_seq, v_prev FROM audit_chain_head h WHERE h.chain_no = v_chain FOR UPDATE;
  IF v_head IS NULL THEN
    RAISE EXCEPTION 'fn_lic_audit: audit_chain_head % missing', v_chain USING ERRCODE = '23503';
  END IF;
  v_seq := v_seq + 1;
  v_hash := digest(coalesce(v_prev, '\x'::BYTES) || convert_to(concat_ws('|', v_id::STRING,
     (extract(epoch FROM v_occ) * 1000000)::INT8::STRING, coalesce(p_actor::STRING, ''), v_at::STRING,
     v_act::STRING, v_et::STRING, coalesce(p_entity_ref::STRING, ''), v_out::STRING, p_request::STRING,
     v_chain::STRING, v_seq::STRING), 'utf8'), 'sha256');
  INSERT INTO audit_event (id, created_by, occurred_at, actor_app_user_id, actor_type_id, audit_action_id, audit_entity_type_id, entity_ref,
      audit_outcome_id, after_state, reason_text, request_ref, ip_address, chain_no, chain_seq, prev_hash, row_hash)
  VALUES (v_id, COALESCE(p_actor, '00000000-0000-0000-0000-000000000001'), v_occ, p_actor, v_at, v_act, v_et, p_entity_ref, v_out,
      p_after, p_reason, p_request, p_ip, v_chain, v_seq, v_prev, v_hash);
  UPDATE audit_chain_head SET last_chain_seq = v_seq, last_row_hash = v_hash, updated_at = clock_timestamp(),
      updated_by = COALESCE(p_actor, '00000000-0000-0000-0000-000000000001') WHERE id = v_head;
  RETURN v_id;
END $$;
-- @@
-- BR-LIC-14 (+ slot provisioning for BR-LIC-02/05): make licence_seat_slot rows 1..max_users and, for every live seat,
-- device_slot rows 1..max_devices_per_user exist; slots above the limit are retired (never deleted: held slots drain naturally).
CREATE OR REPLACE FUNCTION fn_licence_sync_slots(p_licence UUID, p_actor UUID DEFAULT '00000000-0000-0000-0000-000000000001')
RETURNS BOOL LANGUAGE PLpgSQL AS $$
DECLARE v_max_users INT4; v_max_dev INT4;
BEGIN
  SELECT l.max_users, l.max_devices_per_user INTO v_max_users, v_max_dev FROM licence l WHERE l.id = p_licence FOR UPDATE;
  IF v_max_users IS NULL THEN RAISE EXCEPTION 'licence % not found', p_licence USING ERRCODE = '02000'; END IF;
  INSERT INTO licence_seat_slot (licence_id, slot_no, created_by, updated_by)
    SELECT p_licence, g, p_actor, p_actor FROM generate_series(1, v_max_users) g
    ON CONFLICT (licence_id, slot_no) DO NOTHING;
  UPDATE licence_seat_slot SET is_retired = (slot_no > v_max_users), updated_at = now(), updated_by = p_actor
    WHERE licence_id = p_licence AND is_retired <> (slot_no > v_max_users);
  INSERT INTO device_slot (licence_seat_id, slot_no, created_by, updated_by)
    SELECT s.id, g, p_actor, p_actor FROM licence_seat s, generate_series(1, v_max_dev) g
    WHERE s.licence_id = p_licence AND s.revoked_at IS NULL AND s.deleted_at IS NULL
    ON CONFLICT (licence_seat_id, slot_no) DO NOTHING;
  UPDATE device_slot SET is_retired = (slot_no > v_max_dev), updated_at = now(), updated_by = p_actor
    WHERE licence_seat_id IN (SELECT s.id FROM licence_seat s WHERE s.licence_id = p_licence AND s.revoked_at IS NULL AND s.deleted_at IS NULL)
      AND is_retired <> (slot_no > v_max_dev);
  RETURN TRUE;
END $$;
-- @@
-- BR-LIC-14: change limits under the licence lock; surplus slots are retired, never killed.
CREATE OR REPLACE FUNCTION fn_licence_set_limits(p_licence UUID, p_max_users INT4, p_max_devices INT4, p_actor UUID)
RETURNS BOOL LANGUAGE PLpgSQL AS $$
DECLARE v_x INT4; v_ok BOOL;
BEGIN
  SELECT 1 INTO v_x FROM licence l WHERE l.id = p_licence FOR UPDATE;
  IF v_x IS NULL THEN RAISE EXCEPTION 'licence % not found', p_licence USING ERRCODE = '02000'; END IF;
  UPDATE licence SET max_users = p_max_users, max_devices_per_user = p_max_devices, updated_at = now(), updated_by = p_actor WHERE id = p_licence;
  v_ok := fn_licence_sync_slots(p_licence, p_actor);
  RETURN v_ok;
END $$;
-- @@
-- BR-LIC-03 (and BR-LIC-02): seat assignment, serialised per licence.
-- o_outcome: assigned | already_seated | licence_inactive | seat_limit_reached
CREATE OR REPLACE FUNCTION fn_seat_assign(p_licence UUID, p_user UUID, p_actor UUID, p_request UUID DEFAULT gen_random_uuid())
RETURNS TABLE (o_outcome STRING, o_seat_id UUID, o_slot_no INT4) LANGUAGE PLpgSQL AS $$
DECLARE
  v_status STRING; v_from TIMESTAMPTZ; v_to TIMESTAMPTZ; v_max_dev INT4; v_slot UUID; v_slot_no INT4; v_seat UUID; v_lic UUID; v_aud UUID;
BEGIN
  SELECT l.id, s.code, l.valid_from, l.valid_to, l.max_devices_per_user INTO v_lic, v_status, v_from, v_to, v_max_dev
    FROM licence l JOIN licence_status s ON s.id = l.licence_status_id WHERE l.id = p_licence FOR UPDATE OF l;
  IF v_lic IS NULL THEN RAISE EXCEPTION 'licence % not found', p_licence USING ERRCODE = '02000'; END IF;
  IF v_status <> 'active' OR v_from > now() OR (v_to IS NOT NULL AND v_to <= now()) THEN
    v_aud := fn_lic_audit(p_actor, 'access_denied', 'licence', p_licence, 'denied', p_request, 'licence_inactive');
    RETURN QUERY SELECT 'licence_inactive'::STRING, NULL::UUID, NULL::INT4; RETURN;
  END IF;
  SELECT ls.id INTO v_seat FROM licence_seat ls
   WHERE ls.licence_id = p_licence AND ls.app_user_id = p_user AND ls.revoked_at IS NULL AND ls.deleted_at IS NULL;
  IF v_seat IS NOT NULL THEN
    RETURN QUERY SELECT 'already_seated'::STRING, v_seat, NULL::INT4; RETURN;
  END IF;
  SELECT sl.id, sl.slot_no INTO v_slot, v_slot_no FROM licence_seat_slot sl
   WHERE sl.licence_id = p_licence AND NOT sl.is_retired
     AND sl.id NOT IN (SELECT x.licence_seat_slot_id FROM licence_seat x WHERE x.licence_id = p_licence AND x.revoked_at IS NULL AND x.deleted_at IS NULL)
   ORDER BY sl.slot_no LIMIT 1;
  IF v_slot IS NULL THEN
    v_aud := fn_lic_audit(p_actor, 'access_denied', 'licence', p_licence, 'denied', p_request, 'seat_limit_reached');
    RETURN QUERY SELECT 'seat_limit_reached'::STRING, NULL::UUID, NULL::INT4; RETURN;
  END IF;
  v_seat := gen_random_uuid();
  INSERT INTO licence_seat (id, created_by, updated_by, licence_id, licence_seat_slot_id, app_user_id, seat_status_id, assigned_by_app_user_id)
    VALUES (v_seat, p_actor, p_actor, p_licence, v_slot, p_user, (SELECT id FROM seat_status WHERE code = 'active'), p_actor);
  INSERT INTO device_slot (licence_seat_id, slot_no, created_by, updated_by)
    SELECT v_seat, g, p_actor, p_actor FROM generate_series(1, v_max_dev) g;
  UPDATE licence SET seats_in_use = seats_in_use + 1, updated_at = now(), updated_by = p_actor WHERE id = p_licence;
  v_aud := fn_lic_audit(p_actor, 'create', 'licence_seat', v_seat, 'success', p_request, 'seat_assigned');
  RETURN QUERY SELECT 'assigned'::STRING, v_seat, v_slot_no;
END $$;
-- @@
-- BR-LIC-02/05: release a seat: ends its live sessions (seat_removed), frees the seat slot, seats_in_use - 1. Returns false when not live.
CREATE OR REPLACE FUNCTION fn_seat_revoke(p_seat UUID, p_actor UUID, p_request UUID DEFAULT gen_random_uuid())
RETURNS BOOL LANGUAGE PLpgSQL AS $$
DECLARE v_lic UUID; v_x UUID; v_aud UUID;
BEGIN
  SELECT s.licence_id INTO v_lic FROM licence_seat s WHERE s.id = p_seat;
  IF v_lic IS NULL THEN RETURN FALSE; END IF;
  SELECT l.id INTO v_x FROM licence l WHERE l.id = v_lic FOR UPDATE;
  SELECT s.id INTO v_x FROM licence_seat s WHERE s.id = p_seat AND s.revoked_at IS NULL AND s.deleted_at IS NULL FOR UPDATE;
  IF v_x IS NULL THEN RETURN FALSE; END IF;
  -- identity_session rows of the ended device sessions are revoked by fn_identity_session_sync_ended (auth_svc job / next refresh)
  UPDATE device_session SET ended_at = now(), updated_at = now(), session_end_reason_id = (SELECT id FROM session_end_reason WHERE code = 'seat_removed')
   WHERE licence_seat_id = p_seat AND ended_at IS NULL;
  UPDATE licence_seat SET revoked_at = now(), seat_status_id = (SELECT id FROM seat_status WHERE code = 'revoked'), updated_at = now(), updated_by = p_actor
   WHERE id = p_seat;
  UPDATE licence SET seats_in_use = seats_in_use - 1, updated_at = now(), updated_by = p_actor WHERE id = v_lic;
  v_aud := fn_lic_audit(p_actor, 'delete', 'licence_seat', p_seat, 'success', p_request, 'seat_revoked');
  RETURN TRUE;
END $$;
-- @@
-- BR-LIC-06 / BR-LIC-07 (+BR-LIC-15 gate): login device-limit claim, one transaction (caller retries on 40001 and on a unique
-- violation of uq_device_session_device_slot_id). Locks only this user's seat row (FOR UPDATE OF s). Denials return normally and
-- write login_attempt (+ audit_event + device_limit_violation for the device limit) so the caller COMMITs the evidence.
-- o_outcome: success | no_seat | licence_inactive | ip_blocked | device_revoked | device_limit_denied
CREATE OR REPLACE FUNCTION fn_login_claim(
  p_user UUID, p_licence UUID, p_fingerprint BYTES, p_platform STRING, p_ip INET, p_request_ref STRING, p_refresh_hash STRING,
  p_user_agent STRING DEFAULT 'test-agent', p_auth_method STRING DEFAULT 'password', p_identifier_hmac BYTES DEFAULT NULL
) RETURNS TABLE (o_outcome STRING, o_identity_session_id UUID, o_device_session_id UUID, o_device_id UUID, o_seat_id UUID, o_slot_no INT4)
LANGUAGE PLpgSQL AS $$
DECLARE
  v_sys UUID := '00000000-0000-0000-0000-000000000001';
  v_lic UUID; v_status STRING; v_from TIMESTAMPTZ; v_to TIMESTAMPTZ; v_max_dev INT4; v_policy_id UUID; v_idle INT4; v_hours INT4; v_req_ip BOOL;
  v_seat UUID; v_attempt UUID := gen_random_uuid(); v_ident BYTES;
  v_dev UUID; v_dev_revoked TIMESTAMPTZ; v_slot UUID; v_slot_no INT4; v_active INT4; v_policy STRING; v_oldest UUID;
  v_audit UUID; v_idsess UUID := gen_random_uuid(); v_dsess UUID := gen_random_uuid(); v_amethod UUID;
  v_req_uuid UUID := gen_random_uuid(); v_lic_active BOOL; v_denied_ip BOOL;
BEGIN
  v_ident := COALESCE(p_identifier_hmac, digest(convert_to(p_user::STRING, 'utf8'), 'sha256'));
  v_amethod := (SELECT id FROM authentication_method WHERE code = p_auth_method);
  -- 1) seat + licence; lock only this user's seat
  SELECT l.id, ls.code, l.valid_from, l.valid_to, l.max_devices_per_user, l.device_limit_policy_id,
         l.session_idle_timeout_minutes, l.session_max_lifetime_hours, l.require_ip_allowlist, s.id
    INTO v_lic, v_status, v_from, v_to, v_max_dev, v_policy_id, v_idle, v_hours, v_req_ip, v_seat
    FROM licence l JOIN licence_seat s ON s.licence_id = l.id JOIN licence_status ls ON ls.id = l.licence_status_id
   WHERE s.app_user_id = p_user AND s.revoked_at IS NULL AND s.deleted_at IS NULL AND l.id = p_licence
     FOR UPDATE OF s;
  IF v_lic IS NULL THEN
    INSERT INTO login_attempt (id, created_by, app_user_id, identifier_hmac, login_outcome_id, authentication_method_id, licence_id, device_fingerprint_hash, ip_address, user_agent, request_ref)
      VALUES (v_attempt, v_sys, p_user, v_ident, (SELECT id FROM login_outcome WHERE code = 'no_seat'), v_amethod, p_licence, p_fingerprint, p_ip, p_user_agent, p_request_ref);
    RETURN QUERY SELECT 'no_seat'::STRING, NULL::UUID, NULL::UUID, NULL::UUID, NULL::UUID, NULL::INT4; RETURN;
  END IF;
  v_lic_active := v_status = 'active' AND v_from <= now() AND (v_to IS NULL OR v_to > now());
  IF NOT v_lic_active THEN
    INSERT INTO login_attempt (id, created_by, app_user_id, identifier_hmac, login_outcome_id, authentication_method_id, licence_id, device_fingerprint_hash, ip_address, user_agent, request_ref)
      VALUES (v_attempt, v_sys, p_user, v_ident, (SELECT id FROM login_outcome WHERE code = 'licence_inactive'), v_amethod, p_licence, p_fingerprint, p_ip, p_user_agent, p_request_ref);
    RETURN QUERY SELECT 'licence_inactive'::STRING, NULL::UUID, NULL::UUID, NULL::UUID, v_seat, NULL::INT4; RETURN;
  END IF;
  -- 2) IP allowlist
  v_denied_ip := v_req_ip AND NOT EXISTS (SELECT 1 FROM licence_ip_allowlist a WHERE a.licence_id = p_licence AND a.is_active AND a.deleted_at IS NULL AND a.ip_network >>= p_ip);
  IF v_denied_ip THEN
    INSERT INTO login_attempt (id, created_by, app_user_id, identifier_hmac, login_outcome_id, authentication_method_id, licence_id, device_fingerprint_hash, ip_address, user_agent, request_ref)
      VALUES (v_attempt, v_sys, p_user, v_ident, (SELECT id FROM login_outcome WHERE code = 'ip_blocked'), v_amethod, p_licence, p_fingerprint, p_ip, p_user_agent, p_request_ref);
    RETURN QUERY SELECT 'ip_blocked'::STRING, NULL::UUID, NULL::UUID, NULL::UUID, v_seat, NULL::INT4; RETURN;
  END IF;
  -- 3) lazy release of stale sessions of this seat
  UPDATE device_session SET ended_at = now(), updated_at = now(),
         session_end_reason_id = (SELECT id FROM session_end_reason WHERE code = CASE WHEN expires_at <= now() THEN 'absolute_expiry' ELSE 'idle_timeout' END)
   WHERE licence_seat_id = v_seat AND ended_at IS NULL
     AND (expires_at <= now() OR last_activity_at < now() - v_idle * INTERVAL '1 minute');
  -- 4) device upsert
  INSERT INTO device (app_user_id, device_fingerprint_hash, device_platform_id, last_ip_address, created_by, updated_by)
    VALUES (p_user, p_fingerprint, (SELECT id FROM device_platform WHERE code = p_platform), p_ip, p_user, p_user)
    ON CONFLICT (app_user_id, device_fingerprint_hash) WHERE deleted_at IS NULL
    DO UPDATE SET last_seen_at = now(), last_ip_address = p_ip, updated_at = now()
    RETURNING id, revoked_at INTO v_dev, v_dev_revoked;
  IF v_dev_revoked IS NOT NULL THEN
    INSERT INTO login_attempt (id, created_by, app_user_id, identifier_hmac, login_outcome_id, authentication_method_id, licence_id, device_id, device_fingerprint_hash, ip_address, user_agent, request_ref)
      VALUES (v_attempt, v_sys, p_user, v_ident, (SELECT id FROM login_outcome WHERE code = 'device_revoked'), v_amethod, p_licence, v_dev, p_fingerprint, p_ip, p_user_agent, p_request_ref);
    RETURN QUERY SELECT 'device_revoked'::STRING, NULL::UUID, NULL::UUID, v_dev, v_seat, NULL::INT4; RETURN;
  END IF;
  -- 5) same-device re-login replaces that device's old session
  UPDATE identity_session SET revoked_at = now(), updated_at = now(), session_revoke_reason_id = (SELECT id FROM session_revoke_reason WHERE code = 'logout')
   WHERE revoked_at IS NULL AND device_session_id IN (SELECT d.id FROM device_session d WHERE d.device_id = v_dev AND d.ended_at IS NULL);
  UPDATE device_session SET ended_at = now(), updated_at = now(), session_end_reason_id = (SELECT id FROM session_end_reason WHERE code = 'replaced')
   WHERE device_id = v_dev AND ended_at IS NULL;
  -- 6) claim a free, non-retired device slot (lowest slot_no)
  SELECT ds.id, ds.slot_no INTO v_slot, v_slot_no FROM device_slot ds
   WHERE ds.licence_seat_id = v_seat AND NOT ds.is_retired
     AND NOT EXISTS (SELECT 1 FROM device_session x WHERE x.device_slot_id = ds.id AND x.ended_at IS NULL)
   ORDER BY ds.slot_no LIMIT 1;
  IF v_slot IS NULL THEN
    SELECT count(*)::INT4 INTO v_active FROM device_session x WHERE x.licence_seat_id = v_seat AND x.ended_at IS NULL;
    SELECT code INTO v_policy FROM device_limit_policy WHERE id = v_policy_id;
    v_audit := fn_lic_audit(p_user, 'device_limit_exceeded', 'licence_seat', v_seat, 'denied', v_req_uuid, 'device_limit_' || v_policy, p_ip);
    IF v_policy = 'replace_oldest' THEN
      SELECT x.id, x.device_slot_id INTO v_oldest, v_slot FROM device_session x
       WHERE x.licence_seat_id = v_seat AND x.ended_at IS NULL
         AND x.device_slot_id IN (SELECT ds.id FROM device_slot ds WHERE ds.licence_seat_id = v_seat AND NOT ds.is_retired)
       ORDER BY x.last_activity_at, x.started_at LIMIT 1;
    END IF;
    IF v_policy <> 'replace_oldest' OR v_oldest IS NULL THEN
      -- 7) deny_new: record the evidence (committed by the caller; the denial is a normal return)
      INSERT INTO login_attempt (id, created_by, app_user_id, identifier_hmac, login_outcome_id, authentication_method_id, licence_id, device_id, device_fingerprint_hash, ip_address, user_agent, request_ref)
        VALUES (v_attempt, v_sys, p_user, v_ident, (SELECT id FROM login_outcome WHERE code = 'device_limit_denied'), v_amethod, p_licence, v_dev, p_fingerprint, p_ip, p_user_agent, p_request_ref);
      INSERT INTO device_limit_violation (created_by, licence_seat_id, app_user_id, device_id, attempted_fingerprint_hash, active_session_count,
                                         max_devices_allowed, device_limit_policy_id, login_attempt_id, audit_event_id)
        VALUES (v_sys, v_seat, p_user, v_dev, p_fingerprint, GREATEST(v_active, v_max_dev), v_max_dev, v_policy_id, v_attempt, v_audit);
      RETURN QUERY SELECT 'device_limit_denied'::STRING, NULL::UUID, NULL::UUID, v_dev, v_seat, NULL::INT4; RETURN;
    END IF;
    -- replace_oldest: end the session with the lowest last_activity_at and reuse its slot
    UPDATE device_session SET ended_at = now(), updated_at = now(), session_end_reason_id = (SELECT id FROM session_end_reason WHERE code = 'replaced')
     WHERE id = v_oldest;
    UPDATE identity_session SET revoked_at = now(), updated_at = now(), session_revoke_reason_id = (SELECT id FROM session_revoke_reason WHERE code = 'device_limit')
     WHERE revoked_at IS NULL AND device_session_id = v_oldest;
    SELECT ds.slot_no INTO v_slot_no FROM device_slot ds WHERE ds.id = v_slot;
    INSERT INTO login_attempt (id, created_by, app_user_id, identifier_hmac, login_outcome_id, authentication_method_id, licence_id, device_id, device_fingerprint_hash, ip_address, user_agent, request_ref)
      VALUES (v_attempt, v_sys, p_user, v_ident, (SELECT id FROM login_outcome WHERE code = 'success'), v_amethod, p_licence, v_dev, p_fingerprint, p_ip, p_user_agent, p_request_ref);
    INSERT INTO device_limit_violation (created_by, licence_seat_id, app_user_id, device_id, attempted_fingerprint_hash, active_session_count,
                                       max_devices_allowed, device_limit_policy_id, replaced_device_session_id, login_attempt_id, audit_event_id)
      VALUES (v_sys, v_seat, p_user, v_dev, p_fingerprint, GREATEST(v_active, v_max_dev), v_max_dev, v_policy_id, v_oldest, v_attempt, v_audit);
  ELSE
    INSERT INTO login_attempt (id, created_by, app_user_id, identifier_hmac, login_outcome_id, authentication_method_id, licence_id, device_id, device_fingerprint_hash, ip_address, user_agent, request_ref)
      VALUES (v_attempt, v_sys, p_user, v_ident, (SELECT id FROM login_outcome WHERE code = 'success'), v_amethod, p_licence, v_dev, p_fingerprint, p_ip, p_user_agent, p_request_ref);
  END IF;
  -- 8) sessions
  INSERT INTO identity_session (id, created_by, updated_by, app_user_id, refresh_token_hash, token_family_ref, auth_method_id, issued_at, expires_at, absolute_expires_at, ip_address, user_agent)
    VALUES (v_idsess, p_user, p_user, p_user, p_refresh_hash, gen_random_uuid(), (SELECT id FROM auth_method WHERE code = p_auth_method),
            now(), now() + v_idle * INTERVAL '1 minute', now() + v_hours * INTERVAL '1 hour', p_ip, p_user_agent);
  INSERT INTO device_session (id, created_by, updated_by, identity_session_id, app_user_id, device_id, licence_seat_id, device_slot_id, expires_at, ip_address, authentication_method_id)
    VALUES (v_dsess, p_user, p_user, v_idsess, p_user, v_dev, v_seat, v_slot, now() + v_hours * INTERVAL '1 hour', p_ip, v_amethod);
  UPDATE identity_session SET device_session_id = v_dsess WHERE id = v_idsess;
  UPDATE licence_seat SET last_login_at = now(), updated_at = now() WHERE id = v_seat;
  RETURN QUERY SELECT 'success'::STRING, v_idsess, v_dsess, v_dev, v_seat, v_slot_no;
END $$;
-- @@
-- BR-LIC-05 / BR-LIC-08(a): end one session (logout / revoke / expiry), release its slot, revoke the linked identity_session.
CREATE OR REPLACE FUNCTION fn_device_session_end(p_device_session UUID, p_reason STRING DEFAULT 'logout')
RETURNS BOOL LANGUAGE PLpgSQL AS $$
DECLARE v_seat UUID;
BEGIN
  SELECT d.licence_seat_id INTO v_seat FROM device_session d WHERE d.id = p_device_session AND d.ended_at IS NULL;
  IF v_seat IS NULL THEN RETURN FALSE; END IF;
  UPDATE device_session SET ended_at = now(), updated_at = now(), session_end_reason_id = (SELECT id FROM session_end_reason WHERE code = p_reason)
   WHERE id = p_device_session AND ended_at IS NULL;
  UPDATE identity_session SET revoked_at = now(), updated_at = now(), session_revoke_reason_id = (SELECT id FROM session_revoke_reason WHERE code = 'logout')
   WHERE device_session_id = p_device_session AND revoked_at IS NULL;
  RETURN TRUE;
END $$;
-- @@
-- BR-LIC-08(c): the 1-minute job: release expired / idle sessions. Returns the number of released sessions.
CREATE OR REPLACE FUNCTION fn_device_session_expire_sweep() RETURNS INT8 LANGUAGE PLpgSQL AS $$
DECLARE v_n INT8;
BEGIN
  WITH ended AS (
    UPDATE device_session d SET ended_at = GREATEST(d.started_at, LEAST(now(), d.expires_at)), updated_at = now(),
           session_end_reason_id = (SELECT id FROM session_end_reason WHERE code = CASE WHEN d.expires_at <= now() THEN 'absolute_expiry' ELSE 'idle_timeout' END)
     WHERE d.ended_at IS NULL AND (d.expires_at <= now() OR d.last_activity_at < now() - (
        SELECT l.session_idle_timeout_minutes * INTERVAL '1 minute' FROM licence_seat s JOIN licence l ON l.id = s.licence_id WHERE s.id = d.licence_seat_id))
     RETURNING d.id)
  SELECT count(*) INTO v_n FROM ended;
  RETURN v_n;
END $$;
-- @@
-- auth_svc job (and used by tests): revoke identity_session rows whose device_session ended (seat removed, licence expired, swept).
CREATE OR REPLACE FUNCTION fn_identity_session_sync_ended() RETURNS INT8 LANGUAGE PLpgSQL AS $$
DECLARE v_n INT8;
BEGIN
  WITH r AS (
    UPDATE identity_session SET revoked_at = now(), updated_at = now(),
           session_revoke_reason_id = (SELECT id FROM session_revoke_reason WHERE code = 'admin_revoked')
     WHERE revoked_at IS NULL AND device_session_id IN (SELECT d.id FROM device_session d WHERE d.ended_at IS NOT NULL)
     RETURNING id)
  SELECT count(*) INTO v_n FROM r;
  RETURN v_n;
END $$;
-- @@
-- BR-LIC-15: licence expiry job: active licences past valid_to become expired and their live sessions end (licence_inactive).
-- Returns the number of licences moved.
CREATE OR REPLACE FUNCTION fn_licence_expire_sweep(p_actor UUID DEFAULT '00000000-0000-0000-0000-000000000001') RETURNS INT4 LANGUAGE PLpgSQL AS $$
DECLARE v_n INT4;
BEGIN
  UPDATE device_session SET ended_at = now(), updated_at = now(), session_end_reason_id = (SELECT id FROM session_end_reason WHERE code = 'licence_inactive')
   WHERE ended_at IS NULL AND licence_seat_id IN (
     SELECT s.id FROM licence_seat s JOIN licence l ON l.id = s.licence_id JOIN licence_status st ON st.id = l.licence_status_id
      WHERE st.code = 'active' AND l.valid_to IS NOT NULL AND l.valid_to <= now() AND l.deleted_at IS NULL);
  WITH moved AS (
    UPDATE licence SET licence_status_id = (SELECT id FROM licence_status WHERE code = 'expired'), updated_at = now(), updated_by = p_actor
     WHERE licence_status_id = (SELECT id FROM licence_status WHERE code = 'active') AND valid_to IS NOT NULL AND valid_to <= now() AND deleted_at IS NULL
     RETURNING id)
  SELECT count(*)::INT4 INTO v_n FROM moved;
  RETURN v_n;
END $$;
-- @@
-- BR-LIC-09: revoke a device (auditable): device.revoked_at, end live sessions, device_revocation + session_revocation per session,
-- audit_event, all in one txn. Returns the number of sessions ended, or -1 when the device is unknown / already revoked.
CREATE OR REPLACE FUNCTION fn_device_revoke(p_device UUID, p_actor UUID, p_source STRING, p_reason STRING, p_note STRING DEFAULT NULL,
  p_request UUID DEFAULT gen_random_uuid())
RETURNS INT4 LANGUAGE PLpgSQL AS $$
DECLARE v_user UUID; v_audit UUID; v_rev UUID := gen_random_uuid(); v_n INT4; v_end STRING; v_src UUID; v_rsn UUID;
BEGIN
  SELECT d.app_user_id INTO v_user FROM device d WHERE d.id = p_device AND d.revoked_at IS NULL FOR UPDATE;
  IF v_user IS NULL THEN RETURN -1; END IF;
  v_end := CASE WHEN p_source = 'self_service' THEN 'revoked_by_user' WHEN p_source = 'system' THEN 'revoked_by_system' ELSE 'revoked_by_admin' END;
  v_src := (SELECT id FROM revocation_source WHERE code = p_source);
  v_rsn := (SELECT id FROM revocation_reason WHERE code = p_reason);
  SELECT count(*)::INT4 INTO v_n FROM device_session d WHERE d.device_id = p_device AND d.ended_at IS NULL;
  v_audit := fn_lic_audit(p_actor, 'device_revoke', 'device', p_device, 'success', p_request, p_reason);
  UPDATE device SET revoked_at = now(), updated_at = now(), updated_by = p_actor WHERE id = p_device;
  INSERT INTO device_revocation (id, created_by, device_id, app_user_id, revoked_by_app_user_id, revocation_source_id, revocation_reason_id,
                                 sessions_ended, audit_event_id, note)
    VALUES (v_rev, p_actor, p_device, v_user, p_actor, v_src, v_rsn, v_n, v_audit, p_note);
  INSERT INTO session_revocation (created_by, device_session_id, device_revocation_id, app_user_id, revoked_by_app_user_id,
                                  revocation_source_id, revocation_reason_id, audit_event_id)
    SELECT p_actor, d.id, v_rev, v_user, p_actor, v_src, v_rsn, v_audit FROM device_session d WHERE d.device_id = p_device AND d.ended_at IS NULL;
  UPDATE identity_session SET revoked_at = now(), updated_at = now(), session_revoke_reason_id = (SELECT id FROM session_revoke_reason WHERE code = 'admin_revoked')
   WHERE revoked_at IS NULL AND device_session_id IN (SELECT d.id FROM device_session d WHERE d.device_id = p_device AND d.ended_at IS NULL);
  UPDATE device_session SET ended_at = now(), updated_at = now(), session_end_reason_id = (SELECT id FROM session_end_reason WHERE code = v_end)
   WHERE device_id = p_device AND ended_at IS NULL;
  RETURN v_n;
END $$;
-- @@
-- BR-ADM-16: refresh-token rotation with reuse detection. o_outcome: rotated | reuse_detected | unknown | expired | session_ended.
-- reuse_detected revokes the whole token family (and ends the family's device sessions) and writes security_event; it is a
-- normal return so the revocation commits.
CREATE OR REPLACE FUNCTION fn_refresh_rotate(p_old_hash STRING, p_new_hash STRING, p_ip INET DEFAULT '127.0.0.1', p_user_agent STRING DEFAULT 'test-agent')
RETURNS TABLE (o_outcome STRING, o_new_session_id UUID, o_app_user_id UUID, o_family UUID) LANGUAGE PLpgSQL AS $$
DECLARE
  v_id UUID; v_user UUID; v_family UUID; v_revoked TIMESTAMPTZ; v_exp TIMESTAMPTZ; v_abs TIMESTAMPTZ; v_issued TIMESTAMPTZ;
  v_mfa BOOL; v_org UUID; v_dsess UUID; v_new UUID := gen_random_uuid(); v_n INT8;
BEGIN
  SELECT s.id, s.app_user_id, s.token_family_ref, s.revoked_at, s.expires_at, s.absolute_expires_at, s.issued_at, s.mfa_satisfied,
         s.acting_organisation_id, s.device_session_id
    INTO v_id, v_user, v_family, v_revoked, v_exp, v_abs, v_issued, v_mfa, v_org, v_dsess
    FROM identity_session s WHERE s.refresh_token_hash = p_old_hash FOR UPDATE;
  IF v_id IS NULL THEN
    RETURN QUERY SELECT 'unknown'::STRING, NULL::UUID, NULL::UUID, NULL::UUID; RETURN;
  END IF;
  IF v_revoked IS NOT NULL THEN
    WITH r AS (
      UPDATE identity_session SET revoked_at = now(), updated_at = now(),
             session_revoke_reason_id = (SELECT id FROM session_revoke_reason WHERE code = 'reuse_detected')
       WHERE token_family_ref = v_family AND revoked_at IS NULL RETURNING id)
    SELECT count(*) INTO v_n FROM r;
    UPDATE device_session SET ended_at = now(), updated_at = now(), session_end_reason_id = (SELECT id FROM session_end_reason WHERE code = 'revoked_by_system')
     WHERE ended_at IS NULL AND identity_session_id IN (SELECT i.id FROM identity_session i WHERE i.token_family_ref = v_family);
    INSERT INTO security_event (created_by, security_event_type_id, event_severity_id, app_user_id, ip_address, details)
      VALUES (v_user, (SELECT id FROM security_event_type WHERE code = 'refresh_token_reuse'),
              (SELECT id FROM event_severity WHERE code = 'critical'), v_user, p_ip,
              jsonb_build_object('token_family_ref', v_family, 'reused_session_id', v_id, 'family_sessions_revoked', v_n));
    RETURN QUERY SELECT 'reuse_detected'::STRING, NULL::UUID, v_user, v_family; RETURN;
  END IF;
  IF v_dsess IS NOT NULL AND (SELECT d.ended_at FROM device_session d WHERE d.id = v_dsess) IS NOT NULL THEN
    UPDATE identity_session SET revoked_at = now(), updated_at = now(), session_revoke_reason_id = (SELECT id FROM session_revoke_reason WHERE code = 'admin_revoked') WHERE id = v_id;
    RETURN QUERY SELECT 'session_ended'::STRING, NULL::UUID, v_user, v_family; RETURN;
  END IF;
  IF v_exp <= now() OR v_abs <= now() THEN
    RETURN QUERY SELECT 'expired'::STRING, NULL::UUID, v_user, v_family; RETURN;
  END IF;
  UPDATE identity_session SET revoked_at = now(), last_used_at = now(), updated_at = now(),
         session_revoke_reason_id = (SELECT id FROM session_revoke_reason WHERE code = 'rotated') WHERE id = v_id;
  INSERT INTO identity_session (id, created_by, updated_by, app_user_id, refresh_token_hash, token_family_ref, parent_identity_session_id, auth_method_id,
                                mfa_satisfied, acting_organisation_id, device_session_id, issued_at, expires_at, absolute_expires_at, ip_address, user_agent)
    VALUES (v_new, v_user, v_user, v_user, p_new_hash, v_family, v_id, (SELECT id FROM auth_method WHERE code = 'refresh'), v_mfa, v_org, v_dsess,
            now(), LEAST(now() + (v_exp - v_issued), v_abs), v_abs, p_ip, p_user_agent);
  UPDATE device_session SET identity_session_id = v_new, last_activity_at = now(), updated_at = now()
   WHERE identity_session_id = v_id AND ended_at IS NULL;
  RETURN QUERY SELECT 'rotated'::STRING, v_new, v_user, v_family;
END $$;
-- @@
-- BR-ORG-04 (BR-ORG-01/02 backstops): close + reopen a recruiter mapping atomically. Lock = the organisation row (P4). If a mapping is
-- open it is closed (effective_to = p_at, end reason) and a successor with sequence_no + 1 is inserted; if the pair has only closed history
-- the new row chains onto the latest closed row; otherwise sequence_no = 1. Returns the id of the open mapping.
CREATE OR REPLACE FUNCTION fn_recruiter_mapping_change(p_recruiter UUID, p_org UUID, p_scope STRING, p_end_reason STRING, p_actor UUID,
  p_at TIMESTAMPTZ DEFAULT NULL) RETURNS UUID LANGUAGE PLpgSQL AS $$
DECLARE v_lock UUID; v_prev UUID; v_seq INT4; v_from TIMESTAMPTZ; v_to TIMESTAMPTZ; v_id UUID := gen_random_uuid(); v_at TIMESTAMPTZ;
BEGIN
  SELECT o.id INTO v_lock FROM organisation o WHERE o.id = p_org FOR UPDATE;
  IF v_lock IS NULL THEN RAISE EXCEPTION 'organisation % not found', p_org USING ERRCODE = '02000'; END IF;
  SELECT m.id, m.sequence_no, m.effective_from, m.effective_to INTO v_prev, v_seq, v_from, v_to FROM recruiter_organisation_mapping m
   WHERE m.recruiter_app_user_id = p_recruiter AND m.organisation_id = p_org ORDER BY m.sequence_no DESC LIMIT 1;
  v_at := GREATEST(COALESCE(p_at, clock_timestamp()), COALESCE(v_to, v_from + INTERVAL '1 microsecond', '-infinity'::TIMESTAMPTZ));
  IF v_prev IS NOT NULL AND v_to IS NULL THEN
    UPDATE recruiter_organisation_mapping SET effective_to = v_at, mapping_end_reason_id = (SELECT id FROM mapping_end_reason WHERE code = p_end_reason),
           updated_at = now(), updated_by = p_actor WHERE id = v_prev;
  END IF;
  INSERT INTO recruiter_organisation_mapping (id, created_by, updated_by, recruiter_app_user_id, organisation_id, mapping_scope_id, sequence_no,
        prior_recruiter_organisation_mapping_id, prior_ended_at, effective_from, approved_by_app_user_id)
    VALUES (v_id, p_actor, p_actor, p_recruiter, p_org, (SELECT id FROM mapping_scope WHERE code = p_scope),
            COALESCE(v_seq, 0) + 1, v_prev, CASE WHEN v_prev IS NULL THEN NULL ELSE v_at END, v_at, p_actor);
  RETURN v_id;
END $$;
-- @@
-- BR-ORG-04: close the open mapping without reopening. Returns false when none is open.
CREATE OR REPLACE FUNCTION fn_recruiter_mapping_close(p_recruiter UUID, p_org UUID, p_end_reason STRING, p_actor UUID)
RETURNS BOOL LANGUAGE PLpgSQL AS $$
DECLARE v_lock UUID; v_n INT8;
BEGIN
  SELECT o.id INTO v_lock FROM organisation o WHERE o.id = p_org FOR UPDATE;
  WITH c AS (
    UPDATE recruiter_organisation_mapping SET effective_to = GREATEST(clock_timestamp(), effective_from + INTERVAL '1 microsecond'),
           mapping_end_reason_id = (SELECT id FROM mapping_end_reason WHERE code = p_end_reason), updated_at = now(), updated_by = p_actor
     WHERE recruiter_app_user_id = p_recruiter AND organisation_id = p_org AND effective_to IS NULL RETURNING id)
  SELECT count(*) INTO v_n FROM c;
  RETURN v_n > 0;
END $$;
-- @@
-- BR-ORG-06 (and BR-ORG-07): add an account administrator; account.active_admin_count is maintained under SELECT account FOR UPDATE.
CREATE OR REPLACE FUNCTION fn_account_admin_add(p_account UUID, p_user UUID, p_level STRING, p_primary BOOL, p_actor UUID)
RETURNS UUID LANGUAGE PLpgSQL AS $$
DECLARE v_id UUID := gen_random_uuid(); v_lock UUID;
BEGIN
  SELECT a.id INTO v_lock FROM account a WHERE a.id = p_account FOR UPDATE;
  IF v_lock IS NULL THEN RAISE EXCEPTION 'account % not found', p_account USING ERRCODE = '02000'; END IF;
  INSERT INTO account_admin (id, created_by, updated_by, account_id, admin_app_user_id, account_admin_level_id, is_primary_contact)
    VALUES (v_id, p_actor, p_actor, p_account, p_user, (SELECT id FROM account_admin_level WHERE code = p_level), p_primary);
  UPDATE account SET active_admin_count = (SELECT count(*) FROM account_admin x WHERE x.account_id = p_account AND x.effective_to IS NULL)::INT4,
         updated_at = now(), updated_by = p_actor WHERE id = p_account;
  RETURN v_id;
END $$;
-- @@
-- BR-ORG-06: close an administrator grant; rejected (23514 ck_account_operational_needs_admin) when it would leave an activated account without admin.
CREATE OR REPLACE FUNCTION fn_account_admin_close(p_account UUID, p_user UUID, p_reason STRING, p_actor UUID)
RETURNS BOOL LANGUAGE PLpgSQL AS $$
DECLARE v_lock UUID; v_n INT8;
BEGIN
  SELECT a.id INTO v_lock FROM account a WHERE a.id = p_account FOR UPDATE;
  WITH c AS (
    UPDATE account_admin SET effective_to = GREATEST(clock_timestamp(), effective_from), revocation_reason = p_reason, is_primary_contact = false,
           updated_at = now(), updated_by = p_actor
     WHERE account_id = p_account AND admin_app_user_id = p_user AND effective_to IS NULL RETURNING id)
  SELECT count(*) INTO v_n FROM c;
  IF v_n = 0 THEN RETURN FALSE; END IF;
  UPDATE account SET active_admin_count = (SELECT count(*) FROM account_admin x WHERE x.account_id = p_account AND x.effective_to IS NULL)::INT4,
         updated_at = now(), updated_by = p_actor WHERE id = p_account;
  RETURN TRUE;
END $$;
-- @@
-- BR-ORG-08: move an organisation to another account: close the open account_organisation row and insert the new one in one txn,
-- serialised on the organisation row (P4).
CREATE OR REPLACE FUNCTION fn_organisation_account_move(p_org UUID, p_account UUID, p_billing_owner BOOL, p_actor UUID)
RETURNS UUID LANGUAGE PLpgSQL AS $$
DECLARE v_id UUID := gen_random_uuid(); v_lock UUID;
BEGIN
  SELECT o.id INTO v_lock FROM organisation o WHERE o.id = p_org FOR UPDATE;
  IF v_lock IS NULL THEN RAISE EXCEPTION 'organisation % not found', p_org USING ERRCODE = '02000'; END IF;
  UPDATE account_organisation SET effective_to = GREATEST(clock_timestamp(), effective_from), end_note = 'moved', updated_at = now(), updated_by = p_actor
   WHERE organisation_id = p_org AND effective_to IS NULL;
  INSERT INTO account_organisation (id, created_by, updated_by, account_id, organisation_id, is_billing_owner, effective_from)
    VALUES (v_id, p_actor, p_actor, p_account, p_org, p_billing_owner, clock_timestamp() + INTERVAL '1 microsecond');
  RETURN v_id;
END $$;
-- @@
-- BR-ADM-07: add a role-inheritance edge; self edge / duplicate are table constraints, longer cycles are rejected here under the
-- caller's SERIALIZABLE txn (23514 'cycle').
CREATE OR REPLACE FUNCTION fn_role_inheritance_add(p_child UUID, p_parent UUID, p_actor UUID) RETURNS UUID LANGUAGE PLpgSQL AS $$
DECLARE v_id UUID := gen_random_uuid(); v_cycle BOOL;
BEGIN
  v_cycle := p_child <> p_parent AND EXISTS (
      WITH RECURSIVE up(r) AS (SELECT p_parent UNION SELECT i.parent_app_role_id FROM app_role_inheritance i JOIN up ON i.child_app_role_id = up.r)
      SELECT 1 FROM up WHERE r = p_child);
  IF v_cycle THEN
    RAISE EXCEPTION 'role inheritance cycle: % -> % would close a cycle', p_child, p_parent USING ERRCODE = '23514';
  END IF;
  INSERT INTO app_role_inheritance (id, created_by, updated_by, child_app_role_id, parent_app_role_id) VALUES (v_id, p_actor, p_actor, p_child, p_parent);
  RETURN v_id;
END $$;
-- @@
-- BR-LGS-03: open the next legacy sync run. Locks the legacy_source_system row, reads the watermark of the latest completed run for the
-- same source and entity kind, and inserts the new run with watermark_from_ref = that watermark. Rejects (55006) when a run is open.
CREATE OR REPLACE FUNCTION fn_legacy_sync_open_run(p_source UUID, p_kind UUID, p_run_key STRING, p_conflict_policy UUID, p_actor UUID)
RETURNS UUID LANGUAGE PLpgSQL AS $$
DECLARE v_wm STRING; v_id UUID := gen_random_uuid(); v_lock UUID; v_open BOOL;
BEGIN
  SELECT s.id INTO v_lock FROM legacy_source_system s WHERE s.id = p_source FOR UPDATE;
  IF v_lock IS NULL THEN RAISE EXCEPTION 'legacy_source_system % not found', p_source USING ERRCODE = '02000'; END IF;
  v_open := EXISTS (SELECT 1 FROM legacy_sync_run r WHERE r.legacy_source_system_id = p_source AND r.legacy_entity_kind_id = p_kind AND r.is_active_run);
  IF v_open THEN
    RAISE EXCEPTION 'a sync run is already open for this source and entity kind' USING ERRCODE = '55006';
  END IF;
  SELECT r.watermark_to_ref INTO v_wm FROM legacy_sync_run r JOIN import_status i ON i.id = r.import_status_id
   WHERE r.legacy_source_system_id = p_source AND r.legacy_entity_kind_id = p_kind AND i.code = 'completed' AND NOT r.is_dry_run
   ORDER BY r.finished_at DESC LIMIT 1;
  INSERT INTO legacy_sync_run (id, created_by, updated_by, legacy_source_system_id, legacy_entity_kind_id, import_status_id, sync_conflict_policy_id, run_key, watermark_from_ref)
    VALUES (v_id, p_actor, p_actor, p_source, p_kind, (SELECT id FROM import_status WHERE code = 'running'), p_conflict_policy, p_run_key, v_wm);
  RETURN v_id;
END $$;

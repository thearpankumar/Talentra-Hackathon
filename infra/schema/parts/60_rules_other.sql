-- 60_rules_other.sql : procedural (TXN_LOCK) business rules for the modules
--   gig_marketplace, jobs_applications, profile, ext_network, notifications_community, sourcing_consultant,
--   ext_talent_ops, ext_learning, ext_matching, analytics.
-- Conventions
--   * every function is SECURITY INVOKER (runs with the caller's table privileges; callers are app_service / etl_service),
--   * written for SERIALIZABLE: SELECT ... FOR UPDATE on the parent/limit row, check, write.  Callers MUST retry the whole
--     transaction on SQLSTATE 40001 (tests use conftest.retry()),
--   * business rejections use stable SQLSTATEs:
--       23514 check_violation        capacity / limit / headcount exceeded (message starts with the BR id)
--       23505 unique_violation       overlap or duplicate caught by the declarative twin (raised by the table, not re-raised here)
--       55000 object_not_in_prerequisite_state   state precondition (closed, locked, not open, window over, ...)
--       55006 object_in_use          optimistic-lock failure: caller read a stale status_sequence_no (re-read, do not blind-retry)
--       42501 insufficient_privilege blocked / blacklisted party
--   * audit columns: the acting app_user id is passed as p_actor.
-- Statements are separated by the marker line below.

-- ===================================================================================================================
-- BR-GIG-12  accepted applications never exceed headcount (lock gig_request, bump accepted_count)
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_gig_accept_application(p_application_id UUID, p_actor UUID) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE
  v_request UUID; v_headcount INT4; v_accepted INT4; v_is_acc BOOL; v_status_acc UUID;
BEGIN
  SELECT gig_request_id, is_accepted INTO v_request, v_is_acc FROM gig_application WHERE id = p_application_id AND deleted_at IS NULL;
  IF v_request IS NULL THEN
    RAISE EXCEPTION 'BR-GIG-12: gig_application % not found', p_application_id USING ERRCODE = '55000';
  END IF;
  SELECT headcount, accepted_count INTO v_headcount, v_accepted FROM gig_request WHERE id = v_request FOR UPDATE;
  SELECT is_accepted INTO v_is_acc FROM gig_application WHERE id = p_application_id FOR UPDATE;
  IF v_is_acc THEN
    RETURN p_application_id;  -- idempotent
  END IF;
  IF v_accepted >= v_headcount THEN
    RAISE EXCEPTION 'BR-GIG-12: gig_request % is full (% of % accepted)', v_request, v_accepted, v_headcount USING ERRCODE = '23514';
  END IF;
  SELECT id INTO v_status_acc FROM gig_application_status WHERE is_accepted AND is_active ORDER BY sort_order LIMIT 1;
  UPDATE gig_application SET is_accepted = true, gig_application_status_id = v_status_acc, decided_at = now(),
         decided_by_app_user_id = p_actor, updated_at = now(), updated_by = p_actor WHERE id = p_application_id;
  UPDATE gig_request SET accepted_count = accepted_count + 1, updated_at = now(), updated_by = p_actor,
         gig_request_status_id = CASE WHEN accepted_count + 1 >= headcount
                                      THEN (SELECT id FROM gig_request_status WHERE code = 'filled') ELSE gig_request_status_id END
   WHERE id = v_request;
  RETURN p_application_id;
END $$;
-- @@
-- ===================================================================================================================
-- BR-GIG-01 / BR-GIG-10  assignment + slot allocation in one txn; overlap is rejected by UNIQUE(gig_worker_profile_id, slot_start_at)
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_gig_create_assignment(
  p_application_id UUID, p_actor UUID,
  p_window_start TIMESTAMPTZ DEFAULT NULL, p_window_end TIMESTAMPTZ DEFAULT NULL,
  p_agreed_amount DECIMAL DEFAULT NULL, p_idempotency_key TEXT DEFAULT NULL) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE
  v_req UUID; v_worker UUID; v_org UUID; v_is_acc BOOL; v_proposed DECIMAL;
  v_rs TIMESTAMPTZ; v_re TIMESTAMPTZ; v_cur UUID; v_fixed DECIMAL; v_rate DECIMAL; v_unit UUID; v_rb BOOL;
  v_id UUID := gen_random_uuid(); v_start TIMESTAMPTZ; v_end TIMESTAMPTZ; v_slots INT4;
BEGIN
  SELECT gig_request_id, gig_worker_profile_id, is_accepted, proposed_amount
    INTO v_req, v_worker, v_is_acc, v_proposed FROM gig_application WHERE id = p_application_id AND deleted_at IS NULL FOR UPDATE;
  IF v_req IS NULL THEN
    RAISE EXCEPTION 'BR-GIG-10: gig_application % not found', p_application_id USING ERRCODE = '55000';
  END IF;
  IF NOT v_is_acc THEN
    RAISE EXCEPTION 'BR-GIG-10: gig_application % is not accepted', p_application_id USING ERRCODE = '55000';
  END IF;
  SELECT organisation_id, window_start_at, window_end_at, currency_id, fixed_price, rate_amount, gig_rate_unit_id, is_rate_based
    INTO v_org, v_rs, v_re, v_cur, v_fixed, v_rate, v_unit, v_rb FROM gig_request WHERE id = v_req;
  v_start := COALESCE(p_window_start, v_rs);
  v_end := COALESCE(p_window_end, v_re);
  IF v_start < v_rs OR v_end > v_re OR v_end <= v_start THEN
    RAISE EXCEPTION 'BR-GIG-01: window outside the gig_request window' USING ERRCODE = '23514';
  END IF;
  v_slots := (extract(epoch FROM (v_end - v_start)) / 900)::INT4;
  INSERT INTO gig_assignment (id, created_by, updated_by, gig_application_id, gig_request_id, gig_worker_profile_id, organisation_id,
                              application_is_accepted, gig_assignment_status_id, is_completed, window_start_at, window_end_at,
                              expected_slot_count, agreed_amount, gig_rate_unit_id, currency_id, handoff_idempotency_key, gig_reconcile_state_id)
  VALUES (v_id, p_actor, p_actor, p_application_id, v_req, v_worker, v_org, true,
          (SELECT id FROM gig_assignment_status WHERE code = 'scheduled'), false, v_start, v_end, v_slots,
          COALESCE(p_agreed_amount, v_proposed, v_fixed, v_rate), CASE WHEN v_rb THEN v_unit END, v_cur,
          COALESCE(p_idempotency_key, 'asg-' || v_id::STRING), (SELECT id FROM gig_reconcile_state WHERE code = 'pending'));
  -- explode the window into 15-minute slots; the UNIQUE key rejects any overlap with another assignment or time off (23505)
  INSERT INTO gig_slot (created_by, gig_worker_profile_id, gig_assignment_id, slot_start_at)
  SELECT p_actor, v_worker, v_id, s FROM generate_series(v_start, v_end - INTERVAL '15 minutes', INTERVAL '15 minutes') AS s;
  RETURN v_id;
END $$;
-- @@
-- ===================================================================================================================
-- BR-GIG-03  time off exploded to slots (same UNIQUE catches clashes with assignments); cancel releases them
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_gig_time_off_add(p_worker UUID, p_type UUID, p_start TIMESTAMPTZ, p_end TIMESTAMPTZ,
                                              p_actor UUID, p_reason TEXT DEFAULT NULL) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE v_id UUID := gen_random_uuid();
BEGIN
  INSERT INTO gig_time_off (id, created_by, updated_by, gig_worker_profile_id, gig_time_off_type_id, time_off_start_at, time_off_end_at, reason)
  VALUES (v_id, p_actor, p_actor, p_worker, p_type, p_start, p_end, p_reason);
  INSERT INTO gig_slot (created_by, gig_worker_profile_id, gig_time_off_id, slot_start_at)
  SELECT p_actor, p_worker, v_id, s FROM generate_series(p_start, p_end - INTERVAL '15 minutes', INTERVAL '15 minutes') AS s;
  RETURN v_id;
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_gig_time_off_cancel(p_time_off_id UUID, p_actor UUID) RETURNS INT4
LANGUAGE plpgsql AS $$
DECLARE v_n INT4; v_x UUID;
BEGIN
  SELECT id INTO v_x FROM gig_time_off WHERE id = p_time_off_id AND deleted_at IS NULL FOR UPDATE;
  IF v_x IS NULL THEN
    RAISE EXCEPTION 'BR-GIG-03: gig_time_off % not found or already cancelled', p_time_off_id USING ERRCODE = '55000';
  END IF;
  WITH d AS (DELETE FROM gig_slot WHERE gig_time_off_id = p_time_off_id RETURNING 1) SELECT count(*)::INT4 INTO v_n FROM d;
  UPDATE gig_time_off SET deleted_at = now(), deleted_by = p_actor, updated_at = now(), updated_by = p_actor WHERE id = p_time_off_id;
  RETURN v_n;
END $$;
-- @@
-- ===================================================================================================================
-- BR-GIG-05  cancellation releases slots (cancellation row + slot delete + status in one txn)
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_gig_cancel_assignment(p_assignment_id UUID, p_party UUID, p_reason UUID, p_actor UUID,
                                                   p_comment TEXT DEFAULT NULL) RETURNS INT4
LANGUAGE plpgsql AS $$
DECLARE v_completed BOOL; v_status UUID; v_start TIMESTAMPTZ; v_n INT4; v_cancelled UUID;
BEGIN
  SELECT is_completed, gig_assignment_status_id, window_start_at INTO v_completed, v_status, v_start
    FROM gig_assignment WHERE id = p_assignment_id AND deleted_at IS NULL FOR UPDATE;
  IF v_status IS NULL THEN
    RAISE EXCEPTION 'BR-GIG-05: gig_assignment % not found', p_assignment_id USING ERRCODE = '55000';
  END IF;
  SELECT id INTO v_cancelled FROM gig_assignment_status WHERE code = 'cancelled';
  IF v_completed OR v_status = v_cancelled THEN
    RAISE EXCEPTION 'BR-GIG-05: gig_assignment % is already completed or cancelled', p_assignment_id USING ERRCODE = '55000';
  END IF;
  WITH d AS (DELETE FROM gig_slot WHERE gig_assignment_id = p_assignment_id RETURNING 1) SELECT count(*)::INT4 INTO v_n FROM d;
  INSERT INTO gig_cancellation (created_by, updated_by, gig_assignment_id, cancelled_by_gig_actor_party_id, gig_cancellation_reason_id,
                                cancelled_at, notice_hours, slots_released_count, slots_released_at, cancellation_comment)
  VALUES (p_actor, p_actor, p_assignment_id, p_party, p_reason, now(),
          floor(extract(epoch FROM (v_start - now())) / 3600)::INT4, v_n, now(), p_comment);
  UPDATE gig_assignment SET gig_assignment_status_id = v_cancelled, updated_at = now(), updated_by = p_actor WHERE id = p_assignment_id;
  RETURN v_n;
END $$;
-- @@
-- ===================================================================================================================
-- BR-GIG-13  blacklist guard + apply / invite
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_gig_assert_not_blacklisted(p_organisation_id UUID, p_worker UUID) RETURNS BOOL
LANGUAGE plpgsql AS $$
DECLARE v_x UUID;
BEGIN
  SELECT l.id INTO v_x FROM gig_requester_worker_list l JOIN gig_worker_list_type t ON t.id = l.gig_worker_list_type_id
   WHERE l.organisation_id = p_organisation_id AND l.gig_worker_profile_id = p_worker AND l.deleted_at IS NULL AND t.code = 'blacklist' LIMIT 1;
  IF v_x IS NOT NULL THEN
    RAISE EXCEPTION 'BR-GIG-13: worker % is blacklisted by organisation %', p_worker, p_organisation_id USING ERRCODE = '42501';
  END IF;
  RETURN TRUE;
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_gig_apply(p_request UUID, p_worker UUID, p_actor UUID, p_proposed DECIMAL DEFAULT NULL,
                                       p_cover TEXT DEFAULT NULL) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE v_org UUID; v_cur UUID; v_deadline TIMESTAMPTZ; v_status_open BOOL; v_id UUID := gen_random_uuid(); v_ok BOOL;
BEGIN
  SELECT organisation_id, currency_id, application_deadline_at, (gig_request_status_id = (SELECT id FROM gig_request_status WHERE code = 'open'))
    INTO v_org, v_cur, v_deadline, v_status_open FROM gig_request WHERE id = p_request AND deleted_at IS NULL FOR SHARE;
  IF v_org IS NULL OR NOT v_status_open THEN
    RAISE EXCEPTION 'BR-GIG-13: gig_request % is not open for applications', p_request USING ERRCODE = '55000';
  END IF;
  IF v_deadline IS NOT NULL AND v_deadline < now() THEN
    RAISE EXCEPTION 'BR-GIG-13: application deadline passed' USING ERRCODE = '55000';
  END IF;
  v_ok := fn_gig_assert_not_blacklisted(v_org, p_worker);
  INSERT INTO gig_application (id, created_by, updated_by, gig_request_id, gig_worker_profile_id, gig_application_status_id, is_accepted,
                               cover_note, proposed_amount, currency_id)
  VALUES (v_id, p_actor, p_actor, p_request, p_worker, (SELECT id FROM gig_application_status WHERE code = 'submitted'), false,
          p_cover, p_proposed, v_cur);
  RETURN v_id;
END $$;
-- @@
-- ===================================================================================================================
-- BR-JOB-11 / BR-JOB-12  application creation writes history row 1; transitions are optimistic on status_sequence_no
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_job_application_create(p_candidate UUID, p_requirement UUID, p_source UUID, p_actor UUID,
                                                    p_actor_kind TEXT DEFAULT 'candidate', p_message TEXT DEFAULT NULL) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE v_org UUID; v_live BOOL; v_id UUID := gen_random_uuid(); v_applied UUID; v_kind UUID;
BEGIN
  SELECT organisation_id, is_live INTO v_org, v_live FROM job_requirement WHERE id = p_requirement AND deleted_at IS NULL FOR SHARE;
  IF v_org IS NULL OR NOT v_live THEN
    RAISE EXCEPTION 'BR-JOB-12: job_requirement % is not live', p_requirement USING ERRCODE = '55000';
  END IF;
  SELECT id INTO v_applied FROM application_status WHERE code = 'applied';
  SELECT id INTO v_kind FROM actor_kind WHERE code = p_actor_kind;
  INSERT INTO job_application (id, created_by, updated_by, candidate_profile_id, organisation_id, job_requirement_id, job_source_id,
                               application_status_id, status_sequence_no, candidate_message, submitted_by_app_user_id)
  VALUES (v_id, p_actor, p_actor, p_candidate, v_org, p_requirement, p_source, v_applied, 1, p_message, p_actor);
  INSERT INTO job_application_status_history (created_by, job_application_id, job_requirement_id, sequence_no, prior_sequence_no,
                                              from_application_status_id, to_application_status_id, actor_kind_id)
  VALUES (p_actor, v_id, p_requirement, 1, NULL, NULL, v_applied, v_kind);
  RETURN v_id;
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_job_application_transition(
  p_application_id UUID, p_to_status UUID, p_expected_seq INT4, p_actor UUID,
  p_actor_kind TEXT DEFAULT 'employer', p_rejection_reason UUID DEFAULT NULL, p_comment TEXT DEFAULT NULL,
  p_idempotency_key TEXT DEFAULT NULL) RETURNS INT4
LANGUAGE plpgsql AS $$
DECLARE v_seq INT4; v_cur UUID; v_req UUID; v_kind UUID; v_new INT4; v_is_withdrawn BOOL;
BEGIN
  SELECT status_sequence_no, application_status_id, job_requirement_id INTO v_seq, v_cur, v_req
    FROM job_application WHERE id = p_application_id AND deleted_at IS NULL FOR UPDATE;
  IF v_seq IS NULL THEN
    RAISE EXCEPTION 'BR-JOB-12: job_application % not found', p_application_id USING ERRCODE = '55000';
  END IF;
  IF v_seq <> p_expected_seq THEN
    RAISE EXCEPTION 'BR-JOB-12: stale status_sequence_no (expected %, found %)', p_expected_seq, v_seq USING ERRCODE = '55006';
  END IF;
  SELECT id INTO v_kind FROM actor_kind WHERE code = p_actor_kind;
  v_new := v_seq + 1;
  -- the composite FK (from,to) -> application_status_transition rejects an illegal move (23503); the chain FK rejects forks
  INSERT INTO job_application_status_history (created_by, job_application_id, job_requirement_id, sequence_no, prior_sequence_no,
                                              from_application_status_id, to_application_status_id, actor_kind_id, rejection_reason_id,
                                              comment_text, idempotency_key)
  VALUES (p_actor, p_application_id, v_req, v_new, v_seq, v_cur, p_to_status, v_kind, p_rejection_reason, p_comment, p_idempotency_key);
  SELECT code = 'withdrawn' INTO v_is_withdrawn FROM application_status WHERE id = p_to_status;
  UPDATE job_application SET application_status_id = p_to_status, status_sequence_no = v_new, status_changed_at = now(),
         withdrawn_at = CASE WHEN v_is_withdrawn THEN now() ELSE withdrawn_at END, updated_at = now(), updated_by = p_actor
   WHERE id = p_application_id AND status_sequence_no = v_seq;
  RETURN v_new;
END $$;
-- @@
-- ===================================================================================================================
-- BR-JOB-25  position fill keeps filled_count consistent with openings (lock job_requirement)
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_job_position_fill(p_position_id UUID, p_application_id UUID, p_actor UUID) RETURNS INT4
LANGUAGE plpgsql AS $$
DECLARE
  v_req UUID; v_openings INT4; v_filled INT4; v_pos_filled TIMESTAMPTZ; v_app_req UUID; v_new INT4;
BEGIN
  SELECT job_requirement_id INTO v_req FROM job_position WHERE id = p_position_id AND deleted_at IS NULL;
  IF v_req IS NULL THEN
    RAISE EXCEPTION 'BR-JOB-25: job_position % not found', p_position_id USING ERRCODE = '55000';
  END IF;
  SELECT openings_count, filled_count INTO v_openings, v_filled FROM job_requirement WHERE id = v_req FOR UPDATE;
  SELECT filled_at INTO v_pos_filled FROM job_position WHERE id = p_position_id FOR UPDATE;
  SELECT job_requirement_id INTO v_app_req FROM job_application WHERE id = p_application_id;
  IF v_app_req IS DISTINCT FROM v_req THEN
    RAISE EXCEPTION 'BR-JOB-25: application % does not belong to requirement %', p_application_id, v_req USING ERRCODE = '23514';
  END IF;
  IF v_pos_filled IS NOT NULL THEN
    RAISE EXCEPTION 'BR-JOB-25: job_position % is already filled', p_position_id USING ERRCODE = '55000';
  END IF;
  IF v_filled >= v_openings THEN
    RAISE EXCEPTION 'BR-JOB-25: job_requirement % has no open position left (% of %)', v_req, v_filled, v_openings USING ERRCODE = '23514';
  END IF;
  v_new := v_filled + 1;
  UPDATE job_position SET filled_at = now(), filled_by_job_application_id = p_application_id,
         job_position_status_id = (SELECT id FROM job_position_status WHERE code = 'filled'), updated_at = now(), updated_by = p_actor
   WHERE id = p_position_id;
  UPDATE job_requirement SET filled_count = v_new, updated_at = now(), updated_by = p_actor,
         job_requirement_status_id = (SELECT id FROM job_requirement_status WHERE code = CASE WHEN v_new >= v_openings THEN 'filled' ELSE 'partially_filled' END)
   WHERE id = v_req;
  RETURN v_new;
END $$;
-- @@
-- ===================================================================================================================
-- BR-OPS-20  workforce_request filled_count <= headcount under row lock
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_workforce_request_fill(p_request_id UUID, p_actor UUID, p_count INT4 DEFAULT 1) RETURNS INT4
LANGUAGE plpgsql AS $$
DECLARE v_head INT4; v_filled INT4; v_new INT4;
BEGIN
  SELECT headcount, filled_count INTO v_head, v_filled FROM workforce_request WHERE id = p_request_id AND deleted_at IS NULL FOR UPDATE;
  IF v_head IS NULL THEN
    RAISE EXCEPTION 'BR-OPS-20: workforce_request % not found', p_request_id USING ERRCODE = '55000';
  END IF;
  v_new := v_filled + p_count;
  IF p_count < 1 OR v_new > v_head THEN
    RAISE EXCEPTION 'BR-OPS-20: filled_count % would exceed headcount %', v_new, v_head USING ERRCODE = '23514';
  END IF;
  UPDATE workforce_request SET filled_count = v_new, updated_at = now(), updated_by = p_actor,
         workforce_request_status_id = (SELECT id FROM workforce_request_status WHERE code = CASE WHEN v_new >= v_head THEN 'filled' ELSE 'partially_filled' END)
   WHERE id = p_request_id;
  RETURN v_new;
END $$;
-- @@
-- ===================================================================================================================
-- BR-OPS-24  locked timesheet cannot be edited (guard to call first thing in the edit txn; takes the row lock)
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_timesheet_assert_editable(p_timesheet_id UUID) RETURNS BOOL
LANGUAGE plpgsql AS $$
DECLARE v_locked TIMESTAMPTZ; v_found UUID;
BEGIN
  SELECT id, locked_at INTO v_found, v_locked FROM timesheet WHERE id = p_timesheet_id AND deleted_at IS NULL FOR UPDATE;
  IF v_found IS NULL THEN
    RAISE EXCEPTION 'BR-OPS-24: timesheet % not found', p_timesheet_id USING ERRCODE = '55000';
  END IF;
  IF v_locked IS NOT NULL THEN
    RAISE EXCEPTION 'BR-OPS-24: timesheet % is locked since %', p_timesheet_id, v_locked USING ERRCODE = '55000';
  END IF;
  RETURN TRUE;
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_timesheet_entry_set_hours(p_entry_id UUID, p_hours DECIMAL, p_actor UUID) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE v_ts UUID; v_ok BOOL;
BEGIN
  SELECT timesheet_id INTO v_ts FROM timesheet_entry WHERE id = p_entry_id;
  IF v_ts IS NULL THEN
    RAISE EXCEPTION 'BR-OPS-24: timesheet_entry % not found', p_entry_id USING ERRCODE = '55000';
  END IF;
  v_ok := fn_timesheet_assert_editable(v_ts);
  UPDATE timesheet_entry SET hours = p_hours, updated_at = now(), updated_by = p_actor WHERE id = p_entry_id;
  UPDATE timesheet SET total_hours = (SELECT COALESCE(sum(hours), 0) FROM timesheet_entry WHERE timesheet_id = v_ts AND deleted_at IS NULL),
         updated_at = now(), updated_by = p_actor WHERE id = v_ts;
END $$;
-- @@
-- ===================================================================================================================
-- BR-LRN-14  answers after the sitting deadline are rejected (guard + lock)
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_assessment_assert_in_time(p_session_id UUID) RETURNS BOOL
LANGUAGE plpgsql AS $$
DECLARE v_found UUID; v_deadline TIMESTAMPTZ; v_ext INT4; v_submitted TIMESTAMPTZ;
BEGIN
  SELECT id, deadline_at, time_extension_seconds, submitted_at INTO v_found, v_deadline, v_ext, v_submitted
    FROM assessment_session WHERE id = p_session_id FOR UPDATE;
  IF v_found IS NULL THEN
    RAISE EXCEPTION 'BR-LRN-14: assessment_session % not found', p_session_id USING ERRCODE = '55000';
  END IF;
  IF v_submitted IS NOT NULL THEN
    RAISE EXCEPTION 'BR-LRN-14: assessment_session % already submitted', p_session_id USING ERRCODE = '55000';
  END IF;
  IF v_deadline IS NOT NULL AND now() > v_deadline + COALESCE(v_ext, 0) * INTERVAL '1 second' THEN
    RAISE EXCEPTION 'BR-LRN-14: sitting deadline passed for assessment_session %', p_session_id USING ERRCODE = '55000';
  END IF;
  RETURN TRUE;
END $$;
-- @@
-- ===================================================================================================================
-- BR-NET-06 / BR-NET-04 / BR-NET-17  network: block guard, send, accept, RSVP
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_assert_not_blocked(p_a UUID, p_b UUID) RETURNS BOOL
LANGUAGE plpgsql AS $$
DECLARE v_x UUID;
BEGIN
  SELECT id INTO v_x FROM user_block WHERE effective_to IS NULL
     AND ((blocker_app_user_id = p_a AND blocked_app_user_id = p_b) OR (blocker_app_user_id = p_b AND blocked_app_user_id = p_a)) LIMIT 1;
  IF v_x IS NOT NULL THEN
    RAISE EXCEPTION 'BR-NET-06: users % and % are blocked from each other', p_a, p_b USING ERRCODE = '42501';
  END IF;
  RETURN TRUE;
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_connection_request_send(p_requester UUID, p_recipient UUID, p_origin UUID, p_note TEXT DEFAULT NULL) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE v_id UUID := gen_random_uuid(); v_lo UUID := LEAST(p_requester, p_recipient); v_hi UUID := GREATEST(p_requester, p_recipient); v_ok BOOL; v_x UUID;
BEGIN
  v_ok := fn_assert_not_blocked(p_requester, p_recipient);
  SELECT lo_app_user_id INTO v_x FROM connection WHERE lo_app_user_id = v_lo AND hi_app_user_id = v_hi;
  IF v_x IS NOT NULL THEN
    RAISE EXCEPTION 'BR-NET-04: users are already connected' USING ERRCODE = '55000';
  END IF;
  v_x := NULL;
  SELECT id INTO v_x FROM connection_request WHERE pair_low_key = v_lo AND pair_high_key = v_hi AND cooldown_ends_at > now() AND NOT is_pending LIMIT 1;
  IF v_x IS NOT NULL THEN
    RAISE EXCEPTION 'BR-NET-04: re-request cooldown still running' USING ERRCODE = '55000';
  END IF;
  -- a second open request for the pair (either direction) is rejected by uq_connection_request_pair_low_key_pair_high_key_open (23505)
  INSERT INTO connection_request (id, created_by, updated_by, requester_app_user_id, recipient_app_user_id, connection_request_status_id,
                                  connection_origin_id, note_text, is_pending, expires_at)
  VALUES (v_id, p_requester, p_requester, p_requester, p_recipient, (SELECT id FROM connection_request_status WHERE code = 'pending'),
          p_origin, p_note, true, now() + INTERVAL '30 days');
  RETURN v_id;
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_connection_accept(p_request_id UUID, p_actor UUID) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE
  v_req UUID; v_rec UUID; v_pending BOOL; v_origin UUID; v_conn UUID; v_lo UUID; v_hi UUID; v_ok BOOL;
BEGIN
  SELECT requester_app_user_id, recipient_app_user_id, is_pending, connection_origin_id INTO v_req, v_rec, v_pending, v_origin
    FROM connection_request WHERE id = p_request_id FOR UPDATE;
  IF v_req IS NULL THEN
    RAISE EXCEPTION 'BR-NET-04: connection_request % not found', p_request_id USING ERRCODE = '55000';
  END IF;
  IF p_actor <> v_rec THEN
    RAISE EXCEPTION 'BR-NET-04: only the recipient may accept' USING ERRCODE = '42501';
  END IF;
  IF NOT v_pending THEN
    RAISE EXCEPTION 'BR-NET-04: connection_request % is no longer pending', p_request_id USING ERRCODE = '55000';
  END IF;
  v_ok := fn_assert_not_blocked(v_req, v_rec);
  v_lo := LEAST(v_req, v_rec); v_hi := GREATEST(v_req, v_rec);
  INSERT INTO connection (created_by, updated_by, lo_app_user_id, hi_app_user_id, connection_origin_id, initiator_app_user_id, connection_request_id, connected_at)
  VALUES (p_actor, p_actor, v_lo, v_hi, v_origin, v_req, p_request_id, now())
  ON CONFLICT (lo_app_user_id, hi_app_user_id) DO NOTHING;
  SELECT id INTO v_conn FROM connection WHERE lo_app_user_id = v_lo AND hi_app_user_id = v_hi;
  UPDATE connection_request SET is_pending = false, responded_at = now(), updated_at = now(), updated_by = p_actor,
         connection_request_status_id = (SELECT id FROM connection_request_status WHERE code = 'accepted') WHERE id = p_request_id;
  INSERT INTO connection_event (created_by, lo_app_user_id, hi_app_user_id, connection_event_type_id, actor_app_user_id)
  VALUES (p_actor, v_lo, v_hi, (SELECT id FROM connection_event_type WHERE code = 'connected'), p_actor);
  RETURN v_conn;
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_user_block(p_blocker UUID, p_blocked UUID, p_blocker_actor_type UUID, p_blocked_actor_type UUID,
                                         p_reason UUID, p_note TEXT DEFAULT NULL) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE v_id UUID := gen_random_uuid(); v_lo UUID := LEAST(p_blocker, p_blocked); v_hi UUID := GREATEST(p_blocker, p_blocked); v_n INT4;
BEGIN
  INSERT INTO user_block (id, created_by, updated_by, blocker_app_user_id, blocked_app_user_id, blocker_actor_type_id, blocked_actor_type_id,
                          block_reason_id, note) VALUES (v_id, p_blocker, p_blocker, p_blocker, p_blocked, p_blocker_actor_type, p_blocked_actor_type, p_reason, p_note);
  WITH d AS (DELETE FROM connection WHERE lo_app_user_id = v_lo AND hi_app_user_id = v_hi RETURNING 1) SELECT count(*)::INT4 INTO v_n FROM d;
  UPDATE connection_request SET is_pending = false, responded_at = now(), updated_at = now(), updated_by = p_blocker,
         connection_request_status_id = (SELECT id FROM connection_request_status WHERE code = 'declined')
   WHERE pair_low_key = v_lo AND pair_high_key = v_hi AND is_pending;
  DELETE FROM mutual_connection_cache WHERE lo_app_user_id = v_lo AND hi_app_user_id = v_hi;
  DELETE FROM network_second_degree WHERE (app_user_id = p_blocker AND reach_app_user_id = p_blocked) OR (app_user_id = p_blocked AND reach_app_user_id = p_blocker);
  IF v_n > 0 THEN
    INSERT INTO connection_event (created_by, lo_app_user_id, hi_app_user_id, connection_event_type_id, actor_app_user_id)
    VALUES (p_blocker, v_lo, v_hi, (SELECT id FROM connection_event_type WHERE code = 'removed_by_block'), p_blocker);
  END IF;
  RETURN v_id;
END $$;
-- @@
-- returns the resulting RSVP status code ('going' | 'waitlisted' | the requested code)
CREATE OR REPLACE FUNCTION fn_event_rsvp(p_event_id UUID, p_user UUID, p_status TEXT, p_origin UUID, p_guests INT4 DEFAULT 0) RETURNS TEXT
LANGUAGE plpgsql AS $$
DECLARE
  v_cap INT4; v_closes TIMESTAMPTZ; v_taken INT4; v_final TEXT := p_status; v_going UUID; v_wait UUID; v_cand UUID; v_seats INT4; v_ev UUID;
BEGIN
  SELECT id, capacity, registration_closes_at INTO v_ev, v_cap, v_closes FROM event WHERE id = p_event_id AND deleted_at IS NULL FOR UPDATE;
  IF v_ev IS NULL THEN
    RAISE EXCEPTION 'BR-NET-17: event % not found', p_event_id USING ERRCODE = '55000';
  END IF;
  IF p_status IN ('going', 'maybe') AND v_closes IS NOT NULL AND v_closes < now() THEN
    RAISE EXCEPTION 'BR-NET-17: registration closed' USING ERRCODE = '55000';
  END IF;
  SELECT id INTO v_going FROM event_rsvp_status WHERE code = 'going';
  SELECT id INTO v_wait FROM event_rsvp_status WHERE code = 'waitlisted';
  IF p_status = 'going' AND v_cap IS NOT NULL THEN
    SELECT COALESCE(sum(1 + guest_count), 0) INTO v_taken FROM event_rsvp
     WHERE event_id = p_event_id AND event_rsvp_status_id = v_going AND attendee_app_user_id <> p_user;
    IF v_taken + 1 + p_guests > v_cap THEN
      v_final := 'waitlisted';
    END IF;
  END IF;
  INSERT INTO event_rsvp (created_by, updated_by, event_id, attendee_app_user_id, event_rsvp_status_id, connection_origin_id, guest_count, responded_at)
  VALUES (p_user, p_user, p_event_id, p_user, (SELECT id FROM event_rsvp_status WHERE code = v_final), p_origin, p_guests, now())
  ON CONFLICT (event_id, attendee_app_user_id) DO UPDATE
    SET event_rsvp_status_id = EXCLUDED.event_rsvp_status_id, guest_count = EXCLUDED.guest_count, responded_at = now(), updated_at = now(), updated_by = p_user;
  -- a freed seat promotes waitlisted attendees in arrival order while they fit
  IF v_cap IS NOT NULL AND p_status <> 'going' THEN
    LOOP
      SELECT COALESCE(sum(1 + guest_count), 0) INTO v_taken FROM event_rsvp WHERE event_id = p_event_id AND event_rsvp_status_id = v_going;
      SELECT id, 1 + guest_count INTO v_cand, v_seats FROM event_rsvp
       WHERE event_id = p_event_id AND event_rsvp_status_id = v_wait ORDER BY responded_at, id LIMIT 1;
      EXIT WHEN v_cand IS NULL OR v_taken + v_seats > v_cap;
      UPDATE event_rsvp SET event_rsvp_status_id = v_going, updated_at = now() WHERE id = v_cand;
    END LOOP;
  END IF;
  RETURN v_final;
END $$;
-- @@
-- ===================================================================================================================
-- BR-NTF-17  alert limit.  The limit is passed by the caller (entitlement resolution lives in the service layer: the
-- candidate-side alert limit is not an entitlement_feature row); default 20.
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_job_alert_create(p_user UUID, p_name TEXT, p_frequency UUID, p_channel UUID, p_actor UUID,
                                              p_max_alerts INT4 DEFAULT 20, p_keywords JSONB DEFAULT '[]') RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE v_n INT4; v_id UUID := gen_random_uuid(); v_x UUID;
BEGIN
  SELECT id INTO v_x FROM app_user WHERE id = p_user FOR UPDATE;      -- serialisation point per user (also closes the phantom window)
  SELECT count(*) INTO v_n FROM job_alert_preference WHERE app_user_id = p_user AND deleted_at IS NULL;
  IF v_n >= p_max_alerts THEN
    RAISE EXCEPTION 'BR-NTF-17: alert limit % reached', p_max_alerts USING ERRCODE = '23514';
  END IF;
  INSERT INTO job_alert_preference (id, created_by, updated_by, app_user_id, alert_name, keyword_list, alert_frequency_id, notification_channel_id)
  VALUES (v_id, p_actor, p_actor, p_user, p_name, p_keywords, p_frequency, p_channel);
  RETURN v_id;
END $$;
-- @@
-- ===================================================================================================================
-- BR-PRF-18  shortlist changes always leave a history row
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_shortlist_add(p_org UUID, p_candidate UUID, p_actor UUID, p_kind UUID, p_stage UUID,
                                           p_folder UUID DEFAULT NULL, p_requirement UUID DEFAULT NULL) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE v_id UUID; v_deleted TIMESTAMPTZ; v_action TEXT;
BEGIN
  SELECT id, deleted_at INTO v_id, v_deleted FROM candidate_shortlist_item
   WHERE organisation_id = p_org AND candidate_profile_id = p_candidate AND job_requirement_id IS NOT DISTINCT FROM p_requirement
   ORDER BY (deleted_at IS NULL) DESC, created_at DESC LIMIT 1 FOR UPDATE;
  IF v_id IS NOT NULL AND v_deleted IS NULL THEN
    RAISE EXCEPTION 'BR-PRF-18: candidate already shortlisted' USING ERRCODE = '23505';
  END IF;
  IF v_id IS NOT NULL THEN
    UPDATE candidate_shortlist_item SET deleted_at = NULL, deleted_by = NULL, shortlist_kind_id = p_kind, shortlist_stage_id = p_stage,
           candidate_shortlist_folder_id = p_folder, updated_at = now(), updated_by = p_actor WHERE id = v_id;
    v_action := 'restored';
  ELSE
    v_id := gen_random_uuid();
    INSERT INTO candidate_shortlist_item (id, created_by, updated_by, organisation_id, candidate_profile_id, added_by_app_user_id,
                                          candidate_shortlist_folder_id, job_requirement_id, shortlist_kind_id, shortlist_stage_id)
    VALUES (v_id, p_actor, p_actor, p_org, p_candidate, p_actor, p_folder, p_requirement, p_kind, p_stage);
    v_action := 'added';
  END IF;
  INSERT INTO candidate_shortlist_history (created_by, candidate_shortlist_item_id, organisation_id, candidate_profile_id, actor_app_user_id,
                                           shortlist_action_id, to_shortlist_stage_id, to_candidate_shortlist_folder_id)
  VALUES (p_actor, v_id, p_org, p_candidate, p_actor, (SELECT id FROM shortlist_action WHERE code = v_action), p_stage, p_folder);
  RETURN v_id;
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_shortlist_remove(p_item_id UUID, p_actor UUID) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE v_org UUID; v_cand UUID; v_stage UUID; v_folder UUID;
BEGIN
  SELECT organisation_id, candidate_profile_id, shortlist_stage_id, candidate_shortlist_folder_id INTO v_org, v_cand, v_stage, v_folder
    FROM candidate_shortlist_item WHERE id = p_item_id AND deleted_at IS NULL FOR UPDATE;
  IF v_org IS NULL THEN
    RAISE EXCEPTION 'BR-PRF-18: shortlist item % not found', p_item_id USING ERRCODE = '55000';
  END IF;
  UPDATE candidate_shortlist_item SET deleted_at = now(), deleted_by = p_actor, updated_at = now(), updated_by = p_actor WHERE id = p_item_id;
  INSERT INTO candidate_shortlist_history (created_by, candidate_shortlist_item_id, organisation_id, candidate_profile_id, actor_app_user_id,
                                           shortlist_action_id, from_shortlist_stage_id, from_candidate_shortlist_folder_id)
  VALUES (p_actor, p_item_id, v_org, v_cand, p_actor, (SELECT id FROM shortlist_action WHERE code = 'removed'), v_stage, v_folder);
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_shortlist_move(p_item_id UUID, p_actor UUID, p_stage UUID, p_folder UUID DEFAULT NULL) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE v_org UUID; v_cand UUID; v_stage UUID; v_folder UUID;
BEGIN
  SELECT organisation_id, candidate_profile_id, shortlist_stage_id, candidate_shortlist_folder_id INTO v_org, v_cand, v_stage, v_folder
    FROM candidate_shortlist_item WHERE id = p_item_id AND deleted_at IS NULL FOR UPDATE;
  IF v_org IS NULL THEN
    RAISE EXCEPTION 'BR-PRF-18: shortlist item % not found', p_item_id USING ERRCODE = '55000';
  END IF;
  UPDATE candidate_shortlist_item SET shortlist_stage_id = p_stage, candidate_shortlist_folder_id = p_folder, updated_at = now(), updated_by = p_actor
   WHERE id = p_item_id;
  INSERT INTO candidate_shortlist_history (created_by, candidate_shortlist_item_id, organisation_id, candidate_profile_id, actor_app_user_id,
                                           shortlist_action_id, from_shortlist_stage_id, to_shortlist_stage_id,
                                           from_candidate_shortlist_folder_id, to_candidate_shortlist_folder_id)
  VALUES (p_actor, p_item_id, v_org, v_cand, p_actor,
          (SELECT id FROM shortlist_action WHERE code = CASE WHEN v_stage IS DISTINCT FROM p_stage THEN 'stage_changed' ELSE 'moved' END),
          v_stage, p_stage, v_folder, p_folder);
END $$;
-- @@
-- ===================================================================================================================
-- BR-PRF-04  candidate_profile.contact_* mirrors the primary multi-value row
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_candidate_set_primary_phone(p_candidate UUID, p_phone_row UUID, p_actor UUID) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE v_phone TEXT; v_country UUID; v_verified TIMESTAMPTZ; v_x UUID;
BEGIN
  SELECT id INTO v_x FROM candidate_profile WHERE id = p_candidate AND deleted_at IS NULL FOR UPDATE;
  IF v_x IS NULL THEN
    RAISE EXCEPTION 'BR-PRF-04: candidate_profile % not found', p_candidate USING ERRCODE = '55000';
  END IF;
  SELECT phone, phone_country_id, verified_at INTO v_phone, v_country, v_verified FROM candidate_contact_phone
   WHERE id = p_phone_row AND candidate_profile_id = p_candidate FOR UPDATE;
  IF v_phone IS NULL THEN
    RAISE EXCEPTION 'BR-PRF-04: phone row % does not belong to candidate %', p_phone_row, p_candidate USING ERRCODE = '55000';
  END IF;
  UPDATE candidate_contact_phone SET is_primary = false, updated_at = now(), updated_by = p_actor WHERE candidate_profile_id = p_candidate AND is_primary AND id <> p_phone_row;
  UPDATE candidate_contact_phone SET is_primary = true, updated_at = now(), updated_by = p_actor WHERE id = p_phone_row;
  UPDATE candidate_profile SET contact_phone = v_phone, contact_phone_country_id = v_country, contact_phone_verified_at = v_verified,
         updated_at = now(), updated_by = p_actor WHERE id = p_candidate;
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_candidate_set_primary_email(p_candidate UUID, p_email_row UUID, p_actor UUID) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE v_email TEXT; v_verified TIMESTAMPTZ; v_x UUID;
BEGIN
  SELECT id INTO v_x FROM candidate_profile WHERE id = p_candidate AND deleted_at IS NULL FOR UPDATE;
  IF v_x IS NULL THEN
    RAISE EXCEPTION 'BR-PRF-04: candidate_profile % not found', p_candidate USING ERRCODE = '55000';
  END IF;
  SELECT email, verified_at INTO v_email, v_verified FROM candidate_contact_email
   WHERE id = p_email_row AND candidate_profile_id = p_candidate FOR UPDATE;
  IF v_email IS NULL THEN
    RAISE EXCEPTION 'BR-PRF-04: email row % does not belong to candidate %', p_email_row, p_candidate USING ERRCODE = '55000';
  END IF;
  UPDATE candidate_contact_email SET is_primary = false, updated_at = now(), updated_by = p_actor WHERE candidate_profile_id = p_candidate AND is_primary AND id <> p_email_row;
  UPDATE candidate_contact_email SET is_primary = true, updated_at = now(), updated_by = p_actor WHERE id = p_email_row;
  UPDATE candidate_profile SET contact_email = v_email, contact_email_verified_at = v_verified, updated_at = now(), updated_by = p_actor WHERE id = p_candidate;
END $$;
-- @@
-- ===================================================================================================================
-- BR-SRC-02  consultant openness only for requirements open to consultants; closing cascades
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_consultant_openness_open(p_requirement UUID, p_actor UUID, p_consultant UUID DEFAULT NULL,
    p_valid_to TIMESTAMPTZ DEFAULT NULL, p_max_consultants INT4 DEFAULT NULL, p_commission DECIMAL DEFAULT NULL,
    p_fixed_fee DECIMAL DEFAULT NULL, p_currency UUID DEFAULT NULL) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE v_org UUID; v_open BOOL; v_cur UUID; v_id UUID := gen_random_uuid();
BEGIN
  SELECT organisation_id, is_open_to_consultants, currency_id INTO v_org, v_open, v_cur FROM job_requirement
   WHERE id = p_requirement AND deleted_at IS NULL AND closed_at IS NULL FOR UPDATE;
  IF v_org IS NULL OR NOT v_open THEN
    RAISE EXCEPTION 'BR-SRC-02: job_requirement % is not open to consultants', p_requirement USING ERRCODE = '55000';
  END IF;
  -- one live all-consultant window / one live invitation per consultant: partial unique indexes (23505)
  INSERT INTO consultant_job_openness (id, created_by, updated_by, job_requirement_id, organisation_id, consultant_profile_id, opened_by_app_user_id,
                                       valid_to, max_consultants, offered_commission_percentage, offered_fixed_fee, currency_id)
  VALUES (v_id, p_actor, p_actor, p_requirement, v_org, p_consultant, p_actor, p_valid_to, p_max_consultants, p_commission, p_fixed_fee,
          COALESCE(p_currency, v_cur));
  RETURN v_id;
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_consultant_openness_close(p_requirement UUID, p_actor UUID) RETURNS INT4
LANGUAGE plpgsql AS $$
DECLARE v_n INT4; v_x UUID;
BEGIN
  SELECT id INTO v_x FROM job_requirement WHERE id = p_requirement FOR UPDATE;
  IF v_x IS NULL THEN
    RAISE EXCEPTION 'BR-SRC-02: job_requirement % not found', p_requirement USING ERRCODE = '55000';
  END IF;
  UPDATE job_requirement SET is_open_to_consultants = false, updated_at = now(), updated_by = p_actor WHERE id = p_requirement AND is_open_to_consultants;
  WITH c AS (UPDATE consultant_job_openness SET closed_at = now(), updated_at = now(), updated_by = p_actor
   WHERE job_requirement_id = p_requirement AND closed_at IS NULL RETURNING 1) SELECT count(*)::INT4 INTO v_n FROM c;
  UPDATE sourcing_request SET closed_at = now(), updated_at = now(), updated_by = p_actor,
         sourcing_request_status_id = (SELECT id FROM sourcing_request_status WHERE code = 'closed')
   WHERE job_requirement_id = p_requirement AND closed_at IS NULL;
  RETURN v_n;
END $$;
-- @@
-- ===================================================================================================================
-- BR-ANA-13  ETL watermark only moves forward and only with a succeeded run; BR-MTH-10 outbox claim
-- ===================================================================================================================
CREATE OR REPLACE FUNCTION fn_etl_watermark_advance(p_pipeline TEXT, p_to TIMESTAMPTZ, p_run_id UUID) RETURNS BOOL
LANGUAGE plpgsql AS $$
DECLARE v_cur TIMESTAMPTZ; v_found UUID; v_ok BOOL;
BEGIN
  SELECT id, last_watermark_at INTO v_found, v_cur FROM dim_etl_watermark WHERE pipeline_code = p_pipeline FOR UPDATE;
  IF v_found IS NULL THEN
    RAISE EXCEPTION 'BR-ANA-13: unknown pipeline %', p_pipeline USING ERRCODE = '55000';
  END IF;
  SELECT s.code = 'succeeded' INTO v_ok FROM dim_etl_run r JOIN dim_etl_run_status s ON s.id = r.dim_etl_run_status_id
   WHERE r.id = p_run_id AND r.pipeline_code = p_pipeline;
  IF v_ok IS NOT TRUE THEN
    RAISE EXCEPTION 'BR-ANA-13: run % is not a succeeded run of pipeline %', p_run_id, p_pipeline USING ERRCODE = '55000';
  END IF;
  IF v_cur IS NOT NULL AND v_cur >= p_to THEN
    RETURN false;      -- never moves backwards
  END IF;
  UPDATE dim_etl_watermark SET last_watermark_at = p_to, last_successful_dim_etl_run_id = p_run_id, updated_at = now() WHERE id = v_found;
  RETURN true;
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_search_outbox_claim(p_worker TEXT, p_lease INTERVAL, p_limit INT4 DEFAULT 100) RETURNS SETOF UUID
LANGUAGE sql AS $$
  UPDATE search_index_outbox SET claimed_by_ref = p_worker, claimed_until_at = now() + p_lease, attempt_count = attempt_count + 1, updated_at = now()
   WHERE id IN (SELECT id FROM search_index_outbox
                 WHERE NOT is_done AND next_attempt_at <= now() AND (claimed_until_at IS NULL OR claimed_until_at < now())
                 ORDER BY next_attempt_at LIMIT p_limit FOR UPDATE)
  RETURNING id;
$$;

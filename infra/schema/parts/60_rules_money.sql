-- 60_rules_money.sql -- procedural enforcement of the MONEY business rules (subscriptions_payments + money-adjacent
-- rules of ext_ads and sourcing_consultant).  Owner of this area: team "rules money".
--
-- Conventions
--   * every function is SECURITY INVOKER: it runs with the privileges of the caller (billing_svc for everything that
--     touches wallet / ledger / payments; app_service for fn_ad_budget_settle).  GRANT EXECUTE is in 75_post_grants_money.sql.
--   * every function is idempotent where the business flow can be retried: the caller passes an idempotency key (or a
--     target sequence number); a replay returns the ORIGINAL result with "replayed": true and writes nothing.
--   * concurrency: serialisation point = SELECT ... FOR UPDATE on the aggregate root (wallet, subscription, payment_order,
--     coupon, invoice_number_series, ad_campaign).  The cluster runs SERIALIZABLE: a caller MUST retry on SQLSTATE 40001
--     (and may retry on 23505 after a replay race); implicit single-statement transactions (SELECT fn_...()) are
--     retried by the server, explicit transactions by the client (tests use conftest.retry()).
--   * business errors use custom SQLSTATEs (class TW wallet, TS subscription, TI invoice, TR refund, TE entitlement,
--     TC coupon, TO outreach, TA ads) so the application can map them without parsing text.
--   * canonical hash of a ledger row (BR-SUB-10): sha256(prev_hash || 'wallet|seq|type|delta|balance_after|key|created|actor')
--
-- Rule map (see the final report of the task for the verified-declaratively list):
--   BR-SUB-08/09/11/22  fn_wallet_post / fn_wallet_debit / fn_wallet_credit
--   BR-SUB-07           fn_wallet_reverse (reverse once)
--   BR-SUB-10           ledger hash chain written by fn_wallet_post, verified by fn_wallet_verify_chain
--   BR-SUB-05           fn_subscription_renew
--   BR-SUB-12           fn_series_next_number, fn_invoice_issue, fn_credit_note_issue
--   BR-SUB-15           fn_refund_request / fn_refund_approve / fn_refund_reject / fn_refund_complete
--   BR-SUB-20           fn_entitlement_consume
--   BR-SUB-21           fn_coupon_redeem
--   BR-SRC-09           fn_outreach_reserve / consume / release / refund_on_reply
--   BR-ADS-05           fn_ad_budget_settle (pacing flush, app_service)
--   BR-ADS-09           fn_ad_wallet_settle (prepaid settlement through the wallet, billing_svc)

-- ---------------------------------------------------------------------------------------------------------------------
-- hash of one ledger row
CREATE OR REPLACE FUNCTION fn_wallet_row_hash(p_prev BYTES, p_wallet_id UUID, p_seq INT8, p_type_id UUID, p_delta INT8,
  p_balance_after INT8, p_key TEXT, p_created_at TIMESTAMPTZ, p_actor UUID) RETURNS BYTES
LANGUAGE SQL AS $$
  SELECT sha256(COALESCE(p_prev, ''::BYTES) || convert_to(concat_ws('|', p_wallet_id::TEXT, p_seq::TEXT, p_type_id::TEXT,
    p_delta::TEXT, p_balance_after::TEXT, p_key, to_char(p_created_at AT TIME ZONE 'UTC', 'YYYYMMDDHH24MISSUS'),
    COALESCE(p_actor::TEXT, '')), 'UTF8'))
$$;
-- @@
-- ---------------------------------------------------------------------------------------------------------------------
-- core: one wallet movement.  p_delta < 0 = debit, > 0 = credit.
-- p_ref_kind: payment_order | refund | outreach_batch | job_requirement | coupon_redemption | wallet_expiry_bucket |
--             reversal | NULL (no FK reference: p_note mandatory, has_fk_reference = false)
-- Errors: TW001 insufficient credits, TW002 wallet not active, TW003 spend limit, TW004 idempotency key reused with
--         different parameters, TW005 wallet not found, TW008 bad arguments.
CREATE OR REPLACE FUNCTION fn_wallet_post(p_wallet_id UUID, p_delta INT8, p_type_code TEXT, p_idempotency_key TEXT,
  p_actor UUID, p_ref_kind TEXT, p_ref_id UUID, p_note TEXT, p_expires_at TIMESTAMPTZ, p_enforce_limits BOOL)
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_sys CONSTANT UUID := '00000000-0000-0000-0000-000000000001';
  v_actor UUID := COALESCE(p_actor, v_sys);
  v_old_id UUID; v_old_wallet UUID; v_old_delta INT8; v_old_seq INT8; v_old_bal INT8;
  v_w_id UUID; v_w_bal INT8; v_w_seq INT8; v_w_status UUID; v_w_org UUID;
  v_l_scope TEXT; v_l_period TEXT; v_l_used INT8; v_l_max INT8;
  v_type UUID;
  v_status TEXT;
  v_seq INT8;
  v_bal INT8;
  v_prev BYTES;
  v_hash BYTES;
  v_id UUID := gen_random_uuid();
  v_now TIMESTAMPTZ := date_trunc('milliseconds', now());
  v_start TIMESTAMPTZ;
  v_used INT8;
  v_need INT8;
  v_take INT8;
  v_b_id UUID; v_b_rem INT8;
BEGIN
  IF p_delta IS NULL OR p_delta = 0 THEN
    RAISE EXCEPTION 'wallet movement must be non-zero' USING ERRCODE = 'TW008';
  END IF;
  IF p_idempotency_key IS NULL OR length(p_idempotency_key) = 0 THEN
    RAISE EXCEPTION 'idempotency key required' USING ERRCODE = 'TW008';
  END IF;
  IF p_ref_kind IS NULL AND (p_note IS NULL OR length(p_note) = 0) THEN
    RAISE EXCEPTION 'a movement without FK reference needs an adjustment note' USING ERRCODE = 'TW008';
  END IF;
  IF p_ref_kind IS NOT NULL AND (p_ref_id IS NULL OR p_ref_kind NOT IN ('payment_order', 'refund', 'outreach_batch',
       'job_requirement', 'coupon_redemption', 'wallet_expiry_bucket', 'reversal')) THEN
    RAISE EXCEPTION 'bad reference kind/id' USING ERRCODE = 'TW008';
  END IF;

  -- 1) replay by idempotency key (also re-checked implicitly by UNIQUE(idempotency_key) at insert)
  SELECT l.id, l.wallet_id, l.credits_delta, l.sequence_no, l.balance_after INTO v_old_id, v_old_wallet, v_old_delta, v_old_seq, v_old_bal
    FROM wallet_ledger_entry l WHERE l.idempotency_key = p_idempotency_key;
  IF v_old_id IS NOT NULL THEN
    IF v_old_wallet <> p_wallet_id OR v_old_delta <> p_delta THEN
      RAISE EXCEPTION 'idempotency key % already used for a different movement', p_idempotency_key USING ERRCODE = 'TW004';
    END IF;
    RETURN jsonb_build_object('ledger_entry_id', v_old_id, 'sequence_no', v_old_seq,
                              'balance_after', v_old_bal, 'replayed', true);
  END IF;

  -- 2) serialise on the wallet row
  SELECT w.id, w.credit_balance, w.ledger_sequence, w.wallet_status_id, w.owner_organisation_id INTO v_w_id, v_w_bal, v_w_seq, v_w_status, v_w_org
    FROM wallet w WHERE w.id = p_wallet_id AND w.deleted_at IS NULL FOR UPDATE;
  IF v_w_id IS NULL THEN
    RAISE EXCEPTION 'wallet % not found', p_wallet_id USING ERRCODE = 'TW005';
  END IF;
  SELECT s.code INTO v_status FROM wallet_status s WHERE s.id = v_w_status;
  IF v_status <> 'active' THEN
    RAISE EXCEPTION 'wallet % is %', p_wallet_id, v_status USING ERRCODE = 'TW002';
  END IF;
  SELECT t.id INTO v_type FROM ledger_entry_type t WHERE t.code = p_type_code;
  IF v_type IS NULL THEN
    RAISE EXCEPTION 'unknown ledger entry type %', p_type_code USING ERRCODE = 'TW008';
  END IF;

  -- 3) balance + spend limits (BR-SUB-22: hard blocks abort the transaction)
  v_bal := v_w_bal + p_delta;
  IF v_bal < 0 THEN
    RAISE EXCEPTION 'insufficient credits: balance %, requested %', v_w_bal, -p_delta USING ERRCODE = 'TW001';
  END IF;
  IF p_delta < 0 AND p_enforce_limits AND v_w_org IS NOT NULL THEN
    -- first hard-blocking limit that this debit would exceed (set-based: this dialect has no loop over a query)
    SELECT x.scope_code, x.period_code, x.used, x.max_credits INTO v_l_scope, v_l_period, v_l_used, v_l_max FROM (
      SELECT sc.code AS scope_code, sp.code AS period_code, sl.max_credits,
             (SELECT COALESCE(-SUM(l.credits_delta), 0)::INT8 FROM wallet_ledger_entry l
               WHERE l.wallet_id = p_wallet_id AND l.is_debit
                 AND l.created_at >= date_trunc(CASE sp.code WHEN 'daily' THEN 'day' WHEN 'weekly' THEN 'week' ELSE 'month' END, now())
                 AND (sc.code = 'organisation' OR l.actor_app_user_id = sl.target_app_user_id)) AS used
        FROM spend_limit sl
        JOIN spend_limit_scope sc ON sc.id = sl.spend_limit_scope_id
        JOIN spend_limit_period sp ON sp.id = sl.spend_limit_period_id
       WHERE sl.organisation_id = v_w_org AND sl.deleted_at IS NULL AND sl.max_credits IS NOT NULL AND sl.is_hard_block
         AND sl.effective_from_date <= current_date AND (sl.effective_to_date IS NULL OR sl.effective_to_date >= current_date)
         AND (sc.code = 'organisation' OR (sc.code = 'user' AND sl.target_app_user_id = p_actor))) x
     WHERE x.used + (-p_delta) > x.max_credits LIMIT 1;
    IF v_l_scope IS NOT NULL THEN
      RAISE EXCEPTION 'spend limit (% %) exceeded: used %, requested %, max %', v_l_scope, v_l_period,
        v_l_used, -p_delta, v_l_max USING ERRCODE = 'TW003';
    END IF;
  END IF;

  -- 4) gapless sequence + hash chain
  v_seq := v_w_seq + 1;
  IF v_seq > 1 THEN
    SELECT l.row_hash INTO v_prev FROM wallet_ledger_entry l WHERE l.wallet_id = p_wallet_id AND l.sequence_no = v_seq - 1;
    IF v_prev IS NULL THEN
      RAISE EXCEPTION 'ledger gap before sequence % of wallet %', v_seq, p_wallet_id USING ERRCODE = 'TW008';
    END IF;
  END IF;
  v_hash := fn_wallet_row_hash(v_prev, p_wallet_id, v_seq, v_type, p_delta, v_bal, p_idempotency_key, v_now, p_actor);

  INSERT INTO wallet_ledger_entry (id, created_at, created_by, wallet_id, sequence_no, prev_sequence_no, ledger_entry_type_id,
      credits_delta, is_debit, balance_after, idempotency_key, actor_app_user_id, reverses_wallet_ledger_entry_id,
      has_fk_reference, payment_order_id, refund_id, outreach_batch_id, job_requirement_id, coupon_redemption_id,
      wallet_expiry_bucket_id, adjustment_note, prev_hash, row_hash)
  VALUES (v_id, v_now, v_actor, p_wallet_id, v_seq, CASE WHEN v_seq > 1 THEN v_seq - 1 END, v_type,
      p_delta, p_delta < 0, v_bal, p_idempotency_key, p_actor,
      CASE WHEN p_ref_kind = 'reversal' THEN p_ref_id END, p_ref_kind IS NOT NULL,
      CASE WHEN p_ref_kind = 'payment_order' THEN p_ref_id END, CASE WHEN p_ref_kind = 'refund' THEN p_ref_id END,
      CASE WHEN p_ref_kind = 'outreach_batch' THEN p_ref_id END, CASE WHEN p_ref_kind = 'job_requirement' THEN p_ref_id END,
      CASE WHEN p_ref_kind = 'coupon_redemption' THEN p_ref_id END, CASE WHEN p_ref_kind = 'wallet_expiry_bucket' THEN p_ref_id END,
      p_note, v_prev, v_hash);

  -- 5) FIFO lots: a debit consumes the soonest-expiring unexpired lots first; a credit with an expiry opens a lot.
  --    (credits without a lot never expire and are consumed after the lots; lots can never be over-consumed.)
  IF p_delta < 0 THEN
    v_need := -p_delta;
    WHILE v_need > 0 LOOP
      v_b_id := NULL;
      SELECT b.id, b.credits_remaining INTO v_b_id, v_b_rem FROM wallet_expiry_bucket b
       WHERE b.wallet_id = p_wallet_id AND b.credits_remaining > 0 AND b.expires_at > v_now
       ORDER BY b.expires_at, b.id LIMIT 1 FOR UPDATE;
      EXIT WHEN v_b_id IS NULL;
      v_take := LEAST(v_need, v_b_rem);
      UPDATE wallet_expiry_bucket SET credits_remaining = credits_remaining - v_take, updated_at = v_now, updated_by = v_actor,
             fully_consumed_at = CASE WHEN credits_remaining - v_take = 0 THEN v_now END
       WHERE id = v_b_id;
      INSERT INTO wallet_bucket_consumption (created_at, created_by, wallet_ledger_entry_id, wallet_expiry_bucket_id, credits)
      VALUES (v_now, v_actor, v_id, v_b_id, v_take);
      v_need := v_need - v_take;
    END LOOP;
  ELSIF p_expires_at IS NOT NULL THEN
    INSERT INTO wallet_expiry_bucket (created_at, created_by, updated_at, updated_by, wallet_id, source_wallet_ledger_entry_id,
        credits_granted, credits_remaining, expires_at)
    VALUES (v_now, v_actor, v_now, v_actor, p_wallet_id, v_id, p_delta, p_delta, p_expires_at);
  END IF;

  -- 6) wallet header (same transaction, row locked)
  UPDATE wallet SET credit_balance = v_bal, ledger_sequence = v_seq, last_entry_at = v_now, updated_at = v_now, updated_by = v_actor
   WHERE id = p_wallet_id;

  RETURN jsonb_build_object('ledger_entry_id', v_id, 'sequence_no', v_seq, 'balance_after', v_bal, 'replayed', false);
END $$;
-- @@
-- debit: p_credits > 0
CREATE OR REPLACE FUNCTION fn_wallet_debit(p_wallet_id UUID, p_credits INT8, p_type_code TEXT, p_idempotency_key TEXT,
  p_actor UUID DEFAULT NULL, p_ref_kind TEXT DEFAULT NULL, p_ref_id UUID DEFAULT NULL, p_note TEXT DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql AS $$
BEGIN
  IF p_credits IS NULL OR p_credits <= 0 THEN
    RAISE EXCEPTION 'debit amount must be positive' USING ERRCODE = 'TW008';
  END IF;
  RETURN fn_wallet_post(p_wallet_id, -p_credits, p_type_code, p_idempotency_key, p_actor, p_ref_kind, p_ref_id, p_note, NULL, true);
END $$;
-- @@
-- credit: p_credits > 0; p_expires_at opens a FIFO lot (promotional / expiring credits)
CREATE OR REPLACE FUNCTION fn_wallet_credit(p_wallet_id UUID, p_credits INT8, p_type_code TEXT, p_idempotency_key TEXT,
  p_actor UUID DEFAULT NULL, p_ref_kind TEXT DEFAULT NULL, p_ref_id UUID DEFAULT NULL, p_note TEXT DEFAULT NULL,
  p_expires_at TIMESTAMPTZ DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql AS $$
BEGIN
  IF p_credits IS NULL OR p_credits <= 0 THEN
    RAISE EXCEPTION 'credit amount must be positive' USING ERRCODE = 'TW008';
  END IF;
  RETURN fn_wallet_post(p_wallet_id, p_credits, p_type_code, p_idempotency_key, p_actor, p_ref_kind, p_ref_id, p_note, p_expires_at, false);
END $$;
-- @@
-- BR-SUB-07: correction = new row that reverses the original; a row is reversed at most once (TW006), a reversal row is
-- never reversed (TW007).  UNIQUE(reverses_wallet_ledger_entry_id) is the backstop.
CREATE OR REPLACE FUNCTION fn_wallet_reverse(p_ledger_entry_id UUID, p_idempotency_key TEXT, p_actor UUID DEFAULT NULL,
  p_note TEXT DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_o_id UUID; v_o_wallet UUID; v_o_delta INT8; v_o_rev UUID;
  v_dummy UUID;
BEGIN
  SELECT l.id, l.wallet_id, l.credits_delta, l.reverses_wallet_ledger_entry_id INTO v_o_id, v_o_wallet, v_o_delta, v_o_rev
    FROM wallet_ledger_entry l WHERE l.id = p_ledger_entry_id;
  IF v_o_id IS NULL THEN
    RAISE EXCEPTION 'ledger entry % not found', p_ledger_entry_id USING ERRCODE = 'TW008';
  END IF;
  IF EXISTS (SELECT 1 FROM wallet_ledger_entry l WHERE l.idempotency_key = p_idempotency_key) THEN
    RETURN fn_wallet_post(v_o_wallet, -v_o_delta, 'reversal', p_idempotency_key, p_actor, 'reversal', p_ledger_entry_id,
                          p_note, NULL, false);   -- replay
  END IF;
  IF v_o_rev IS NOT NULL THEN
    RAISE EXCEPTION 'entry % is itself a reversal', p_ledger_entry_id USING ERRCODE = 'TW007';
  END IF;
  SELECT w.id INTO v_dummy FROM wallet w WHERE w.id = v_o_wallet FOR UPDATE;       -- serialise reversals of this wallet
  IF EXISTS (SELECT 1 FROM wallet_ledger_entry l WHERE l.reverses_wallet_ledger_entry_id = p_ledger_entry_id) THEN
    RAISE EXCEPTION 'entry % is already reversed', p_ledger_entry_id USING ERRCODE = 'TW006';
  END IF;
  RETURN fn_wallet_post(v_o_wallet, -v_o_delta, 'reversal', p_idempotency_key, p_actor, 'reversal', p_ledger_entry_id,
                        p_note, NULL, false);
END $$;
-- @@
-- BR-SUB-10: recomputes the hash chain of one wallet; returns {ok, entries, first_bad_sequence_no}
CREATE OR REPLACE FUNCTION fn_wallet_verify_chain(p_wallet_id UUID) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_bad INT8;
  v_n INT8;
BEGIN
  SELECT count(*) INTO v_n FROM wallet_ledger_entry l WHERE l.wallet_id = p_wallet_id;
  SELECT min(t.sequence_no) INTO v_bad FROM (
    SELECT x.sequence_no, x.row_hash, x.prev_hash, x.exp_prev,
           fn_wallet_row_hash(x.exp_prev, x.wallet_id, x.sequence_no, x.ledger_entry_type_id, x.credits_delta, x.balance_after,
                              x.idempotency_key, x.created_at, x.actor_app_user_id) AS calc
      FROM (SELECT l.*, lag(l.row_hash) OVER (ORDER BY l.sequence_no) AS exp_prev
              FROM wallet_ledger_entry l WHERE l.wallet_id = p_wallet_id) x) t
   WHERE t.row_hash IS DISTINCT FROM t.calc OR t.prev_hash IS DISTINCT FROM t.exp_prev;
  RETURN jsonb_build_object('ok', v_bad IS NULL, 'entries', v_n, 'first_bad_sequence_no', v_bad);
END $$;
-- @@
-- ---------------------------------------------------------------------------------------------------------------------
-- BR-SUB-05: renewal = next period (chained to the tail) + payment order + status history, in one transaction.
-- p_target_sequence_no makes the call idempotent: if the tail already has sequence_no >= target the existing period is
-- returned (replayed = true), so N parallel renewals for the same target produce exactly one new period.
-- Errors: TS001 subscription not live / not found, TS002 no initial period, TS003 target skips a period, TS004 one-time plan.
CREATE OR REPLACE FUNCTION fn_subscription_renew(p_subscription_id UUID, p_actor UUID DEFAULT NULL,
  p_target_sequence_no INT4 DEFAULT NULL, p_payment_gateway_code TEXT DEFAULT 'razorpay', p_idempotency_key TEXT DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_sys CONSTANT UUID := '00000000-0000-0000-0000-000000000001';
  v_actor UUID := COALESCE(p_actor, v_sys);
  v_s_id UUID; v_s_org UUID; v_s_lic UUID; v_s_plan UUID; v_s_status UUID; v_s_interval UUID; v_s_cur UUID; v_s_amt DECIMAL; v_s_ended TIMESTAMPTZ; v_s_deleted TIMESTAMPTZ;
  v_tail_id UUID; v_tail_seq INT4; v_tail_end DATE;
  v_ex_id UUID; v_ex_start DATE; v_ex_end DATE;
  v_interval TEXT;
  v_months INT4;
  v_target INT4;
  v_start DATE;
  v_end DATE;
  v_period UUID := gen_random_uuid();
  v_order UUID;
  v_status_code TEXT;
BEGIN
  SELECT s.id, s.organisation_id, s.licence_id, s.plan_version_id, s.subscription_status_id, s.billing_interval_id, s.currency_id,
         s.recurring_amount, s.ended_at, s.deleted_at INTO v_s_id, v_s_org, v_s_lic, v_s_plan, v_s_status, v_s_interval, v_s_cur, v_s_amt, v_s_ended, v_s_deleted
    FROM subscription s WHERE s.id = p_subscription_id FOR UPDATE;
  IF v_s_id IS NULL OR v_s_ended IS NOT NULL OR v_s_deleted IS NOT NULL THEN
    RAISE EXCEPTION 'subscription % is not live', p_subscription_id USING ERRCODE = 'TS001';
  END IF;
  SELECT p.id, p.sequence_no, p.period_end_date INTO v_tail_id, v_tail_seq, v_tail_end FROM subscription_period p
   WHERE p.subscription_id = p_subscription_id ORDER BY p.sequence_no DESC LIMIT 1;
  IF v_tail_id IS NULL THEN
    RAISE EXCEPTION 'subscription % has no initial period', p_subscription_id USING ERRCODE = 'TS002';
  END IF;
  v_target := COALESCE(p_target_sequence_no, v_tail_seq + 1);
  IF v_tail_seq >= v_target THEN
    SELECT p.id, p.period_start_date, p.period_end_date INTO v_ex_id, v_ex_start, v_ex_end FROM subscription_period p
     WHERE p.subscription_id = p_subscription_id AND p.sequence_no = v_target;
    RETURN jsonb_build_object('period_id', v_ex_id, 'sequence_no', v_target, 'period_start_date', v_ex_start,
                              'period_end_date', v_ex_end, 'payment_order_id',
                              (SELECT o.id FROM payment_order o WHERE o.idempotency_key = COALESCE(p_idempotency_key, 'renew:' || p_subscription_id::TEXT || ':' || v_target::TEXT)),
                              'replayed', true);
  END IF;
  IF v_target <> v_tail_seq + 1 THEN
    RAISE EXCEPTION 'cannot renew to sequence % (tail is %)', v_target, v_tail_seq USING ERRCODE = 'TS003';
  END IF;
  SELECT bi.code INTO v_interval FROM billing_interval bi WHERE bi.id = v_s_interval;
  v_months := CASE v_interval WHEN 'monthly' THEN 1 WHEN 'quarterly' THEN 3 WHEN 'half_yearly' THEN 6 WHEN 'annual' THEN 12 END;
  IF v_months IS NULL THEN
    RAISE EXCEPTION 'billing interval % does not renew', v_interval USING ERRCODE = 'TS004';
  END IF;
  v_start := v_tail_end;
  v_end := (v_start + v_months * INTERVAL '1 month')::DATE;

  INSERT INTO subscription_period (id, created_by, updated_by, subscription_id, licence_id, sequence_no, period_start_date,
      period_end_date, prior_period_id, prior_period_end_date, subscription_period_status_id, plan_version_id, billed_amount,
      currency_id)
  VALUES (v_period, v_actor, v_actor, p_subscription_id, v_s_lic, v_target, v_start, v_end, v_tail_id, v_tail_end,
      (SELECT x.id FROM subscription_period_status x WHERE x.code = 'scheduled'), v_s_plan, v_s_amt, v_s_cur);

  IF v_s_amt > 0 THEN
    v_order := gen_random_uuid();
    INSERT INTO payment_order (id, created_by, updated_by, organisation_id, subscription_id, payment_purpose_id, payment_gateway_id,
        payment_order_status_id, amount, currency_id, idempotency_key, created_by_app_user_id)
    VALUES (v_order, v_actor, v_actor, v_s_org, p_subscription_id,
        (SELECT x.id FROM payment_purpose x WHERE x.code = 'subscription_fee'),
        (SELECT x.id FROM payment_gateway x WHERE x.code = p_payment_gateway_code),
        (SELECT x.id FROM payment_order_status x WHERE x.code = 'created'),
        v_s_amt, v_s_cur,
        COALESCE(p_idempotency_key, 'renew:' || p_subscription_id::TEXT || ':' || v_target::TEXT), p_actor);
  END IF;

  SELECT x.code INTO v_status_code FROM subscription_status x WHERE x.id = v_s_status;
  INSERT INTO subscription_status_history (created_by, subscription_id, from_subscription_status_id, to_subscription_status_id,
      changed_at, actor_app_user_id, change_reason)
  VALUES (v_actor, p_subscription_id, v_s_status, v_s_status, now(), p_actor,
      'renewal period ' || v_target::TEXT || ' (' || v_status_code || ')');

  RETURN jsonb_build_object('period_id', v_period, 'sequence_no', v_target, 'period_start_date', v_start, 'period_end_date', v_end,
                            'payment_order_id', v_order, 'replayed', false);
END $$;
-- @@
-- ---------------------------------------------------------------------------------------------------------------------
-- BR-SUB-12: next number of a series under row lock.  Gapless because the increment commits (or rolls back) with the
-- document that uses it.  Returns {number, label}.  Errors: TI001 series not found / closed.
CREATE OR REPLACE FUNCTION fn_series_next_number(p_series_id UUID, p_actor UUID DEFAULT NULL) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_ser_id UUID; v_ser_prefix TEXT; v_ser_fy TEXT; v_ser_next INT8; v_ser_open BOOL;
BEGIN
  SELECT s.id, s.prefix, s.fiscal_year_label, s.next_number, s.is_open INTO v_ser_id, v_ser_prefix, v_ser_fy, v_ser_next, v_ser_open
    FROM invoice_number_series s WHERE s.id = p_series_id FOR UPDATE;
  IF v_ser_id IS NULL OR NOT v_ser_open THEN
    RAISE EXCEPTION 'number series % not found or closed', p_series_id USING ERRCODE = 'TI001';
  END IF;
  UPDATE invoice_number_series SET next_number = v_ser_next + 1, updated_at = now(),
         updated_by = COALESCE(p_actor, '00000000-0000-0000-0000-000000000001'::UUID)
   WHERE id = p_series_id;
  RETURN jsonb_build_object('number', v_ser_next,
                            'label', v_ser_prefix || '/' || v_ser_fy || '/' || lpad(v_ser_next::TEXT, 6, '0'));
END $$;
-- @@
-- issue a draft invoice: assigns the next number of its series.  Replay on an already issued invoice returns its number.
-- Errors: TI002 invoice not found / void.
CREATE OR REPLACE FUNCTION fn_invoice_issue(p_invoice_id UUID, p_actor UUID DEFAULT NULL) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_actor UUID := COALESCE(p_actor, '00000000-0000-0000-0000-000000000001'::UUID);
  v_inv_id UUID; v_inv_series UUID; v_inv_number INT8; v_inv_label TEXT; v_inv_void TIMESTAMPTZ; v_inv_del TIMESTAMPTZ;
  v_n JSONB;
BEGIN
  SELECT i.id, i.invoice_number_series_id, i.invoice_number, i.invoice_label, i.voided_at, i.deleted_at
    INTO v_inv_id, v_inv_series, v_inv_number, v_inv_label, v_inv_void, v_inv_del
    FROM invoice i WHERE i.id = p_invoice_id FOR UPDATE;
  IF v_inv_id IS NULL OR v_inv_void IS NOT NULL OR v_inv_del IS NOT NULL THEN
    RAISE EXCEPTION 'invoice % not found or void', p_invoice_id USING ERRCODE = 'TI002';
  END IF;
  IF v_inv_number IS NOT NULL THEN
    RETURN jsonb_build_object('invoice_number', v_inv_number, 'invoice_label', v_inv_label, 'replayed', true);
  END IF;
  v_n := fn_series_next_number(v_inv_series, p_actor);
  UPDATE invoice SET invoice_number = (v_n->>'number')::INT8, invoice_label = v_n->>'label', issued_at = now(),
         issue_date = current_date, invoice_status_id = (SELECT x.id FROM invoice_status x WHERE x.code = 'issued'),
         updated_at = now(), updated_by = v_actor
   WHERE id = p_invoice_id;
  RETURN jsonb_build_object('invoice_number', (v_n->>'number')::INT8, 'invoice_label', v_n->>'label', 'replayed', false);
END $$;
-- @@
-- credit note numbering from a credit_note series (series_document_kind = credit_note).  Errors: TI001, TI002, TI003 (series kind).
CREATE OR REPLACE FUNCTION fn_credit_note_issue(p_invoice_id UUID, p_series_id UUID, p_reason_code TEXT, p_subtotal DECIMAL,
  p_tax DECIMAL, p_refund_id UUID DEFAULT NULL, p_actor UUID DEFAULT NULL) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_actor UUID := COALESCE(p_actor, '00000000-0000-0000-0000-000000000001'::UUID);
  v_inv_id UUID; v_inv_cur UUID; v_inv_number INT8;
  v_kind TEXT;
  v_n JSONB;
  v_id UUID := gen_random_uuid();
BEGIN
  SELECT i.id, i.currency_id, i.invoice_number INTO v_inv_id, v_inv_cur, v_inv_number FROM invoice i WHERE i.id = p_invoice_id FOR UPDATE;
  IF v_inv_id IS NULL OR v_inv_number IS NULL THEN
    RAISE EXCEPTION 'invoice % not found or not issued', p_invoice_id USING ERRCODE = 'TI002';
  END IF;
  SELECT k.code INTO v_kind FROM invoice_number_series s JOIN series_document_kind k ON k.id = s.series_document_kind_id
   WHERE s.id = p_series_id;
  IF v_kind IS DISTINCT FROM 'credit_note' THEN
    RAISE EXCEPTION 'series % is not a credit note series', p_series_id USING ERRCODE = 'TI003';
  END IF;
  v_n := fn_series_next_number(p_series_id, p_actor);
  INSERT INTO credit_note (id, created_by, updated_by, invoice_id, invoice_number_series_id, credit_note_number, credit_note_label,
      credit_note_reason_id, refund_id, subtotal_amount, tax_amount, total_amount, currency_id, issue_date)
  VALUES (v_id, v_actor, v_actor, p_invoice_id, p_series_id, (v_n->>'number')::INT8, v_n->>'label',
      (SELECT x.id FROM credit_note_reason x WHERE x.code = p_reason_code), p_refund_id, p_subtotal, p_tax, p_subtotal + p_tax,
      v_inv_cur, current_date);
  RETURN jsonb_build_object('credit_note_id', v_id, 'number', (v_n->>'number')::INT8, 'label', v_n->>'label');
END $$;
-- @@
-- ---------------------------------------------------------------------------------------------------------------------
-- BR-SUB-15: refunds.  Request: lock the payment order, require it to be paid, sum of live refunds + new <= amount paid.
-- Errors: TR001 order not found / not refundable, TR002 approver = requester, TR003 over-refund, TR004 wrong refund state,
--         TR005 bad arguments.  Replay by idempotency key returns the original refund.
CREATE OR REPLACE FUNCTION fn_refund_request(p_payment_order_id UUID, p_amount DECIMAL, p_reason_code TEXT, p_requested_by UUID,
  p_idempotency_key TEXT, p_credits_clawed_back INT8 DEFAULT NULL) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_o_id UUID; v_o_amt DECIMAL; v_o_cur UUID; v_o_wallet UUID; v_o_status TEXT;
  v_old_id UUID; v_old_order UUID; v_old_amt DECIMAL;
  v_sum DECIMAL;
  v_id UUID := gen_random_uuid();
BEGIN
  IF p_amount IS NULL OR p_amount <= 0 THEN
    RAISE EXCEPTION 'refund amount must be positive' USING ERRCODE = 'TR005';
  END IF;
  SELECT r.id, r.payment_order_id, r.amount INTO v_old_id, v_old_order, v_old_amt FROM refund r WHERE r.idempotency_key = p_idempotency_key;
  IF v_old_id IS NOT NULL THEN
    IF v_old_order <> p_payment_order_id OR v_old_amt <> p_amount THEN
      RAISE EXCEPTION 'idempotency key % already used for a different refund', p_idempotency_key USING ERRCODE = 'TR005';
    END IF;
    RETURN jsonb_build_object('refund_id', v_old_id, 'replayed', true);
  END IF;
  SELECT o.id, o.amount, o.currency_id, o.wallet_id, s.code INTO v_o_id, v_o_amt, v_o_cur, v_o_wallet, v_o_status
    FROM payment_order o JOIN payment_order_status s ON s.id = o.payment_order_status_id
   WHERE o.id = p_payment_order_id AND o.deleted_at IS NULL FOR UPDATE OF o;
  IF v_o_id IS NULL OR v_o_status NOT IN ('paid', 'partially_refunded') THEN
    RAISE EXCEPTION 'payment order % not found or not paid', p_payment_order_id USING ERRCODE = 'TR001';
  END IF;
  IF p_credits_clawed_back IS NOT NULL AND v_o_wallet IS NULL THEN
    RAISE EXCEPTION 'order has no wallet to claw credits back from' USING ERRCODE = 'TR005';
  END IF;
  SELECT COALESCE(SUM(r.amount), 0) INTO v_sum FROM refund r
    JOIN refund_status s ON s.id = r.refund_status_id
   WHERE r.payment_order_id = p_payment_order_id AND r.deleted_at IS NULL AND s.code NOT IN ('rejected', 'failed');
  IF v_sum + p_amount > v_o_amt THEN
    RAISE EXCEPTION 'refunds % + % exceed amount paid %', v_sum, p_amount, v_o_amt USING ERRCODE = 'TR003';
  END IF;
  INSERT INTO refund (id, created_by, updated_by, payment_order_id, refund_status_id, refund_reason_id, amount, currency_id,
      credits_clawed_back, idempotency_key, requested_by_app_user_id)
  VALUES (v_id, p_requested_by, p_requested_by, p_payment_order_id, (SELECT x.id FROM refund_status x WHERE x.code = 'requested'),
      (SELECT x.id FROM refund_reason x WHERE x.code = p_reason_code), p_amount, v_o_cur, p_credits_clawed_back,
      p_idempotency_key, p_requested_by);
  RETURN jsonb_build_object('refund_id', v_id, 'refunded_so_far', v_sum + p_amount, 'replayed', false);
END $$;
-- @@
-- approve: four eyes (approver <> requester; ck_refund_four_eyes is the backstop)
CREATE OR REPLACE FUNCTION fn_refund_approve(p_refund_id UUID, p_approver UUID) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_ord UUID; v_req UUID; v_st TEXT;
  v_dummy UUID;
BEGIN
  SELECT r.payment_order_id INTO v_ord FROM refund r WHERE r.id = p_refund_id;
  IF v_ord IS NULL THEN
    RAISE EXCEPTION 'refund % not found', p_refund_id USING ERRCODE = 'TR004';
  END IF;
  SELECT o.id INTO v_dummy FROM payment_order o WHERE o.id = v_ord FOR UPDATE;
  SELECT r.requested_by_app_user_id, s.code INTO v_req, v_st
    FROM refund r JOIN refund_status s ON s.id = r.refund_status_id WHERE r.id = p_refund_id FOR UPDATE OF r;
  IF v_st = 'approved' AND EXISTS (SELECT 1 FROM refund r WHERE r.id = p_refund_id AND r.approved_by_app_user_id = p_approver) THEN
    RETURN jsonb_build_object('refund_id', p_refund_id, 'replayed', true);
  END IF;
  IF v_st <> 'requested' THEN
    RAISE EXCEPTION 'refund % is %, not requested', p_refund_id, v_st USING ERRCODE = 'TR004';
  END IF;
  IF p_approver IS NULL OR p_approver = v_req THEN
    RAISE EXCEPTION 'refund approver must differ from the requester' USING ERRCODE = 'TR002';
  END IF;
  UPDATE refund SET approved_by_app_user_id = p_approver, refund_status_id = (SELECT x.id FROM refund_status x WHERE x.code = 'approved'),
         updated_at = now(), updated_by = p_approver WHERE id = p_refund_id;
  RETURN jsonb_build_object('refund_id', p_refund_id, 'replayed', false);
END $$;
-- @@
-- reject: frees the amount for new requests
CREATE OR REPLACE FUNCTION fn_refund_reject(p_refund_id UUID, p_actor UUID) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_ord UUID; v_st TEXT;
  v_dummy UUID;
BEGIN
  SELECT r.payment_order_id INTO v_ord FROM refund r WHERE r.id = p_refund_id;
  IF v_ord IS NULL THEN
    RAISE EXCEPTION 'refund % not found', p_refund_id USING ERRCODE = 'TR004';
  END IF;
  SELECT o.id INTO v_dummy FROM payment_order o WHERE o.id = v_ord FOR UPDATE;
  SELECT s.code INTO v_st FROM refund r JOIN refund_status s ON s.id = r.refund_status_id WHERE r.id = p_refund_id FOR UPDATE OF r;
  IF v_st NOT IN ('requested', 'approved') THEN
    RAISE EXCEPTION 'refund % is %, cannot reject', p_refund_id, v_st USING ERRCODE = 'TR004';
  END IF;
  UPDATE refund SET refund_status_id = (SELECT x.id FROM refund_status x WHERE x.code = 'rejected'), updated_at = now(), updated_by = p_actor
   WHERE id = p_refund_id;
  RETURN jsonb_build_object('refund_id', p_refund_id);
END $$;
-- @@
-- complete: gateway confirmed.  Claws credits back (ledger row type 'refund', reference refund) under the wallet lock and
-- moves the order to partially_refunded / refunded.
CREATE OR REPLACE FUNCTION fn_refund_complete(p_refund_id UUID, p_gateway_refund_ref TEXT, p_actor UUID DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_actor UUID := COALESCE(p_actor, '00000000-0000-0000-0000-000000000001'::UUID);
  v_ord UUID; v_claw INT8; v_st TEXT;
  v_o_id UUID; v_o_amt DECIMAL; v_o_wallet UUID;
  v_done DECIMAL;
  v_led JSONB;
BEGIN
  SELECT r.payment_order_id INTO v_ord FROM refund r WHERE r.id = p_refund_id;
  IF v_ord IS NULL THEN
    RAISE EXCEPTION 'refund % not found', p_refund_id USING ERRCODE = 'TR004';
  END IF;
  SELECT o.id, o.amount, o.wallet_id INTO v_o_id, v_o_amt, v_o_wallet FROM payment_order o WHERE o.id = v_ord FOR UPDATE;
  SELECT r.credits_clawed_back, s.code INTO v_claw, v_st
    FROM refund r JOIN refund_status s ON s.id = r.refund_status_id WHERE r.id = p_refund_id FOR UPDATE OF r;
  IF v_st = 'completed' THEN
    RETURN jsonb_build_object('refund_id', p_refund_id, 'replayed', true);
  END IF;
  IF v_st NOT IN ('approved', 'processing') THEN
    RAISE EXCEPTION 'refund % is %, must be approved first', p_refund_id, v_st USING ERRCODE = 'TR004';
  END IF;
  IF v_claw IS NOT NULL THEN
    v_led := fn_wallet_post(v_o_wallet, -v_claw, 'refund', 'refund:' || p_refund_id::TEXT, p_actor,
                            'refund', p_refund_id, NULL, NULL, false);
  END IF;
  UPDATE refund SET refund_status_id = (SELECT x.id FROM refund_status x WHERE x.code = 'completed'), processed_at = now(),
         gateway_refund_ref = COALESCE(p_gateway_refund_ref, gateway_refund_ref), updated_at = now(), updated_by = v_actor
   WHERE id = p_refund_id;
  SELECT COALESCE(SUM(r.amount), 0) INTO v_done FROM refund r JOIN refund_status s ON s.id = r.refund_status_id
   WHERE r.payment_order_id = v_o_id AND s.code = 'completed' AND r.deleted_at IS NULL;
  UPDATE payment_order SET updated_at = now(), updated_by = v_actor,
         payment_order_status_id = (SELECT x.id FROM payment_order_status x WHERE x.code = CASE WHEN v_done >= v_o_amt THEN 'refunded' ELSE 'partially_refunded' END)
   WHERE id = v_o_id;
  RETURN jsonb_build_object('refund_id', p_refund_id, 'clawback', v_led, 'replayed', false);
END $$;
-- @@
-- ---------------------------------------------------------------------------------------------------------------------
-- BR-SUB-20: free-quota consumption.  Returns {ok:false} when exhausted (caller falls back to the paid path);
-- never exceeds limit_value (row locked; ck_subscription_entitlement_used_bounds is the backstop).
CREATE OR REPLACE FUNCTION fn_entitlement_consume(p_subscription_id UUID, p_feature_code TEXT, p_units INT8 DEFAULT 1,
  p_at TIMESTAMPTZ DEFAULT NULL, p_actor UUID DEFAULT NULL) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_e_id UUID; v_e_limit INT8; v_e_used INT8;
  v_at TIMESTAMPTZ := COALESCE(p_at, now());
BEGIN
  IF p_units IS NULL OR p_units <= 0 THEN
    RAISE EXCEPTION 'units must be positive' USING ERRCODE = 'TE001';
  END IF;
  SELECT e.id, e.limit_value, e.used_count INTO v_e_id, v_e_limit, v_e_used FROM subscription_entitlement e
    JOIN entitlement_feature f ON f.id = e.entitlement_feature_id
   WHERE e.subscription_id = p_subscription_id AND f.code = p_feature_code
     AND e.usage_window_start_at <= v_at AND v_at < e.usage_window_end_at
   ORDER BY e.usage_window_start_at DESC LIMIT 1 FOR UPDATE OF e;
  IF v_e_id IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'no_entitlement');
  END IF;
  IF v_e_limit IS NOT NULL AND v_e_used + p_units > v_e_limit THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'quota_exhausted', 'used', v_e_used, 'limit', v_e_limit);
  END IF;
  UPDATE subscription_entitlement SET used_count = used_count + p_units, updated_at = now(),
         updated_by = COALESCE(p_actor, '00000000-0000-0000-0000-000000000001'::UUID) WHERE id = v_e_id;
  RETURN jsonb_build_object('ok', true, 'used', v_e_used + p_units, 'limit', v_e_limit);
END $$;
-- @@
-- ---------------------------------------------------------------------------------------------------------------------
-- BR-SUB-21: coupon redemption.  Errors: TC001 unknown, TC002 outside validity window, TC003 global cap reached,
-- TC004 per-organisation limit reached, TC005 bonus coupon without wallet, TC006 currency mismatch.
-- Bonus coupons credit the wallet (type promo_grant, reference = the redemption) in the same transaction.
CREATE OR REPLACE FUNCTION fn_coupon_redeem(p_coupon_code TEXT, p_organisation_id UUID, p_actor UUID, p_base_amount DECIMAL DEFAULT NULL,
  p_currency_id UUID DEFAULT NULL, p_payment_order_id UUID DEFAULT NULL, p_invoice_id UUID DEFAULT NULL, p_wallet_id UUID DEFAULT NULL,
  p_bonus_expires_at TIMESTAMPTZ DEFAULT NULL) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_c_id UUID; v_c_from TIMESTAMPTZ; v_c_to TIMESTAMPTZ; v_c_max INT8; v_c_cnt INT8; v_c_per INT8; v_c_pct DECIMAL; v_c_amt DECIMAL; v_c_cur UUID; v_c_bonus INT8; v_c_type TEXT;
  v_n INT8;
  v_id UUID := gen_random_uuid();
  v_cur UUID;
  v_disc DECIMAL;
  v_bonus INT8;
  v_led JSONB;
BEGIN
  SELECT c.id, c.valid_from, c.valid_to, c.max_redemptions, c.redemption_count, c.per_organisation_limit, c.percent_off, c.amount_off,
         c.currency_id, c.bonus_credits, t.code INTO v_c_id, v_c_from, v_c_to, v_c_max, v_c_cnt, v_c_per, v_c_pct, v_c_amt, v_c_cur, v_c_bonus, v_c_type
    FROM coupon c JOIN discount_type t ON t.id = c.discount_type_id
   WHERE c.coupon_code = upper(p_coupon_code) AND c.deleted_at IS NULL FOR UPDATE OF c;
  IF v_c_id IS NULL THEN
    RAISE EXCEPTION 'coupon % unknown', p_coupon_code USING ERRCODE = 'TC001';
  END IF;
  IF now() < v_c_from OR (v_c_to IS NOT NULL AND now() > v_c_to) THEN
    RAISE EXCEPTION 'coupon % is not valid now', p_coupon_code USING ERRCODE = 'TC002';
  END IF;
  IF v_c_max IS NOT NULL AND v_c_cnt >= v_c_max THEN
    RAISE EXCEPTION 'coupon % is exhausted', p_coupon_code USING ERRCODE = 'TC003';
  END IF;
  SELECT count(*) INTO v_n FROM coupon_redemption r WHERE r.coupon_id = v_c_id AND r.organisation_id = p_organisation_id;
  IF v_n >= v_c_per THEN
    RAISE EXCEPTION 'organisation already redeemed coupon % (% of %)', p_coupon_code, v_n, v_c_per USING ERRCODE = 'TC004';
  END IF;
  v_cur := COALESCE(p_currency_id, v_c_cur);
  IF v_c_type = 'bonus_credits' THEN
    IF p_wallet_id IS NULL OR v_cur IS NULL THEN
      RAISE EXCEPTION 'bonus coupon needs a wallet and a currency' USING ERRCODE = 'TC005';
    END IF;
    v_bonus := v_c_bonus;
  ELSE
    IF v_cur IS NULL OR p_base_amount IS NULL THEN
      RAISE EXCEPTION 'discount coupon needs base amount and currency' USING ERRCODE = 'TC005';
    END IF;
    IF v_c_type = 'percent_off' THEN
      v_disc := round(p_base_amount * v_c_pct / 100, 4);
    ELSE
      IF v_c_cur IS DISTINCT FROM v_cur THEN
        RAISE EXCEPTION 'coupon currency mismatch' USING ERRCODE = 'TC006';
      END IF;
      v_disc := LEAST(v_c_amt, p_base_amount);
    END IF;
  END IF;
  INSERT INTO coupon_redemption (id, created_by, coupon_id, organisation_id, payment_order_id, invoice_id, discount_amount, currency_id,
      bonus_credits_granted, redeemed_at)
  VALUES (v_id, p_actor, v_c_id, p_organisation_id, p_payment_order_id, p_invoice_id, v_disc, v_cur, v_bonus, now());
  UPDATE coupon SET redemption_count = redemption_count + 1, updated_at = now(), updated_by = p_actor WHERE id = v_c_id;
  IF v_bonus IS NOT NULL THEN
    v_led := fn_wallet_post(p_wallet_id, v_bonus, 'promo_grant', 'coupon:' || v_id::TEXT, p_actor, 'coupon_redemption', v_id, NULL,
                            p_bonus_expires_at, false);
  END IF;
  RETURN jsonb_build_object('redemption_id', v_id, 'discount_amount', v_disc, 'bonus_credits', v_bonus, 'ledger', v_led);
END $$;
-- @@
-- ---------------------------------------------------------------------------------------------------------------------
-- BR-SRC-09: outreach credits.  reserve = wallet debit (ledger row referencing the batch) + usage row 'reserve';
-- consume / release_unused / refund_on_reply are usage rows (release and refund also credit the wallet).
-- The usage idempotency key (<= 80 chars) blocks a double charge on retry; ledger key = 'outreach:' || key.
-- outreach_batch.credits_reserved is maintained by the application (billing_svc has no UPDATE on outreach_batch).
-- Errors: TO001 batch not found, TO002 more than reserved, TO003 nothing to refund / no consumption for recipient,
--         TO004 refund window elapsed, TO005 bad arguments.
CREATE OR REPLACE FUNCTION fn_outreach_reserve(p_batch_id UUID, p_wallet_id UUID, p_credits INT4, p_key TEXT, p_actor UUID DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_actor UUID := COALESCE(p_actor, '00000000-0000-0000-0000-000000000001'::UUID);
  v_old_id UUID; v_old_cr INT4; v_old_led UUID; v_old_batch UUID;
  v_led JSONB;
  v_id UUID := gen_random_uuid();
BEGIN
  IF p_credits IS NULL OR p_credits <= 0 THEN
    RAISE EXCEPTION 'credits must be positive' USING ERRCODE = 'TO005';
  END IF;
  SELECT u.id, u.credits, u.wallet_ledger_entry_id, u.outreach_batch_id INTO v_old_id, v_old_cr, v_old_led, v_old_batch FROM outreach_credit_usage u WHERE u.idempotency_key = p_key;
  IF v_old_id IS NOT NULL THEN
    IF v_old_batch <> p_batch_id OR v_old_cr <> p_credits THEN
      RAISE EXCEPTION 'idempotency key % reused with different parameters', p_key USING ERRCODE = 'TO005';
    END IF;
    RETURN jsonb_build_object('usage_id', v_old_id, 'ledger_entry_id', v_old_led, 'replayed', true);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM outreach_batch b WHERE b.id = p_batch_id) THEN
    RAISE EXCEPTION 'outreach batch % not found', p_batch_id USING ERRCODE = 'TO001';
  END IF;
  v_led := fn_wallet_post(p_wallet_id, -p_credits, 'consumption_outreach', 'outreach:' || p_key, p_actor, 'outreach_batch', p_batch_id,
                          NULL, NULL, true);
  INSERT INTO outreach_credit_usage (id, created_by, outreach_batch_id, outreach_credit_event_id, credits, wallet_ledger_entry_id, idempotency_key)
  VALUES (v_id, v_actor, p_batch_id, (SELECT x.id FROM outreach_credit_event x WHERE x.code = 'reserve'), p_credits,
          (v_led->>'ledger_entry_id')::UUID, p_key);
  RETURN jsonb_build_object('usage_id', v_id, 'ledger_entry_id', v_led->>'ledger_entry_id', 'balance_after', v_led->'balance_after', 'replayed', false);
END $$;
-- @@
-- helper: outstanding = reserved - consumed - released for a batch (also locks the wallet that paid for the reservations)
CREATE OR REPLACE FUNCTION fn_outreach_outstanding(p_batch_id UUID) RETURNS INT8 LANGUAGE plpgsql AS $$
DECLARE
  v_dummy UUID;
  v_out INT8;
BEGIN
  SELECT w.id INTO v_dummy FROM wallet w WHERE w.id = (
      SELECT l.wallet_id FROM outreach_credit_usage u JOIN wallet_ledger_entry l ON l.id = u.wallet_ledger_entry_id
       WHERE u.outreach_batch_id = p_batch_id ORDER BY u.occurred_at LIMIT 1) FOR UPDATE;
  SELECT COALESCE(SUM(CASE e.code WHEN 'reserve' THEN u.credits WHEN 'consume' THEN -u.credits WHEN 'release_unused' THEN -u.credits ELSE 0 END), 0)
    INTO v_out FROM outreach_credit_usage u JOIN outreach_credit_event e ON e.id = u.outreach_credit_event_id WHERE u.outreach_batch_id = p_batch_id;
  RETURN v_out;
END $$;
-- @@
CREATE OR REPLACE FUNCTION fn_outreach_consume(p_batch_id UUID, p_recipient_id UUID, p_credits INT4, p_key TEXT, p_actor UUID DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_actor UUID := COALESCE(p_actor, '00000000-0000-0000-0000-000000000001'::UUID);
  v_old_id UUID;
  v_id UUID := gen_random_uuid();
  v_out INT8;
BEGIN
  IF p_credits IS NULL OR p_credits <= 0 THEN
    RAISE EXCEPTION 'credits must be positive' USING ERRCODE = 'TO005';
  END IF;
  SELECT u.id INTO v_old_id FROM outreach_credit_usage u WHERE u.idempotency_key = p_key;
  IF v_old_id IS NOT NULL THEN
    RETURN jsonb_build_object('usage_id', v_old_id, 'replayed', true);
  END IF;
  v_out := fn_outreach_outstanding(p_batch_id);
  IF v_out < p_credits THEN
    RAISE EXCEPTION 'consume % exceeds outstanding reservation %', p_credits, v_out USING ERRCODE = 'TO002';
  END IF;
  INSERT INTO outreach_credit_usage (id, created_by, outreach_batch_id, outreach_recipient_id, outreach_credit_event_id, credits, idempotency_key)
  VALUES (v_id, v_actor, p_batch_id, p_recipient_id, (SELECT x.id FROM outreach_credit_event x WHERE x.code = 'consume'), p_credits, p_key);
  RETURN jsonb_build_object('usage_id', v_id, 'outstanding', v_out - p_credits, 'replayed', false);
END $$;
-- @@
-- release the unused part of the reservation back to the wallet (ledger type refund, reference = batch)
CREATE OR REPLACE FUNCTION fn_outreach_release(p_batch_id UUID, p_key TEXT, p_actor UUID DEFAULT NULL) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_actor UUID := COALESCE(p_actor, '00000000-0000-0000-0000-000000000001'::UUID);
  v_old_id UUID; v_old_cr INT4;
  v_id UUID := gen_random_uuid();
  v_out INT8;
  v_wallet UUID;
  v_led JSONB;
BEGIN
  SELECT u.id, u.credits INTO v_old_id, v_old_cr FROM outreach_credit_usage u WHERE u.idempotency_key = p_key;
  IF v_old_id IS NOT NULL THEN
    RETURN jsonb_build_object('usage_id', v_old_id, 'released', v_old_cr, 'replayed', true);
  END IF;
  v_out := fn_outreach_outstanding(p_batch_id);
  IF v_out <= 0 THEN
    RETURN jsonb_build_object('released', 0, 'replayed', false);
  END IF;
  SELECT l.wallet_id INTO v_wallet FROM outreach_credit_usage u JOIN wallet_ledger_entry l ON l.id = u.wallet_ledger_entry_id
   WHERE u.outreach_batch_id = p_batch_id ORDER BY u.occurred_at LIMIT 1;
  v_led := fn_wallet_post(v_wallet, v_out, 'refund', 'outreach:' || p_key, p_actor, 'outreach_batch', p_batch_id, NULL, NULL, false);
  INSERT INTO outreach_credit_usage (id, created_by, outreach_batch_id, outreach_credit_event_id, credits, wallet_ledger_entry_id, idempotency_key)
  VALUES (v_id, v_actor, p_batch_id, (SELECT x.id FROM outreach_credit_event x WHERE x.code = 'release_unused'), v_out::INT4,
          (v_led->>'ledger_entry_id')::UUID, p_key);
  RETURN jsonb_build_object('usage_id', v_id, 'released', v_out, 'ledger_entry_id', v_led->>'ledger_entry_id', 'replayed', false);
END $$;
-- @@
-- refund_on_reply: a new row (never an update) crediting back credits already consumed for a recipient who replied, within the window
CREATE OR REPLACE FUNCTION fn_outreach_refund_on_reply(p_batch_id UUID, p_recipient_id UUID, p_key TEXT, p_window INTERVAL DEFAULT INTERVAL '7 days',
  p_actor UUID DEFAULT NULL) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_actor UUID := COALESCE(p_actor, '00000000-0000-0000-0000-000000000001'::UUID);
  v_old_id UUID; v_old_cr INT4;
  v_c_cr INT4; v_c_at TIMESTAMPTZ;
  v_wallet UUID;
  v_dummy INT8;
  v_id UUID := gen_random_uuid();
  v_led JSONB;
BEGIN
  SELECT u.id, u.credits INTO v_old_id, v_old_cr FROM outreach_credit_usage u WHERE u.idempotency_key = p_key;
  IF v_old_id IS NOT NULL THEN
    RETURN jsonb_build_object('usage_id', v_old_id, 'refunded', v_old_cr, 'replayed', true);
  END IF;
  v_dummy := fn_outreach_outstanding(p_batch_id);                       -- locks the wallet
  SELECT u.credits, u.occurred_at INTO v_c_cr, v_c_at FROM outreach_credit_usage u JOIN outreach_credit_event e ON e.id = u.outreach_credit_event_id
   WHERE u.outreach_batch_id = p_batch_id AND u.outreach_recipient_id = p_recipient_id AND e.code = 'consume'
   ORDER BY u.occurred_at DESC LIMIT 1;
  IF v_c_cr IS NULL THEN
    RAISE EXCEPTION 'no consumed credit for recipient %', p_recipient_id USING ERRCODE = 'TO003';
  END IF;
  IF EXISTS (SELECT 1 FROM outreach_credit_usage u JOIN outreach_credit_event e ON e.id = u.outreach_credit_event_id
              WHERE u.outreach_batch_id = p_batch_id AND u.outreach_recipient_id = p_recipient_id AND e.code = 'refund_on_reply') THEN
    RAISE EXCEPTION 'recipient % already refunded', p_recipient_id USING ERRCODE = 'TO003';
  END IF;
  IF now() - v_c_at > p_window THEN
    RAISE EXCEPTION 'refund window of % elapsed', p_window USING ERRCODE = 'TO004';
  END IF;
  SELECT l.wallet_id INTO v_wallet FROM outreach_credit_usage u JOIN wallet_ledger_entry l ON l.id = u.wallet_ledger_entry_id
   WHERE u.outreach_batch_id = p_batch_id ORDER BY u.occurred_at LIMIT 1;
  v_led := fn_wallet_post(v_wallet, v_c_cr, 'refund', 'outreach:' || p_key, p_actor, 'outreach_batch', p_batch_id, NULL, NULL, false);
  INSERT INTO outreach_credit_usage (id, created_by, outreach_batch_id, outreach_recipient_id, outreach_credit_event_id, credits,
      wallet_ledger_entry_id, idempotency_key)
  VALUES (v_id, v_actor, p_batch_id, p_recipient_id, (SELECT x.id FROM outreach_credit_event x WHERE x.code = 'refund_on_reply'),
          v_c_cr, (v_led->>'ledger_entry_id')::UUID, p_key);
  RETURN jsonb_build_object('usage_id', v_id, 'refunded', v_c_cr, 'replayed', false);
END $$;
-- @@
-- ---------------------------------------------------------------------------------------------------------------------
-- BR-ADS-05: pacing flush for one campaign and one hour (run by app_service, never on the serve path).
-- One transaction: lock campaign, sum the 16 shards, cap at the remaining budget (excess is absorbed, never charged above
-- budget), insert the spend_accrual ledger row (key accrue:{campaign}:{hour}, hash chain per campaign), update spent_amount /
-- ledger_sequence, zero the shards.  Replay returns the original accrual.  Error TA001 campaign not found.
CREATE OR REPLACE FUNCTION fn_ad_budget_settle(p_campaign_id UUID, p_hour TIMESTAMPTZ, p_actor UUID DEFAULT NULL) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_actor UUID := COALESCE(p_actor, '00000000-0000-0000-0000-000000000001'::UUID);
  v_c_id UUID; v_c_acct UUID; v_c_budget DECIMAL; v_c_spent DECIMAL; v_c_seq INT8; v_c_cur UUID;
  v_key TEXT;
  v_old_id UUID; v_old_amt DECIMAL; v_old_seq INT8;
  v_pending DECIMAL;
  v_acc DECIMAL;
  v_seq INT8;
  v_prev BYTES;
  v_hash BYTES;
  v_id UUID := gen_random_uuid();
  v_now TIMESTAMPTZ := now();
  v_hour TIMESTAMPTZ := date_trunc('hour', p_hour);
BEGIN
  v_key := 'accrue:' || p_campaign_id::TEXT || ':' || to_char(v_hour AT TIME ZONE 'UTC', 'YYYYMMDDHH24');
  SELECT l.id, l.amount_delta, l.sequence_no INTO v_old_id, v_old_amt, v_old_seq FROM ad_budget_ledger l WHERE l.idempotency_key = v_key;
  IF v_old_id IS NOT NULL THEN
    RETURN jsonb_build_object('ledger_id', v_old_id, 'accrued', v_old_amt, 'sequence_no', v_old_seq, 'replayed', true);
  END IF;
  SELECT c.id, c.ad_account_id, c.budget_amount, c.spent_amount, c.ledger_sequence, c.currency_id INTO v_c_id, v_c_acct, v_c_budget, v_c_spent, v_c_seq, v_c_cur
    FROM ad_campaign c WHERE c.id = p_campaign_id AND c.deleted_at IS NULL FOR UPDATE;
  IF v_c_id IS NULL THEN
    RAISE EXCEPTION 'campaign % not found', p_campaign_id USING ERRCODE = 'TA001';
  END IF;
  SELECT COALESCE(SUM(s.pending_spend_amount), 0) INTO v_pending FROM ad_budget_shard s WHERE s.ad_campaign_id = p_campaign_id;
  IF v_pending = 0 THEN
    RETURN jsonb_build_object('accrued', 0, 'replayed', false);
  END IF;
  v_acc := round(LEAST(v_pending, GREATEST(v_c_budget - v_c_spent, 0)), 4);
  v_seq := v_c_seq + 1;
  IF v_seq > 1 THEN
    SELECT l.row_hash INTO v_prev FROM ad_budget_ledger l WHERE l.ad_campaign_id = p_campaign_id AND l.sequence_no = v_seq - 1;
  END IF;
  v_hash := sha256(COALESCE(v_prev, ''::BYTES) || convert_to(concat_ws('|', p_campaign_id::TEXT, v_seq::TEXT, v_acc::TEXT,
              (v_c_spent + v_acc)::TEXT, v_key), 'UTF8'));
  IF v_acc > 0 THEN
    INSERT INTO ad_budget_ledger (id, created_at, created_by, ad_account_id, ad_campaign_id, sequence_no, prev_sequence_no,
        ad_ledger_entry_type_id, amount_delta, currency_id, spent_after, usage_hour_at, idempotency_key, prev_hash, row_hash)
    VALUES (v_id, v_now, v_actor, v_c_acct, p_campaign_id, v_seq, CASE WHEN v_seq > 1 THEN v_seq - 1 END,
        (SELECT x.id FROM ad_ledger_entry_type x WHERE x.code = 'spend_accrual'), v_acc, v_c_cur, v_c_spent + v_acc,
        v_hour, v_key, v_prev, v_hash);
    UPDATE ad_campaign SET spent_amount = v_c_spent + v_acc, ledger_sequence = v_seq, last_pacing_run_at = v_now,
           updated_at = v_now, updated_by = v_actor WHERE id = p_campaign_id;
  ELSE
    v_id := NULL;
    UPDATE ad_campaign SET last_pacing_run_at = v_now, updated_at = v_now, updated_by = v_actor WHERE id = p_campaign_id;
  END IF;
  UPDATE ad_budget_shard SET pending_spend_amount = 0, event_count = 0, last_flushed_at = v_now, updated_at = v_now, updated_by = v_actor
   WHERE ad_campaign_id = p_campaign_id;
  RETURN jsonb_build_object('ledger_id', v_id, 'accrued', v_acc, 'absorbed', v_pending - v_acc, 'sequence_no', CASE WHEN v_acc > 0 THEN v_seq END,
                            'replayed', false);
END $$;
-- @@
-- BR-ADS-09: ads never bypass the wallet.  Prepaid settlement: wallet debit (FOR UPDATE, balance >= 0) then ad_billing_event.
-- credits = ceil(spend * rate).  Ledger row has no FK reference (the link is ad_billing_event.wallet_ledger_entry_id), type defaults to
-- consumption_posting (there is no dedicated ads ledger entry type in the lookup seeds).  Replay by idempotency key.
-- Errors: TA002 bad arguments, plus every TW error of the wallet debit.
CREATE OR REPLACE FUNCTION fn_ad_wallet_settle(p_ad_account_id UUID, p_wallet_id UUID, p_period_start TIMESTAMPTZ, p_period_end TIMESTAMPTZ,
  p_spend_amount DECIMAL, p_currency_id UUID, p_credit_rate DECIMAL, p_key TEXT, p_actor UUID DEFAULT NULL,
  p_event_type_code TEXT DEFAULT 'settlement_debit', p_ledger_type_code TEXT DEFAULT 'consumption_posting') RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
  v_actor UUID := COALESCE(p_actor, '00000000-0000-0000-0000-000000000001'::UUID);
  v_old_id UUID; v_old_cr INT8; v_old_led UUID; v_old_spend DECIMAL;
  v_id UUID := gen_random_uuid();
  v_credits INT8;
  v_led JSONB;
BEGIN
  IF p_spend_amount IS NULL OR p_spend_amount <= 0 OR p_credit_rate IS NULL OR p_credit_rate <= 0 THEN
    RAISE EXCEPTION 'spend and credit rate must be positive' USING ERRCODE = 'TA002';
  END IF;
  SELECT e.id, e.credits_debited, e.wallet_ledger_entry_id, e.spend_amount INTO v_old_id, v_old_cr, v_old_led, v_old_spend FROM ad_billing_event e WHERE e.idempotency_key = p_key;
  IF v_old_id IS NOT NULL THEN
    IF v_old_spend <> p_spend_amount THEN
      RAISE EXCEPTION 'idempotency key % reused with different parameters', p_key USING ERRCODE = 'TA002';
    END IF;
    RETURN jsonb_build_object('event_id', v_old_id, 'credits_debited', v_old_cr,
                              'ledger_entry_id', v_old_led, 'replayed', true);
  END IF;
  v_credits := ceil(p_spend_amount * p_credit_rate)::INT8;
  IF v_credits > 0 THEN
    v_led := fn_wallet_post(p_wallet_id, -v_credits, p_ledger_type_code, 'adbill:' || p_key, p_actor, NULL, NULL,
                            'ad_billing_event ' || v_id::TEXT, NULL, true);
  END IF;
  INSERT INTO ad_billing_event (id, created_by, ad_account_id, ad_billing_event_type_id, idempotency_key, period_start_at, period_end_at,
      spend_amount, currency_id, credit_rate, credits_debited, wallet_ledger_entry_id)
  VALUES (v_id, v_actor, p_ad_account_id, (SELECT x.id FROM ad_billing_event_type x WHERE x.code = p_event_type_code), p_key,
      p_period_start, p_period_end, p_spend_amount, p_currency_id, p_credit_rate,
      CASE WHEN v_credits > 0 THEN v_credits END, (v_led->>'ledger_entry_id')::UUID);
  RETURN jsonb_build_object('event_id', v_id, 'credits_debited', v_credits, 'ledger_entry_id', v_led->>'ledger_entry_id',
                            'balance_after', v_led->'balance_after', 'replayed', false);
END $$;

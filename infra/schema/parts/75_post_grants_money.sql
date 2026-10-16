-- 75_post_grants_money.sql -- EXECUTE grants for the money functions of 60_rules_money.sql (RBAC GR-17/GR-18: billing_svc is the only
-- wallet / ledger writer; the pacing flush of ads runs as app_service).  Functions are SECURITY INVOKER, so a role that may execute a
-- function still needs the table privileges of the statements inside it: this file only adds what the RBAC matrix lacks for the
-- procedures to work (documented deviation: INSERT on ad_budget_ledger for app_service, append-only by trigger/no UPDATE/DELETE).

REVOKE ALL ON FUNCTION fn_wallet_row_hash(BYTES, UUID, INT8, UUID, INT8, INT8, TEXT, TIMESTAMPTZ, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_wallet_row_hash(BYTES, UUID, INT8, UUID, INT8, INT8, TEXT, TIMESTAMPTZ, UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_wallet_post(UUID, INT8, TEXT, TEXT, UUID, TEXT, UUID, TEXT, TIMESTAMPTZ, BOOL) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_wallet_post(UUID, INT8, TEXT, TEXT, UUID, TEXT, UUID, TEXT, TIMESTAMPTZ, BOOL) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_wallet_debit(UUID, INT8, TEXT, TEXT, UUID, TEXT, UUID, TEXT) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_wallet_debit(UUID, INT8, TEXT, TEXT, UUID, TEXT, UUID, TEXT) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_wallet_credit(UUID, INT8, TEXT, TEXT, UUID, TEXT, UUID, TEXT, TIMESTAMPTZ) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_wallet_credit(UUID, INT8, TEXT, TEXT, UUID, TEXT, UUID, TEXT, TIMESTAMPTZ) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_wallet_reverse(UUID, TEXT, UUID, TEXT) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_wallet_reverse(UUID, TEXT, UUID, TEXT) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_wallet_verify_chain(UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_wallet_verify_chain(UUID) TO billing_svc, app_service;
-- @@
REVOKE ALL ON FUNCTION fn_subscription_renew(UUID, UUID, INT4, TEXT, TEXT) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_subscription_renew(UUID, UUID, INT4, TEXT, TEXT) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_series_next_number(UUID, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_series_next_number(UUID, UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_invoice_issue(UUID, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_invoice_issue(UUID, UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_credit_note_issue(UUID, UUID, TEXT, DECIMAL, DECIMAL, UUID, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_credit_note_issue(UUID, UUID, TEXT, DECIMAL, DECIMAL, UUID, UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_refund_request(UUID, DECIMAL, TEXT, UUID, TEXT, INT8) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_refund_request(UUID, DECIMAL, TEXT, UUID, TEXT, INT8) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_refund_approve(UUID, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_refund_approve(UUID, UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_refund_reject(UUID, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_refund_reject(UUID, UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_refund_complete(UUID, TEXT, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_refund_complete(UUID, TEXT, UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_entitlement_consume(UUID, TEXT, INT8, TIMESTAMPTZ, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_entitlement_consume(UUID, TEXT, INT8, TIMESTAMPTZ, UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_coupon_redeem(TEXT, UUID, UUID, DECIMAL, UUID, UUID, UUID, UUID, TIMESTAMPTZ) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_coupon_redeem(TEXT, UUID, UUID, DECIMAL, UUID, UUID, UUID, UUID, TIMESTAMPTZ) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_outreach_reserve(UUID, UUID, INT4, TEXT, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_outreach_reserve(UUID, UUID, INT4, TEXT, UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_outreach_outstanding(UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_outreach_outstanding(UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_outreach_consume(UUID, UUID, INT4, TEXT, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_outreach_consume(UUID, UUID, INT4, TEXT, UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_outreach_release(UUID, TEXT, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_outreach_release(UUID, TEXT, UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_outreach_refund_on_reply(UUID, UUID, TEXT, INTERVAL, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_outreach_refund_on_reply(UUID, UUID, TEXT, INTERVAL, UUID) TO billing_svc;
-- @@
REVOKE ALL ON FUNCTION fn_ad_budget_settle(UUID, TIMESTAMPTZ, UUID) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_ad_budget_settle(UUID, TIMESTAMPTZ, UUID) TO app_service;
-- @@
REVOKE ALL ON FUNCTION fn_ad_wallet_settle(UUID, UUID, TIMESTAMPTZ, TIMESTAMPTZ, DECIMAL, UUID, DECIMAL, TEXT, UUID, TEXT, TEXT) FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_ad_wallet_settle(UUID, UUID, TIMESTAMPTZ, TIMESTAMPTZ, DECIMAL, UUID, DECIMAL, TEXT, UUID, TEXT, TEXT) TO billing_svc;
-- @@
GRANT INSERT ON TABLE ad_budget_ledger TO app_service;

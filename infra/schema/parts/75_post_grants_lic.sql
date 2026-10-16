-- 75_post_grants_lic.sql : EXECUTE grants for 60_rules_lic.sql (functions are SECURITY INVOKER: the caller also needs the table
-- privileges of the RoleMatrix, see docs/13_rbac_matrix.xlsx).
--  auth_svc    : login / session / device / refresh-token functions (owns device, device_session, identity_session writes)
--  app_service : licence / seat / org / account / role / legacy-sync administration functions
-- Deviation from the RoleMatrix (documented in the report): seat assignment by app_service must create device_slot rows and end the
-- sessions of a removed seat, so app_service additionally gets INSERT/UPDATE on device_slot and UPDATE on device_session (no secrets).
-- app_service gets NOTHING on identity_session (BR-ADM-04); CockroachDB has no column-level GRANT, so identity_session rows of ended
-- device sessions are revoked by fn_identity_session_sync_ended (auth_svc) and fn_refresh_rotate refuses a token whose device session ended.
REVOKE EXECUTE ON FUNCTION fn_lic_audit FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_lic_audit TO auth_svc, app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_login_claim FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_login_claim TO auth_svc;
-- @@
REVOKE EXECUTE ON FUNCTION fn_refresh_rotate FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_refresh_rotate TO auth_svc;
-- @@
REVOKE EXECUTE ON FUNCTION fn_device_session_end FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_device_session_end TO auth_svc;
-- @@
REVOKE EXECUTE ON FUNCTION fn_device_session_expire_sweep FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_device_session_expire_sweep TO auth_svc;
-- @@
REVOKE EXECUTE ON FUNCTION fn_device_revoke FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_device_revoke TO auth_svc;
-- @@
REVOKE EXECUTE ON FUNCTION fn_licence_sync_slots FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_licence_sync_slots TO app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_licence_set_limits FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_licence_set_limits TO app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_seat_assign FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_seat_assign TO app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_seat_revoke FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_seat_revoke TO app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_licence_expire_sweep FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_licence_expire_sweep TO app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_recruiter_mapping_change FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_recruiter_mapping_change TO app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_recruiter_mapping_close FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_recruiter_mapping_close TO app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_account_admin_add FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_account_admin_add TO app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_account_admin_close FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_account_admin_close TO app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_organisation_account_move FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_organisation_account_move TO app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_role_inheritance_add FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_role_inheritance_add TO app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_legacy_sync_open_run FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_legacy_sync_open_run TO app_service;
-- @@
GRANT INSERT, UPDATE ON TABLE device_slot TO app_service;
-- @@
GRANT UPDATE ON TABLE device_session TO app_service;
-- @@
REVOKE EXECUTE ON FUNCTION fn_identity_session_sync_ended FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_identity_session_sync_ended TO auth_svc;

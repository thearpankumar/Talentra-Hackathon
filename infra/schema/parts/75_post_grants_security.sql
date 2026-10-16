-- Grants for the security helper functions (60_security.sql). Triggers run with the invoker's rights, so the roles that
-- write user_credential need INSERT on user_credential_history (auth_svc has it by the RoleMatrix).
REVOKE EXECUTE ON FUNCTION fn_audit_append FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_audit_append TO audit_writer;
-- @@
REVOKE EXECUTE ON FUNCTION fn_audit_verify_chain FROM PUBLIC;
-- @@
GRANT EXECUTE ON FUNCTION fn_audit_verify_chain TO audit_writer;

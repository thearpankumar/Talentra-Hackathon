"""RBAC grants: the live grants equal the matrix-derived expectation (docs/13 RoleMatrix + Grants GR-08..GR-25),
role/membership graph, default deny, DDL denial and sampled positive/negative probes per role (SET ROLE, SQLSTATE 42501)."""
import random

import psycopg
import pytest

import helpers_security as hs
from conftest import connect

ROLES = hs.ALL_ROLES
CRED_TABLES = ["user_credential", "mfa_factor", "mfa_recovery_code", "password_reset_token", "email_verification_token",
               "identity_session", "api_key", "user_credential_history", "device_trust_challenge"]
GR25_CHANGEFEED = ["audit_event", "wallet_ledger_entry", "notification", "outreach_message"]


@pytest.fixture(scope="module")
def live(admin):
    """{grantee: {relation: {privs}}} for relations in schema public, from the live catalog."""
    out = {}
    for g, t, p in admin.execute(
            "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_schema = 'public'").fetchall():
        out.setdefault(g, {}).setdefault(t, set()).add(p)
    return out


@pytest.fixture(scope="module")
def relkind(admin):
    return {t: k for t, k in admin.execute(
        "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema='public'").fetchall()}


def expected_for(role):
    e = {t: set(p) for t, p in hs.expected_table_grants(role).items()}
    if role == "etl_service":  # GR-25: changefeed sources need SELECT + CHANGEFEED
        for t in GR25_CHANGEFEED:
            e.setdefault(t, set()).update({"SELECT", "CHANGEFEED"})
    return e


# ---------------------------------------------------------------- (a) grants == matrix
@pytest.mark.parametrize("role", ROLES)
def test_table_grants_equal_matrix(live, relkind, role):
    exp = expected_for(role)
    got = {t: p for t, p in live.get(role, {}).items() if relkind.get(t) == "BASE TABLE"}
    missing = {t: sorted(exp[t] - got.get(t, set())) for t in exp if exp[t] - got.get(t, set())}
    extra = {t: sorted(got[t] - exp.get(t, set())) for t in got if got[t] - exp.get(t, set())}
    assert not missing, f"{role}: grants missing vs matrix: {dict(list(missing.items())[:8])} (n={len(missing)})"
    assert not extra, f"{role}: grants beyond the matrix: {dict(list(extra.items())[:8])} (n={len(extra)})"


@pytest.mark.parametrize("role", ROLES)
def test_view_grants_within_matrix(live, relkind, role):
    allowed = hs.allowed_views(role)
    views = {t: p for t, p in live.get(role, {}).items() if relkind.get(t) == "VIEW"}
    bad = {v: sorted(p) for v, p in views.items() if v not in allowed or p != {"SELECT"}}
    assert not bad, f"{role}: view grants outside the matrix V cells / non-SELECT: {dict(list(bad.items())[:8])}"
    # every V cell that has at least one existing view must be reachable
    for t in hs.view_cells(role):
        cand = {v.strip() for v in str(hs.role_matrix()[t].get("views_over_table") or "").split(",") if v.strip()}
        cand |= {f"vw_{t}_masked", f"vw_{t}_visible", f"vw_etl_{t}"}
        existing = {v for v in cand if relkind.get(v) == "VIEW"}
        if existing:
            assert existing & set(views), f"{role}: V cell on {t} but no SELECT on any of {sorted(existing)}"


def test_no_grants_to_public(admin):
    rows = admin.execute("SELECT count(*) FROM information_schema.role_table_grants "
                         "WHERE table_schema='public' AND grantee = 'public'").fetchone()
    assert rows[0] == 0


def test_default_privileges_deny_public(admin):
    """GR-06: ALTER DEFAULT PRIVILEGES ... REVOKE ALL ... FROM PUBLIC leaves no default grant for PUBLIC on tables."""
    rows = admin.execute("SHOW DEFAULT PRIVILEGES").fetchall()
    for r in rows:
        txt = " ".join(str(x) for x in r).lower()
        assert not ("public" in txt and "table" in txt and "select" in txt and "revoke" not in txt), r
    # a freshly created table as schema_owner has no PUBLIC privilege
    admin.execute("SET ROLE schema_owner")
    try:
        admin.execute("CREATE TABLE IF NOT EXISTS zz_sec_default_probe (id INT PRIMARY KEY)")
        g = admin.execute("SELECT grantee FROM information_schema.role_table_grants WHERE table_name='zz_sec_default_probe'").fetchall()
        assert {x[0] for x in g} <= {"schema_owner", "root", "admin"}, g
    finally:
        admin.execute("DROP TABLE IF EXISTS zz_sec_default_probe")
        admin.execute("RESET ROLE")


# ---------------------------------------------------------------- GR-14..GR-25 rules (independent of the cell parser)
def test_append_only_tables_insert_select_only(live):
    ao = set(hs.tables_with_policy("append_only"))
    assert len(ao) == 125
    bad = [(r, t, sorted(p)) for r in ROLES for t, p in live.get(r, {}).items() if t in ao and p - {"SELECT", "INSERT"}]
    assert not bad, bad[:5]


@pytest.mark.parametrize("cls,owner", [("GC-AUTH", "auth_svc"), ("GC-SESSION", "auth_svc")])
def test_credential_tables_only_auth_svc(live, cls, owner):
    tabs = set(hs.tables_of_class(cls))
    assert tabs
    for r in ROLES:
        if r == owner:
            continue
        held = {t: p for t, p in live.get(r, {}).items() if t in tabs}
        assert not held, f"{r} holds privileges on {cls} tables: {list(held)[:5]}"


def test_named_credential_tables_closed_to_everybody_but_auth_svc(live):
    for r in ROLES:
        if r == "auth_svc":
            continue
        for t in CRED_TABLES:
            assert not live.get(r, {}).get(t), f"{r} has {live[r][t]} on {t}"
    assert live["auth_svc"]["user_credential"] == {"SELECT", "INSERT", "UPDATE"}
    assert live["auth_svc"]["user_credential_history"] == {"SELECT", "INSERT"}


def test_money_tables_only_billing_svc_writes(live):
    money = set(hs.tables_of_class("GC-MONEY", "GC-MONEY-APPEND"))
    assert len(money) == 52
    for r in ROLES:
        if r == "billing_svc":
            continue
        for t in money:
            assert not (live.get(r, {}).get(t, set()) & {"INSERT", "UPDATE", "DELETE"}), f"{r} writes money table {t}"
    assert live["billing_svc"]["wallet"] == {"SELECT", "INSERT", "UPDATE"}
    assert live["app_service"]["wallet"] == {"SELECT"}                    # AT-53
    assert live["billing_svc"]["wallet_ledger_entry"] == {"SELECT", "INSERT"}  # BR-SUB-07


def test_compliance_vault_only_compliance_svc_reads(live):
    vault = set(hs.tables_of_class("GC-COMPLIANCE"))
    assert len(vault) == 8
    for r in ROLES:
        for t in vault:
            p = live.get(r, {}).get(t, set())
            if r == "compliance_svc":
                assert {"SELECT", "INSERT", "UPDATE"} <= p
            elif r == "app_service":
                assert p == {"INSERT"}, f"app_service on vault {t}: {p}"       # GR-19 inbox
            elif r == "purge_svc":
                assert "UPDATE" not in p and "INSERT" not in p
            else:
                assert "SELECT" not in p and not p, f"{r} reads vault {t}"


def test_app_service_has_nothing_on_credentials(live):
    cred = set(CRED_TABLES) | set(hs.tables_of_class("GC-AUTH", "GC-SESSION"))
    assert not {t for t in live.get("app_service", {}) if t in cred}


def test_audit_reader_only_through_redacted_view(live, relkind):
    g = live["audit_reader"]
    assert "audit_event" not in g and "audit_chain_head" not in g
    assert g.get("vw_audit_event_redacted") == {"SELECT"} or relkind.get("vw_audit_event_redacted") is None
    assert not any(p - {"SELECT"} for p in g.values())


def test_audit_writer_privileges_gr15(live):
    g = live["audit_writer"]
    assert g["audit_event"] == {"SELECT", "INSERT"}
    assert g["audit_chain_checkpoint"] == {"SELECT", "INSERT"}
    assert g["audit_chain_head"] == {"SELECT", "UPDATE"}
    for r in ("app_service", "audit_writer"):
        for t in ("audit_event", "user_consent", "user_credential_history", "audit_chain_checkpoint"):
            assert not (live.get(r, {}).get(t, set()) & {"UPDATE", "DELETE"})


def test_analytics_reader_has_no_oltp(live, relkind):
    mod = {t: r["module"] for t, r in hs.role_matrix().items()}
    oltp_bad = [t for t, p in live["analytics_reader"].items()
                if relkind.get(t) == "BASE TABLE" and mod.get(t) not in ("analytics", "master_data", "tier3_reference")
                and hs.role_matrix()[t]["kind"] != "lookup"]
    assert not oltp_bad, oltp_bad[:10]
    assert all(p == {"SELECT"} for p in live["analytics_reader"].values())


def test_persona_pools_never_hold_tablewide_grant_on_personal_tables(live):
    """Validation sheet: 'No persona pool role holds a table-wide grant (G) on a table with Personal/Sensitive columns'."""
    pers = {r["table"] for r in hs.sensitive_columns() if r["class"] in ("Personal", "Sensitive")}
    for role in hs.PERSONA.values():
        bad = sorted(t for t in live.get(role, {}) if t in pers)
        # the dictionary's matrix gives persona pools G on a few personal tables owned by the persona (scoped by the app);
        # the documented rule is checked against the matrix cells: no G cell at all
        expected_bad = sorted(t for t in pers if hs.table_privs(t, hs.ROLE_ACTOR[role]))
        assert bad == expected_bad
        assert not expected_bad, f"{role}: matrix gives table-wide G on personal tables {expected_bad[:5]}"


def test_etl_service_closed_on_auth_vault_message(live):
    closed = set(hs.tables_of_class("GC-AUTH", "GC-SESSION", "GC-COMPLIANCE", "GC-VAULT")) | {"message", "message_attachment"}
    assert not {t for t in live["etl_service"] if t in closed}


def test_changefeed_grants_gr25(admin):
    rows = admin.execute("SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                         "WHERE grantee='etl_service' AND privilege_type='CHANGEFEED'").fetchall()
    assert {t for t, _ in rows} == set(GR25_CHANGEFEED)


def test_purge_svc_grants_gr24(live):
    g = live["purge_svc"]
    assert {"SELECT"} <= g["data_retention_policy"] and {"SELECT"} <= g["legal_hold"]
    assert g["purge_job_run"] >= {"SELECT", "INSERT"}
    for t in ("data_subject_request", "candidate_erasure_request", "app_user"):
        assert {"SELECT", "UPDATE"} <= g[t], t
    ao = set(hs.tables_with_policy("append_only"))
    assert not any(p & {"UPDATE", "DELETE"} for t, p in g.items() if t in ao)


def test_backup_operator_has_database_privileges_only(admin, live):
    rows = admin.execute("SHOW GRANTS ON DATABASE " + admin.info.dbname + " FOR backup_operator").fetchall()
    privs = {r[3] if len(r) > 3 else r[-1] for r in rows}
    flat = " ".join(str(x) for r in rows for x in r).upper()
    assert "BACKUP" in flat and "RESTORE" in flat, rows
    assert not live.get("backup_operator"), "backup_operator must hold no table grants"


# ---------------------------------------------------------------- roles, logins, membership
def test_group_roles_nologin_and_owner_role(admin):
    rows = {r[0]: r[1] for r in admin.execute("SELECT username, options FROM [SHOW ROLES]").fetchall()}
    for r in hs.GROUP_ROLES:
        assert r in rows, r
        assert "NOLOGIN" in str(rows[r]).upper(), f"{r}: {rows[r]}"


def test_logins_have_no_password_and_login_allowed(admin):
    for u in hs.LOGIN_MEMBERSHIP:
        row = admin.execute("SELECT options FROM [SHOW ROLES] WHERE username = %s", (u,)).fetchone()
        assert row is not None, u
        opts = str(row[0]).upper()
        assert "NOLOGIN" not in opts
        # password-less: no VALID UNTIL/password hash exposed; roles table has hashedPassword NULL
        h = admin.execute("SELECT \"hashedPassword\" FROM system.users WHERE username = %s", (u,)).fetchone()
        assert h is None or not h[0], f"{u} has a password"


def test_membership_graph_equals_docs(admin):
    rows = admin.execute("SELECT role, member FROM system.role_members").fetchall()
    graph = {}
    for role, member in rows:
        graph.setdefault(member, set()).add(role)
    for login, groups in hs.LOGIN_MEMBERSHIP.items():
        assert graph.get(login, set()) == groups, (login, graph.get(login))
    for svc in hs.AUDIT_WRITER_MEMBERS:
        assert "audit_writer" in graph.get(svc, set()), svc
    members_of_audit_writer = {m for r, m in rows if r == "audit_writer"}
    assert hs.AUDIT_WRITER_MEMBERS <= members_of_audit_writer
    extra = members_of_audit_writer - hs.AUDIT_WRITER_MEMBERS - {"root", "admin"}
    assert not extra, extra


def test_schema_owner_membership_empty_outside_window(admin):
    rows = admin.execute("SELECT member FROM system.role_members WHERE role = 'schema_owner'").fetchall()
    assert not [m for (m,) in rows if m not in ("root", "admin")], rows
    for login in ("svc_migrate", "svc_worker", "svc_auth"):
        assert not admin.execute("SELECT 1 FROM system.role_members WHERE role='schema_owner' AND member=%s", (login,)).fetchall()


def test_no_application_role_is_cluster_admin(admin):
    rows = admin.execute("SELECT member FROM system.role_members WHERE role = 'admin'").fetchall()
    app = set(hs.GROUP_ROLES) | set(hs.LOGIN_MEMBERSHIP)
    assert not [m for (m,) in rows if m in app], rows


def test_resource_guards_gr29(admin):
    exp = {"app_guest": "5s", "app_candidate": "10s", "app_recruiter": "10s", "app_staff_admin": "30s"}
    for role, t in exp.items():
        conn = connect(role=role)
        try:
            v = conn.execute("SHOW statement_timeout").fetchone()[0]
        finally:
            conn.close()
        assert v in (t, t.replace("s", "000ms"), f"{int(t[:-1]) * 1000}"), (role, v)


# ---------------------------------------------------------------- DDL / GRANT denied for every role
DDL = ["DROP TABLE app_user", "ALTER TABLE app_user ADD COLUMN zz_x INT", "TRUNCATE TABLE audit_event",
       "GRANT SELECT ON TABLE user_credential TO PUBLIC", "CREATE TABLE zz_secdenied (id INT)",
       "CREATE ROLE zz_secdenied", "GRANT schema_owner TO CURRENT_USER", "ALTER TABLE app_user OWNER TO CURRENT_USER",
       "DROP DATABASE defaultdb"]


@pytest.mark.parametrize("role", ROLES)
def test_no_role_can_ddl_or_grant(role):
    c = connect(role=role)
    try:
        for stmt in DDL:
            with pytest.raises(psycopg.Error) as ei:
                c.execute(stmt)
            assert ei.value.sqlstate in ("42501", "25006", "42P01"), (role, stmt, ei.value.sqlstate, str(ei.value)[:100])
    finally:
        c.close()


# ---------------------------------------------------------------- sampled allowed / forbidden operations per role
PROBE = {
    "SELECT": "SELECT 1 FROM {t} LIMIT 0",
    "INSERT": "INSERT INTO {t} DEFAULT VALUES",
    "UPDATE": "UPDATE {t} SET {c} = {c} WHERE false",
    "DELETE": "DELETE FROM {t} WHERE false",
}
ALLOWED_ERR = {"23502", "23503", "23514", "23505", "22P02", "25006", "55000"}  # past the privilege check


@pytest.fixture(scope="module")
def first_col(catalog):
    return {t: cs[0]["name"] for t, cs in catalog["columns"].items()}


def probe(conn, op, t, col):
    """run op inside a rolled back txn; returns None when permitted (or failed past the privilege check), else sqlstate"""
    conn.execute("BEGIN")
    try:
        conn.execute(PROBE[op].format(t=t, c=col))
        return None
    except psycopg.Error as e:
        return None if e.sqlstate in ALLOWED_ERR else e.sqlstate
    finally:
        conn.execute("ROLLBACK")


@pytest.mark.parametrize("role", ROLES)
def test_sampled_allowed_and_forbidden_ops(role, first_col, relkind):
    rnd = random.Random(role)
    exp = expected_for(role)
    ops = [(t, op) for t, ps in sorted(exp.items()) for op in sorted(ps) if op in PROBE]
    alltabs = sorted(t for t in hs.role_matrix())
    forb = [(t, op) for t in alltabs for op in PROBE if op not in exp.get(t, set())]
    # always include the interesting negatives: append-only update/delete, credentials, money, vault
    must = [("user_credential", "SELECT"), ("wallet", "UPDATE"), ("audit_event", "DELETE"), ("audit_event", "UPDATE"),
            ("candidate_diversity_declaration", "SELECT"), ("wallet_ledger_entry", "DELETE")]
    must = [m for m in must if m in set(forb)]
    allowed_s = rnd.sample(ops, min(6, len(ops)))
    forb_s = must[:3] + rnd.sample(forb, 6)
    conn = connect(role=role)
    try:
        for t, op in allowed_s:
            assert probe(conn, op, t, first_col[t]) is None, f"{role} should be allowed {op} on {t}"
        for t, op in forb_s:
            code = probe(conn, op, t, first_col[t])
            assert code in ("42501", "25006"), f"{role} must be denied {op} on {t}, got {code}"
    finally:
        conn.close()

"""Business-rule enforcement for modules licences_devices, organisation_account, administration (docs/06 rules BR-LIC/ORG/ADM/LGS).

Part A verifies the declarative rules on the live DB (negative tests: violating row rejected with the right SQLSTATE and constraint,
valid row accepted). Part B tests the procedures of infra/schema/parts/60_rules_lic.sql with real concurrency (threads, separate
connections, SET ROLE to the service role that is supposed to call them). SERIALIZABLE: every txn goes through conftest.retry().
Run: cd tests && TALENTRA_DB=ts_rules .venv/Scripts/python -m pytest test_rules_lic.py -q
"""
import datetime
import uuid

import psycopg
import pytest

from helpers_lic import ACT, Conn, policy_user, txn, assign_seat, fp, login, mk_account, mk_licence, mk_role, mk_seat, mk_user, run_parallel
from helpers_rowbuilder import build_row, lookup_id


@pytest.fixture(scope="module")
def adm():
    c = Conn()
    yield c
    c.close()


def violates(conn, sql, params, sqlstate, name):
    """statement must fail with sqlstate and mention the constraint/index name"""
    with pytest.raises(psycopg.Error) as ei:
        conn.execute(sql, params)
    e = ei.value
    assert e.sqlstate == sqlstate, f"{e.sqlstate} != {sqlstate}: {e}"
    assert name in str(e) or name == (e.diag.constraint_name or ""), f"{name!r} not in {e}"


def rowinsert(conn, table, **ov):
    return build_row(conn, table, **ov)


def now_db(conn):
    return conn.execute("SELECT now()").fetchone()[0]


# ----------------------------------------------------------------------------------------------- A. declarative rules
class TestDeclarative:
    def test_lic01_max_users_and_devices_positive(self, adm):
        acct = mk_account(adm)
        for kw, name in (({"max_users": 0}, "ck_licence_max_users_positive"), ({"max_devices_per_user": 0}, "ck_licence_max_devices_positive"),
                         ({"max_devices_per_user": 101}, "ck_licence_max_devices_positive")):
            with pytest.raises(psycopg.errors.CheckViolation) as ei:
                build_row(adm, "licence", account_id=acct, licence_status_id=lookup_id(adm, "licence_status", "active"), max_users=kw.get("max_users", 1),
                          max_devices_per_user=kw.get("max_devices_per_user", 1), seats_in_use=0)
            assert name == ei.value.diag.constraint_name, ei.value.diag.constraint_name
        ok = build_row(adm, "licence", account_id=acct, max_users=1, max_devices_per_user=100, seats_in_use=0)
        assert ok["max_devices_per_user"] == 100

    def test_lic02_04_one_live_seat_per_user_and_slot(self, adm):
        lic = mk_licence(adm, max_users=3)
        u1, u2 = mk_user(adm), mk_user(adm)
        slots = [r[0] for r in adm.execute("SELECT id FROM licence_seat_slot WHERE licence_id=%s ORDER BY slot_no", (lic,)).fetchall()]
        st = lookup_id(adm, "seat_status", "active")
        base = dict(licence_id=lic, seat_status_id=st)
        build_row(adm, "licence_seat", licence_seat_slot_id=slots[0], app_user_id=u1, **base)
        # same user, other slot -> uq_licence_seat_licence_id_app_user_id
        with pytest.raises(psycopg.errors.UniqueViolation) as e1:
            build_row(adm, "licence_seat", licence_seat_slot_id=slots[1], app_user_id=u1, **base)
        assert "uq_licence_seat_licence_id_app_user_id" in str(e1.value)
        # other user, same slot -> uq_licence_seat_licence_seat_slot_id
        with pytest.raises(psycopg.errors.UniqueViolation) as e2:
            build_row(adm, "licence_seat", licence_seat_slot_id=slots[0], app_user_id=u2, **base)
        assert "uq_licence_seat_licence_seat_slot_id" in str(e2.value)
        build_row(adm, "licence_seat", licence_seat_slot_id=slots[1], app_user_id=u2, **base)

    def test_lic02_slot_must_belong_to_same_licence(self, adm):
        la, lb = mk_licence(adm, 2), mk_licence(adm, 2)
        slot_b = adm.execute("SELECT id FROM licence_seat_slot WHERE licence_id=%s LIMIT 1", (lb,)).fetchone()[0]
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as e:
            build_row(adm, "licence_seat", licence_id=la, licence_seat_slot_id=slot_b, app_user_id=mk_user(adm),
                      seat_status_id=lookup_id(adm, "seat_status", "active"))
        assert "fk_licence_seat_licence_seat_slot_id_licence_id" in str(e.value)

    def test_lic05_11_device_session_chain_fks_and_uniques(self, adm):
        lic = mk_licence(adm, 2, 2)
        u, other = mk_user(adm), mk_user(adm)
        seat = mk_seat(adm, lic, u)
        slots = [r[0] for r in adm.execute("SELECT id FROM device_slot WHERE licence_seat_id=%s ORDER BY slot_no", (seat,)).fetchall()]
        d1 = build_row(adm, "device", app_user_id=u)["id"]
        d2 = build_row(adm, "device", app_user_id=u)["id"]
        d_other = build_row(adm, "device", app_user_id=other)["id"]
        am = lookup_id(adm, "authentication_method")
        base = dict(app_user_id=u, licence_seat_id=seat, authentication_method_id=am, identity_session_id=None)
        s1 = build_row(adm, "device_session", device_id=d1, device_slot_id=slots[0], expires_at=now_db(adm) + datetime.timedelta(hours=1), **base)
        # same slot twice while live
        violates_ctx = pytest.raises(psycopg.errors.UniqueViolation)
        with violates_ctx as e:
            build_row(adm, "device_session", device_id=d2, device_slot_id=slots[0], expires_at=now_db(adm) + datetime.timedelta(hours=1), **base)
        assert "uq_device_session_device_slot_id" in str(e.value)
        # same device twice while live
        with pytest.raises(psycopg.errors.UniqueViolation) as e:
            build_row(adm, "device_session", device_id=d1, device_slot_id=slots[1], expires_at=now_db(adm) + datetime.timedelta(hours=1), **base)
        assert "uq_device_session_device_id" in str(e.value)
        # device of another user -> composite FK (device_id, app_user_id)
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as e:
            build_row(adm, "device_session", device_id=d_other, device_slot_id=slots[1], expires_at=now_db(adm) + datetime.timedelta(hours=1), **base)
        assert "fk_device_session_device_id_app_user_id" in str(e.value)
        # session user must equal seat user
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as e:
            build_row(adm, "device_session", device_id=d_other, device_slot_id=slots[1], expires_at=now_db(adm) + datetime.timedelta(hours=1),
                      **{**base, "app_user_id": other})
        assert "fk_device_session_licence_seat_id_app_user_id" in str(e.value)
        # slot of another seat -> composite FK (licence_seat_id, device_slot_id)
        lic2 = mk_licence(adm, 1, 1)
        seat2 = mk_seat(adm, lic2, mk_user(adm))
        slot2 = adm.execute("SELECT id FROM device_slot WHERE licence_seat_id=%s", (seat2,)).fetchone()[0]
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as e:
            build_row(adm, "device_session", device_id=d2, device_slot_id=slot2, expires_at=now_db(adm) + datetime.timedelta(hours=1), **base)
        assert "fk_device_session_device_slot_id_licence_seat_id" in str(e.value)
        # ended session frees slot + device for a new live row; reason and ended_at travel together
        with pytest.raises(psycopg.errors.CheckViolation):
            adm.execute("UPDATE device_session SET ended_at = now() WHERE id=%s", (s1["id"],))
        adm.execute("UPDATE device_session SET ended_at = now(), session_end_reason_id=%s WHERE id=%s",
                    (lookup_id(adm, "session_end_reason", "logout"), s1["id"]))
        build_row(adm, "device_session", device_id=d1, device_slot_id=slots[0], expires_at=now_db(adm) + datetime.timedelta(hours=1), **base)

    def test_lic07_violation_count_check(self, adm):
        lic = mk_licence(adm, 1, 3)
        u = mk_user(adm)
        seat = mk_seat(adm, lic, u)
        pol = lookup_id(adm, "device_limit_policy", "deny_new")
        kw = dict(licence_seat_id=seat, app_user_id=u, attempted_fingerprint_hash=fp(), max_devices_allowed=3, device_limit_policy_id=pol)
        with pytest.raises(psycopg.errors.CheckViolation) as e:
            build_row(adm, "device_limit_violation", active_session_count=2, **kw)
        assert "ck_device_limit_violation_count_at_limit" in str(e.value) or "active_session_count" in str(e.value)
        build_row(adm, "device_limit_violation", active_session_count=3, **kw)

    def test_adm02_email_unique_among_live_accounts(self, adm):
        email = f"dup{uuid.uuid4().hex[:10]}@example.test"
        a = mk_user(adm, email=email)
        with pytest.raises(psycopg.errors.UniqueViolation) as e:
            mk_user(adm, email=email)
        assert "uq_app_user_email_live" in str(e.value)
        with pytest.raises(psycopg.Error) as e:
            mk_user(adm, email="UPPER" + uuid.uuid4().hex[:6] + "@example.test")
        assert e.value.sqlstate == "23514"
        # after soft delete the address is free again
        adm.execute("UPDATE app_user SET deleted_at = now(), deleted_by=%s WHERE id=%s", (ACT(), a))
        mk_user(adm, email=email)

    def test_adm03_one_active_credential(self, adm):
        u = mk_user(adm)
        argon = dict(password_hash="$argon2id$v=19$m=19456,t=2,p=1$c2FsdHNhbHRzYWx0$aGFzaGhhc2hoYXNoaGFzaA",
                     hash_algorithm_id=lookup_id(adm, "hash_algorithm", "argon2id"), argon2_memory_kib=19456, argon2_iterations=2,
                     argon2_parallelism=1, pepper_version=1)
        build_row(adm, "user_credential", app_user_id=u, **argon)
        with pytest.raises(psycopg.errors.UniqueViolation) as e:
            build_row(adm, "user_credential", app_user_id=u, **argon)
        assert "uq_user_credential_app_user_id_active" in str(e.value)
        # change = deactivate old + insert new in one txn
        with adm.transaction():
            adm.execute("UPDATE user_credential SET is_active=false, deactivated_at=now() WHERE app_user_id=%s AND is_active", (u,))
            build_row(adm, "user_credential", app_user_id=u, **argon)
        assert adm.execute("SELECT count(*) FROM user_credential WHERE app_user_id=%s AND is_active", (u,)).fetchone()[0] == 1

    def test_adm01_registration_policy_candidate_cannot_be_registered_by_recruiter(self, adm):
        cand, rec = lookup_id(adm, "actor_type", "candidate"), lookup_id(adm, "actor_type", "recruiter")
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as e:
            mk_user(adm, actor_type_id=cand, registrar_actor_type_id=rec)
        assert "registration_policy" in str(e.value) or "fk_app_user" in str(e.value)
        # a permitted combo is accepted: any seeded policy row
        pol = adm.execute("SELECT actor_type_id, registrar_actor_type_id, registration_channel_id FROM registration_policy LIMIT 1").fetchone()
        assert pol is not None
        mk_user(adm, actor_type_id=pol[0], registrar_actor_type_id=pol[1], registration_channel_id=pol[2])

    def test_adm06_role_only_for_eligible_actor_types(self, adm):
        cand_user = policy_user(adm, "candidate")
        if cand_user is None:
            pytest.skip("no candidate registration policy")
        cand_type = adm.execute("SELECT actor_type_id FROM app_user WHERE id=%s", (cand_user,)).fetchone()[0]
        admin_type = lookup_id(adm, "actor_type", "admin")
        role = mk_role(adm, is_organisation_scoped=False)
        build_row(adm, "app_role_eligibility", app_role_id=role, actor_type_id=admin_type)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            build_row(adm, "app_user_role", app_user_id=cand_user, app_role_id=role, organisation_id=None, actor_type_id=cand_type)
        build_row(adm, "app_role_eligibility", app_role_id=role, actor_type_id=cand_type)
        build_row(adm, "app_user_role", app_user_id=cand_user, app_role_id=role, organisation_id=None, actor_type_id=cand_type)
        # open duplicate grant of the same platform role is rejected (BR-ADM-05)
        with pytest.raises(psycopg.errors.UniqueViolation):
            build_row(adm, "app_user_role", app_user_id=cand_user, app_role_id=role, organisation_id=None, actor_type_id=cand_type)

    def test_adm07_role_inheritance_self_and_duplicate(self, adm):
        r1, r2 = mk_role(adm), mk_role(adm)
        with pytest.raises(psycopg.errors.CheckViolation):
            build_row(adm, "app_role_inheritance", child_app_role_id=r1, parent_app_role_id=r1)
        build_row(adm, "app_role_inheritance", child_app_role_id=r1, parent_app_role_id=r2)
        with pytest.raises(psycopg.errors.UniqueViolation) as e:
            build_row(adm, "app_role_inheritance", child_app_role_id=r1, parent_app_role_id=r2)
        assert "uq_app_role_inheritance_pair" in str(e.value)

    def test_org01_02_recruiter_mapping_open_unique_and_chain(self, adm):
        org, rec = build_row(adm, "organisation")["id"], mk_user(adm)
        m1 = build_row(adm, "recruiter_organisation_mapping", recruiter_app_user_id=rec, organisation_id=org, sequence_no=1)
        # second open mapping for the same pair
        with pytest.raises(psycopg.errors.UniqueViolation) as e:
            build_row(adm, "recruiter_organisation_mapping", recruiter_app_user_id=rec, organisation_id=org, sequence_no=2,
                      prior_recruiter_organisation_mapping_id=m1["id"], prior_ended_at=now_db(adm))
        assert "recruiter_organisation_mapping" in str(e.value)
        # a second head (prior NULL) is rejected too (chain has one head)
        adm.execute("UPDATE recruiter_organisation_mapping SET effective_to=now()+INTERVAL '1 second', mapping_end_reason_id=%s WHERE id=%s",
                    (lookup_id(adm, "mapping_end_reason"), m1["id"]))
        with pytest.raises(psycopg.errors.UniqueViolation):
            build_row(adm, "recruiter_organisation_mapping", recruiter_app_user_id=rec, organisation_id=org, sequence_no=1)
        # successor starting before the prior ended -> overlap rejected
        with pytest.raises(psycopg.errors.CheckViolation):
            build_row(adm, "recruiter_organisation_mapping", recruiter_app_user_id=rec, organisation_id=org, sequence_no=2,
                      prior_recruiter_organisation_mapping_id=m1["id"], prior_ended_at=m1["effective_to"] or now_db(adm) + datetime.timedelta(seconds=1),
                      effective_from=now_db(adm) - datetime.timedelta(days=1))

    def test_org06_activated_account_needs_admin(self, adm):
        acc = {"id": mk_account(adm)}
        with pytest.raises(psycopg.errors.CheckViolation) as e:
            adm.execute("UPDATE account SET activated_at = now() WHERE id=%s", (acc["id"],))
        assert "ck_account_operational_needs_admin" in str(e.value) or "active_admin_count" in str(e.value)
        adm.execute("UPDATE account SET activated_at = now(), active_admin_count = 1 WHERE id=%s", (acc["id"],))

    def test_org07_08_account_admin_and_account_organisation_open_uniques(self, adm):
        acc = mk_account(adm)
        u = mk_user(adm)
        lvl = lookup_id(adm, "account_admin_level")
        build_row(adm, "account_admin", account_id=acc, admin_app_user_id=u, account_admin_level_id=lvl, is_primary_contact=True)
        with pytest.raises(psycopg.errors.UniqueViolation):
            build_row(adm, "account_admin", account_id=acc, admin_app_user_id=u, account_admin_level_id=lvl)
        with pytest.raises(psycopg.errors.UniqueViolation):
            build_row(adm, "account_admin", account_id=acc, admin_app_user_id=mk_user(adm), account_admin_level_id=lvl, is_primary_contact=True)
        org = build_row(adm, "organisation")["id"]
        build_row(adm, "account_organisation", account_id=acc, organisation_id=org)
        with pytest.raises(psycopg.errors.UniqueViolation):
            build_row(adm, "account_organisation", account_id=mk_account(adm), organisation_id=org)

    def test_org12_slug_url_safe_and_unique_live(self, adm):
        slug = "s" + uuid.uuid4().hex[:10]
        build_row(adm, "organisation", slug=slug)
        with pytest.raises(psycopg.errors.UniqueViolation):
            build_row(adm, "organisation", slug=slug)
        with pytest.raises(psycopg.errors.CheckViolation):
            build_row(adm, "organisation", slug="Bad_Slug!")

    def test_lgs02_one_open_sync_run_per_source_kind(self, adm):
        src = build_row(adm, "legacy_source_system")["id"]
        kind = lookup_id(adm, "legacy_entity_kind")
        build_row(adm, "legacy_sync_run", legacy_source_system_id=src, legacy_entity_kind_id=kind, is_active_run=True, finished_at=None)
        with pytest.raises(psycopg.errors.UniqueViolation):
            build_row(adm, "legacy_sync_run", legacy_source_system_id=src, legacy_entity_kind_id=kind, is_active_run=True, finished_at=None)
        with pytest.raises(psycopg.errors.CheckViolation):
            build_row(adm, "legacy_sync_run", legacy_source_system_id=src, legacy_entity_kind_id=kind, is_active_run=True,
                      finished_at=now_db(adm) + datetime.timedelta(hours=1))

    def test_adm09_audit_chain_constraints(self, adm):
        # first chain row needs prev_hash NULL; hash length 32
        with pytest.raises(psycopg.errors.CheckViolation):
            adm.execute("INSERT INTO audit_event (created_by, actor_type_id, audit_action_id, audit_entity_type_id, audit_outcome_id, request_ref, chain_no, chain_seq, prev_hash, row_hash)"
                        " SELECT %s, (SELECT id FROM actor_type LIMIT 1), (SELECT id FROM audit_action LIMIT 1), (SELECT id FROM audit_entity_type LIMIT 1),"
                        " (SELECT id FROM audit_outcome LIMIT 1), gen_random_uuid(), 0, 1, '\\x00'::BYTES, '\\x01'::BYTES", (ACT(),))


# ----------------------------------------------------------------------------------------------- B. procedures
SVC_RETRY_FIXTURE_NOTE = "auth_svc / app_service connections via as_role"


def conn_as(role):
    return Conn(role)


def tx(conn, sql, params=()):
    def go():
        with conn.transaction():
            cur = conn.execute(sql, params)
            return cur.fetchall() if cur.description else None
    return txn(conn, go)


class TestLoginDeviceLimit:
    def test_lic06_07_parallel_logins_exactly_limit_and_denials_audited(self, adm):
        lic = mk_licence(adm, max_users=2, max_devices=3)
        user = mk_user(adm)
        mk_seat(adm, lic, user)
        n = 12

        def one(i):
            c = conn_as("auth_svc")
            try:
                return login(c, user, lic, fp())
            finally:
                c.close()
        res = run_parallel(n, one)
        outcomes = [r["o_outcome"] for r in res]
        assert outcomes.count("success") == 3, outcomes
        assert outcomes.count("device_limit_denied") == 9, outcomes
        seat = adm.execute("SELECT id FROM licence_seat WHERE licence_id=%s AND app_user_id=%s", (lic, user)).fetchone()[0]
        assert adm.execute("SELECT count(*) FROM device_session WHERE licence_seat_id=%s AND ended_at IS NULL", (seat,)).fetchone()[0] == 3
        assert adm.execute("SELECT count(DISTINCT device_slot_id) FROM device_session WHERE licence_seat_id=%s AND ended_at IS NULL", (seat,)).fetchone()[0] == 3
        assert adm.execute("SELECT count(*) FROM device_limit_violation WHERE licence_seat_id=%s", (seat,)).fetchone()[0] == 9
        # each violation links a login_attempt(device_limit_denied) and an audit_event(device_limit_exceeded)
        assert adm.execute(
            "SELECT count(*) FROM device_limit_violation v JOIN login_attempt a ON a.id=v.login_attempt_id JOIN login_outcome o ON o.id=a.login_outcome_id "
            "JOIN audit_event e ON e.id=v.audit_event_id JOIN audit_action x ON x.id=e.audit_action_id "
            "WHERE v.licence_seat_id=%s AND o.code='device_limit_denied' AND x.code='device_limit_exceeded' AND v.active_session_count >= v.max_devices_allowed",
            (seat,)).fetchone()[0] == 9
        assert adm.execute("SELECT count(*) FROM login_attempt WHERE licence_id=%s AND app_user_id=%s", (lic, user)).fetchone()[0] == 12

    def test_lic06_denial_commits_while_no_session_is_created(self, adm):
        lic = mk_licence(adm, 1, 1)
        u = mk_user(adm)
        mk_seat(adm, lic, u)
        c = conn_as("auth_svc")
        a = login(c, u, lic, fp())
        b = login(c, u, lic, fp())
        c.close()
        assert (a["o_outcome"], b["o_outcome"]) == ("success", "device_limit_denied")
        assert b["o_identity_session_id"] is None
        assert adm.execute("SELECT count(*) FROM identity_session WHERE app_user_id=%s", (u,)).fetchone()[0] == 1

    def test_lic05_08_session_end_frees_slot_then_login_succeeds(self, adm):
        lic = mk_licence(adm, 1, 2)
        u = mk_user(adm)
        mk_seat(adm, lic, u)
        c = conn_as("auth_svc")
        l1, l2 = login(c, u, lic, fp()), login(c, u, lic, fp())
        assert login(c, u, lic, fp())["o_outcome"] == "device_limit_denied"
        assert tx(c, "SELECT fn_device_session_end(%s,'logout')", (l1["o_device_session_id"],))[0][0] is True
        assert tx(c, "SELECT fn_device_session_end(%s,'logout')", (l1["o_device_session_id"],))[0][0] is False  # idempotent
        l3 = login(c, u, lic, fp())
        c.close()
        assert l3["o_outcome"] == "success"
        # the new session reuses the slot just released
        assert l3["o_slot_no"] == l1["o_slot_no"]
        assert adm.execute("SELECT session_end_reason_id=%s FROM device_session WHERE id=%s",
                           (lookup_id(adm, "session_end_reason", "logout"), l1["o_device_session_id"])).fetchone()[0]
        assert adm.execute("SELECT revoked_at IS NOT NULL FROM identity_session WHERE id=%s", (l1["o_identity_session_id"],)).fetchone()[0]

    def test_lic06_same_device_relogin_replaces_old_session(self, adm):
        lic = mk_licence(adm, 1, 1)
        u = mk_user(adm)
        mk_seat(adm, lic, u)
        f = fp()
        c = conn_as("auth_svc")
        a, b = login(c, u, lic, f), login(c, u, lic, f)
        c.close()
        assert b["o_outcome"] == "success" and a["o_device_id"] == b["o_device_id"]
        assert adm.execute("SELECT count(*) FROM device_session WHERE licence_seat_id=%s AND ended_at IS NULL", (a["o_seat_id"],)).fetchone()[0] == 1
        assert adm.execute("SELECT r.code FROM device_session d JOIN session_end_reason r ON r.id=d.session_end_reason_id WHERE d.id=%s",
                           (a["o_device_session_id"],)).fetchone()[0] == "replaced"

    def test_lic06_replace_oldest_policy(self, adm):
        lic = mk_licence(adm, 1, 2, policy="replace_oldest")
        u = mk_user(adm)
        mk_seat(adm, lic, u)
        c = conn_as("auth_svc")
        a, b = login(c, u, lic, fp()), login(c, u, lic, fp())
        # make 'a' clearly the oldest
        adm.execute("UPDATE device_session SET last_activity_at = started_at WHERE id=%s", (a["o_device_session_id"],))
        d = login(c, u, lic, fp())
        c.close()
        assert d["o_outcome"] == "success"
        assert adm.execute("SELECT ended_at IS NOT NULL FROM device_session WHERE id=%s", (a["o_device_session_id"],)).fetchone()[0]
        assert adm.execute("SELECT ended_at IS NULL FROM device_session WHERE id=%s", (b["o_device_session_id"],)).fetchone()[0]
        v = adm.execute("SELECT replaced_device_session_id, audit_event_id FROM device_limit_violation WHERE licence_seat_id=%s", (a["o_seat_id"],)).fetchall()
        assert len(v) == 1 and str(v[0][0]) == str(a["o_device_session_id"]) and v[0][1] is not None

    def test_lic06_no_seat_licence_inactive_ip_blocked(self, adm):
        lic = mk_licence(adm, 2, 2)
        u = mk_user(adm)
        c = conn_as("auth_svc")
        assert login(c, u, lic, fp())["o_outcome"] == "no_seat"
        mk_seat(adm, lic, u)
        adm.execute("UPDATE licence SET licence_status_id=%s WHERE id=%s", (lookup_id(adm, "licence_status", "suspended"), lic))
        assert login(c, u, lic, fp())["o_outcome"] == "licence_inactive"
        adm.execute("UPDATE licence SET licence_status_id=%s, valid_to = now() - INTERVAL '1 hour', valid_from = now() - INTERVAL '2 days' WHERE id=%s",
                    (lookup_id(adm, "licence_status", "active"), lic))
        assert login(c, u, lic, fp())["o_outcome"] == "licence_inactive"
        adm.execute("UPDATE licence SET valid_to = NULL, require_ip_allowlist = true WHERE id=%s", (lic,))
        assert login(c, u, lic, fp(), ip="10.9.9.9")["o_outcome"] == "ip_blocked"
        build_row(adm, "licence_ip_allowlist", licence_id=lic, ip_network="10.9.0.0/16")
        assert login(c, u, lic, fp(), ip="10.9.9.9")["o_outcome"] == "success"
        c.close()
        got = {r[0] for r in adm.execute("SELECT o.code FROM login_attempt a JOIN login_outcome o ON o.id=a.login_outcome_id WHERE a.licence_id=%s", (lic,)).fetchall()}
        assert got == {"no_seat", "licence_inactive", "ip_blocked", "success"}

    def test_lic06_idle_sessions_released_lazily_at_login(self, adm):
        lic = mk_licence(adm, 1, 1, idle_min=5)
        u = mk_user(adm)
        mk_seat(adm, lic, u)
        c = conn_as("auth_svc")
        a = login(c, u, lic, fp())
        assert login(c, u, lic, fp())["o_outcome"] == "device_limit_denied"
        adm.execute("UPDATE device_session SET last_activity_at = now() - INTERVAL '10 minutes', started_at = now() - INTERVAL '20 minutes' WHERE id=%s",
                    (a["o_device_session_id"],))
        b = login(c, u, lic, fp())
        c.close()
        assert b["o_outcome"] == "success"
        assert adm.execute("SELECT r.code FROM device_session d JOIN session_end_reason r ON r.id=d.session_end_reason_id WHERE d.id=%s",
                           (a["o_device_session_id"],)).fetchone()[0] == "idle_timeout"

    def test_lic08_expire_sweep_job(self, adm):
        lic = mk_licence(adm, 1, 2, idle_min=5)
        u = mk_user(adm)
        mk_seat(adm, lic, u)
        c = conn_as("auth_svc")
        a, b = login(c, u, lic, fp()), login(c, u, lic, fp())
        adm.execute("UPDATE device_session SET last_activity_at = now() - INTERVAL '30 minutes', started_at = now() - INTERVAL '1 hour' WHERE id=%s",
                    (a["o_device_session_id"],))
        n = tx(c, "SELECT fn_device_session_expire_sweep()")[0][0]
        c.close()
        assert n >= 1
        assert adm.execute("SELECT ended_at IS NOT NULL FROM device_session WHERE id=%s", (a["o_device_session_id"],)).fetchone()[0]
        assert adm.execute("SELECT ended_at IS NULL FROM device_session WHERE id=%s", (b["o_device_session_id"],)).fetchone()[0]

    def test_lic09_device_revoke_is_atomic_and_audited(self, adm):
        lic = mk_licence(adm, 1, 2)
        u = mk_user(adm)
        mk_seat(adm, lic, u)
        c = conn_as("auth_svc")
        f = fp()
        a = login(c, u, lic, f)
        n = tx(c, "SELECT fn_device_revoke(%s,%s,'self_service','lost_device')", (a["o_device_id"], u))[0][0]
        assert n == 1
        assert tx(c, "SELECT fn_device_revoke(%s,%s,'self_service','lost_device')", (a["o_device_id"], u))[0][0] == -1
        assert adm.execute("SELECT count(*) FROM device_revocation r JOIN audit_event e ON e.id=r.audit_event_id WHERE r.device_id=%s AND r.sessions_ended=1",
                           (a["o_device_id"],)).fetchone()[0] == 1
        assert adm.execute("SELECT count(*) FROM session_revocation WHERE device_session_id=%s AND audit_event_id IS NOT NULL", (a["o_device_session_id"],)).fetchone()[0] == 1
        assert login(c, u, lic, f)["o_outcome"] == "device_revoked"
        assert login(c, u, lic, fp())["o_outcome"] == "success"  # another device still works, slot was released
        c.close()

    def test_lic06_service_role_boundaries(self, adm):
        lic = mk_licence(adm, 1, 1)
        u = mk_user(adm)
        for role in ("app_service", "app_recruiter", "app_candidate"):
            c = conn_as(role)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                tx(c, "SELECT * FROM fn_login_claim(%s,%s,%s,'web','10.0.0.1'::INET,'r','t')", (u, lic, fp()))
            c.close()
        c = conn_as("auth_svc")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            tx(c, "SELECT * FROM fn_seat_assign(%s,%s,%s)", (lic, u, ACT()))
        c.close()


class TestSeats:
    def test_lic02_03_parallel_assignment_respects_max_users(self, adm):
        lic = mk_licence(adm, max_users=3, max_devices=2)
        users = [mk_user(adm) for _ in range(8)]

        def one(i):
            c = conn_as("app_service")
            try:
                return assign_seat(c, lic, users[i])
            finally:
                c.close()
        res = run_parallel(8, one)
        outs = [r[0] for r in res]
        assert outs.count("assigned") == 3 and outs.count("seat_limit_reached") == 5, outs
        assert adm.execute("SELECT seats_in_use FROM licence WHERE id=%s", (lic,)).fetchone()[0] == 3
        assert adm.execute("SELECT count(*) FROM licence_seat WHERE licence_id=%s AND revoked_at IS NULL", (lic,)).fetchone()[0] == 3
        assert adm.execute("SELECT count(DISTINCT licence_seat_slot_id) FROM licence_seat WHERE licence_id=%s", (lic,)).fetchone()[0] == 3
        # each seat has device slots 1..max_devices
        assert adm.execute("SELECT min(c), max(c) FROM (SELECT count(*) c FROM device_slot WHERE licence_seat_id IN "
                           "(SELECT id FROM licence_seat WHERE licence_id=%s) GROUP BY licence_seat_id)", (lic,)).fetchone() == (2, 2)
        # rejected assignments leave an audit row
        assert adm.execute("SELECT count(*) FROM audit_event WHERE entity_ref=%s", (lic,)).fetchone()[0] == 5

    def test_lic03_already_seated_and_inactive(self, adm):
        lic = mk_licence(adm, 2, 1)
        u = mk_user(adm)
        c = conn_as("app_service")
        assert assign_seat(c, lic, u)[0] == "assigned"
        assert assign_seat(c, lic, u)[0] == "already_seated"
        adm.execute("UPDATE licence SET licence_status_id=%s WHERE id=%s", (lookup_id(adm, "licence_status", "suspended"), lic))
        assert assign_seat(c, lic, mk_user(adm))[0] == "licence_inactive"
        c.close()

    def test_lic02_revoke_seat_frees_slot_and_ends_sessions(self, adm):
        lic = mk_licence(adm, 1, 1)
        u1, u2 = mk_user(adm), mk_user(adm)
        c = conn_as("app_service")
        s1 = assign_seat(c, lic, u1)[1]
        assert assign_seat(c, lic, u2)[0] == "seat_limit_reached"
        a = conn_as("auth_svc")
        l = login(a, u1, lic, fp())
        assert l["o_outcome"] == "success"
        assert tx(c, "SELECT fn_seat_revoke(%s,%s)", (s1, ACT()))[0][0] is True
        assert tx(c, "SELECT fn_seat_revoke(%s,%s)", (s1, ACT()))[0][0] is False
        assert adm.execute("SELECT ended_at IS NOT NULL FROM device_session WHERE id=%s", (l["o_device_session_id"],)).fetchone()[0]
        assert adm.execute("SELECT seats_in_use FROM licence WHERE id=%s", (lic,)).fetchone()[0] == 0
        assert assign_seat(c, lic, u2)[0] == "assigned"
        assert login(a, u1, lic, fp())["o_outcome"] == "no_seat"
        a.close()
        c.close()

    def test_lic14_reducing_device_limit_retires_slots_without_killing_sessions(self, adm):
        lic = mk_licence(adm, 2, 3)
        u = mk_user(adm)
        mk_seat(adm, lic, u)
        a = conn_as("auth_svc")
        s = [login(a, u, lic, fp()) for _ in range(3)]
        assert all(x["o_outcome"] == "success" for x in s)
        c = conn_as("app_service")
        tx(c, "SELECT fn_licence_set_limits(%s,2,1,%s)", (lic, ACT()))
        # held sessions survive
        assert adm.execute("SELECT count(*) FROM device_session WHERE licence_seat_id=%s AND ended_at IS NULL", (s[0]["o_seat_id"],)).fetchone()[0] == 3
        assert adm.execute("SELECT count(*) FROM device_slot WHERE licence_seat_id=%s AND is_retired", (s[0]["o_seat_id"],)).fetchone()[0] == 2
        # end the session in retired slot 3: slot is NOT claimable, nor is occupied slot 1
        retired_session = [x for x in s if x["o_slot_no"] == 3][0]
        tx(a, "SELECT fn_device_session_end(%s)", (retired_session["o_device_session_id"],))
        assert login(a, u, lic, fp())["o_outcome"] == "device_limit_denied"
        # raising the limit again un-retires
        tx(c, "SELECT fn_licence_set_limits(%s,2,3,%s)", (lic, ACT()))
        assert login(a, u, lic, fp())["o_outcome"] == "success"
        a.close()
        c.close()

    def test_lic14_reduce_max_users_retires_free_slots_only(self, adm):
        lic = mk_licence(adm, 4, 1)
        c = conn_as("app_service")
        s1 = assign_seat(c, lic, mk_user(adm))
        tx(c, "SELECT fn_licence_set_limits(%s,2,1,%s)", (lic, ACT()))
        assert adm.execute("SELECT count(*) FROM licence_seat_slot WHERE licence_id=%s AND is_retired", (lic,)).fetchone()[0] == 2
        assert assign_seat(c, lic, mk_user(adm))[0] == "assigned"
        assert assign_seat(c, lic, mk_user(adm))[0] == "seat_limit_reached"
        # cannot cut below the seats in use (ck_licence_seats_in_use_range)
        with pytest.raises(psycopg.errors.CheckViolation):
            tx(c, "SELECT fn_licence_set_limits(%s,1,1,%s)", (lic, ACT()))
        c.close()


class TestRefreshTokens:
    def _login(self, adm):
        lic = mk_licence(adm, 1, 2)
        u = mk_user(adm)
        mk_seat(adm, lic, u)
        c = conn_as("auth_svc")
        l = login(c, u, lic, fp())
        return c, u, l

    def test_adm16_rotation_chain_and_old_token_revoked(self, adm):
        c, u, l = self._login(adm)
        r1 = tx(c, "SELECT * FROM fn_refresh_rotate(%s,%s)", (l["refresh"], "rt_" + uuid.uuid4().hex))[0]
        assert r1[0] == "rotated"
        old = adm.execute("SELECT r.code FROM identity_session s JOIN session_revoke_reason r ON r.id=s.session_revoke_reason_id WHERE s.id=%s", (l["o_identity_session_id"],)).fetchone()
        assert old[0] == "rotated"
        child = adm.execute("SELECT parent_identity_session_id, token_family_ref, revoked_at FROM identity_session WHERE id=%s", (r1[1],)).fetchone()
        assert str(child[0]) == str(l["o_identity_session_id"]) and child[2] is None
        # the device session follows the new token
        assert str(adm.execute("SELECT identity_session_id FROM device_session WHERE id=%s", (l["o_device_session_id"],)).fetchone()[0]) == str(r1[1])
        c.close()

    def test_adm16_reuse_revokes_family_and_ends_device_session(self, adm):
        c, u, l = self._login(adm)
        h2, h3 = "rt_" + uuid.uuid4().hex, "rt_" + uuid.uuid4().hex
        assert tx(c, "SELECT * FROM fn_refresh_rotate(%s,%s)", (l["refresh"], h2))[0][0] == "rotated"
        assert tx(c, "SELECT * FROM fn_refresh_rotate(%s,%s)", (h2, h3))[0][0] == "rotated"
        res = tx(c, "SELECT * FROM fn_refresh_rotate(%s,%s)", (l["refresh"], "rt_" + uuid.uuid4().hex))[0]  # attacker replays token 1
        assert res[0] == "reuse_detected"
        fam = adm.execute("SELECT token_family_ref FROM identity_session WHERE id=%s", (l["o_identity_session_id"],)).fetchone()[0]
        assert adm.execute("SELECT count(*) FROM identity_session WHERE token_family_ref=%s AND revoked_at IS NULL", (fam,)).fetchone()[0] == 0
        assert adm.execute("SELECT r.code FROM identity_session s JOIN session_revoke_reason r ON r.id=s.session_revoke_reason_id WHERE s.refresh_token_hash=%s", (h3,)).fetchone()[0] == "reuse_detected"
        assert adm.execute("SELECT count(*) FROM security_event e JOIN security_event_type t ON t.id=e.security_event_type_id WHERE t.code='refresh_token_reuse' AND e.app_user_id=%s", (u,)).fetchone()[0] == 1
        assert adm.execute("SELECT ended_at IS NOT NULL FROM device_session WHERE id=%s", (l["o_device_session_id"],)).fetchone()[0]
        # even the legitimate newest token is dead now
        assert tx(c, "SELECT * FROM fn_refresh_rotate(%s,%s)", (h3, "rt_" + uuid.uuid4().hex))[0][0] == "reuse_detected"
        c.close()

    def test_adm16_concurrent_use_of_same_token_exactly_one_wins(self, adm):
        c, u, l = self._login(adm)
        c.close()

        def one(i):
            cc = conn_as("auth_svc")
            try:
                return tx(cc, "SELECT o_outcome FROM fn_refresh_rotate(%s,%s)", (l["refresh"], "rt_" + uuid.uuid4().hex))[0][0]
            finally:
                cc.close()
        res = run_parallel(4, one)
        assert res.count("rotated") == 1 and res.count("reuse_detected") == 3, res
        fam = adm.execute("SELECT token_family_ref FROM identity_session WHERE id=%s", (l["o_identity_session_id"],)).fetchone()[0]
        assert adm.execute("SELECT count(*) FROM identity_session WHERE token_family_ref=%s AND revoked_at IS NULL", (fam,)).fetchone()[0] == 0

    def test_adm16_unknown_and_expired(self, adm):
        c, u, l = self._login(adm)
        assert tx(c, "SELECT o_outcome FROM fn_refresh_rotate(%s,%s)", ("nope" + uuid.uuid4().hex, "x" + uuid.uuid4().hex))[0][0] == "unknown"
        adm.execute("UPDATE identity_session SET issued_at = now() - INTERVAL '2 hours', expires_at = now() - INTERVAL '1 hour', "
                    "absolute_expires_at = now() + INTERVAL '5 hours' WHERE id=%s", (l["o_identity_session_id"],))
        assert tx(c, "SELECT o_outcome FROM fn_refresh_rotate(%s,%s)", (l["refresh"], "x" + uuid.uuid4().hex))[0][0] == "expired"
        c.close()

    def test_adm04_refresh_tokens_only_for_auth_svc(self, adm):
        c, u, l = self._login(adm)
        c.close()
        for role in ("app_service", "app_recruiter"):
            cc = conn_as(role)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cc.execute("SELECT refresh_token_hash FROM identity_session LIMIT 1")
            cc.close()


class TestOrganisationAccount:
    def test_org04_mapping_change_chain_and_atomicity(self, adm):
        org, rec = build_row(adm, "organisation")["id"], mk_user(adm)
        c = conn_as("app_service")
        m1 = tx(c, "SELECT fn_recruiter_mapping_change(%s,%s,'organisation_only','reassigned',%s)", (rec, org, ACT()))[0][0]
        m2 = tx(c, "SELECT fn_recruiter_mapping_change(%s,%s,'with_descendants','reassigned',%s)", (rec, org, ACT()))[0][0]
        m3 = tx(c, "SELECT fn_recruiter_mapping_change(%s,%s,'organisation_only','reassigned',%s)", (rec, org, ACT()))[0][0]
        rows = adm.execute("SELECT id, sequence_no, prior_recruiter_organisation_mapping_id, effective_to FROM recruiter_organisation_mapping "
                           "WHERE recruiter_app_user_id=%s AND organisation_id=%s ORDER BY sequence_no", (rec, org)).fetchall()
        assert [r[1] for r in rows] == [1, 2, 3]
        assert [str(r[0]) for r in rows] == [str(m1), str(m2), str(m3)]
        assert str(rows[1][2]) == str(m1) and str(rows[2][2]) == str(m2)
        assert rows[0][3] is not None and rows[1][3] is not None and rows[2][3] is None
        assert rows[0][3] <= adm.execute("SELECT effective_from FROM recruiter_organisation_mapping WHERE id=%s", (m2,)).fetchone()[0]
        c.close()

    def test_org04_concurrent_changes_serialise(self, adm):
        org, rec = build_row(adm, "organisation")["id"], mk_user(adm)
        c0 = conn_as("app_service")
        tx(c0, "SELECT fn_recruiter_mapping_change(%s,%s,'organisation_only','reassigned',%s)", (rec, org, ACT()))
        c0.close()

        def one(i):
            c = conn_as("app_service")
            try:
                return tx(c, "SELECT fn_recruiter_mapping_change(%s,%s,'organisation_only','reassigned',%s)", (rec, org, ACT()))[0][0]
            finally:
                c.close()
        run_parallel(5, one)
        rows = adm.execute("SELECT sequence_no, effective_to IS NULL FROM recruiter_organisation_mapping WHERE recruiter_app_user_id=%s AND organisation_id=%s ORDER BY sequence_no", (rec, org)).fetchall()
        assert [r[0] for r in rows] == [1, 2, 3, 4, 5, 6]
        assert [r[1] for r in rows].count(True) == 1 and rows[-1][1] is True

    def test_org04_reopen_after_close(self, adm):
        org, rec = build_row(adm, "organisation")["id"], mk_user(adm)
        c = conn_as("app_service")
        tx(c, "SELECT fn_recruiter_mapping_change(%s,%s,'organisation_only','reassigned',%s)", (rec, org, ACT()))
        assert tx(c, "SELECT fn_recruiter_mapping_close(%s,%s,'recruiter_left',%s)", (rec, org, ACT()))[0][0] is True
        assert tx(c, "SELECT fn_recruiter_mapping_close(%s,%s,'recruiter_left',%s)", (rec, org, ACT()))[0][0] is False
        tx(c, "SELECT fn_recruiter_mapping_change(%s,%s,'organisation_only','reassigned',%s)", (rec, org, ACT()))
        assert adm.execute("SELECT max(sequence_no), count(*) FILTER (WHERE effective_to IS NULL) FROM recruiter_organisation_mapping "
                           "WHERE recruiter_app_user_id=%s AND organisation_id=%s", (rec, org)).fetchone() == (2, 1)
        c.close()

    def test_org06_account_admin_count_maintained_and_last_admin_protected(self, adm):
        acc = mk_account(adm)
        u1, u2 = mk_user(adm), mk_user(adm)
        c = conn_as("app_service")
        tx(c, "SELECT fn_account_admin_add(%s,%s,%s,true,%s)", (acc, u1, adm.execute("SELECT code FROM account_admin_level LIMIT 1").fetchone()[0], ACT()))
        assert adm.execute("SELECT active_admin_count FROM account WHERE id=%s", (acc,)).fetchone()[0] == 1
        adm.execute("UPDATE account SET activated_at = now() WHERE id=%s", (acc,))
        with pytest.raises(psycopg.errors.CheckViolation):
            tx(c, "SELECT fn_account_admin_close(%s,%s,'left',%s)", (acc, u1, ACT()))
        tx(c, "SELECT fn_account_admin_add(%s,%s,%s,false,%s)", (acc, u2, adm.execute("SELECT code FROM account_admin_level LIMIT 1").fetchone()[0], ACT()))
        assert tx(c, "SELECT fn_account_admin_close(%s,%s,'left',%s)", (acc, u1, ACT()))[0][0] is True
        assert adm.execute("SELECT active_admin_count FROM account WHERE id=%s", (acc,)).fetchone()[0] == 1
        c.close()

    def test_org06_parallel_admin_adds_keep_count_exact(self, adm):
        acc = mk_account(adm)
        users = [mk_user(adm) for _ in range(5)]
        lvl = adm.execute("SELECT code FROM account_admin_level LIMIT 1").fetchone()[0]

        def one(i):
            c = conn_as("app_service")
            try:
                return tx(c, "SELECT fn_account_admin_add(%s,%s,%s,false,%s)", (acc, users[i], lvl, ACT()))[0][0]
            finally:
                c.close()
        run_parallel(5, one)
        assert adm.execute("SELECT active_admin_count FROM account WHERE id=%s", (acc,)).fetchone()[0] == 5

    def test_org08_organisation_moves_between_accounts(self, adm):
        a1, a2 = mk_account(adm), mk_account(adm)
        org = build_row(adm, "organisation")["id"]
        c = conn_as("app_service")
        tx(c, "SELECT fn_organisation_account_move(%s,%s,true,%s)", (org, a1, ACT()))
        tx(c, "SELECT fn_organisation_account_move(%s,%s,true,%s)", (org, a2, ACT()))
        rows = adm.execute("SELECT account_id, effective_to IS NULL FROM account_organisation WHERE organisation_id=%s ORDER BY effective_from", (org,)).fetchall()
        assert [str(r[0]) for r in rows] == [str(a1), str(a2)] and [r[1] for r in rows] == [False, True]
        c.close()


class TestAdministration:
    def test_adm07_role_inheritance_cycle_rejected(self, adm):
        a, b, c3 = (mk_role(adm) for _ in range(3))
        s = conn_as("app_service")
        tx(s, "SELECT fn_role_inheritance_add(%s,%s,%s)", (a, b, ACT()))
        tx(s, "SELECT fn_role_inheritance_add(%s,%s,%s)", (b, c3, ACT()))
        with pytest.raises(psycopg.errors.CheckViolation) as e:
            tx(s, "SELECT fn_role_inheritance_add(%s,%s,%s)", (c3, a, ACT()))
        assert "cycle" in str(e.value)
        s.close()

    def test_lgs03_watermark_advances_only_from_completed_run(self, adm):
        src = build_row(adm, "legacy_source_system")["id"]
        kind = lookup_id(adm, "legacy_entity_kind")
        pol = lookup_id(adm, "sync_conflict_policy")
        s = conn_as("app_service")
        r1 = tx(s, "SELECT fn_legacy_sync_open_run(%s,%s,%s,%s,%s)", (src, kind, "k" + uuid.uuid4().hex, pol, ACT()))[0][0]
        assert adm.execute("SELECT watermark_from_ref FROM legacy_sync_run WHERE id=%s", (r1,)).fetchone()[0] is None
        with pytest.raises(psycopg.Error) as e:   # second open run for the same source+kind
            tx(s, "SELECT fn_legacy_sync_open_run(%s,%s,%s,%s,%s)", (src, kind, "k" + uuid.uuid4().hex, pol, ACT()))
        assert e.value.sqlstate == "55006"
        adm.execute("UPDATE legacy_sync_run SET is_active_run=false, finished_at=now(), watermark_to_ref='W-100', import_status_id=%s WHERE id=%s",
                    (lookup_id(adm, "import_status", "completed"), r1))
        r2 = tx(s, "SELECT fn_legacy_sync_open_run(%s,%s,%s,%s,%s)", (src, kind, "k" + uuid.uuid4().hex, pol, ACT()))[0][0]
        assert adm.execute("SELECT watermark_from_ref FROM legacy_sync_run WHERE id=%s", (r2,)).fetchone()[0] == "W-100"
        # a failed run does not move the watermark
        adm.execute("UPDATE legacy_sync_run SET is_active_run=false, finished_at=now(), watermark_to_ref='W-999', import_status_id=%s WHERE id=%s",
                    (lookup_id(adm, "import_status", "failed"), r2))
        r3 = tx(s, "SELECT fn_legacy_sync_open_run(%s,%s,%s,%s,%s)", (src, kind, "k" + uuid.uuid4().hex, pol, ACT()))[0][0]
        assert adm.execute("SELECT watermark_from_ref FROM legacy_sync_run WHERE id=%s", (r3,)).fetchone()[0] == "W-100"
        s.close()

    def test_adm09_audit_chain_stays_verifiable_under_concurrent_denials(self, adm):
        lic = mk_licence(adm, 1, 1)
        u = mk_user(adm)
        mk_seat(adm, lic, u)
        c = conn_as("auth_svc")
        login(c, u, lic, fp())
        c.close()

        def one(i):
            cc = conn_as("auth_svc")
            try:
                return login(cc, u, lic, fp())["o_outcome"]
            finally:
                cc.close()
        assert run_parallel(6, one) == ["device_limit_denied"] * 6
        chains = [r[0] for r in adm.execute("SELECT DISTINCT chain_no FROM audit_event WHERE entity_ref IN (SELECT id FROM licence_seat WHERE licence_id=%s)", (lic,)).fetchall()]
        assert chains
        for ch in chains:
            assert adm.execute("SELECT count(*) FROM fn_audit_verify_chain(%s::INT2)", (ch,)).fetchone()[0] == 0

    def test_lic10_history_tables_are_append_only_for_service_roles(self, adm):
        lic = mk_licence(adm, 1, 1)
        u = mk_user(adm)
        mk_seat(adm, lic, u)
        c = conn_as("auth_svc")
        login(c, u, lic, fp())
        for tbl in ("login_attempt", "device_limit_violation", "device_revocation", "session_revocation"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"DELETE FROM {tbl} WHERE false")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"UPDATE {tbl} SET created_by = created_by WHERE false")
        c.close()

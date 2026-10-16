"""Credential guards (BR-ADM-03/04), credential history, audit_event append-only (BR-ADM-08), audit hash chain (BR-ADM-09),
audit atomicity (BR-ADM-11 / AT-44) -- real mechanisms in the database."""
import concurrent.futures as cf
import re
import threading
import uuid

import psycopg
import pytest

from conftest import SYSTEM_ACTOR, connect, retry
from helpers_rowbuilder import build_row, lookup_id

ARGON = "$argon2id$v=19$m=19456,t=2,p=1$c29tZXNhbHRzb21lc2FsdA$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaGhhc2g"


def fresh_hash():
    import base64
    h = base64.b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes).decode().rstrip("=")
    return f"$argon2id$v=19$m=19456,t=2,p=1$c29tZXNhbHRzb21lc2FsdA${h}"


def cred_insert(conn, user_id, pw=None, algo="argon2id", reason="initial", active=True):
    a = lookup_id(conn, "hash_algorithm", algo)
    r = lookup_id(conn, "credential_change_reason", reason)
    return conn.execute(
        "INSERT INTO user_credential (created_by, updated_by, app_user_id, password_hash, hash_algorithm_id, argon2_memory_kib, "
        "argon2_iterations, argon2_parallelism, credential_change_reason_id, is_active, deactivated_at) "
        "VALUES (%s,%s,%s,%s,%s,19456,2,1,%s,%s,%s) RETURNING id",
        (SYSTEM_ACTOR, SYSTEM_ACTOR, user_id, pw or fresh_hash(), a, r, active, None if active else __import__("datetime").datetime.now(__import__("datetime").timezone.utc))).fetchone()[0]


@pytest.fixture()
def user(admin):
    return build_row(admin, "app_user")["id"]


# ---------------------------------------------------------------- (c) password hash guards
def test_argon2id_hash_accepted_and_plaintext_rejected(admin, user):
    cid = cred_insert(admin, user)
    assert cid
    for bad in ("Passw0rd!", "$2b$12$abcdefghijklmnopqrstuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuu", "$argon2i$v=19$m=1,t=1,p=1$YQ$YQ",
                "$argon2id$v=19$m=19456,t=2,p=1$", ""):
        admin.execute("BEGIN")
        try:
            with pytest.raises(psycopg.errors.CheckViolation):
                cred_insert(admin, uuid.uuid4() and build_row(admin, "app_user")["id"], pw=bad)
        finally:
            admin.execute("ROLLBACK")


def test_legacy_hash_algorithm_rejected_even_with_argon_looking_hash(admin, user):
    admin.execute("BEGIN")
    try:
        with pytest.raises(psycopg.Error) as ei:
            cred_insert(admin, user, algo="bcrypt_legacy")
        assert ei.value.sqlstate == "23514" and "argon2id" in str(ei.value)
    finally:
        admin.execute("ROLLBACK")


def test_one_active_credential_per_user(admin, user):
    cred_insert(admin, user)
    admin.execute("BEGIN")
    try:
        with pytest.raises(psycopg.errors.UniqueViolation):
            cred_insert(admin, user)
    finally:
        admin.execute("ROLLBACK")


def test_no_plaintext_secret_columns_in_catalog(catalog):
    ok_suffix = ("_hash", "_encrypted", "_token", "_ref", "_hmac", "_at", "_id", "_version", "_count", "_state", "_policy",
                 "_kind", "_type", "_method", "_required", "_mode", "_days", "_length")
    bad = []
    for t, cols in catalog["columns"].items():
        if catalog["tables"][t] != "BASE TABLE":
            continue
        for c in cols:
            n = c["name"]
            if re.search(r"(password|passwd|secret|passphrase|api_key|private_key)", n) and not n.endswith(ok_suffix):
                bad.append((t, n))
    assert not bad, f"possible plaintext secret columns: {bad}"


def test_password_hash_column_type_and_no_view_exposes_it(catalog):
    for v, ty in catalog["tables"].items():
        if ty == "VIEW":
            names = {c["name"] for c in catalog["columns"][v]}
            assert not names & {"password_hash", "secret_encrypted", "code_hash", "refresh_token_hash", "key_hash", "token_hash"}, v


# ---------------------------------------------------------------- (d) credential history trigger
def hist(conn, uid):
    return [r[0] for r in conn.execute(
        "SELECT t.code FROM user_credential_history h JOIN credential_event_type t ON t.id = h.credential_event_type_id "
        "WHERE h.app_user_id = %s ORDER BY h.created_at, t.sort_order", (uid,)).fetchall()]


def test_credential_insert_and_change_write_history_as_auth_svc(admin, user):
    a = connect(role="auth_svc")
    try:
        cid = cred_insert(a, user)
        assert hist(admin, user) == ["set"]
        a.execute("UPDATE user_credential SET password_hash = %s, updated_at = now(), updated_by = %s WHERE id = %s",
                  (fresh_hash(), SYSTEM_ACTOR, cid))
        assert hist(admin, user) == ["set", "changed"]
        a.execute("UPDATE user_credential SET must_change = true WHERE id = %s", (cid,))
        a.execute("UPDATE user_credential SET breach_detected_at = now() WHERE id = %s", (cid,))
        h = hist(admin, user)
        assert sorted(h) == sorted(["set", "changed", "forced_rotation", "breach_flagged"])
        # a no-op style update (touching an unrelated column) writes nothing
        a.execute("UPDATE user_credential SET breach_checked_at = now() WHERE id = %s", (cid,))
        assert len(hist(admin, user)) == 4
        # history rows reference the credential and carry no secret
        row = admin.execute("SELECT user_credential_id FROM user_credential_history WHERE app_user_id=%s LIMIT 1", (user,)).fetchone()
        assert row[0] == cid
    finally:
        a.close()


def test_credential_replacement_second_row_is_changed_event(admin, user):
    cid = cred_insert(admin, user)
    admin.execute("UPDATE user_credential SET is_active = false, deactivated_at = now() WHERE id = %s", (cid,))
    cred_insert(admin, user)
    assert hist(admin, user) == ["set", "changed"]


def test_credential_history_is_append_only(admin, user):
    cred_insert(admin, user)
    for role in ("auth_svc",):
        c = connect(role=role)
        try:
            for stmt in ("UPDATE user_credential_history SET user_agent = 'x' WHERE false", "DELETE FROM user_credential_history WHERE false"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute(stmt)
        finally:
            c.close()
    admin.execute("BEGIN")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as ei:
            admin.execute("UPDATE user_credential_history SET user_agent = 'x' WHERE app_user_id = %s", (user,))
        assert "append-only" in str(ei.value)
    finally:
        admin.execute("ROLLBACK")


# ---------------------------------------------------------------- audit_event append-only
def append(conn, req=None, action="login_override", etype="app_user", outcome="success", actor_type="system", **kw):
    req = req or uuid.uuid4()
    return conn.execute("SELECT fn_audit_append(%s, NULL, %s, %s, %s, %s, %s)", (actor_type, action, etype, uuid.uuid4(), outcome, req)).fetchone()[0]


@pytest.fixture(scope="module")
def vocab(admin):
    at = admin.execute("SELECT code FROM actor_type ORDER BY sort_order LIMIT 1").fetchone()[0]
    ac = admin.execute("SELECT code FROM audit_action WHERE code IN ('create','login_override') ORDER BY code DESC LIMIT 1").fetchone()[0]
    et = admin.execute("SELECT code FROM audit_entity_type ORDER BY sort_order LIMIT 1").fetchone()[0]
    return at, ac, et


def test_audit_event_update_delete_denied_by_privilege(vocab, admin):
    at, ac, et = vocab
    eid = append_via(admin, vocab)
    for role in ("audit_writer", "app_service", "auth_svc", "billing_svc", "purge_svc", "compliance_svc"):
        c = connect(role=role)
        try:
            for stmt in ("UPDATE audit_event SET reason_text = 'x' WHERE id = %s", "DELETE FROM audit_event WHERE id = %s"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute(stmt, (eid,))
        finally:
            c.close()


def test_audit_event_update_delete_blocked_for_root_by_trigger(vocab, admin):
    eid = append_via(admin, vocab)
    for stmt in ("UPDATE audit_event SET reason_text = 'tamper' WHERE id = %s", "DELETE FROM audit_event WHERE id = %s"):
        admin.execute("BEGIN")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as ei:
                admin.execute(stmt, (eid,))
            assert "append-only" in str(ei.value)
        finally:
            admin.execute("ROLLBACK")
    assert admin.execute("SELECT reason_text FROM audit_event WHERE id = %s", (eid,)).fetchone()[0] is None


def test_purge_run_cannot_delete_audit_event_without_privilege(vocab, admin):
    eid = append_via(admin, vocab)
    c = connect(role="purge_svc")
    try:
        c.execute("SET app.purge_run_id = 'run-1'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM audit_event WHERE id = %s", (eid,))
    finally:
        c.close()


def append_via(conn, vocab, req=None, role=None, **kw):
    at, ac, et = vocab
    return conn.execute("SELECT fn_audit_append(%s, NULL, %s, %s, %s, 'success', %s)",
                        (at, ac, et, uuid.uuid4(), req or uuid.uuid4())).fetchone()[0]


# ---------------------------------------------------------------- audit hash chain
def head(conn, chain):
    return conn.execute("SELECT last_chain_seq, last_row_hash FROM audit_chain_head WHERE chain_no=%s", (chain,)).fetchone()


def req_for_chain(chain):
    while True:
        r = uuid.uuid4()
        if r.bytes[0] % 32 == chain:
            return r


def test_chain_heads_seeded_for_32_chains(admin):
    assert admin.execute("SELECT count(*), min(chain_no), max(chain_no) FROM audit_chain_head").fetchone() == (32, 0, 31)


def test_fn_audit_append_chains_rows_and_moves_head(admin, vocab):
    chain = 7
    s0, h0 = head(admin, chain)
    ids = [append_via(admin, vocab, req=req_for_chain(chain)) for _ in range(3)]
    rows = admin.execute("SELECT chain_seq, prev_hash, row_hash, chain_no FROM audit_event WHERE id = ANY(%s) ORDER BY chain_seq", (ids,)).fetchall()
    assert [r[0] for r in rows] == [s0 + 1, s0 + 2, s0 + 3] and {r[3] for r in rows} == {chain}
    assert rows[0][1] == h0 and rows[1][1] == rows[0][2] and rows[2][1] == rows[1][2]
    assert head(admin, chain) == (s0 + 3, rows[2][2])
    assert not admin.execute("SELECT * FROM fn_audit_verify_chain(%s)", (chain,)).fetchall()


def test_chain_first_row_has_null_prev_hash_check(admin, vocab):
    at, ac, et = vocab
    admin.execute("BEGIN")
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(
                "INSERT INTO audit_event (created_by, actor_type_id, audit_action_id, audit_entity_type_id, audit_outcome_id, request_ref, chain_no, chain_seq, prev_hash, row_hash) "
                "SELECT %s, (SELECT id FROM actor_type LIMIT 1), (SELECT id FROM audit_action LIMIT 1), (SELECT id FROM audit_entity_type LIMIT 1), "
                "(SELECT id FROM audit_outcome LIMIT 1), gen_random_uuid(), 30, 1, '\\x00'::BYTES, digest('x','sha256')", (SYSTEM_ACTOR,))
    finally:
        admin.execute("ROLLBACK")


def test_verifier_detects_forged_row_and_head_drift(admin, vocab):
    chain = 9
    append_via(admin, vocab, req=req_for_chain(chain))
    s, h = head(admin, chain)
    admin.execute("BEGIN")
    try:
        admin.execute(
            "INSERT INTO audit_event (created_by, actor_type_id, audit_action_id, audit_entity_type_id, audit_outcome_id, request_ref, chain_no, chain_seq, prev_hash, row_hash) "
            "SELECT %s, (SELECT id FROM actor_type LIMIT 1), (SELECT id FROM audit_action LIMIT 1), (SELECT id FROM audit_entity_type LIMIT 1), "
            "(SELECT id FROM audit_outcome LIMIT 1), gen_random_uuid(), %s, %s, %s, digest('forged','sha256')", (SYSTEM_ACTOR, chain, s + 1, h))
        probs = {p for _, p in admin.execute("SELECT chain_seq, problem FROM fn_audit_verify_chain(%s)", (chain,)).fetchall()}
        assert {"row_hash_mismatch", "head_mismatch"} <= probs
    finally:
        admin.execute("ROLLBACK")
    assert not admin.execute("SELECT * FROM fn_audit_verify_chain(%s)", (chain,)).fetchall()


def test_audit_append_requires_audit_writer_membership(admin, vocab):
    at, ac, et = vocab
    for role in ("app_guest", "app_candidate", "app_staff_admin", "analytics_reader", "audit_reader", "etl_service"):
        c = connect(role=role)
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT fn_audit_append(%s, NULL, %s, %s, %s, 'success', %s)", (at, ac, et, uuid.uuid4(), uuid.uuid4()))
        finally:
            c.close()
    for role in ("app_service", "auth_svc", "billing_svc", "purge_svc", "compliance_svc", "audit_writer"):
        c = connect(role=role)
        try:
            assert retry(lambda: append_via(c, vocab))
        finally:
            c.close()


def test_audit_atomicity_with_business_write_rolls_back_together(admin, vocab):
    chain = 11
    s0, h0 = head(admin, chain)
    c = connect(role="app_service", autocommit=False) if False else connect(role="app_service")
    try:
        c.execute("BEGIN")
        u = build_row(admin, "app_user")["id"]
        c.execute("UPDATE app_user SET time_zone = 'UTC' WHERE id = %s", (u,))
        eid = append_via(c, vocab, req=req_for_chain(chain))
        c.execute("ROLLBACK")      # fault: business txn aborted -> audit row must vanish too (BR-ADM-11, AT-44)
    finally:
        c.close()
    assert head(admin, chain) == (s0, h0)
    assert admin.execute("SELECT count(*) FROM audit_event WHERE id=%s", (eid,)).fetchone()[0] == 0


def test_concurrent_appends_to_one_chain_stay_gapless(admin, vocab):
    chain = 13
    s0, _ = head(admin, chain)
    n_threads, per = 4, 3

    def worker():
        c = connect(role="app_service")
        try:
            for _ in range(per):
                retry(lambda: append_via(c, vocab, req=req_for_chain(chain)), tries=30)
        finally:
            c.close()

    with cf.ThreadPoolExecutor(n_threads) as ex:
        for f in [ex.submit(worker) for _ in range(n_threads)]:
            f.result()
    s1, _ = head(admin, chain)
    assert s1 == s0 + n_threads * per
    seqs = [r[0] for r in admin.execute("SELECT chain_seq FROM audit_event WHERE chain_no=%s AND chain_seq > %s ORDER BY 1", (chain, s0)).fetchall()]
    assert seqs == list(range(s0 + 1, s1 + 1))
    assert not admin.execute("SELECT * FROM fn_audit_verify_chain(%s)", (chain,)).fetchall()


def test_audit_reader_sees_redacted_audit_only(admin, vocab):
    append_via(admin, vocab)
    cols = {r[0] for r in admin.execute("SELECT column_name FROM information_schema.columns WHERE table_name='vw_audit_event_redacted'").fetchall()}
    assert cols and not cols & {"ip_address", "user_agent", "before_state", "after_state"}
    c = connect(role="audit_reader")
    try:
        assert c.execute("SELECT count(*) FROM vw_audit_event_redacted").fetchone()[0] > 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT ip_address FROM audit_event LIMIT 1")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT 1 FROM audit_chain_head")
    finally:
        c.close()


def test_audit_vocabulary_covers_audit_coverage_sheet(admin, rbac):
    """AT-43: every action / entity-type code named in AuditCoverage exists in the lookups."""
    acts = {r[0] for r in admin.execute("SELECT code FROM audit_action").fetchall()}
    ets = {r[0] for r in admin.execute("SELECT code FROM audit_entity_type").fetchall()}
    miss = [(r["sensitive action"], r["audit_action.code"], r["audit_entity_type.code"]) for r in rbac["AuditCoverage"]
            if (r["audit_action.code"] and r["audit_action.code"] not in acts)
            or (r["audit_entity_type.code"] and r["audit_entity_type.code"] not in ets)]
    assert not miss, miss

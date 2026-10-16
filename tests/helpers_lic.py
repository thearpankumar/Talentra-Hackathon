"""Helpers for tests/test_rules_lic.py: fixtures (users, licences, seats) and function wrappers with the client-side 40001 retry."""
import os
import threading
import time
import uuid

import psycopg
from psycopg.types.json import Jsonb

from conftest import connect
from helpers_rowbuilder import build_row, lookup_id

SYS = "00000000-0000-0000-0000-000000000001"


class Conn:
    """autocommit psycopg connection that reconnects (and re-issues the statement) when HAProxy drops the session between statements.
    Inside an open transaction nothing is retried: use txn() so the whole transaction is re-run."""

    def __init__(self, role=None):
        # LIC_AS_ROOT=1: debugging aid only (skips SET ROLE) for databases whose RoleMatrix grants are not applied yet
        self.role = None if os.environ.get('LIC_AS_ROOT') == '1' else role
        self._c = None
        self.reconnect()

    def reconnect(self):
        try:
            if self._c is not None:
                self._c.close()
        except Exception:  # noqa
            pass
        for i in range(10):
            try:
                self._c = connect(role=self.role)
                return
            except psycopg.OperationalError:
                time.sleep(1 + i)
        self._c = connect(role=self.role)

    @property
    def info(self):
        return self._c.info

    def execute(self, q, p=None):
        for i in range(8):
            try:
                return self._c.execute(q, p)
            except (psycopg.OperationalError, psycopg.InterfaceError):
                if self._c.closed or self._c.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
                    if i == 7:
                        raise
                    self.reconnect()
                else:
                    raise

    def transaction(self):
        return self._c.transaction()

    def close(self):
        try:
            self._c.close()
        except Exception:  # noqa
            pass


def txn(conn, fn, tries=12):
    """run fn() (which opens its own `with conn.transaction()`), retrying on 40001 and on dropped connections"""
    for i in range(tries):
        try:
            return fn()
        except psycopg.errors.SerializationFailure:
            if i == tries - 1:
                raise
            time.sleep(0.05 * (i + 1))
        except (psycopg.OperationalError, psycopg.InterfaceError):
            if i == tries - 1:
                raise
            time.sleep(0.5)
            conn.reconnect()


_ACT = []


def ACT():
    """a real application user used as actor (audit_event / assigned_by FKs need an existing app_user)"""
    if not _ACT:
        c = Conn()
        _ACT.append(mk_user(c, display_name="rules-lic actor"))
        c.close()
    return _ACT[0]


def policy_user(adm, actor_code, **kw):
    """app_user of the given actor type registered through a permitted (registrar, channel) combination"""
    p = adm.execute("SELECT p.actor_type_id, p.registrar_actor_type_id, p.registration_channel_id FROM registration_policy p "
                    "JOIN actor_type t ON t.id = p.actor_type_id WHERE t.code = %s ORDER BY p.id LIMIT 1", (actor_code,)).fetchone()
    if p is None:
        return None
    return mk_user(adm, actor_type_id=p[0], registrar_actor_type_id=p[1], registration_channel_id=p[2], **kw)


def mk_user(adm, **kw):
    """app_user satisfying the registration_policy composite FK (first seeded policy row unless overridden)."""
    if not ({"actor_type_id", "registrar_actor_type_id", "registration_channel_id"} & set(kw)):
        p = adm.execute("SELECT actor_type_id, registrar_actor_type_id, registration_channel_id FROM registration_policy ORDER BY created_at, id LIMIT 1").fetchone()
        kw.update(actor_type_id=p[0], registrar_actor_type_id=p[1], registration_channel_id=p[2])
    kw.setdefault("preferred_language_id", adm.execute("SELECT id FROM language ORDER BY language_tag LIMIT 1").fetchone()[0])
    kw.setdefault("email", f"u{uuid.uuid4().hex[:12]}@example.test")
    try:
        return str(build_row(adm, "app_user", **kw)["id"])
    except psycopg.errors.UniqueViolation:
        # the INSERT was committed, then the pooled connection dropped and Conn re-issued it: return the row that exists
        r = adm.execute("SELECT id FROM app_user WHERE email = %s AND deleted_at IS NULL", (kw["email"],)).fetchone()
        if r is None:
            raise
        return str(r[0])


def mk_account(adm, **kw):
    one = lambda q: adm.execute(q).fetchone()[0]  # noqa
    kw.setdefault("billing_currency_id", one("SELECT id FROM currency ORDER BY currency_code LIMIT 1"))
    kw.setdefault("tax_residence_country_id", one("SELECT id FROM country ORDER BY id LIMIT 1"))
    kw.setdefault("payment_term_id", one("SELECT id FROM payment_term ORDER BY id LIMIT 1"))
    kw.setdefault("active_admin_count", 0)
    return str(build_row(adm, "account", **kw)["id"])


def mk_role(adm, **kw):
    kw.setdefault("name_i18n", Jsonb({"en": "r"}))
    return str(build_row(adm, "app_role", **kw)["id"])


def mk_licence(adm, max_users=5, max_devices=3, policy="deny_new", status="active", require_ip=False, valid_to=None,
               idle_min=30, max_hours=24):
    """licence + licence_seat_slot rows 1..max_users (via fn_licence_sync_slots)."""
    lic = build_row(adm, "licence", account_id=mk_account(adm),
                    licence_status_id=lookup_id(adm, "licence_status", status),
                    device_limit_policy_id=lookup_id(adm, "device_limit_policy", policy),
                    max_users=max_users, max_devices_per_user=max_devices, seats_in_use=0,
                    valid_from=adm.execute("SELECT now() - INTERVAL '1 day'").fetchone()[0], valid_to=valid_to,
                    require_ip_allowlist=require_ip, session_idle_timeout_minutes=idle_min, session_max_lifetime_hours=max_hours)
    adm.execute("SELECT fn_licence_sync_slots(%s, %s)", (lic["id"], SYS))
    return str(lic["id"])


def assign_seat(conn, lic, user, actor=None):
    actor = actor or ACT()
    def go():
        with conn.transaction():
            return conn.execute("SELECT o_outcome, o_seat_id, o_slot_no FROM fn_seat_assign(%s,%s,%s)", (lic, user, actor)).fetchone()
    return txn(conn, go)


def mk_seat(adm, lic, user):
    out = assign_seat(adm, lic, user)
    assert out[0] == "assigned", out
    return str(out[1])


def login(conn, user, lic, fp, ip="10.1.1.1", refresh=None, req=None, platform="web"):
    """One login txn (retry on 40001 / slot unique violation like the app would). Returns the result row as a dict."""
    refresh = refresh or "rt_" + uuid.uuid4().hex
    req = req or "req_" + uuid.uuid4().hex

    def go():
        with conn.transaction():
            cur = conn.execute("SELECT * FROM fn_login_claim(%s,%s,%s,%s,%s::INET,%s,%s)",
                               (user, lic, fp, platform, ip, req, refresh))
            r = cur.fetchone()
            d = dict(zip([c.name for c in cur.description], r))
            d["refresh"] = refresh
            return d
    return txn(conn, go)


def fp():
    return uuid.uuid4().bytes + uuid.uuid4().bytes


def run_parallel(n, fn):
    """Run fn(i) in n threads (each with its own connections created inside fn). Returns results list in index order; raises first error."""
    res = [None] * n
    err = [None] * n
    barrier = threading.Barrier(n)

    def w(i):
        try:
            barrier.wait(timeout=30)
            res[i] = fn(i)
        except Exception as e:  # noqa
            err[i] = e
    ts = [threading.Thread(target=w, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=180)
    for e in err:
        if e:
            raise e
    return res

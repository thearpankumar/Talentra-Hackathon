"""Mechanisms of the live catalog: row-level TTL, append-only enforcement + purge path, idempotent re-apply, seed smoke.
(DB selected by TALENTRA_DB; the idempotency test creates and drops its own scratch database.)"""
import datetime
import os
import random
import re
import string
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest

import conftest
import helpers_conf as hc
from helpers_live import Live
import helpers_rowbuilder as rb

D = hc.get_dict()
ROOT = Path(__file__).resolve().parent.parent
APPEND_ONLY = sorted(t for t, r in D.tables.items() if r["delete_policy"] == "append_only")
TTL_TABLES = sorted(t for t, r in D.tables.items() if r["ttl"])
UTC = datetime.timezone.utc


def _ttl_info(t):
    m = re.match(r"expire_after=(.+?)\s+column=(\S*)$", D.tables[t]["ttl"].strip())
    return m.group(1), (m.group(2) or "created_at")


# ======================================================================================= TTL (structure)
def test_ttl_table_count():
    assert len(TTL_TABLES) == 75


@pytest.fixture(scope="module")
def live(admin):
    return Live(admin)


def test_ttl_expression_in_all_ttl_tables(live):
    bad = [t for t in TTL_TABLES if t not in live.ttl or "ttl = 'on'" not in live.parsed[t]["options"]]
    assert not bad, bad


def test_ttl_expression_uses_documented_column_and_interval(live):
    bad = []
    for t in TTL_TABLES:
        interval, col = _ttl_info(t)
        expr = live.ttl.get(t, "")
        if col not in expr:
            bad.append(f"{t}: column {col} not in {expr!r}")
        num = re.match(r"(\d+)\s*(\w+)", interval)
        if num and int(num.group(1)) > 0 and "INTERVAL" in expr and num.group(1) not in expr:
            bad.append(f"{t}: interval {interval!r} not in {expr!r}")
    assert not bad, bad[:10]


def test_only_documented_tables_have_ttl(live):
    assert sorted(live.ttl) == TTL_TABLES


def test_ttl_column_exists_and_is_timestamptz(live):
    for t in TTL_TABLES:
        _, col = _ttl_info(t)
        c = {x["name"]: x for x in live.columns[t]}
        assert col in c and c[col]["type"] == "TIMESTAMPTZ", t


# ======================================================================================= TTL (functional)
@pytest.fixture()
def scratch_ttl_tables(admin):
    """Two scratch tables built exactly like the generator builds TTL tables: a plain one and an append-only one (trigger that
    lets the TTL job delete expired rows).  TTL cron every minute; dropped afterwards (shared database: never touch real tables)."""
    tag = uuid.uuid4().hex[:8]
    plain, ao = f"ts_conf_ttl_plain_{tag}", f"ts_conf_ttl_ao_{tag}"
    for t in (plain, ao):
        admin.execute(f"CREATE TABLE {t} (id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY, label STRING NOT NULL, "
                      "expires_at TIMESTAMPTZ NULL) WITH (ttl_expiration_expression = 'expires_at', ttl_job_cron = '* * * * *')")
    admin.execute(f"CREATE OR REPLACE FUNCTION fn_ao_{ao}() RETURNS TRIGGER LANGUAGE PLpgSQL AS $$ BEGIN "
                  "IF TG_OP = 'DELETE' AND ((OLD).expires_at < now() OR fn_is_purge_run()) THEN RETURN OLD; END IF; "
                  "RAISE EXCEPTION 'table % is append-only (% not allowed before TTL expiry)', TG_TABLE_NAME, TG_OP USING ERRCODE = '42501'; END $$")
    admin.execute(f"CREATE TRIGGER trg_{ao}_append_only BEFORE UPDATE OR DELETE ON {ao} FOR EACH ROW EXECUTE FUNCTION fn_ao_{ao}()")
    yield plain, ao
    for t in (plain, ao):
        try:
            admin.execute(f"DROP TABLE {t}")
        except Exception:
            pass
    try:
        admin.execute(f"DROP FUNCTION fn_ao_{ao}()")
    except Exception:
        pass


@pytest.mark.slow
def test_ttl_job_deletes_only_expired_rows(admin, scratch_ttl_tables):
    """Expired rows are removed by the row-level TTL job (<= 4 min), unexpired and NULL rows are kept - also for the
    append-only trigger variant (delete allowed only past expiry)."""
    now = datetime.datetime.now(UTC)
    for t in scratch_ttl_tables:
        for label, val in (("expired", now - datetime.timedelta(days=30)), ("future", now + datetime.timedelta(days=30)),
                           ("null", None)):
            admin.execute(f"INSERT INTO {t} (label, expires_at) VALUES (%s, %s)", [label, val])
    deadline = time.time() + 240
    pending = set(scratch_ttl_tables)
    while pending and time.time() < deadline:
        for t in list(pending):
            if admin.execute(f"SELECT count(*) FROM {t} WHERE label = 'expired'").fetchone()[0] == 0:
                pending.discard(t)
        if pending:
            time.sleep(10)
    assert not pending, f"expired rows not deleted by the TTL job in {pending}"
    for t in scratch_ttl_tables:
        left = {r[0] for r in admin.execute(f"SELECT label FROM {t}").fetchall()}
        assert left == {"future", "null"}, (t, left)


# ======================================================================================= append-only
def test_append_only_trigger_on_all_125_tables(admin):
    rows = admin.execute("SELECT tgrelid::regclass::string, tgname FROM pg_trigger WHERE NOT tgisinternal").fetchall()
    have = {r[0]: r[1] for r in rows if r[1].endswith("_append_only")}
    assert len(APPEND_ONLY) == 125
    assert sorted(have) == APPEND_ONLY
    assert all(n == f"trg_{t}_append_only" for t, n in have.items())


def test_append_only_functions_for_ttl_tables(admin):
    fns = {r[0] for r in admin.execute("SELECT proname FROM pg_proc WHERE proname LIKE 'fn_ao_%' OR proname IN "
                                        "('fn_block_mutation','fn_is_purge_run')").fetchall()}
    assert {"fn_block_mutation", "fn_is_purge_run"} <= fns
    want = {f"fn_ao_{t}" for t in APPEND_ONLY if D.tables[t]["ttl"]}
    assert want <= fns


_AO_ROWS = {}


def _ao_row(admin, t, **ovr):
    s = hc.CheckSolver(admin, t)
    explicit, _ = s.valid_baseline(**ovr)
    names = list(explicit)
    ph = ", ".join("%s" + ("::INET" if hc.norm_type(s.cols[n]["type"]) == "INET" else "") for n in names)
    admin.execute(f"INSERT INTO {t} ({', '.join(names)}) VALUES ({ph})", [explicit[n] for n in names])
    return explicit


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_append_only_insert_ok_update_delete_blocked_even_for_root(admin, table):
    ovr = {}
    if D.tables[table]["ttl"]:
        _, col = _ttl_info(table)
        ovr[col] = datetime.datetime.now(UTC) + datetime.timedelta(days=3650)  # not expired
    row = _ao_row(admin, table, **ovr)       # INSERT allowed
    n = admin.execute(f"SELECT count(*) FROM {table} WHERE id = %s", [row["id"]]).fetchone()[0]
    assert n == 1
    upd_col = next(c["column"] for c in D.cols[table] if c["column"] == "created_at")
    with pytest.raises(psycopg.Error) as e1:
        admin.execute(f"UPDATE {table} SET {upd_col} = {upd_col} WHERE id = %s", [row["id"]])
    assert "append-only" in str(e1.value)
    with pytest.raises(psycopg.Error) as e2:
        admin.execute(f"DELETE FROM {table} WHERE id = %s", [row["id"]])
    assert "append-only" in str(e2.value)


@pytest.mark.parametrize("table", [t for t in APPEND_ONLY if D.tables[t]["ttl"]][:12])
def test_append_only_ttl_table_allows_delete_of_expired_rows_only(admin, table):
    _, col = _ttl_info(table)
    old = _ao_row(admin, table, **{col: datetime.datetime.now(UTC) - datetime.timedelta(days=36500)})
    new = _ao_row(admin, table, **{col: datetime.datetime.now(UTC) + datetime.timedelta(days=36500)})
    admin.execute(f"DELETE FROM {table} WHERE id = %s", [old["id"]])      # expired -> allowed (TTL job path)
    with pytest.raises(psycopg.Error):
        admin.execute(f"DELETE FROM {table} WHERE id = %s", [new["id"]])


@pytest.fixture()
def purge_role(admin):
    """A scratch role that is a member of purge_svc with DELETE/SELECT on the tables under test."""
    r = "ts_conf_purge_" + uuid.uuid4().hex[:6]
    admin.execute(f"CREATE ROLE {r} NOLOGIN")
    admin.execute(f"GRANT purge_svc TO {r}")
    yield r
    for stmt in (f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {r}", f"REVOKE purge_svc FROM {r}", f"DROP ROLE {r}"):
        try:
            admin.execute(stmt)
        except Exception:
            pass


def _cascade_pairs():
    out = []
    for t, fl in D.fks.items():
        for f in fl:
            if f["on_delete"] == "CASCADE" and D.tables[t]["delete_policy"] == "append_only" \
                    and D.tables[f["ref_table"]]["kind"] != "lookup" and "," not in f["columns"]:
                out.append((f["ref_table"], t, f["columns"]))
    return out


CASCADE = _cascade_pairs()


def test_dictionary_has_cascade_into_append_only_children():
    assert len(CASCADE) >= 60


@pytest.mark.parametrize("parent,child,fkcol", CASCADE[:6])
def test_purge_run_allows_cascade_delete_into_append_only(admin, purge_role, parent, child, fkcol):
    # parent + child rows (child parent-id column -> the parent built here)
    ps = hc.CheckSolver(admin, parent)
    pexp, _ = ps.valid_baseline()
    names = list(pexp)
    admin.execute(f"INSERT INTO {parent} ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))})", [pexp[n] for n in names])
    try:
        crow = _ao_row(admin, child, **{fkcol: pexp["id"]})
    except Exception as e:
        pytest.skip(f"cannot build child row for {child}: {str(e).splitlines()[0]}")
    admin.execute(f"GRANT SELECT, DELETE ON {parent}, {child} TO {purge_role}")
    # without a purge run id: parent delete fails because the cascade hits the blocking trigger
    c = conftest.connect(role=purge_role)
    try:
        with pytest.raises(psycopg.Error) as e:
            c.execute(f"DELETE FROM {parent} WHERE id = %s", [pexp["id"]])
        assert "append-only" in str(e.value)
        c.execute("SET app.purge_run_id = 'run-1'")
        c.execute(f"DELETE FROM {parent} WHERE id = %s", [pexp["id"]])
    finally:
        c.close()
    assert admin.execute(f"SELECT count(*) FROM {child} WHERE id = %s", [crow["id"]]).fetchone()[0] == 0


def test_purge_run_id_without_membership_does_not_unblock(admin):
    t = APPEND_ONLY[0]
    row = _ao_row(admin, t, **({_ttl_info(t)[1]: datetime.datetime.now(UTC) + datetime.timedelta(days=3650)} if D.tables[t]["ttl"] else {}))
    r = "ts_conf_nopurge_" + uuid.uuid4().hex[:6]
    admin.execute(f"CREATE ROLE {r} NOLOGIN")
    admin.execute(f"GRANT SELECT, DELETE ON {t} TO {r}")
    try:
        c = conftest.connect(role=r)
        c.execute("SET app.purge_run_id = 'x'")
        with pytest.raises(psycopg.Error) as e:
            c.execute(f"DELETE FROM {t} WHERE id = %s", [row["id"]])
        assert "append-only" in str(e.value)
        c.close()
    finally:
        admin.execute(f"REVOKE ALL ON {t} FROM {r}")
        admin.execute(f"DROP ROLE {r}")


# ======================================================================================= idempotent re-apply
def _snapshot(dbname):
    c = conftest.connect(db=dbname)
    try:
        L = Live(c)
        cnt = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in sorted(L.tables)
               if t.startswith(("country", "currency", "language", "actor_type"))}
        return dict(tables=sorted(L.tables), views=sorted(L.views), columns={t: [(x["name"], x["type"], x["nullable"], x["default"]) for x in v]
                                                                                 for t, v in L.columns.items()},
                    indexes=L.indexes, fks=L.fks, checks=L.checks, constraints=L.constraints, counts=cnt)
    finally:
        c.close()


@pytest.mark.slow
def test_apply_is_idempotent_on_scratch_database(admin):
    db = "ts_idem_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    admin.execute(f"GRANT ALL ON DATABASE {db} TO schema_owner")
    dsn = conftest.dsn(db=db)
    cmd = [sys.executable, str(ROOT / "infra" / "schema" / "apply.py"), "--dsn", dsn, "--only",
           "00_,05_,10_,11_,20_tables_00_", "--workers", "3"]
    timings, snaps = [], []
    try:
        for run in (1, 2):
            t0 = time.time()
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=1500)
            timings.append(time.time() - t0)
            assert p.returncode == 0, f"run {run} failed:\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}"
            assert "0 file errors, 0 soft statement errors" in p.stdout, p.stdout[-500:]
            snaps.append(_snapshot(db))
        assert len(snaps[0]["tables"]) > 538, "lookups + first module expected"
        for k in snaps[0]:
            assert snaps[0][k] == snaps[1][k], f"catalog changed between apply runs: {k}"
        print(f"apply timings (s): first={timings[0]:.1f} second={timings[1]:.1f}")
    finally:
        try:
            admin.execute(f"DROP DATABASE {db} CASCADE")
        except Exception as e:
            print("cleanup failed", e)


# ======================================================================================= seed smoke
def _randomise(v, n=8):
    """Same-shaped variant of a string value (keeps case class and separators) to avoid unique collisions."""
    if "@" in v:
        return "u" + uuid.uuid4().hex[:10] + v[v.index("@"):]
    chars = list(v)
    idx = [i for i, c in enumerate(chars) if c.isalnum()][-n:]
    for i in idx:
        chars[i] = random.choice(string.ascii_lowercase if chars[i].islower() else string.ascii_uppercase if chars[i].isupper()
                                 else string.digits)
    return "".join(chars)


class Bulk:
    """Insert many rows of one table from a solved template (fast path) with fresh-solve fallback."""

    def __init__(self, conn, table, **fixed):
        self.conn, self.table = conn, table
        self.solver = hc.CheckSolver(conn, table)
        self.solver.randomize = True
        self.template, _ = self.solver.valid_baseline(**fixed)
        uniq = set()
        for c in D.cons.get(table, []):
            if c["constraint_type"] == "UNIQUE":
                uniq |= {x.strip() for x in c["columns"].split(",")}
        for i in D.idx.get(table, []):
            if hc.yes(i["unique"]):
                uniq |= {x.strip().split(" ")[0] for x in i["columns"].split(",")}
        fkcols = {x.strip() for f in D.fks.get(table, []) for x in f["columns"].split(",")}
        self.vary_cols = [c for c in uniq if isinstance(self.template.get(c), str) and c not in fkcols]
        self.fixed = fixed
        self.fallbacks = 0

    def insert(self, **vary):
        row = dict(self.template)
        row["id"] = uuid.uuid4()
        for c in self.vary_cols:
            row[c] = _randomise(row[c])
        row.update(vary)
        try:
            return hc.insert_row(self.conn, self.table, row, self.solver.cols)
        except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation, psycopg.errors.ForeignKeyViolation):
            self.fallbacks += 1
            return hc.build_valid_row(self.conn, self.table, **{**self.fixed, **vary})


SEED_TIME_LIMIT = float(os.environ.get("TS_SEED_LIMIT", "60"))


@pytest.mark.slow
def test_seed_smoke_few_hundred_rows(admin):
    """200 users / 60 organisations / 100 job requirements / 300 applications / ledger rows through the row builder in < 60 s;
    every constraint and FK holds (the inserts are real), row counts verified."""
    rc = hc.RConn()
    t0 = time.time()
    users = Bulk(rc, "app_user")
    uids = [users.insert()["id"] for _ in range(200)]
    orgs = Bulk(rc, "organisation")
    oids = [orgs.insert()["id"] for _ in range(60)]
    profs = Bulk(rc, "candidate_profile")
    pids = [profs.insert(app_user_id=u)["id"] for u in uids[:60]]
    jobs = Bulk(rc, "job_requirement")
    job_org = {}
    for _ in range(100):
        o = random.choice(oids)
        j = jobs.insert(organisation_id=o)["id"]
        job_org[j] = o
    apps = Bulk(rc, "job_application")
    pairs = random.sample([(p, j) for p in pids for j in job_org], 300)
    aids = [apps.insert(candidate_profile_id=p, job_requirement_id=j, organisation_id=job_org[j])["id"] for p, j in pairs]
    wallets = Bulk(rc, "wallet")
    led = []
    for w in range(4):
        wid = wallets.insert(owner_organisation_id=oids[w])["id"]
        lb = Bulk(rc, "wallet_ledger_entry", wallet_id=wid)
        bal = 0
        for seq in range(1, 11):
            bal += 1
            led.append(lb.insert(wallet_id=wid, sequence_no=seq, credits_delta=1, balance_after=bal,
                                 idempotency_key=uuid.uuid4().hex, row_hash=uuid.uuid4().hex)["id"])
    elapsed = time.time() - t0
    total = len(uids) + len(oids) + len(pids) + len(job_org) + len(aids) + len(led)
    print(f"seed smoke: {total} rows in {elapsed:.1f}s")
    for table, ids in (("app_user", uids), ("organisation", oids), ("job_requirement", list(job_org)), ("job_application", aids),
                       ("wallet_ledger_entry", led)):
        n = rc.execute(f"SELECT count(*) FROM {table} WHERE id = ANY(%s)", [ids]).fetchone()[0]
        assert n == len(ids), f"{table}: {n} of {len(ids)} rows present"
    n = rc.execute("SELECT count(*) FROM job_application a JOIN job_requirement j ON j.id = a.job_requirement_id "
                   "AND j.organisation_id = a.organisation_id JOIN candidate_profile p ON p.id = a.candidate_profile_id "
                   "WHERE a.id = ANY(%s)", [aids]).fetchone()[0]
    assert n == 300
    assert elapsed < SEED_TIME_LIMIT, f"seed took {elapsed:.1f}s (limit {SEED_TIME_LIMIT}s)"

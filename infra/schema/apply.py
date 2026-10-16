#!/usr/bin/env python3
"""Apply infra/schema/ddl/*.sql + infra/schema/parts/*.sql (merged, sorted by file name) to a CockroachDB database.
Idempotent: every statement is IF NOT EXISTS / OR REPLACE / ON CONFLICT based.

  python apply.py --dsn postgresql://root@localhost:26267/talentra?sslmode=disable [--only 20_,30_] [--workers 6]

DSN may also come from SCHEMA_DSN.  Every file other than 00_roles is run with SET ROLE schema_owner so all objects
are owned by the NOLOGIN owner role (docs/13_rbac_matrix.xlsx GR-01).  Statements in a file are separated by
lines '-- @@'.  Files named 20_tables_* are executed in dependency waves with --workers parallel connections (the
dependencies are read from REFERENCES clauses); all other files run sequentially.  Dropped connections (node
restart, failover) are re-established and the statement retried (statements are idempotent).
{{DB}} in a file is replaced with the target database name.
"""
import argparse
import concurrent.futures as cf
import os
import re
import sys
import time
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
SEP = re.compile(r"^-- @@\s*$", re.M)
RETRYABLE = (psycopg.errors.SerializationFailure, psycopg.OperationalError, psycopg.errors.AdminShutdown,
             psycopg.errors.CannotConnectNow)


def files(only):
    fs = sorted((p for d in (HERE / "ddl", HERE / "parts") if d.exists() for p in d.glob("*.sql")), key=lambda p: p.name)
    if only:
        pre = tuple(only.split(","))
        fs = [p for p in fs if p.name.startswith(pre)]
    return fs


class Session:
    def __init__(self, dsn, role):
        self.dsn, self.role, self.conn = dsn, role, None
        self.connect()

    def connect(self):
        last = None
        for i in range(30):
            try:
                self.conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=10)
                if self.role:
                    self.conn.execute(f"SET ROLE {self.role}")
                self.conn.execute("SET statement_timeout = 0")
                return
            except psycopg.Error as e:
                last = e
                time.sleep(2)
        raise last

    @property
    def dbname(self):
        return self.conn.info.dbname

    def run(self, sql, tries=12):
        for n in range(tries):
            try:
                self.conn.execute(sql)
                return
            except RETRYABLE as e:
                if n == tries - 1:
                    raise
                if self.conn.closed or isinstance(e, psycopg.OperationalError):
                    time.sleep(2)
                    self.connect()
                else:
                    time.sleep(0.3 * (n + 1))


BATCH = 40


def text_head(path):
    return path.read_text(encoding="utf-8")[:200]


def load_statements(path):
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\A-- generated[^\n]*\n", "", text)
    stmts = [s.strip() for s in SEP.split(text) if s.strip()]
    return [s for s in stmts if re.sub(r"--[^\n]*", "", s).strip()]


def fix(s):
    return s if s.rstrip().endswith(";") else s + ";"


SOFT = {"on": False, "errors": []}


def run_stmt(sess, st, name):
    try:
        sess.run(fix(st))
    except Exception as e:
        if st.startswith("-- optional") and "does not exist" in str(e):
            return
        if SOFT["on"]:
            SOFT["errors"].append((name, str(e).splitlines()[0], st))
            print(f"SOFT-ERROR {name}: {str(e).splitlines()[0]}", flush=True)
            return
        raise RuntimeError(f"{name}: {str(e).splitlines()[0]}\n--- statement ---\n{st[:700]}") from e


def waves(stmts):
    """Group CREATE TABLE statements into dependency waves (REFERENCES inside the same file)."""
    names = {}
    for i, s in enumerate(stmts):
        m = re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", s)
        if m:
            names[m.group(1)] = i
    deps = []
    for i, s in enumerate(stmts):
        d = {names[r] for r in re.findall(r"REFERENCES (\w+)", s) if r in names and names[r] != i}
        deps.append(d)
    level, done, out = {}, set(), []
    remaining = list(range(len(stmts)))
    while remaining:
        w = [i for i in remaining if deps[i] <= done]
        if not w:  # cycle inside the file: run the rest sequentially
            out.append(remaining)
            break
        out.append(w)
        done |= set(w)
        remaining = [i for i in remaining if i not in set(w)]
    return out


LANE_KEY = [  # statement -> object key; statements with the same key stay ordered in one lane
    (re.compile(r"^ALTER TABLE (\w+)"), 1),
    (re.compile(r"FUNCTION fn_ao_(\w+)\(\)"), 1),
    (re.compile(r"TRIGGER \w+ .*? ON (\w+) FOR EACH"), 1),
    (re.compile(r"^CREATE (?:OR REPLACE )?(?:MATERIALIZED )?VIEW (\w+)"), 1),
]
# files whose statements are independent per object and may be spread over lanes (everything else runs sequentially)
LANE_FILES = ("30_", "40_")
TABLE_FILES = ("10_lookups", "20_tables")
TABLE_BATCH = int(os.environ.get("SCHEMA_TABLE_BATCH", "6"))
FAST = {"on": False}


def lane_key(st):
    for rx, g in LANE_KEY:
        m = rx.search(st)
        if m:
            return m.group(g)
    return None


def run_batch(sess, b, name):
    """Several DDLs in one implicit transaction (autocommit_before_ddl = off); on any failure replay one by one."""
    if len(b) > 1 and not any(x.startswith("-- optional") for x in b):
        try:
            sess.run(chr(10).join(fix(x) for x in b))
            return
        except Exception:
            pass
    for x in b:
        run_stmt(sess, x, name)


def run_lanes(pool, lanes, name, batch):
    """lanes[k] = ordered statements for pool[k]; lanes run concurrently."""
    def work(k):
        for j in range(0, len(lanes[k]), batch):
            run_batch(pool[k], lanes[k][j:j + batch], name)
    with cf.ThreadPoolExecutor(len(pool)) as ex:
        for f in [ex.submit(work, k) for k in range(len(pool))]:
            f.result()


def make_pool(dsn, main, role, workers):
    pool = [main] + [Session(dsn, role) for _ in range(workers - 1)]
    for x in pool:
        x.run("SET autocommit_before_ddl = off")
    return pool


def apply_file(dsn, path, role, workers, verbose=True):
    t0 = time.time()
    stmts = load_statements(path)
    main = Session(dsn, None if path.name.startswith("00_") else role)
    db = main.dbname
    stmts = [s.replace("{{DB}}", db) for s in stmts]
    n = path.name
    if n.startswith(TABLE_FILES) and workers > 1 and len(stmts) > 1:
        pool = make_pool(dsn, main, role, workers)
        for w in waves(stmts):
            if len(w) == 1:
                run_stmt(main, stmts[w[0]], n)
                continue
            # keep each lane's tables adjacent in the wave order; tables of one wave are independent
            lanes = [[stmts[i] for j, i in enumerate(w) if j % len(pool) == k] for k in range(len(pool))]
            run_lanes(pool, lanes, n, TABLE_BATCH)
        for x in pool[1:]:
            x.conn.close()
    elif n.startswith(LANE_FILES) and workers > 1 and len(stmts) > 40:
        pool = make_pool(dsn, main, role, workers)
        lanes, shared = [[] for _ in pool], []
        for st in stmts:
            key = lane_key(st)
            if key is None:
                shared.append(st)   # keyless statement (e.g. shared functions): run first, sequentially
            else:
                lanes[hash_key(key) % len(pool)].append(st)
        for st in shared:
            run_stmt(main, st, n)
        run_lanes(pool, lanes, n, 20)
        for x in pool[1:]:
            x.conn.close()
    else:
        b = 1 if n.startswith("00_") or "-- nobatch" in text_head(path) else BATCH
        if b > 1:
            main.run("SET autocommit_before_ddl = off")
        for i in range(0, len(stmts), b):
            run_batch(main, stmts[i:i + b], n) if b > 1 else run_stmt(main, stmts[i], n)
    if verbose:
        print(f"{n}: {len(stmts)} statements in {time.time() - t0:.1f}s", flush=True)
    return len(stmts)


def hash_key(k):
    return sum(ord(c) * (i + 1) for i, c in enumerate(k))


FAST_SETTINGS = ["sql.stats.automatic_collection.enabled"]


def fast_enter(dsn):
    """Local-safe speed-up: switch off automatic table statistics (a job per new table) for the duration of the apply.
    Returns the previous values so they can be restored in finally."""
    saved = {}
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as c:
        for k in FAST_SETTINGS:
            saved[k] = c.execute(f"SHOW CLUSTER SETTING {k}").fetchone()[0]
            c.execute(f"SET CLUSTER SETTING {k} = false")
    return saved


def fast_leave(dsn, saved):
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as c:
        for k, v in saved.items():
            c.execute(f"SET CLUSTER SETTING {k} = {str(v).lower()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("SCHEMA_DSN"))
    ap.add_argument("--only", default="")
    ap.add_argument("--role", default="schema_owner")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("SCHEMA_WORKERS", "6")))
    ap.add_argument("--keep-going", action="store_true")
    ap.add_argument("--soft", action="store_true",
                    help="log failing statements and continue; failed statements are retried once at the end "
                         "(copes with hand-written parts that depend on a later file)")
    ap.add_argument("--fast", action="store_true",
                    help="LOCAL/clean-DB only: disable automatic table statistics during the apply (restored in finally)")
    a = ap.parse_args()
    SOFT["on"] = a.soft
    if not a.dsn:
        sys.exit("need --dsn or SCHEMA_DSN")
    t0 = time.time()
    total, errors, saved = 0, 0, None
    try:
        if a.fast:
            saved = fast_enter(a.dsn)
        for p in files(a.only):
            try:
                total += apply_file(a.dsn, p, a.role, a.workers)
            except Exception as e:
                errors += 1
                print(f"ERROR {e}", file=sys.stderr, flush=True)
                if not a.keep_going:
                    sys.exit(1)
        if a.soft and SOFT["errors"]:
            first, SOFT["errors"] = SOFT["errors"], []
            print(f"retry pass for {len(first)} failed statements", flush=True)
            s = Session(a.dsn, a.role)
            for name, _, st in first:
                run_stmt(s, st, name)
    finally:
        if saved:
            fast_leave(a.dsn, saved)
    print(f"applied {total} statements in {time.time() - t0:.1f}s, {errors} file errors, {len(SOFT['errors'])} soft statement errors")
    for n, e, st in SOFT["errors"]:
        print(f"  {n}: {e} :: {' '.join(st.split())[:160]}")
    sys.exit(1 if errors or SOFT["errors"] else 0)


if __name__ == "__main__":
    main()

"""Live catalog snapshot for the conformance suite.

Built from SHOW TABLES + one SHOW CREATE TABLE per table (the pg_catalog / information_schema scans are far too heavy for the
shared local cluster and kill sessions).  Parsed results are cached in tests/.cache/live_<db>.pkl for TS_LIVE_MAX_AGE seconds
(default 1800); TS_LIVE_REFRESH=1 forces a rebuild.  Only the DDL of the generated tables matters here (other agents add views,
functions and grants but never change tables).
"""
import concurrent.futures as cf
import os
import pickle
import re
import time
from pathlib import Path

import psycopg

from helpers_conf import norm_type


def _split_cols(body):
    out, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


_ACTIONS = r"(CASCADE|SET NULL|SET DEFAULT|RESTRICT|NO ACTION)"


def _idx(unique, inverted, cols, tail, raw, kind):
    bm = re.search(r"bucket_count\s*=\s*(\d+)", tail)
    st = re.search(r"STORING \((.*?)\)", tail)
    wh = re.search(r" WHERE (.*)$", tail)
    return dict(unique=unique, inverted=inverted, hash="USING HASH" in tail, buckets=int(bm.group(1)) if bm else None,
                cols=cols, where=wh.group(1).strip() if wh else None,
                storing=[x.strip() for x in st.group(1).split(",")] if st else [],
                opclass="gin_trgm_ops" if "gin_trgm_ops" in raw else None, raw=raw, kind=kind)


def parse_show_create(text):
    t = dict(columns=[], pk=None, uniques={}, fks={}, checks={}, indexes={}, ttl=None, options="")
    pos = 0
    for ln in text.splitlines()[1:]:
        if not ln.startswith("\t"):
            if ln.startswith(")"):
                t["options"] = ln
                m = re.search(r"ttl_expiration_expression = e?'((?:[^'\\]|\\.)*)'", ln)
                if m:
                    t["ttl"] = m.group(1).replace("\\'", "'")
            continue
        ln = ln.strip().rstrip(",")
        if ln.startswith("CONSTRAINT "):
            name = ln.split()[1]
            if " PRIMARY KEY " in ln:
                cols = _split_cols(re.search(r"PRIMARY KEY \((.*)\)", ln).group(1))
                t["pk"] = (name, cols)
                t["indexes"][name] = _idx(True, False, cols, "", ln, "pk")
            elif " FOREIGN KEY " in ln:
                m = re.search(r"FOREIGN KEY \((.*?)\) REFERENCES (?:\w+\.)?(\w+)\s*\((.*?)\)(.*)$", ln)
                d = re.search("ON DELETE " + _ACTIONS, m.group(4))
                u = re.search("ON UPDATE " + _ACTIONS, m.group(4))
                t["fks"][name] = dict(cols=[c.strip() for c in m.group(1).split(",")], ref=m.group(2),
                                      rcols=[c.strip() for c in m.group(3).split(",")],
                                      on_delete=d.group(1) if d else "NO ACTION", on_update=u.group(1) if u else "NO ACTION")
            elif " CHECK " in ln:
                t["checks"][name] = ln.split(" CHECK ", 1)[1]
            elif " UNIQUE " in ln:
                cols = _split_cols(re.search(r"UNIQUE \((.*?)\)", ln).group(1))
                t["uniques"][name] = cols
                t["indexes"][name] = _idx(True, False, cols, "", ln, "unique")
            continue
        m = re.match(r"(UNIQUE |INVERTED )?INDEX (\w+) \((.*?)\)(?= USING HASH| STORING| WITH| WHERE|$)(.*)$", ln)
        if m:
            t["indexes"][m.group(2)] = _idx(m.group(1) == "UNIQUE ", m.group(1) == "INVERTED ", _split_cols(m.group(3)),
                                            m.group(4), ln, "index")
            continue
        m = re.match(r"(\w+) (.*?) (NOT NULL|NULL)(.*)$", ln)
        if not m:
            continue
        name, typ, nn, rest = m.groups()
        if "NOT VISIBLE" in typ:
            continue
        dm = re.match(r"\s*DEFAULT (.*)$", rest)
        gm = re.match(r"\s*AS \((.*)\) (STORED|VIRTUAL)$", rest)
        pos += 1
        t["columns"].append(dict(name=name, type=norm_type(typ), nullable=nn == "NULL", default=dm.group(1) if dm else None,
                                 gen=gm.group(1) if gm else None, pos=pos))
    return t


class Live:
    def __init__(self, conn):
        import conftest
        cache_dir = Path(__file__).resolve().parent / ".cache"
        cache_dir.mkdir(exist_ok=True)
        path = cache_dir / f"live_{conn.info.dbname}.pkl"
        max_age = int(os.environ.get("TS_LIVE_MAX_AGE", "1800"))
        rows = None
        for i in range(60):
            try:
                rows = conn.execute("SHOW TABLES").fetchall()
                break
            except psycopg.OperationalError:
                time.sleep(2)
                conn = conftest.connect()
        kinds = {r[1]: r[2] for r in rows if r[0] == "public"}
        self.relkind = {n: ("r" if k == "table" else "m" if "materialized" in k else "v") for n, k in kinds.items()}
        self.tables = {n for n, k in self.relkind.items() if k == "r"}
        self.views = {n for n, k in self.relkind.items() if k != "r"}
        parsed = {}
        if path.exists() and not os.environ.get("TS_LIVE_REFRESH") and time.time() - path.stat().st_mtime < max_age:
            try:
                parsed = pickle.load(open(path, "rb"))
            except Exception:
                parsed = {}
        todo = sorted(t for t in self.tables if t not in parsed)

        def one(t):
            c = None
            for i in range(80):
                try:
                    if c is None or c.closed:
                        c = conftest.connect()
                    return t, parse_show_create(c.execute(f"SHOW CREATE TABLE {t}").fetchall()[0][1])
                except psycopg.OperationalError:
                    c = None
                    time.sleep(2)
            raise RuntimeError("SHOW CREATE TABLE failed for " + t)

        if todo:
            with cf.ThreadPoolExecutor(int(os.environ.get("TS_LIVE_THREADS", "4"))) as ex:
                for t, p in ex.map(one, todo):
                    parsed[t] = p
            pickle.dump(parsed, open(path, "wb"))
        self.parsed = {t: p for t, p in parsed.items() if t in self.tables}
        P = self.parsed
        self.columns = {t: p["columns"] for t, p in P.items()}
        self.fks = {t: p["fks"] for t, p in P.items()}
        self.checks = {t: p["checks"] for t, p in P.items()}
        self.indexes = {t: p["indexes"] for t, p in P.items()}
        self.ttl = {t: p["ttl"] for t, p in P.items() if p["ttl"]}
        self.constraints = {}
        for t, p in P.items():
            d = self.constraints.setdefault(t, {})
            if p["pk"]:
                d[p["pk"][0]] = "p"
            for n in p["uniques"]:
                d[n] = "u"
            for n in p["fks"]:
                d[n] = "f"
            for n in p["checks"]:
                d[n] = "c"
            for n, i in p["indexes"].items():
                if i["kind"] == "index" and i["unique"]:
                    d[n] = "u"   # unique indexes are reported as unique constraints by pg_constraint too

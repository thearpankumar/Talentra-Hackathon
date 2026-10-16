"""Generic row builder driven by the data dictionary.

    from helpers_rowbuilder import build_row, lookup_id
    uid = build_row(conn, "app_user", email="a@example.test")["id"]

build_row(conn, table, **overrides) inserts ONE row and returns it as a dict (RETURNING *).
  * columns with a DEFAULT, nullable columns and computed columns are omitted unless overridden
  * NOT NULL columns without default get a type/name-appropriate value
  * NOT NULL FK columns get an existing lookup row (first by sort_order) or a recursively built parent row;
    composite FKs copy the referenced columns from the (built) parent so tenant/same-parent FKs hold
  * CHECK constraints are not solved: on CheckViolation/other errors the message names the constraint -> pass overrides
Parents are created through the same connection (autocommit expected; inside a transaction they live and die with it).
The connection should be able to INSERT everywhere: use the admin (root) connection for fixtures, then SET ROLE for the
statement under test.
"""
import datetime
import decimal
import random
import re
import uuid
from functools import lru_cache
from pathlib import Path

import openpyxl
import psycopg
from psycopg.types.json import Jsonb

DOCS = Path(__file__).resolve().parent.parent / "docs"
SYSTEM_ACTOR = uuid.UUID("00000000-0000-0000-0000-000000000001")


@lru_cache(maxsize=1)
def _dd():
    wb = openpyxl.load_workbook(DOCS / "05_data_dictionary.xlsx", read_only=True, data_only=True)
    out = {}
    for ws in wb:
        rows = list(ws.iter_rows(values_only=True))
        h = [str(x) if x is not None else "" for x in rows[0]]
        out[ws.title] = [dict(zip(h, ["" if v is None else v for v in r])) for r in rows[1:]]
    cols, fks, tables = {}, {}, {}
    for c in out["Columns"]:
        cols.setdefault(c["table"], []).append(c)
    for f in out["ForeignKeys"]:
        fks.setdefault(f["table"], []).append(f)
    for t in out["Tables"]:
        tables[t["table"]] = t
    return cols, fks, tables


_lookup_cache = {}


def lookup_id(conn, table, code=None):
    """id of a lookup row (by code, or the first active one by sort_order)."""
    key = (conn.info.dbname, table, code)
    if key in _lookup_cache:
        return _lookup_cache[key]
    if code is None:
        r = conn.execute(f"SELECT id FROM {table} WHERE is_active ORDER BY sort_order, code LIMIT 1").fetchone()
    else:
        r = conn.execute(f"SELECT id FROM {table} WHERE code = %s", (code,)).fetchone()
    if r is None:
        raise LookupError(f"no row in {table} for code={code}")
    _lookup_cache[key] = r[0]
    return r[0]


def _tok(n=8):
    return uuid.uuid4().hex[:n]


def _value(c):
    t = c["type"].upper()
    name = c["column"]
    m = re.match(r"STRING\((\d+)\)", t)
    n = int(m.group(1)) if m else 64
    if t.startswith("STRING"):
        if "email" in name:
            return f"u{_tok(10)}@example.test"
        if "phone" in name or "msisdn" in name:
            return "+9198" + "".join(random.choice("0123456789") for _ in range(8))
        if name.endswith("code") or name == "code":
            return ("c" + _tok(10))[:n]
        if "hash" in name or "token" in name or "key" in name or "secret" in name:
            return _tok(32)[:n]
        if "url" in name or "uri" in name:
            return f"https://example.test/{_tok(8)}"[:n]
        if "currency" in name:
            return "INR"[:n]
        return ("x" + _tok(max(n - 1, 1)))[:n] if n >= 2 else "x"
    if t == "UUID":
        return uuid.uuid4()
    if t == "BOOL":
        return False
    if t in ("INT2", "INT4", "INT8"):
        return 1
    if t.startswith("DECIMAL"):
        return decimal.Decimal("1")
    if t.startswith("FLOAT"):
        return 1.0
    if t == "DATE":
        return datetime.date.today()
    if t == "TIMESTAMPTZ":
        return datetime.datetime.now(datetime.timezone.utc)
    if t == "TIME":
        return datetime.time(10, 0)
    if t == "JSONB":
        return Jsonb({})
    if t == "BYTES":
        return b"x" + _tok(6).encode()
    if t == "INET":
        return "10.0.0.1"
    if t == "INTERVAL":
        return datetime.timedelta(days=1)
    raise ValueError(f"unhandled type {t} for {c['table']}.{name}")


def _cast(c):
    t = c["type"].upper()
    if t == "INET":
        return "::INET"
    return ""


PARENT_BUILDER = None   # optional hook: callable(conn, table, _depth, _stack, **overrides) -> row dict (used for FK parents)


def build_row(conn, table, _depth=0, _stack=(), _plan=False, **overrides):
    """_plan=True: build parents but do NOT insert the row itself; return {column: value} that would be inserted."""
    cols, fks, tables = _dd()
    if table not in cols:
        raise KeyError(f"unknown table {table}")
    if _depth > 12:
        raise RecursionError("FK chain too deep at " + " > ".join(_stack))
    row = dict(overrides)
    colmap = {c["column"]: c for c in cols[table]}
    if tables[table]["kind"] == "lookup":
        if "code" not in row:
            row["code"] = "t_" + _tok(10)
        row.setdefault("name_i18n", Jsonb({"en": row["code"]}))
        row.setdefault("created_by", SYSTEM_ACTOR)
        row.setdefault("updated_by", SYSTEM_ACTOR)
    # composite + single FKs
    for f in fks.get(table, []):
        fcols = [x.strip() for x in f["columns"].split(",")]
        rcols = [x.strip() for x in f["ref_columns"].split(",")]
        need = [x for x in fcols if x not in row and colmap[x]["nullable"] == "NO"]
        if not need and not any(x in row and row[x] is not None for x in fcols):
            continue
        given = {x: row[x] for x in fcols if x in row}
        if len(given) == len(fcols):
            continue
        rt = f["ref_table"]
        if tables[rt]["kind"] == "lookup":
            row[fcols[0]] = lookup_id(conn, rt)
            continue
        if rt in _stack and rt != table:
            continue  # cycle through nullable path
        if rt == table:
            continue
        # parent: reuse caller supplied values for ref columns (e.g. organisation_id) when present
        pov = {rc: row[fc] for fc, rc in zip(fcols, rcols) if fc in row and rc != "id"}
        if "id" in rcols:
            idx = rcols.index("id")
            if fcols[idx] in row:  # explicit parent id: assume it exists
                continue
        parent = (PARENT_BUILDER or build_row)(conn, rt, _depth + 1, _stack + (table,), **pov)
        for fc, rc in zip(fcols, rcols):
            row.setdefault(fc, parent[rc])
    names, vals, ph = [], [], []
    for c in cols[table]:
        n = c["column"]
        if c.get("computed_expr"):
            continue
        if n in row:
            names.append(n)
            vals.append(row[n])
            ph.append("%s" + _cast(c))
            continue
        if n == "id" or c["nullable"] == "YES" or str(c.get("default") or "").strip():
            continue
        if n in ("created_by", "updated_by", "deleted_by"):
            val = SYSTEM_ACTOR
        else:
            val = _value(c)
        names.append(n)
        vals.append(val)
        ph.append("%s" + _cast(c))
    if _plan:
        return dict(zip(names, vals))
    sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join(ph)}) RETURNING *" if names else \
        f"INSERT INTO {table} DEFAULT VALUES RETURNING *"
    cur = conn.execute(sql, vals)
    r = cur.fetchone()
    return dict(zip([d.name for d in cur.description], r))

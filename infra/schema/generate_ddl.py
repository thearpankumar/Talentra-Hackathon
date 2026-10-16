#!/usr/bin/env python3
"""Generate ordered, idempotent CockroachDB DDL from docs/05_data_dictionary.xlsx (+ 06 rules, 13 rbac).

Usage:  python generate_ddl.py [--out ddl] [--docs ../../docs]

Output files (applied in lexical order by apply.py / apply.sh):
  00_roles.sql              roles, memberships, defaults (runs as the cluster admin, not schema_owner)
  05_functions_common.sql   shared trigger / helper functions
  10_lookups.sql            all lookup (enumeration) tables
  11_lookup_seeds.sql       lookup seed rows with i18n names
  20_tables_<module>.sql    non-lookup tables per module (FK-safe order, indexes + TTL inline)
  30_fks_deferred.sql       FKs whose parent is created later (cycles / cross-module order)
  40_append_only.sql        append-only enforcement triggers
  50_views.sql              business views + masked views + ETL views (generated + hand-authored part files)
  60_functions_rules.sql    business-rule procedures (device limit, non-overlap, viewer-scoped functions)
  70_grants.sql             role matrix grants
  80_zone_configs.sql       zone configs
Hand-authored parts live in infra/schema/parts/*.sql and are concatenated by prefix (see PARTS).
Statements inside a file are separated by lines "-- @@"; each chunk is run as one implicit txn.
"""
import argparse
import collections
import datetime
import os
import re
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_security as gs

HERE = Path(__file__).resolve().parent
SYSTEM_ACTOR = "00000000-0000-0000-0000-000000000001"
SEP = "\n-- @@\n"


def load_book(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = {}
    for ws in wb:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        h = [str(x) if x is not None else "" for x in rows[0]]
        out[ws.title] = [dict(zip(h, ["" if v is None else v for v in r])) for r in rows[1:]]
    return out


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


def yes(v):
    return str(v).strip().upper() == "YES"


class Model:
    def __init__(self, docs):
        self.D = load_book(docs / "05_data_dictionary.xlsx")
        self.B = load_book(docs / "06_business_rules.xlsx")
        self.R = load_book(docs / "13_rbac_matrix.xlsx")
        self.tables = {r["table"]: r for r in self.D["Tables"]}
        self.cols = collections.defaultdict(list)
        for c in sorted(self.D["Columns"], key=lambda r: (r["table"], int(r["ordinal"]))):
            self.cols[c["table"]].append(c)
        self.cons = collections.defaultdict(list)
        for c in self.D["Constraints"]:
            self.cons[c["table"]].append(c)
        self.idx = collections.defaultdict(list)
        for c in self.D["Indexes"]:
            self.idx[c["table"]].append(c)
        self.fks = collections.defaultdict(list)
        for c in self.D["ForeignKeys"]:
            self.fks[c["table"]].append(c)
        self.enums = collections.defaultdict(list)
        for c in self.D["Enums"]:
            self.enums[c["lookup_table"]].append(c)
        self.zone = [r for r in self.D["Partitioning"]]

    # ---------------------------------------------------------------- ordering
    def module_order(self):
        mods = []
        for t in self.D["Tables"]:
            if t["module"] not in mods:
                mods.append(t["module"])
        deps = {m: set() for m in mods}
        for t, fl in self.fks.items():
            for f in fl:
                a, b = self.tables[t]["module"], self.tables[f["ref_table"]]["module"]
                if a != b:
                    deps[a].add(b)
        order, left = [], list(mods)
        while left:
            # pick module with fewest unmet deps
            left.sort(key=lambda m: (len(deps[m] - set(order)), mods.index(m)))
            order.append(left.pop(0))
        return order

    def table_order(self):
        """Global creation order: modules in dependency order, tables topologically sorted within and across."""
        names = [t for t in self.tables if self.tables[t]["kind"] != "lookup"]
        modord = {m: i for i, m in enumerate(self.module_order())}
        names.sort(key=lambda t: (modord[self.tables[t]["module"]], t))
        deps = {t: set() for t in names}
        for t in names:
            for f in self.fks[t]:
                r = f["ref_table"]
                if r != t and r in deps:
                    deps[r] and None
                    deps[t].add(r)
        done, order, placed = set(), [], set()
        # Kahn per module-order priority; edges to tables in later modules left deferred if cyclic
        remaining = list(names)
        progress = True
        while remaining and progress:
            progress = False
            nxt = []
            for t in remaining:
                if deps[t] <= placed:
                    order.append(t)
                    placed.add(t)
                    progress = True
                else:
                    nxt.append(t)
            remaining = nxt
        # cycles: break by emitting remaining in module order, their forward FKs get deferred
        for t in remaining:
            order.append(t)
            placed.add(t)
        return order


# ----------------------------------------------------------------------------- DDL pieces
def col_ddl(c):
    parts = [c["column"], c["type"]]
    comp = (c.get("computed_expr") or "").strip()
    if comp:
        m = re.match(r"^(.*)\s+(STORED|VIRTUAL)$", comp, re.S)
        parts.append(f"AS ({m.group(1)}) {m.group(2)}")
    if not yes(c["nullable"]):
        parts.append("NOT NULL")
    d = (str(c.get("default") or "")).strip()
    if d and not comp:
        parts.append(f"DEFAULT {d}")
    return " ".join(parts)


def parse_ttl(ttl, cols, table, retention=""):
    """Return (with_clause_items, expr_for_trigger or None)."""
    if not ttl:
        return [], None
    m = re.match(r"expire_after=(.+?)\s+column=(\S*)$", ttl.strip())
    if not m:
        raise ValueError(f"{table}: cannot parse ttl {ttl!r}")
    interval, column = m.group(1).strip(), m.group(2).strip()
    interval = re.sub(r"\s*\(.*\)\s*$", "", interval).strip()
    zero = re.match(r"^0\s*(seconds?|s)?$", interval)
    if column == "expires_at" and not re.search(r"after (the )?(expires_at|expiry)", retention, re.I):
        zero = True  # expires_at already is the expiry instant (set at insert)
    if not column:
        column = "created_at"
        if column not in {c["column"] for c in cols}:
            raise ValueError(f"{table}: ttl without column and no created_at")
    if zero:
        expr = column
    else:
        expr = f"({column} + INTERVAL '{interval}')"
    return [f"ttl_expiration_expression = {q(expr)}", "ttl_job_cron = '@daily'"], (column, None if zero else interval)


def index_clause(i):
    cols = i["columns"].strip()
    name = i["index_name"]
    uniq = yes(i["unique"])
    inv = yes(i["inverted"])
    pred = (i.get("where_predicate") or "").strip()
    stor = (i.get("storing") or "").strip()
    opc = (i.get("opclass") or "").strip()
    if inv:
        if opc:
            cl = ", ".join(f"{c.strip()} {opc}" if " " not in c.strip() else c.strip() for c in cols.split(","))
            return f"INVERTED INDEX {name} ({cl})" + (f" WHERE {pred}" if pred else "")
        return f"INVERTED INDEX {name} ({cols})" + (f" WHERE {pred}" if pred else "")
    s = ("UNIQUE " if uniq else "") + f"INDEX {name} ({cols})"
    if yes(i["hash_sharded"]):
        bc = i.get("bucket_count") or 16
        s += f" USING HASH WITH (bucket_count = {int(bc)})"
    if stor:
        s += f" STORING ({stor})"
    if pred:
        s += f" WHERE {pred}"
    return s


def fk_clause(f):
    s = (f"CONSTRAINT {f['fk_name']} FOREIGN KEY ({f['columns']}) REFERENCES {f['ref_table']} ({f['ref_columns']})")
    od = (f.get("on_delete") or "").strip().upper()
    ou = (f.get("on_update") or "").strip().upper()
    if od and od != "NO ACTION":
        s += f" ON DELETE {od}"
    if ou and ou != "NO ACTION":
        s += f" ON UPDATE {ou}"
    return s


def create_table(m, t, created, inline_fk=True):
    row = m.tables[t]
    cols = m.cols[t]
    items = [col_ddl(c) for c in cols]
    pk = [c for c in m.cons[t] if c["constraint_type"] == "PRIMARY KEY"]
    for c in pk:
        items.append(f"CONSTRAINT {c['constraint_name']} PRIMARY KEY ({c['columns']})")
    for c in m.cons[t]:
        if c["constraint_type"] == "UNIQUE":
            items.append(f"CONSTRAINT {c['constraint_name']} UNIQUE ({c['columns']})")
        elif c["constraint_type"] == "CHECK":
            items.append(f"CONSTRAINT {c['constraint_name']} CHECK ({c['expression']})")
    deferred = []
    for f in m.fks[t]:
        if f["ref_table"] == t or f["ref_table"] in created:
            items.append(fk_clause(f))
        else:
            deferred.append(f)
    for i in m.idx[t]:
        items.append(index_clause(i))
    with_items, _ = parse_ttl(row["ttl"], cols, t, row.get("retention") or "")
    sql = f"CREATE TABLE IF NOT EXISTS {t} (\n  " + ",\n  ".join(items) + "\n)"
    if with_items:
        sql += " WITH (" + ", ".join(with_items) + ")"
    sql += ";"
    return sql, deferred


def lookup_seed(m, t):
    rows = m.enums.get(t, [])
    if not rows:
        return ""
    by = collections.OrderedDict()
    for r in rows:
        k = r["code"]
        e = by.setdefault(k, {"sort": r["sort_order"], "active": r["is_active"], "names": {}})
        e["names"][r["language_tag"]] = r["display_name"]
    vals = []
    import json
    for code, e in by.items():
        names = json.dumps(e["names"], ensure_ascii=False)
        vals.append(f"({q(code)}, {q(names)}::JSONB, {int(e['sort'] or 0)}, {'true' if yes(e['active']) else 'false'}, '{SYSTEM_ACTOR}', '{SYSTEM_ACTOR}')")
    return (f"INSERT INTO {t} (code, name_i18n, sort_order, is_active, created_by, updated_by) VALUES\n  "
            + ",\n  ".join(vals) + "\nON CONFLICT (code) DO NOTHING;")


# ----------------------------------------------------------------------------- append-only
def append_only_sql(m):
    out = ["CREATE OR REPLACE FUNCTION fn_is_purge_run() RETURNS BOOL LANGUAGE SQL STABLE AS $$\n"
           "SELECT pg_has_role(current_user, 'purge_svc', 'member') AND coalesce(current_setting('app.purge_run_id', true), '') <> '' $$;",
           "CREATE OR REPLACE FUNCTION fn_block_mutation() RETURNS TRIGGER LANGUAGE PLpgSQL AS $$\n"
           "BEGIN\n  IF TG_OP = 'DELETE' AND fn_is_purge_run() THEN RETURN OLD; END IF;\n"
           "  RAISE EXCEPTION 'table % is append-only (% not allowed)', TG_TABLE_NAME, TG_OP USING ERRCODE = '42501';\n"
           "END $$;"]
    for t, row in m.tables.items():
        if row["delete_policy"] != "append_only":
            continue
        _, ttlinfo = parse_ttl(row["ttl"], m.cols[t], t, row.get("retention") or "")
        if ttlinfo:
            col, interval = ttlinfo
            expr = f"(OLD).{col}" + (f" + INTERVAL '{interval}'" if interval else "")
            out.append(
                f"CREATE OR REPLACE FUNCTION fn_ao_{t}() RETURNS TRIGGER LANGUAGE PLpgSQL AS $$\n"
                f"BEGIN\n  IF TG_OP = 'DELETE' AND ({expr} < now() OR fn_is_purge_run()) THEN RETURN OLD; END IF;\n"
                f"  RAISE EXCEPTION 'table % is append-only (% not allowed before TTL expiry)', TG_TABLE_NAME, TG_OP USING ERRCODE = '42501';\n"
                f"END $$;")
            fn = f"fn_ao_{t}"
        else:
            fn = "fn_block_mutation"
        out.append(f"CREATE OR REPLACE TRIGGER trg_{t}_append_only BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION {fn}();")
    return out



COMMON_FUNCS = [
    """CREATE OR REPLACE FUNCTION fn_etl_pseudonym(p_key BYTES, p_pepper BYTES) RETURNS BYTES LANGUAGE SQL IMMUTABLE AS $$ SELECT hmac(p_key, p_pepper, 'sha256') $$;""",
]


def business_views(m, warnings):
    stm, names = [], {}
    over = HERE / "parts" / "views"
    for v in m.D["Views"]:
        n = v["view_name"]
        f = over / f"{n}.sql"
        if f.exists():
            body = f.read_text(encoding="utf-8").strip()
            stm.append(body)
            names[n] = v
            continue
        sk = (v.get("sql_sketch") or "").strip()
        # strip trailing comment-only lines
        lines = [l for l in sk.splitlines()]
        code = "\n".join(l for l in lines if not l.strip().startswith("--")).strip().rstrip(";")
        if not code:
            warnings.append(f"view {n}: empty sketch")
            continue
        if code.upper().startswith("CREATE"):
            s = code
            s = re.sub(r"^CREATE\s+VIEW", "CREATE OR REPLACE VIEW", s, flags=re.I)
        else:
            kind = "MATERIALIZED VIEW IF NOT EXISTS" if v["kind"] == "materialized" else "OR REPLACE VIEW"
            s = f"CREATE {kind} {n} AS\n{code}"
        stm.append(s + ";")
        names[n] = v
    return stm, names


def zone_sql(m):
    out = []
    for t, r in m.tables.items():
        if r["ttl"] and r["volume"] in ("very_high", "high"):
            out.append(f"ALTER TABLE {t} CONFIGURE ZONE USING gc.ttlseconds = 7200;")
    return out

# ----------------------------------------------------------------------------- main
def write_file(outdir, name, stmts):
    p = outdir / name
    header = f"-- generated by infra/schema/generate_ddl.py on {datetime.date.today()} -- DO NOT EDIT\n"
    p.write_text(header + SEP.join(s.rstrip() for s in stmts if s and s.strip()) + "\n", encoding="utf-8", newline="\n")
    return p


def chunked(stmts, n):
    for i in range(0, len(stmts), n):
        yield stmts[i:i + n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "ddl"))
    ap.add_argument("--docs", default=str(HERE.parent.parent / "docs"))
    a = ap.parse_args()
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    for f in outdir.glob("*.sql"):
        if f.name[:2] not in ("00",):  # roles file is regenerated below too
            pass
    m = Model(Path(a.docs))
    stats = collections.Counter()

    # lookups
    lookups = [t for t, r in m.tables.items() if r["kind"] == "lookup"]
    created = set()
    stm, deferred_all = [], []
    for t in lookups:
        s, d = create_table(m, t, set(lookups))  # lookups reference only lookups
        stm.append(s)
        deferred_all += d
        created.add(t)
    write_file(outdir, "10_lookups.sql", stm)
    seeds = [lookup_seed(m, t) for t in lookups]
    write_file(outdir, "11_lookup_seeds.sql", [s for s in seeds if s])
    stats["lookups"] = len(lookups)

    # module tables
    order = m.table_order()
    by_mod = collections.OrderedDict()
    for t in order:
        by_mod.setdefault(m.tables[t]["module"], []).append(t)
    for old in outdir.glob("20_tables_*.sql"):
        old.unlink()
    for i, (mod, ts) in enumerate(by_mod.items()):
        stm = []
        for t in ts:
            s, d = create_table(m, t, created)
            stm.append(s)
            deferred_all += d
            created.add(t)
        write_file(outdir, f"20_tables_{i:02d}_{mod}.sql", stm)
    stats["tables"] = len(created)
    # deferred FKs
    stm = [f"ALTER TABLE {f['table']} ADD CONSTRAINT IF NOT EXISTS {f['fk_name']} "
           + fk_clause(f).split(" ", 2)[2] + ";" for f in deferred_all]
    write_file(outdir, "30_fks_deferred.sql", stm)
    stats["deferred_fks"] = len(deferred_all)

    # append-only
    write_file(outdir, "40_append_only.sql", append_only_sql(m))
    stats["append_only"] = sum(1 for r in m.tables.values() if r["delete_policy"] == "append_only")

    # roles + db privileges
    write_file(outdir, "00_roles.sql", gs.roles_sql(m))
    write_file(outdir, "05_functions_common.sql", COMMON_FUNCS)
    write_file(outdir, "45_db_privileges.sql", [s.replace("{db}", "{{DB}}") for s in gs.db_privs_sql()])

    # views: masked / ETL (generated) + business views (sketch or override)
    warnings = []
    mstm, mnames = gs.masked_views(m, warnings)
    vstm, vnames = gs.visible_views(m, set(mnames))
    mstm += vstm
    mnames.update(vnames)
    write_file(outdir, "50_views_masked.sql", mstm)
    stats["masked_views"] = len(mnames)
    bstm, bnames = business_views(m, warnings)
    write_file(outdir, "52_views_business.sql", bstm)
    stats["business_views"] = len(bnames)
    view_names = set(mnames) | set(bnames)
    write_file(outdir, "70_grants.sql", gs.grants_sql(m, view_names, warnings))
    write_file(outdir, "80_zone_configs.sql", zone_sql(m))
    (outdir / "WARNINGS.txt").write_text("\n".join(sorted(set(warnings))) + "\n", encoding="utf-8")
    stats["warnings"] = len(set(warnings))
    print(dict(stats))
    return m


if __name__ == "__main__":
    main()

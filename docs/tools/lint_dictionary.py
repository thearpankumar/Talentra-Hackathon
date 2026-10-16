#!/usr/bin/env python
"""Lint the Talentra data dictionary against docket Section 5 rules + CockroachDB constraints.

Usage:
  python docs/tools/lint_dictionary.py                       # full: all docs/_staging/*.yaml, registry must resolve
  python docs/tools/lint_dictionary.py docs/_staging/x.yaml  # partial: only this file; FK refs to REGISTRY names allowed
Exit code 1 if any ERROR.  WARN lines never fail.
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
import yaml
from dictlib import load_model, STAGING, MODULES

TOOLS = os.path.dirname(os.path.abspath(__file__))


def words(fn):
    out = set()
    for line in open(os.path.join(TOOLS, fn), encoding="utf-8"):
        if line.startswith("#"):
            continue
        out |= set(line.split())
    return out


RESERVED = words("reserved_words.txt")
AVOID = words("avoid_words.txt")
SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
SING_OK = {"status", "address", "news", "analytics", "metadata", "series", "alias", "basis", "analysis", "campus", "bonus",
           "canvas", "access", "process", "business", "class", "progress", "consensus", "census", "focus", "lens", "radius",
           "virus", "corpus", "genus", "wellness", "gas", "atlas", "bias", "chaos", "address", "consensus"}
SENS = {"Public", "Internal", "Personal", "Sensitive"}
KINDS = {"entity", "link", "event", "ledger", "audit", "snapshot", "config", "dim", "fact", "agg", "bridge", "staging", "lookup"}
DELPOL = {"soft", "hard", "append_only"}
VOLUMES = {"low", "medium", "high", "very_high"}
ONDEL = {"RESTRICT", "CASCADE", "SET NULL", "NO ACTION", "SET DEFAULT"}
DOMAIN_TYPES = {
    "identifier": ["UUID"], "enum_ref": ["UUID"], "person_name": [r"STRING\(150\)"], "free_text": [r"STRING\(\d+\)"],
    "long_text": ["STRING"], "email": [r"STRING\(254\)"], "phone": [r"STRING\(16\)"], "tax_id": [r"STRING\(\d+\)"],
    "money": [r"DECIMAL\(\d+,\d+\)"], "currency_code": [r"STRING\(3\)"], "percentage": [r"DECIMAL\(\d+,\d+\)"],
    "ratio": [r"DECIMAL\(\d+,\d+\)"], "date": ["DATE"], "timestamp": ["TIMESTAMPTZ"], "boolean": ["BOOL"],
    "latitude": [r"DECIMAL\(9,6\)"], "longitude": [r"DECIMAL\(9,6\)"], "ui_label_key": [r"STRING\(150\)"],
    "code": [r"STRING\(\d+\)"], "url": [r"STRING\(\d+\)"], "json": ["JSONB"], "secret_hash": [r"STRING\(\d+\)", "BYTES"],
    "token": [r"STRING\(\d+\)", "BYTES"], "quantity": [r"INT[248]"], "measure": [r"DECIMAL\(\d+,\d+\)", r"FLOAT[48]"],
    "duration": ["INTERVAL", r"INT[248]"], "ip_address": ["INET"], "time_zone": [r"STRING\(64\)"],
    "language_tag": [r"STRING\(\d+\)"], "blob": ["BYTES"], "time_of_day": ["TIME", "TIMETZ"], "vector": [r"VECTOR\(\d+\)"],
    "encrypted": ["BYTES"],
}
PAIRS = [("_start_date", "_end_date"), ("_start_at", "_end_at"), ("starts_at", "ends_at"), ("valid_from", "valid_to"),
         ("effective_from", "effective_to"), ("start_date", "end_date"), ("start_at", "end_at"), ("_from_date", "_to_date"),
         ("from_date", "to_date"), ("window_start_at", "window_end_at")]
TS_NAME_OK = {"valid_from", "valid_to", "effective_from", "effective_to", "starts_at", "ends_at"}


class Rep:
    def __init__(s):
        s.e, s.w = [], []

    def err(s, loc, msg):
        s.e.append(f"ERROR {loc}: {msg}")

    def warn(s, loc, msg):
        s.w.append(f"WARN  {loc}: {msg}")


def check_ident(R, loc, n, is_table=False):
    if not SNAKE.match(n):
        R.err(loc, f"'{n}' is not snake_case")
    if n in RESERVED:
        R.err(loc, f"'{n}' is a reserved word (PG/CRDB/MySQL)")
    elif n in AVOID:
        R.warn(loc, f"'{n}' is a risky non-reserved keyword; qualify it")
    if is_table:
        last = n.split("_")[-1]
        if last.endswith("s") and not last.endswith(("ss", "us", "is")) and last not in SING_OK:
            R.err(loc, f"table name '{n}' looks plural")


def has_check(t, *names):
    return any(k.get("type") == "check" and all(n in str(k.get("expr", "")) for n in names) for k in t["constraints"])


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    partial = bool(args)
    m = load_model(args or None)
    R = Rep()
    rp = os.path.join(STAGING, "REGISTRY.yaml")
    registry = yaml.safe_load(open(rp, encoding="utf-8")) if os.path.exists(rp) else {}
    reg_names = {t: mod for mod, ts in (registry or {}).items() for t in ts}
    tables = {}
    for t in m["tables"]:
        if t["name"] in tables:
            R.err(t["name"], f"duplicate table (in {tables[t['name']]['_file']} and {t['_file']})")
        tables[t["name"]] = t
    if not partial:
        for rn, mod in reg_names.items():
            if rn not in tables:
                R.err("REGISTRY", f"registry table '{rn}' (module {mod}) not defined")
            elif tables[rn]["module"] != mod:
                R.err("REGISTRY", f"'{rn}' defined in module {tables[rn]['module']} but registry says {mod}")
    ref_tables = dict(tables)
    if partial:
        for t2 in load_model(None)['tables']:
            ref_tables.setdefault(t2['name'], t2)
    allidx = {}
    for tn, t in tables.items():
        check_ident(R, tn, tn, True)
        if t["module"] not in MODULES:
            R.err(tn, f"unknown module '{t['module']}'")
        if t["kind"] not in KINDS:
            R.err(tn, f"bad kind '{t['kind']}'")
        if t["delete_policy"] not in DELPOL:
            R.err(tn, f"bad delete_policy '{t['delete_policy']}'")
        if t["volume"] not in VOLUMES:
            R.err(tn, f"bad volume '{t['volume']}'")
        if not t.get("description"):
            R.err(tn, "missing table description")
        if not t.get("retention"):
            R.err(tn, "missing retention statement (Rule 4)")
        if t["module"] == "analytics":
            if not tn.startswith(("dim_", "fact_", "agg_", "bridge_")):
                R.err(tn, "analytics table must start dim_/fact_/agg_/bridge_ (Rule 8)")
        elif tn.startswith(("dim_", "fact_", "agg_")):
            R.err(tn, "dim_/fact_/agg_ prefix is reserved for the analytics module")
        if t["kind"] in ("ledger", "audit") and t["delete_policy"] != "append_only":
            R.err(tn, f"kind {t['kind']} must be delete_policy append_only")
        if t["delete_policy"] == "append_only" and t.get("audit") == "full":
            R.err(tn, "append_only table must use audit: created_only")
        if t["volume"] in ("high", "very_high") and not any(i.get("hash") for i in t["indexes"]) and not t.get("no_hash_why"):
            R.warn(tn, "high/very_high volume table has no hash-sharded index; add one or set no_hash_why")
        if t["_declared_std_conflict"]:
            R.err(tn, f"do not declare standard columns {t['_declared_std_conflict']} (auto-injected)")
        cols = {}
        for c in t["columns"]:
            cn = c["name"]
            cl = f"{tn}.{cn}"
            if cn in cols:
                R.err(cl, "duplicate column")
            cols[cn] = c
            check_ident(R, cl, cn)
            dom = c.get("domain")
            if dom not in DOMAIN_TYPES:
                R.err(cl, f"unknown domain '{dom}'")
                continue
            ty = str(c.get("type", "")).replace(" ", "")
            if not any(re.fullmatch(p, ty) for p in DOMAIN_TYPES[dom]):
                R.err(cl, f"type '{c.get('type')}' does not match domain '{dom}' (allowed {DOMAIN_TYPES[dom]})")
            if c.get("sens") not in SENS:
                R.err(cl, f"sens must be one of {sorted(SENS)}")
            if c.get("null") and not str(c.get("null_why", "")).strip():
                R.err(cl, "nullable column needs null_why (Rule 5)")
            if not c.get("null") and c.get("null_why") and not c.get("injected"):
                R.warn(cl, "null_why set on NOT NULL column")
            if not str(c.get("desc", "")).strip():
                R.err(cl, "missing description")
            if c.get("sens") in ("Sensitive", "Personal") and not str(c.get("pii", "")).strip():
                R.err(cl, f"{c.get('sens')} column needs a 'pii' treatment note (Rule 6)")
            d = c.get("default")
            if d is not None and str(d).strip().strip("'\"").lower() in ("", "na", "n/a", "unknown", "none"):
                R.err(cl, f"sentinel default '{d}' (Rule 5)")
            if dom == "boolean":
                if c.get("null"):
                    R.err(cl, "boolean must be NOT NULL (Rule 3)")
                if d is None:
                    R.err(cl, "boolean needs a stated default")
            if dom == "long_text" and not c.get("unbounded_why"):
                R.err(cl, "unbounded STRING needs unbounded_why (Rule 3)")
            if dom == "email" and not has_check(t, cn, "lower("):
                R.err(cl, "email needs CHECK (col = lower(col)) constraint")
            if dom == "phone":
                if f"{cn}_country_id" not in {x["name"] for x in t["columns"]}:
                    R.err(cl, f"phone needs companion country column '{cn}_country_id' (Rule 3)")
                if not has_check(t, cn, "+"):
                    R.err(cl, "phone needs E.164 regex CHECK (col ~ '^\\+[1-9][0-9]{6,14}$')")
            if dom in ("percentage", "ratio") and not has_check(t, cn):
                R.err(cl, f"{dom} needs a range CHECK (Rule 3)")
            if dom in ("latitude", "longitude"):
                if not has_check(t, cn):
                    R.err(cl, "coordinate needs range CHECK")
                if "geo_source_id" not in cols and "geo_source_id" not in {x["name"] for x in t["columns"]}:
                    R.err(cl, "coordinates need a geo_source_id companion column (stated source)")
            if dom == "money":
                m_ = re.fullmatch(r"DECIMAL\((\d+),(\d+)\)", ty)
                if m_ and int(m_.group(2)) < 2:
                    R.err(cl, "money scale must be >= 2")
                if not any(x.get("domain") == "currency_code" or x["name"].endswith("currency_id") for x in t["columns"]):
                    R.err(cl, "money column needs a companion currency_id (FK currency) or currency_code column")
            if dom == "currency_code" and not has_check(t, cn):
                R.err(cl, "currency_code needs CHECK (col ~ '^[A-Z]{3}$')")
            if dom == "tax_id" and c.get("sens") != "Sensitive":
                R.err(cl, "tax identifiers are Sensitive (Rule 6)")
            if cn.endswith("_at") and dom != "timestamp":
                R.err(cl, "*_at columns must have domain timestamp")
            if cn.endswith("_date") and dom != "date":
                R.err(cl, "*_date columns must have domain date")
            if dom == "timestamp" and not cn.endswith("_at") and cn not in TS_NAME_OK:
                R.warn(cl, "timestamp column name should end _at")
            if dom == "free_text":
                mm = re.search(r"\((\d+)\)", ty)
                if mm and int(mm.group(1)) > 8000:
                    R.warn(cl, "free_text > 8000; consider long_text with unbounded_why")
        for a, b in PAIRS:
            for cn in list(cols):
                if cn.endswith(a):
                    other = cn[: -len(a)] + b
                    if other in cols and not has_check(t, cn, other):
                        R.err(f"{tn}.{cn}", f"date range ({cn},{other}) needs CHECK end >= start (Rule 3)")
        cnames = set()
        led = []
        for k in t["constraints"]:
            kn = k.get("name", "")
            kl = f"{tn}.{kn}"
            if kn in cnames:
                R.err(kl, "duplicate constraint name")
            cnames.add(kn)
            if k.get("type") == "unique":
                if not kn.startswith(f"uq_{tn}_"):
                    R.err(kl, f"unique constraint name must start uq_{tn}_")
                for c in k.get("columns", []) or []:
                    if c not in cols:
                        R.err(kl, f"unknown column {c}")
                if k.get("columns"):
                    led.append(k["columns"][0])
                allidx[kn] = tn
            elif k.get("type") == "check":
                if not kn.startswith(f"ck_{tn}_"):
                    R.err(kl, f"check name must start ck_{tn}_")
                if not k.get("expr"):
                    R.err(kl, "check needs expr")
            else:
                R.err(kl, f"constraint type must be unique|check, got {k.get('type')}")
            if not k.get("desc"):
                R.warn(kl, "constraint without desc")
        for ix in t["indexes"]:
            ixn = ix.get("name", "")
            il = f"{tn}.{ixn}"
            if ixn in allidx:
                R.err(il, f"duplicate index/constraint name (also on {allidx[ixn]})")
            allidx[ixn] = tn
            pref = f"uq_{tn}_" if ix.get("unique") else f"idx_{tn}_"
            if not ixn.startswith(pref):
                R.err(il, f"index name must start with {pref}")
            else:
                first = (ix.get("columns") or [""])[0].split(" ")[0]
                if not ixn[len(pref):].startswith(first):
                    R.err(il, f"index name suffix should start with its first column '{first}'")
            for c in ix.get("columns", []) or []:
                if c.split(" ")[0] not in cols:
                    R.err(il, f"unknown column {c.split(' ')[0]}")
            if ix.get("columns"):
                led.append(ix["columns"][0].split(" ")[0])
            if ix.get("hash") and ix.get("inverted"):
                R.err(il, "cannot be both hash and inverted")
            if ix.get("hash") and not ix.get("bucket_count"):
                R.err(il, "hash index needs bucket_count")
            for c in ix.get("storing", []) or []:
                if c not in cols:
                    R.err(il, f"unknown storing column {c}")
            # opclass convention: `opclass: gin_trgm_ops` (separate key) on every inverted index over a STRING column; never on JSONB
            opc = ix.get("opclass")
            if ix.get("inverted") and ix.get("columns"):
                lastc = ix["columns"][-1].split(" ")[0]
                lt = str(cols.get(lastc, {}).get("type", "")).upper()
                if lt.startswith("STRING") and opc != "gin_trgm_ops":
                    R.err(il, "inverted index on a STRING column needs `opclass: gin_trgm_ops` (trigram)")
                if not lt.startswith("STRING") and opc:
                    R.err(il, f"opclass '{opc}' is only valid on trigram (STRING) inverted indexes")
                if any(" gin_trgm_ops" in c for c in ix["columns"]):
                    R.err(il, "put the opclass in the `opclass` key, not into the column list")
            elif opc:
                R.err(il, "opclass is only valid on inverted indexes")
            if (ix.get("unique") and t["delete_policy"] == "soft" and "deleted_at" not in str(ix.get("where", ""))
                    and not ix.get("where_why")):
                R.warn(il, "unique index on soft-delete table: should be partial WHERE deleted_at IS NULL (or give where_why)")
            if not ix.get("desc"):
                R.warn(il, "index without desc")
        fk_cols = set()
        for fk in t["fks"]:
            fcols = fk.get("columns") or ([fk["column"]] if fk.get("column") else [])
            fk["columns"] = fcols
            fn = fk.get("name", "")
            fl = f"{tn}.{fn}"
            ref = fk.get("ref")
            rcols = fk.get("ref_columns") or ["id"]
            fk["ref_columns"] = rcols
            if not fn.startswith(f"fk_{tn}_"):
                R.err(fl, f"fk name must start fk_{tn}_")
            if fk.get("on_delete") not in ONDEL:
                R.err(fl, "on_delete must be stated: " + "/".join(sorted(ONDEL)))
            fk.setdefault("on_update", "RESTRICT")
            if fk["on_update"] not in ONDEL:
                R.err(fl, "bad on_update")
            if not fk.get("desc"):
                R.warn(fl, "fk without desc (state why this delete rule)")
            for c in fcols:
                fk_cols.add(c)
                if c not in cols:
                    R.err(fl, f"unknown fk column {c}")
                elif c in ("created_by", "updated_by", "deleted_by"):
                    R.err(fl, "created_by/updated_by/deleted_by are not FKs (D-04)")
            rt = ref_tables.get(ref)
            if rt is None:
                if not (partial and ref in reg_names):
                    R.err(fl, f"references unknown table '{ref}'")
                continue
            rcn = {x["name"] for x in rt["columns"]}
            for rc in rcols:
                if rc not in rcn:
                    R.err(fl, f"ref column {ref}.{rc} unknown")
            if rcols != ["id"]:
                ok = (any(set(k.get("columns", [])) == set(rcols) for k in rt["constraints"] if k["type"] == "unique")
                      or any(ix.get("unique") and set(ix.get("columns", [])) == set(rcols) and not ix.get("where") for ix in rt["indexes"]))
                if not ok:
                    R.err(fl, f"ref {ref}({rcols}) needs a matching non-partial unique constraint/index on the referenced table")
            for c, rc in zip(fcols, rcols):
                if c in cols:
                    rcol = next((x for x in rt["columns"] if x["name"] == rc), None)
                    if rcol and str(rcol["type"]).replace(" ", "") != str(cols[c]["type"]).replace(" ", ""):
                        R.err(fl, f"type mismatch {tn}.{c} {cols[c]['type']} vs {ref}.{rc} {rcol['type']}")
            if len(fcols) == 1 and fcols[0] in cols and rcols == ["id"]:
                c0 = fcols[0]
                if not c0.endswith(f"{ref}_id"):
                    R.err(fl, f"fk column '{c0}' must end with '{ref}_id' (role prefix allowed)")
                dom0 = cols[c0].get("domain")
                if rt["kind"] == "lookup" and dom0 != "enum_ref":
                    R.err(fl, "FK to a lookup table must use domain enum_ref")
                if rt["kind"] != "lookup" and dom0 == "enum_ref":
                    R.err(fl, "enum_ref column must reference a lookup table")
            if fcols and fcols[0] not in led and not fk.get("index_exempt") and not t["is_lookup"]:
                R.err(fl, f"FK column {fcols[0]} is not the leading column of any index/unique constraint "
                          f"(add an index or set index_exempt: <reason>)")
        for cn, c in cols.items():
            if cn.endswith("_id") and cn != "id" and cn not in fk_cols and not c.get("external_ref"):
                R.err(f"{tn}.{cn}", "column ending _id without FK (Rule 2); external ids are named *_ref (or external_ref: true)")
            if c.get("domain") == "enum_ref" and cn not in fk_cols:
                R.err(f"{tn}.{cn}", "enum_ref column has no FK to a lookup")
        if t["delete_policy"] == "soft" and "deleted_at" not in cols:
            R.err(tn, "soft table without deleted_at")
        if t.get("ttl") and "expire_after" not in t["ttl"]:
            R.err(tn, "ttl needs expire_after")
        if t["is_lookup"]:
            if not t.get("values"):
                R.warn(tn, "lookup has no seed values")
            for v in t.get("values", []):
                if "code" not in v or "en" not in v:
                    R.err(tn, f"lookup value needs code and en: {v}")
    for v in m["views"]:
        vn = v.get("name", "")
        if not vn.startswith(("vw_", "mv_")):
            R.err(f"view {vn}", "views must be vw_*, materialised mv_* (Rule 8)")
        for tt in v.get("tables", []) or []:
            if tt not in tables and not (partial and tt in reg_names):
                R.err(f"view {vn}", f"unknown table {tt}")
    for r in m["rules"]:
        if not all(r.get(k) for k in ("id", "rule", "mechanism_type", "mechanism")):
            R.err(f"rule {r.get('id')}", "rule needs id, rule, mechanism_type, mechanism")
        for tt in r.get("tables", []) or []:
            if tt not in tables and not (partial and tt in reg_names):
                R.err(f"rule {r.get('id')}", f"unknown table {tt}")
    print("\n".join(R.w[:300]))
    print("\n".join(R.e))
    print(f"\nfiles={m['files']} tables={len(tables)} lookups={sum(1 for t in tables.values() if t['is_lookup'])} "
          f"columns={sum(len(t['columns']) for t in tables.values())} views={len(m['views'])} rules={len(m['rules'])} "
          f"errors={len(R.e)} warnings={len(R.w)}")
    sys.exit(1 if R.e else 0)


if __name__ == "__main__":
    main()

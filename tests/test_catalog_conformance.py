"""Conformance of the LIVE catalog (TALENTRA_DB) to docs/05_data_dictionary.xlsx and the naming / domain conventions
(docket Section 5, docs/04_design_conventions.xlsx, docs/_staging/FORMAT_AND_DECISIONS.md D-01..D-20, docs/tools/lint_dictionary.py).
One catalog snapshot per session (pg_class / information_schema / pg_constraint / pg_indexes; no crdb_internal).
"""
import re
from pathlib import Path

import pytest

import helpers_conf as hc
from helpers_live import Live

D = hc.get_dict()
TOOLS = Path(__file__).resolve().parent.parent / "docs" / "tools"
SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


def _words(fn):
    out = set()
    for line in (TOOLS / fn).read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            out |= set(line.split())
    return out


RESERVED = _words("reserved_words.txt")
AVOID = _words("avoid_words.txt")
MODULES = sorted({t["module"] for t in D.tables.values()})
LOOKUPS = sorted(t for t, r in D.tables.items() if r["kind"] == "lookup")
NONLOOKUPS = sorted(t for t, r in D.tables.items() if r["kind"] != "lookup")


@pytest.fixture(scope="session")
def live(admin):
    return Live(admin)


def by_module(m):
    return sorted(t for t, r in D.tables.items() if r["module"] == m)


# =================================================================================== (1) tables / columns
def test_table_counts(live):
    assert len(D.tables) == 1306
    assert len(LOOKUPS) == 538
    assert len(live.tables) == 1306, f"extra/missing: {sorted(live.tables ^ set(D.tables))[:20]}"


def test_no_missing_or_extra_tables(live):
    assert not (set(D.tables) - live.tables), f"missing tables: {sorted(set(D.tables) - live.tables)[:20]}"
    assert not (live.tables - set(D.tables)), f"extra tables: {sorted(live.tables - set(D.tables))[:20]}"


def test_dictionary_views_exist(live):
    missing = sorted(set(D.views) - live.views)
    assert not missing, f"views missing: {missing}"
    for v, r in D.views.items():
        want = "m" if r["kind"] == "materialized" else "v"
        assert live.relkind[v] == want, f"{v}: relkind {live.relkind[v]} want {want}"


def test_dictionary_counts(live):
    assert len(D.views) == 78
    assert sum(len(v) for v in D.cols.values()) == 15784
    assert sum(len(v) for v in live.columns.values() if v) >= 15784


_DEFAULT_EQ = {}


def _same_default(conn, ctype, dflt, live_dflt):
    """Semantic comparison of a documented default and the live column_default (evaluated in one txn so now() agrees)."""
    key = (ctype, dflt, live_dflt)
    if key not in _DEFAULT_EQ:
        ct = hc.cast_type(ctype)
        _DEFAULT_EQ[key] = conn.execute(f"SELECT (({dflt})::{ct} IS NOT DISTINCT FROM ({live_dflt})::{ct})").fetchone()[0]
    return _DEFAULT_EQ[key]


def _strip_ident(e):
    return re.sub(r"[^a-z0-9_]+", " ", re.sub(r":::\w+(\(\d+(,\d+)?\))?", "", e.lower())).split()


@pytest.mark.parametrize("module", MODULES)
def test_columns_exactly_as_documented(admin, live, module):
    problems = []
    for t in by_module(module):
        lv = live.columns.get(t)
        want = D.cols[t]
        if lv is None:
            problems.append(f"{t}: no columns")
            continue
        wn, ln = [c["column"] for c in want], [c["name"] for c in lv]
        if wn != ln:
            problems.append(f"{t}: columns differ missing={sorted(set(wn) - set(ln))} extra={sorted(set(ln) - set(wn))} "
                            f"order_ok={sorted(wn) == sorted(ln)}")
            continue
        for w, c in zip(want, lv):
            n = w["column"]
            if hc.norm_type(w["type"]) != c["type"]:
                problems.append(f"{t}.{n}: type doc={w['type']} live={c['type']}")
            if (w["nullable"] == "YES") != c["nullable"]:
                problems.append(f"{t}.{n}: nullable doc={w['nullable']} live={c['nullable']}")
            comp = (w.get("computed_expr") or "").strip()
            if bool(comp) != bool(c["gen"]):
                problems.append(f"{t}.{n}: computed doc={comp!r} live={c['gen']!r}")
            elif comp:
                body = re.sub(r"\s+(STORED|VIRTUAL)$", "", comp)
                if set(_strip_ident(body)) - set(_strip_ident(c["gen"])) - {"stored", "virtual"}:
                    problems.append(f"{t}.{n}: computed expr doc={body!r} live={c['gen']!r}")
            else:
                d = str(w.get("default") or "").strip()
                if bool(d) != bool(c["default"]):
                    problems.append(f"{t}.{n}: default doc={d!r} live={c['default']!r}")
                elif d and not _same_default(admin, w["type"], d, c["default"]):
                    problems.append(f"{t}.{n}: default doc={d!r} live={c['default']!r}")
    assert not problems, f"{len(problems)} mismatches:\n" + "\n".join(problems[:40])


@pytest.mark.parametrize("module", MODULES)
def test_primary_key_pk_table_on_uuid_id(live, module):
    problems = []
    for t in by_module(module):
        c = live.columns[t][0]
        if (c["name"], c["type"], c["nullable"]) != ("id", "UUID", False) or "gen_random_uuid" not in (c["default"] or ""):
            problems.append(f"{t}: first column {c['name']} {c['type']} null={c['nullable']} default={c['default']}")
        pk = [n for n, ty in live.constraints.get(t, {}).items() if ty == "p"]
        if pk != [f"pk_{t}"]:
            problems.append(f"{t}: pk constraints {pk}")
    assert not problems, "\n".join(problems[:30])


def test_primary_key_is_id_only(live):
    bad = [t for t, p in live.parsed.items() if p["pk"] != (f"pk_{t}", ["id ASC"])]
    assert not bad, bad[:10]


# =================================================================================== (2) naming
def test_identifiers_snake_case_and_not_reserved(live):
    names = {}
    for t in live.tables | live.views:
        names.setdefault(t, "table/view")
    for t, cs in live.columns.items():
        for c in cs:
            names.setdefault(c["name"], "column")
    for t, ix in live.indexes.items():
        for n in ix:
            names.setdefault(n, "index")
    for t, cs in live.constraints.items():
        for n in cs:
            names.setdefault(n, "constraint")
    hidden = {n for n in names if n.startswith("crdb_internal")}
    bad_case = sorted(n for n in names if n not in hidden and not SNAKE.match(n))
    reserved = sorted(n for n in names if n in RESERVED)
    assert not bad_case, f"not snake_case: {bad_case[:20]}"
    assert not reserved, f"reserved words used as identifiers: {reserved}"


def test_avoid_words_not_used_as_table_names(live):
    assert not sorted(t for t in live.tables if t in AVOID)


def test_columns_using_risky_keywords_are_documented_exceptions(live):
    """lint WARNs about non-reserved risky words; the live catalog may contain them only if the dictionary does."""
    live_cols = {c["name"] for cs in live.columns.values() for c in cs} & AVOID
    doc_cols = {c["column"] for cs in D.cols.values() for c in cs} & AVOID
    assert live_cols == doc_cols


@pytest.mark.parametrize("module", MODULES)
def test_every_identifier_works_unquoted(admin, live, module):
    """Live check: SELECT every column of every table of the module WITHOUT quoting (fails for names that only work quoted)."""
    bad = []
    for t in by_module(module):
        cols = ", ".join(c["name"] for c in live.columns[t])
        try:
            admin.execute(f"SELECT {cols} FROM {t} LIMIT 0")
        except Exception as e:  # noqa
            bad.append(f"{t}: {str(e).splitlines()[0]}")
    assert not bad, "\n".join(bad[:20])


def test_keyword_catalog_probe(admin, live):
    """pg_get_keywords()-style probe if the engine has it: no table/column/index may be a reserved keyword."""
    try:
        kw = {r[0].lower() for r in admin.execute("SELECT word FROM pg_get_keywords() WHERE catcode = 'R'").fetchall()}
    except Exception:
        pytest.skip("pg_get_keywords unavailable")
    ids = set(live.tables) | {c["name"] for cs in live.columns.values() for c in cs} | {n for ix in live.indexes.values() for n in ix}
    assert not sorted(ids & kw)


def test_table_names_singular_and_prefixed():
    sing_ok = {"status", "address", "news", "analytics", "metadata", "series", "alias", "basis", "analysis", "campus", "bonus",
               "canvas", "access", "process", "business", "class", "progress", "consensus", "census", "focus", "lens", "radius",
               "virus", "corpus", "genus", "wellness", "gas", "atlas", "bias", "chaos"}
    plural = [t for t in D.tables if t.split("_")[-1].endswith("s") and not t.split("_")[-1].endswith(("ss", "us", "is"))
              and t.split("_")[-1] not in sing_ok]
    assert not plural, plural
    for t, r in D.tables.items():
        an = r["module"] == "analytics"
        pref = t.startswith(("dim_", "fact_", "agg_", "bridge_"))
        assert an == pref or (an and r["kind"] in ("staging",)), f"{t}: analytics prefix rule ({r['module']})"


def test_view_names(live):
    bad = [v for v in live.views if not v.startswith(("vw_", "mv_"))]
    assert not bad, bad


def test_index_and_constraint_prefixes(live):
    bad = []
    for t, ix in live.indexes.items():
        for n, d in ix.items():
            if n == f"pk_{t}":
                continue
            ok = n.startswith(f"uq_{t}_") if d["unique"] else n.startswith(f"idx_{t}_")
            if not ok:
                bad.append(f"index {n} on {t}")
    for t, cs in live.constraints.items():
        for n, ty in cs.items():
            pref = {"p": "pk_", "f": f"fk_{t}_", "c": f"ck_{t}_", "u": f"uq_{t}_"}[ty]
            if not n.startswith(pref):
                bad.append(f"constraint {n} ({ty}) on {t}")
    assert not bad, bad[:20]


def test_fk_columns_named_after_referenced_table(live):
    bad = []
    for t, fks in live.fks.items():
        for n, f in fks.items():
            if len(f["cols"]) == 1 and f["rcols"] == ["id"]:
                if not f["cols"][0].endswith(f"{f['ref']}_id"):
                    bad.append(f"{n}: {f['cols'][0]} -> {f['ref']}")
    assert not bad, bad[:20]


def test_id_columns_are_fks_or_documented_refs(live):
    """Every *_id column is an FK (Rule 2) except the audit actors (D-04) and the documented external refs."""
    fkcols = {(t, c) for t, fks in live.fks.items() for f in fks.values() for c in f["cols"]}
    bad = [f"{t}.{c['name']}" for t, cs in live.columns.items() for c in cs
           if c["name"].endswith("_id") and c["name"] != "id" and (t, c["name"]) not in fkcols
           and c["name"] not in ("created_by", "updated_by", "deleted_by")]
    doc_ext = {f"{c['table']}.{c['column']}" for cs in D.cols.values() for c in cs
               if c["column"].endswith("_id") and not c.get("fk_ref")}
    assert not (set(bad) - doc_ext), sorted(set(bad) - doc_ext)[:20]


# =================================================================================== (3) audit columns
@pytest.mark.parametrize("module", MODULES)
def test_audit_columns(live, module):
    bad = []
    for t in by_module(module):
        r = D.tables[t]
        names = {c["name"]: c for c in live.columns[t]}
        want = {"created_at", "created_by"}
        if r["audit_columns"] == "full":
            want |= {"updated_at", "updated_by"}
        if r["delete_policy"] == "soft":
            want |= {"deleted_at", "deleted_by"}
        for w in sorted(want):
            if w not in names:
                bad.append(f"{t}: missing {w}")
        for a in ("created_at", "updated_at", "deleted_at"):
            if a in names and names[a]["type"] != "TIMESTAMPTZ":
                bad.append(f"{t}.{a}: type {names[a]['type']}")
        for a in ("created_by", "updated_by", "deleted_by"):
            if a in names and names[a]["type"] != "UUID":
                bad.append(f"{t}.{a}: type {names[a]['type']}")
        if r["delete_policy"] != "soft" and ("deleted_at" in names or "deleted_by" in names):
            bad.append(f"{t}: deleted_* on a non-soft table")
        if r["audit_columns"] != "full" and ("updated_at" in names or "updated_by" in names):
            bad.append(f"{t}: updated_* on created_only table")
        if "created_at" in names and "now()" not in (names["created_at"]["default"] or ""):
            bad.append(f"{t}.created_at: default {names['created_at']['default']}")
    assert not bad, "\n".join(bad[:30])


def test_audit_actor_columns_are_not_fks(live):
    bad = [(t, n) for t, fks in live.fks.items() for n, f in fks.items()
           if set(f["cols"]) & {"created_by", "updated_by", "deleted_by"}]
    assert not bad, bad[:10]


# =================================================================================== (4) foreign keys


def test_fk_count(live):
    n = sum(len(v) for v in live.fks.values())
    assert n == len(D.sheets["ForeignKeys"]), (n, len(D.sheets["ForeignKeys"]))


@pytest.mark.parametrize("module", MODULES)
def test_foreign_keys_as_documented(live, module):
    bad = []
    for t in by_module(module):
        for f in D.fks.get(t, []):
            lf = live.fks.get(t, {}).get(f["fk_name"])
            if lf is None:
                bad.append(f"{t}: FK {f['fk_name']} missing")
                continue
            if lf["cols"] != [c.strip() for c in f["columns"].split(",")] or lf["ref"] != f["ref_table"] \
                    or lf["rcols"] != [c.strip() for c in f["ref_columns"].split(",")]:
                bad.append(f"{f['fk_name']}: cols/ref {lf} vs doc {f['columns']} -> {f['ref_table']}({f['ref_columns']})")
            if lf["on_delete"] != (f["on_delete"] or "NO ACTION").upper() \
                    or lf["on_update"] != (f["on_update"] or "NO ACTION").upper():
                bad.append(f"{f['fk_name']}: actions live={lf['on_delete']}/{lf['on_update']} "
                           f"doc={f['on_delete']}/{f['on_update']}")
        extra = set(live.fks.get(t, {})) - {f["fk_name"] for f in D.fks.get(t, [])}
        if extra:
            bad.append(f"{t}: undocumented FKs {sorted(extra)}")
    assert not bad, "\n".join(bad[:30])


def _idx_info(i):
    return i


def _colname(c):
    return re.split(r"\s+", c.strip())[0]


def _lead_cols(live):
    """table -> set of first columns of every index / unique / pk (D-20)."""
    out = {}
    for t, ix in live.indexes.items():
        for n, d in ix.items():
            i = _idx_info(d)
            cols = [c for c in (_colname(x) for x in i["cols"]) if "shard" not in c]
            if cols:
                out.setdefault(t, set()).add(cols[0])
    return out


def test_every_fk_column_leads_an_index(live):
    lead = _lead_cols(live)
    exempt = {f["fk_name"] for fl in D.fks.values() for f in fl if f.get("index_exempt")}
    bad = []
    for t, fks in live.fks.items():
        if D.tables[t]["kind"] == "lookup":
            continue
        for n, f in fks.items():
            if n in exempt:
                continue
            if f["cols"][0] not in lead.get(t, set()):
                bad.append(f"{t}.{n} ({f['cols'][0]})")
    assert not bad, f"{len(bad)} FK columns without a leading index: {bad[:20]}"


def test_index_exempt_fks_are_documented_with_reason():
    for fl in D.fks.values():
        for f in fl:
            if f.get("index_exempt"):
                assert len(str(f["index_exempt"])) > 5, f["fk_name"]


# =================================================================================== (5) indexes
@pytest.mark.parametrize("module", MODULES)
def test_indexes_as_documented(live, module):
    bad = []
    for t in by_module(module):
        li = live.indexes.get(t, {})
        for w in D.idx.get(t, []):
            n = w["index_name"]
            if n not in li:
                bad.append(f"{t}: index {n} missing")
                continue
            i = _idx_info(li[n])
            if i["unique"] != hc.yes(w["unique"]):
                bad.append(f"{n}: unique live={i['unique']} doc={w['unique']}")
            if i["inverted"] != hc.yes(w["inverted"]):
                bad.append(f"{n}: inverted live={i['inverted']} doc={w['inverted']}")
            if i["hash"] != hc.yes(w["hash_sharded"]):
                bad.append(f"{n}: hash_sharded live={i['hash']} doc={w['hash_sharded']}")
            if hc.yes(w["hash_sharded"]) and i["buckets"] != int(w["bucket_count"] or 16):
                bad.append(f"{n}: bucket_count live={i['buckets']} doc={w['bucket_count']}")
            if bool(i["where"]) != bool((w.get("where_predicate") or "").strip()):
                bad.append(f"{n}: partial predicate live={i['where']!r} doc={w.get('where_predicate')!r}")
            if (i["opclass"] or "") != (w.get("opclass") or ""):
                bad.append(f"{n}: opclass live={i['opclass']} doc={w.get('opclass')}")
            wc = [_colname(c) for c in w["columns"].split(",")]
            lc = [x for x in (_colname(c) for c in i["cols"]) if "shard" not in x]
            if wc != lc:
                bad.append(f"{n}: columns live={lc} doc={wc}")
            if sorted(x.strip() for x in str(w.get("storing") or "").split(",") if x.strip()) != sorted(i["storing"]):
                bad.append(f"{n}: storing live={i['storing']} doc={w.get('storing')}")
        extra = set(li) - {w["index_name"] for w in D.idx.get(t, [])} \
            - {c["constraint_name"] for c in D.cons.get(t, []) if c["constraint_type"] in ("PRIMARY KEY", "UNIQUE")}
        if extra:
            bad.append(f"{t}: undocumented indexes {sorted(extra)}")
    assert not bad, f"{len(bad)}:\n" + "\n".join(bad[:30])


def test_index_totals(live):
    n = sum(len(v) for v in live.indexes.values())
    want = len(D.sheets["Indexes"]) + sum(1 for c in D.sheets["Constraints"] if c["constraint_type"] in ("PRIMARY KEY", "UNIQUE"))
    assert n == want, (n, want)


@pytest.mark.parametrize("module", MODULES)
def test_unique_and_pk_constraints_as_documented(live, module):
    bad = []
    for t in by_module(module):
        for c in D.cons.get(t, []):
            if c["constraint_type"] in ("PRIMARY KEY", "UNIQUE"):
                n = c["constraint_name"]
                ty = live.constraints.get(t, {}).get(n)
                want = "p" if c["constraint_type"] == "PRIMARY KEY" else "u"
                if ty != want:
                    bad.append(f"{t}.{n}: live type {ty} want {want}")
                    continue
                i = _idx_info(live.indexes[t][n])
                if [_colname(x) for x in i["cols"] if "shard" not in x] != [x.strip() for x in c["columns"].split(",")]:
                    bad.append(f"{t}.{n}: columns {i['cols']} vs {c['columns']}")
    assert not bad, "\n".join(bad[:30])


# =================================================================================== (6) checks (existence)
@pytest.mark.parametrize("module", MODULES)
def test_checks_exist_as_documented(live, module):
    bad = []
    names = set()
    for t in by_module(module):
        lc = live.checks.get(t, {})
        for c in D.checks(t):
            n = c["constraint_name"]
            names.add(n)
            if n not in lc:
                bad.append(f"{t}.{n} missing")
                continue
            want = set(hc.refs_of(c["expression"], {x["column"] for x in D.cols[t]}))
            got = set(hc.refs_of(lc[n], {x["column"] for x in D.cols[t]}))
            if want != got:
                bad.append(f"{t}.{n}: columns live={sorted(got)} doc={sorted(want)}")
        extra = set(lc) - {c["constraint_name"] for c in D.checks(t)}
        # NOT NULL constraints are not reported by pg_constraint contype 'c' on CRDB; anything extra is a deviation
        if extra:
            bad.append(f"{t}: undocumented checks {sorted(extra)}")
    assert not bad, f"{len(bad)}:\n" + "\n".join(bad[:30])


def test_check_total(live):
    assert sum(len(v) for v in live.checks.values()) == 2183


# =================================================================================== (7) lookups
def test_lookup_structure(live):
    bad = []
    for t in LOOKUPS:
        names = [c["name"] for c in live.columns[t]]
        for must in ("id", "code", "name_i18n", "sort_order", "is_active", "created_at", "created_by", "updated_at", "updated_by"):
            if must not in names:
                bad.append(f"{t}: missing {must}")
    assert not bad, bad[:10]


def test_lookup_seeds_match_dictionary(admin):
    bad = []
    for t in LOOKUPS:
        rows = admin.execute(f"SELECT code, name_i18n, sort_order, is_active FROM {t}").fetchall()
        live_by = {r[0]: r for r in rows}
        want = {}
        for e in D.enums.get(t, []):
            w = want.setdefault(e["code"], dict(sort=e["sort_order"], active=e["is_active"], names={}))
            w["names"][e["language_tag"]] = e["display_name"]
        for code, w in want.items():
            r = live_by.get(code)
            if r is None:
                bad.append(f"{t}: code {code} missing")
                continue
            if "en" not in r[1]:
                bad.append(f"{t}.{code}: no en name")
            for lang, nm in w["names"].items():
                if r[1].get(lang) != nm:
                    bad.append(f"{t}.{code}: name[{lang}] {r[1].get(lang)!r} != {nm!r}")
            if int(r[2]) != int(w["sort"] or 0) or r[3] != hc.yes(w["active"]):
                bad.append(f"{t}.{code}: sort/active live=({r[2]},{r[3]}) doc=({w['sort']},{w['active']})")
        # extra live codes are allowed only when they are not part of the dictionary (tests may add rows 't_...')
        extra = [c for c in live_by if c not in want and not c.startswith("t_")]
        if extra:
            bad.append(f"{t}: undocumented codes {extra[:5]}")
    assert not bad, f"{len(bad)}:\n" + "\n".join(bad[:30])


def test_every_seeded_lookup_has_at_least_one_code(admin):
    empty = [t for t in LOOKUPS if t in D.enums and admin.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0]
    assert not empty, empty

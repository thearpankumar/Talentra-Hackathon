"""Independent re-implementation of every atomic Section 5 rule check over the expanded model.

Each check returns (violations: int, population: int, sample: list[str]).  Used by build_compliance.py (sheet RuleChecks)
and by build_justification.py (risk register).  Lint cross-reference: the `lint` field names the lint_dictionary.py message
or states 'independent' when the lint does not cover it."""
from __future__ import annotations
import os, re, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import design_common as dc
import lint_dictionary as L

M = dc.model()
T = M["tables"]
TB = {t["name"]: t for t in T}
RES, AVOID = dc.reserved_lists()
SNAKE = L.SNAKE
CHECK_RULES = []   # (id, group, rule, how, lint, fn, severity)


def check(id_, group, rule, how, lint, severity="rule"):
    def deco(fn):
        CHECK_RULES.append((id_, group, rule, how, lint, fn, severity))
        return fn
    return deco


def cols(t):
    return {c["name"]: c for c in t["columns"]}


def has_check(t, *names):
    return any(k["type"] == "check" and all(n in str(k.get("expr", "")) for n in names) for k in t["constraints"])


def res(bad, pop):
    bad = list(bad)
    return len(bad), pop, bad[:5]


# ---------------------------------------------------------------- group 1 naming
@check("N01", 1, "Table names are snake_case", "regex ^[a-z][a-z0-9]*(_[a-z0-9]+)*$ over every table", "check_ident: 'is not snake_case'")
def _(): return res([t["name"] for t in T if not SNAKE.match(t["name"])], len(T))


@check("N02", 1, "Column names are snake_case (no spaces/special characters)", "same regex over every column", "check_ident: 'is not snake_case'")
def _(): return res([f"{t['name']}.{c['name']}" for t in T for c in t["columns"] if not SNAKE.match(c["name"])], sum(len(t["columns"]) for t in T))


@check("N03", 1, "Table names are singular nouns", "last token must not end in plural -s (allow-list for status, address, analytics ...)", "check_ident: 'looks plural'")
def _():
    bad = []
    for t in T:
        last = t["name"].split("_")[-1]
        if last.endswith("s") and not last.endswith(("ss", "us", "is")) and last not in L.SING_OK:
            bad.append(t["name"])
    return res(bad, len(T))


@check("N04", 1, "Primary key is named id (UUID) on every table", "first column is id UUID with default gen_random_uuid(); no other PK", "injected by dictlib; 'do not declare standard columns'")
def _():
    bad = [t["name"] for t in T if not (t["columns"][0]["name"] == "id" and t["columns"][0]["type"] == "UUID" and t["columns"][0]["default"] == "gen_random_uuid()")]
    return res(bad, len(T))


@check("N05", 1, "Foreign key columns are named <referenced table>_id (role prefix allowed)", "every single-column FK to ref(id): column ends with '<ref>_id'", "fk column must end with '<ref>_id'")
def _():
    bad, pop = [], 0
    for t in T:
        for f in t["fks"]:
            if len(f["columns"]) == 1 and f["ref_columns"] == ["id"]:
                pop += 1
                if not f["columns"][0].endswith(f["ref"] + "_id"):
                    bad.append(f"{t['name']}.{f['columns'][0]}->{f['ref']}")
    return res(bad, pop)


@check("N06", 1, "No reserved word is used as a table name", "table names vs reserved_words.txt (PG + CockroachDB + MySQL union)", "check_ident: 'is a reserved word'")
def _(): return res([t["name"] for t in T if t["name"] in RES], len(T))


@check("N07", 1, "No reserved word is used as a column name", "every distinct column name vs reserved_words.txt", "check_ident: 'is a reserved word'")
def _():
    names = {c["name"] for t in T for c in t["columns"]}
    return res(sorted(names & RES), len(names))


@check("N08", 1, "No reserved word is used as a view name", "every view name vs reserved_words.txt", "independent")
def _():
    names = {v["name"] for v in M["views"]}
    return res(sorted(names & RES), len(names))


@check("N09", 1, "Risky non-reserved keywords are not used as identifiers (warning list)", "table/column/view names vs avoid_words.txt", "check_ident WARN: 'risky non-reserved keyword'", "warn")
def _():
    names = {t["name"] for t in T} | {c["name"] for t in T for c in t["columns"]} | {v["name"] for v in M["views"]}
    return res(sorted(names & AVOID), len(names))


@check("N10", 1, "Index names follow idx_<table>_<column> and start with the first key column", "idx_<table>_ prefix and suffix starts with first column", "index name must start with idx_<table>_ / suffix should start with first column")
def _():
    bad, pop = [], 0
    for t in T:
        for i in t["indexes"]:
            if i.get("unique"):
                continue
            pop += 1
            pref = f"idx_{t['name']}_"
            first = (i.get("columns") or [""])[0].split(" ")[0]
            if not (i["name"].startswith(pref) and i["name"][len(pref):].startswith(first)):
                bad.append(i["name"])
    return res(bad, pop)


@check("N11", 1, "Unique constraint / unique index names follow uq_<table>_<column>", "uq_<table>_ prefix on unique constraints and unique indexes", "unique constraint name must start uq_<table>_")
def _():
    bad, pop = [], 0
    for t in T:
        for k in t["constraints"]:
            if k["type"] == "unique":
                pop += 1
                if not k["name"].startswith(f"uq_{t['name']}_"):
                    bad.append(k["name"])
        for i in t["indexes"]:
            if i.get("unique"):
                pop += 1
                if not i["name"].startswith(f"uq_{t['name']}_"):
                    bad.append(i["name"])
    return res(bad, pop)


@check("N12", 1, "Foreign key constraints are named fk_<table>_<col>", "fk_<table>_ prefix", "fk name must start fk_<table>_")
def _():
    bad = [f["name"] for t in T for f in t["fks"] if not f["name"].startswith(f"fk_{t['name']}_")]
    return res(bad, sum(len(t["fks"]) for t in T))


@check("N13", 1, "Check constraints are named ck_<table>_<what>", "ck_<table>_ prefix", "check name must start ck_<table>_")
def _():
    bad = [k["name"] for t in T for k in t["constraints"] if k["type"] == "check" and not k["name"].startswith(f"ck_{t['name']}_")]
    return res(bad, sum(1 for t in T for k in t["constraints"] if k["type"] == "check"))


@check("N14", 1, "Index / constraint names are unique across the schema", "global uniqueness of idx_/uq_/ck_/fk_ names", "duplicate index/constraint name")
def _():
    seen = collections.Counter()
    for t in T:
        for x in t["indexes"] + t["constraints"] + t["fks"]:
            seen[x["name"]] += 1
    bad = [k for k, v in seen.items() if v > 1]
    return res(bad, len(seen))


@check("N15", 1, "Table names are globally unique", "duplicate table name across staging files", "duplicate table")
def _():
    c = collections.Counter(t["name"] for t in T)
    return res([k for k, v in c.items() if v > 1], len(c))


@check("N16", 8, "Analytics tables carry dim_/fact_/agg_/bridge_ prefixes", "module analytics tables start with a prefix", "analytics table must start dim_/fact_/agg_/bridge_")
def _():
    a = [t for t in T if t["module"] == "analytics"]
    return res([t["name"] for t in a if not t["name"].startswith(("dim_", "fact_", "agg_", "bridge_"))], len(a))


@check("N17", 8, "dim_/fact_/agg_ prefixes are not used outside analytics", "non-analytics tables must not start with dim_/fact_/agg_", "dim_/fact_/agg_ prefix is reserved")
def _():
    o = [t for t in T if t["module"] != "analytics"]
    return res([t["name"] for t in o if t["name"].startswith(("dim_", "fact_", "agg_"))], len(o))


@check("N18", 8, "Views are vw_*, materialised views mv_*", "every view name prefix", "views must be vw_*, materialised mv_*")
def _():
    vs = M["views"]
    bad = [v["name"] for v in vs if not v["name"].startswith(("vw_", "mv_"))]
    bad += [v["name"] for v in vs if v.get("kind") == "materialized" and not v["name"].startswith("mv_")]
    return res(bad, len(vs))


@check("N19", 8, "View names are unique across modules", "duplicate view name across staging files", "NOT covered by lint (finding)")
def _():
    c = collections.Counter(v["name"] for v in M["views"])
    return res([f"{k} x{v}" for k, v in c.items() if v > 1], len(c))


@check("N20", 1, "Identifier length <= 63 (portable to stock PostgreSQL)", "index/constraint/FK names > 63 characters (CockroachDB allows 128)", "NOT enforced by lint (deviation DEV-06)", "deviation")
def _():
    bad = [x["name"] for t in T for x in t["indexes"] + t["constraints"] + t["fks"] if len(x["name"]) > 63]
    return res(bad, sum(len(t["indexes"]) + len(t["constraints"]) + len(t["fks"]) for t in T))


@check("N21", 1, "Table and column names stay within 63 characters", "tables and columns", "independent")
def _():
    bad = [t["name"] for t in T if len(t["name"]) > 63] + [f"{t['name']}.{c['name']}" for t in T for c in t["columns"] if len(c["name"]) > 63]
    return res(bad, len(T) + sum(len(t["columns"]) for t in T))


PLAIN = set("""at by is to in of on up as for new set not can has run key day tag tip top off out low net pay use log map raw bid cap ran row sum
no do am i e and or sms ok add all one two per pre non end hit bio top rank rate test live open scan seen sent size read send cost body line page
list note mode role rule plan step tier view fee file free full gap goal hide home hold join kind late lift load loss mask mix next only part path pool
poll post risk root room safe self show sign slot stem term text time unit used uses via vote walk week when with word work year zero zone auto bank bids bill does due ends quiz slug sort task""".split())


@check("N22", 1, "Abbreviations in names are defined in the design (04_design_conventions.xlsx, Abbreviations sheet)", "every name token of <= 4 letters is an English word or a defined abbreviation", "independent (Rule 1: no undefined abbreviations)")
def _():
    from build_conventions import ABBR
    toks = set()
    for t in T:
        toks |= set(t["name"].split("_"))
        for c in t["columns"]:
            toks |= set(c["name"].split("_"))
    short = {x for x in toks if len(x) <= 4 and x.isalpha()}
    bad = sorted(x for x in short if x not in ABBR and x not in PLAIN and not (x in WORDS_OK))
    return res(bad, len(short))


WORDS_OK = set("""name code type date user app job gig ad ref text hash from to end last rate time note max min url data plan days mode item work city tax flag view link tag band open fact
agg dim etl rows lag avg unit page seat line term paid tier geo week ms fee bid live fill team room spam off push api faq set prev seen tip hold diff hire raw scan poll pack mock next
join scam star seq hour byte area bio seo walk base feed hq logo alt back lo hi stat pair met left msp card pay core exit hit kyc jobs mask vote cta root life desk json tile opt four eyes
kib must undo dry late full part bulk age skip burn when safe stem mix pro bono word done lift uses via ids ttl og roll po lost loss law km sum help flat tags idle peak iana utc dst iso2 iso3
eea gst f ror hide pros cons map free ran fk tds rent into hash case kind load rank rule home send sent size path year day body show unit sync tier lag cost hard self test pool use can dpa
top gain pass up xp ci cap call high side os only head sink held host auth http gigs out cpa sac hsn p10 p25 p50 p75 p90 p95 sac""".split())


# ---------------------------------------------------------------- group 2 keys
@check("K01", 2, "No SERIAL / sequence / integer surrogate defaults anywhere", "defaults containing unique_rowid, nextval, serial", "independent")
def _():
    bad = [f"{t['name']}.{c['name']}" for t in T for c in t["columns"] if re.search(r"unique_rowid|nextval|serial", str(c.get("default") or ""), re.I)]
    return res(bad, sum(len(t["columns"]) for t in T))


@check("K02", 2, "Every *_id column (other than id) is an enforced foreign key", "column ends _id, is not id, has no FK and is not external_ref", "column ending _id without FK")
def _():
    bad, pop = [], 0
    for t in T:
        fkc = {c for f in t["fks"] for c in f["columns"]}
        for c in t["columns"]:
            if c["name"].endswith("_id") and c["name"] != "id":
                pop += 1
                if c["name"] not in fkc and not c.get("external_ref"):
                    bad.append(f"{t['name']}.{c['name']}")
    return res(bad, pop)


@check("K03", 2, "Every foreign key states its delete rule", "on_delete in RESTRICT/CASCADE/SET NULL/NO ACTION/SET DEFAULT", "on_delete must be stated")
def _():
    ok = {"RESTRICT", "CASCADE", "SET NULL", "NO ACTION", "SET DEFAULT"}
    bad = [f["name"] for t in T for f in t["fks"] if f.get("on_delete") not in ok]
    return res(bad, sum(len(t["fks"]) for t in T))


@check("K04", 2, "Every foreign key states its update rule", "on_update stated (default RESTRICT)", "bad on_update")
def _():
    ok = {"RESTRICT", "CASCADE", "SET NULL", "NO ACTION", "SET DEFAULT"}
    bad = [f["name"] for t in T for f in t["fks"] if f.get("on_update") not in ok]
    return res(bad, sum(len(t["fks"]) for t in T))


@check("K05", 2, "Every foreign key references an existing table and columns", "ref table and ref columns exist", "references unknown table / ref column unknown")
def _():
    bad = []
    for t in T:
        for f in t["fks"]:
            r = TB.get(f["ref"])
            if r is None or any(rc not in {c["name"] for c in r["columns"]} for rc in f["ref_columns"]):
                bad.append(f["name"])
    return res(bad, sum(len(t["fks"]) for t in T))


@check("K06", 2, "FK column types equal the referenced column types", "type string equality per column pair", "type mismatch")
def _():
    bad = []
    for t in T:
        cc = cols(t)
        for f in t["fks"]:
            r = TB.get(f["ref"])
            if not r:
                continue
            rc = cols(r)
            for a, b in zip(f["columns"], f["ref_columns"]):
                if a in cc and b in rc and cc[a]["type"].replace(" ", "") != rc[b]["type"].replace(" ", ""):
                    bad.append(f["name"])
    return res(bad, sum(len(t["fks"]) for t in T))


@check("K07", 2, "Composite FK targets have a matching non-partial UNIQUE", "UNIQUE constraint or unique non-partial index over exactly the referenced columns", "needs a matching non-partial unique constraint/index")
def _():
    bad, pop = [], 0
    for t in T:
        for f in t["fks"]:
            if f["ref_columns"] != ["id"]:
                pop += 1
                r = TB[f["ref"]]
                ok = any(set(k.get("columns", [])) == set(f["ref_columns"]) for k in r["constraints"] if k["type"] == "unique") or \
                    any(i.get("unique") and set(i.get("columns", [])) == set(f["ref_columns"]) and not i.get("where") for i in r["indexes"])
                if not ok:
                    bad.append(f["name"])
    return res(bad, pop)


@check("K08", 2, "Every FK column leads an index or unique constraint (or carries index_exempt)", "leading-column coverage per FK", "FK column is not the leading column of any index")
def _():
    bad, pop = [], 0
    for t in T:
        if t["is_lookup"]:
            continue
        led = set()
        for k in t["constraints"]:
            if k["type"] == "unique" and k.get("columns"):
                led.add(k["columns"][0])
        for i in t["indexes"]:
            if i.get("columns"):
                led.add(i["columns"][0].split(" ")[0])
        for f in t["fks"]:
            pop += 1
            if f["columns"][0] not in led and not f.get("index_exempt"):
                bad.append(f["name"])
    return res(bad, pop)


@check("K09", 2, "created_by/updated_by/deleted_by are not foreign keys (D-04 deviation, compensating job)", "no FK on the three audit columns", "created_by/updated_by/deleted_by are not FKs (D-04)")
def _():
    bad = [f["name"] for t in T for f in t["fks"] if set(f["columns"]) & {"created_by", "updated_by", "deleted_by"}]
    return res(bad, S_TABLES())


def S_TABLES():
    return len(T)


@check("K10", 2, "FK to a lookup uses domain enum_ref and every enum_ref column has an FK to a lookup", "domain/FK consistency (single or composite FK)", "FK to a lookup table must use domain enum_ref / enum_ref column has no FK")
def _():
    bad, pop = [], 0
    for t in T:
        fkc = {}
        for f in t["fks"]:
            for c in f["columns"]:
                fkc.setdefault(c, []).append(f)
        for c in t["columns"]:
            if c["domain"] == "enum_ref":
                pop += 1
                if not any(TB[f["ref"]]["kind"] == "lookup" for f in fkc.get(c["name"], [])):
                    bad.append(f"{t['name']}.{c['name']}")
        for f in t["fks"]:
            if len(f["columns"]) == 1 and TB[f["ref"]]["kind"] == "lookup" and f["ref_columns"] == ["id"] and cols(t)[f["columns"][0]]["domain"] != "enum_ref":
                bad.append(f"{t['name']}.{f['columns'][0]}")
    return res(bad, pop)


@check("K11", 2, "Natural keys of lookups are UNIQUE constraints (code)", "every lookup has uq_<lookup>_code", "expansion in dictlib.expand_lookup")
def _():
    bad = [t["name"] for t in T if t["is_lookup"] and not any(k["type"] == "unique" and k["columns"] == ["code"] for k in t["constraints"])]
    return res(bad, sum(1 for t in T if t["is_lookup"]))


@check("K12", 2, "Unique indexes on soft-delete tables are partial (deleted_at IS NULL) or justified", "unique index without deleted_at predicate and without where_why", "unique index on soft-delete table: should be partial (or give where_why)")
def _():
    bad, pop = [], 0
    for t in T:
        if t["delete_policy"] == "soft":
            for i in t["indexes"]:
                if i.get("unique"):
                    pop += 1
                    if "deleted_at" not in str(i.get("where", "")) and not i.get("where_why"):
                        bad.append(i["name"])
    return res(bad, pop)


@check("K13", 2, "Licence limits are explicit positive-integer columns with CHECKs", "licence.max_users and max_devices_per_user NOT NULL INT with CHECK > 0 / >= 1", "independent (BR-LIC-01)")
def _():
    t = TB["licence"]
    c = cols(t)
    bad = []
    for n in ("max_users", "max_devices_per_user"):
        if n not in c or c[n].get("null") or not c[n]["type"].startswith("INT"):
            bad.append(n)
    if not has_check(t, "max_users", "> 0"):
        bad.append("ck max_users")
    if not has_check(t, "max_devices_per_user", "1"):
        bad.append("ck max_devices")
    return res(bad, 2)


@check("K14", 2, "Device limit enforced at login by a declarative slot claim (active session claims one slot)", "partial UNIQUE (device_slot_id) WHERE ended_at IS NULL on device_session + composite FK to device_slot", "independent (BR-LIC-05/06)")
def _():
    t = TB["device_session"]
    ok = any(i.get("unique") and i["columns"] == ["device_slot_id"] and "ended_at IS NULL" in str(i.get("where")) for i in t["indexes"])
    ok2 = any(f["ref"] == "device_slot" and len(f["columns"]) == 2 for f in t["fks"])
    return res([] if ok and ok2 else ["device_session slot claim missing"], 1)


@check("K15", 2, "Every FK that guards cross-row invariants is a composite FK where D-19 patterns are declared", "count of composite FKs (information)", "independent", "info")
def _():
    return 0, sum(1 for t in T for f in t["fks"] if len(f["columns"]) > 1), []


# ---------------------------------------------------------------- group 3 domains
@check("D01", 3, "Every column has a known standard domain", "domain in lint DOMAIN_TYPES", "unknown domain")
def _():
    bad = [f"{t['name']}.{c['name']}" for t in T for c in t["columns"] if c["domain"] not in L.DOMAIN_TYPES]
    return res(bad, sum(len(t["columns"]) for t in T))


@check("D02", 3, "Column type matches its domain", "regex of allowed CockroachDB types per domain", "type does not match domain")
def _():
    bad = []
    for t in T:
        for c in t["columns"]:
            ty = str(c["type"]).replace(" ", "")
            if c["domain"] in L.DOMAIN_TYPES and not any(re.fullmatch(p, ty) for p in L.DOMAIN_TYPES[c["domain"]]):
                bad.append(f"{t['name']}.{c['name']}")
    return res(bad, sum(len(t["columns"]) for t in T))


@check("D03", 3, "Person names are STRING(150); emails STRING(254); phones STRING(16)", "domain->type", "type does not match domain")
def _():
    pop = [c for t in T for c in t["columns"] if c["domain"] in ("person_name", "email", "phone")]
    want = {"person_name": "STRING(150)", "email": "STRING(254)", "phone": "STRING(16)"}
    bad = [c["name"] for c in pop if c["type"].replace(" ", "") != want[c["domain"]]]
    return res(bad, len(pop))


@check("D04", 3, "Free text has a stated maximum; unbounded STRING only with unbounded_why", "long_text columns without unbounded_why; free_text > 8000", "unbounded STRING needs unbounded_why / free_text > 8000")
def _():
    bad = [f"{t['name']}.{c['name']}" for t in T for c in t["columns"] if c["domain"] == "long_text" and not c.get("unbounded_why")]
    for t in T:
        for c in t["columns"]:
            if c["domain"] == "free_text":
                m = re.search(r"\((\d+)\)", c["type"])
                if m and int(m.group(1)) > 8000:
                    bad.append(f"{t['name']}.{c['name']}")
    return res(bad, sum(1 for t in T for c in t["columns"] if c["domain"] in ("free_text", "long_text")))


@check("D05", 3, "Email: lowercase CHECK on every email column", "CHECK col = lower(col)", "email needs CHECK (col = lower(col))")
def _():
    bad, pop = [], 0
    for t in T:
        for c in t["columns"]:
            if c["domain"] == "email":
                pop += 1
                if not has_check(t, c["name"], "lower("):
                    bad.append(f"{t['name']}.{c['name']}")
    return res(bad, pop)


@check("D06", 3, "Phone: E.164 regex CHECK and companion country column", "CHECK col ~ '^\\+[1-9]...' and <col>_country_id", "phone needs E.164 regex CHECK / companion country column")
def _():
    bad, pop = [], 0
    for t in T:
        for c in t["columns"]:
            if c["domain"] == "phone":
                pop += 1
                if not has_check(t, c["name"], "+") or f"{c['name']}_country_id" not in cols(t):
                    bad.append(f"{t['name']}.{c['name']}")
    return res(bad, pop)


@check("D07", 3, "Tax/registration identifiers are Sensitive, hashed/encrypted, with country+type code", "tax_id domain columns are Sensitive; organisation_identifier has country_id + tax_id_type_id", "tax identifiers are Sensitive (Rule 6)")
def _():
    bad = [f"{t['name']}.{c['name']}" for t in T for c in t["columns"] if c["domain"] == "tax_id" and c["sens"] != "Sensitive"]
    oi = cols(TB["organisation_identifier"])
    for n in ("country_id", "tax_id_type_id", "value_encrypted", "value_lookup_hash"):
        if n not in oi:
            bad.append("organisation_identifier." + n)
    return res(bad, 1 + 4)


@check("D08", 3, "Money is fixed-precision DECIMAL (scale >= 2) with a companion currency column; never FLOAT", "money domain scale and currency companion in the same table", "money scale must be >= 2 / needs a companion currency")
def _():
    bad, pop = [], 0
    for t in T:
        for c in t["columns"]:
            if c["domain"] == "money":
                pop += 1
                m = re.fullmatch(r"DECIMAL\((\d+),(\d+)\)", c["type"].replace(" ", ""))
                comp = any(x["domain"] == "currency_code" or x["name"].endswith("currency_id") for x in t["columns"])
                if not m or int(m.group(2)) < 2 or not comp:
                    bad.append(f"{t['name']}.{c['name']}")
    return res(bad, pop)


@check("D09", 3, "Currency codes are ISO alpha-3 with a CHECK", "currency_code domain has ^[A-Z]{3}$ CHECK", "currency_code needs CHECK")
def _():
    bad, pop = [], 0
    for t in T:
        for c in t["columns"]:
            if c["domain"] == "currency_code":
                pop += 1
                if not has_check(t, c["name"]):
                    bad.append(f"{t['name']}.{c['name']}")
    return res(bad, pop)


@check("D10", 3, "Percentages and ratios have a stated range CHECK", "range CHECK mentioning the column", "percentage/ratio needs a range CHECK")
def _():
    bad, pop = [], 0
    for t in T:
        for c in t["columns"]:
            if c["domain"] in ("percentage", "ratio"):
                pop += 1
                if not has_check(t, c["name"]):
                    bad.append(f"{t['name']}.{c['name']}")
    return res(bad, pop)


@check("D11", 3, "Dates are DATE (*_date) and timestamps TIMESTAMPTZ (*_at)", "suffix <-> domain consistency", "*_at columns must have domain timestamp / *_date must have domain date")
def _():
    bad, pop = [], 0
    for t in T:
        for c in t["columns"]:
            if c["name"].endswith("_at") or c["name"].endswith("_date"):
                pop += 1
                if c["name"].endswith("_at") and c["domain"] != "timestamp" or c["name"].endswith("_date") and c["domain"] != "date":
                    bad.append(f"{t['name']}.{c['name']}")
    return res(bad, pop)


@check("D12", 3, "Every start/end pair has a CHECK end >= start", "known pair names present in a table need a CHECK mentioning both", "date range needs CHECK end >= start")
def _():
    bad, pop = [], 0
    for t in T:
        cc = cols(t)
        for a, b in L.PAIRS:
            for cn in list(cc):
                if cn.endswith(a):
                    other = cn[: -len(a)] + b
                    if other in cc:
                        pop += 1
                        if not has_check(t, cn, other):
                            bad.append(f"{t['name']}.{cn}")
    return res(bad, pop)


@check("D13", 3, "Minimum/maximum value pairs have a CHECK min <= max (salary, experience, size ...)", "columns with 'min' and 'max' twins in the same table need a CHECK mentioning both", "independent (Rule 2: minimum salary must not exceed maximum)")
def _():
    bad, pop = [], 0
    for t in T:
        cc = cols(t)
        for cn in cc:
            if "_min" in cn or cn.startswith("min_"):
                other = cn.replace("_min", "_max") if "_min" in cn else cn.replace("min_", "max_", 1)
                if other in cc and other != cn:
                    pop += 1
                    if not has_check(t, cn, other):
                        bad.append(f"{t['name']}.{cn}/{other}")
    return res(bad, pop)


@check("D14", 3, "Coordinates carry a range CHECK and a stated source (geo_source_id)", "latitude/longitude columns", "coordinate needs range CHECK / geo_source_id")
def _():
    bad, pop = [], 0
    for t in T:
        for c in t["columns"]:
            if c["domain"] in ("latitude", "longitude"):
                pop += 1
                if not has_check(t, c["name"]) or "geo_source_id" not in cols(t):
                    bad.append(f"{t['name']}.{c['name']}")
    return res(bad, pop)


@check("D15", 3, "Boolean flags are NOT NULL with a stated default", "boolean domain", "boolean must be NOT NULL / needs a stated default")
def _():
    bad, pop = [], 0
    for t in T:
        for c in t["columns"]:
            if c["domain"] == "boolean":
                pop += 1
                if c.get("null") or c.get("default") is None:
                    bad.append(f"{t['name']}.{c['name']}")
    return res(bad, pop)


@check("D16", 3, "UI label keys are keys only: STRING(150) and either the ui_label key itself or guarded by a key-format CHECK / FK", "ui_label_key columns", "type does not match domain; dangling-key guard NOT covered by lint (finding)", "finding")
def _():
    bad, pop = [], 0
    for t in T:
        for c in t["columns"]:
            if c["domain"] == "ui_label_key":
                pop += 1
                if c["type"].replace(" ", "") != "STRING(150)" or not has_check(t, c["name"]):
                    bad.append(f"{t['name']}.{c['name']}")
    return res(bad, pop)


@check("D17", 3, "Enumerations are lookup tables with code, per-language name, sort order, active flag; no CHECK-IN lists", "every lookup has the four columns; no CHECK expression contains an IN ('...') literal list", "dictlib.expand_lookup; independent for CHECK-IN")
def _():
    bad = []
    for t in T:
        if t["is_lookup"] and not {"code", "name_i18n", "sort_order", "is_active"} <= set(cols(t)):
            bad.append(t["name"])
        for k in t["constraints"]:
            if k["type"] == "check" and re.search(r"\bIN\s*\(\s*'", str(k.get("expr"))):
                bad.append(k["name"])
    return res(bad, sum(1 for t in T if t["is_lookup"]) + sum(1 for t in T for k in t["constraints"] if k["type"] == "check"))


# ---------------------------------------------------------------- group 4 audit
@check("A01", 4, "Every table has created_at and created_by", "columns present", "dictlib.inject_standard")
def _():
    return res([t["name"] for t in T if not {"created_at", "created_by"} <= set(cols(t))], len(T))


@check("A02", 4, "Mutable tables (audit full) have updated_at and updated_by", "audit mode full -> columns present", "dictlib.inject_standard")
def _():
    pop = [t for t in T if t["audit"] == "full"]
    return res([t["name"] for t in pop if not {"updated_at", "updated_by"} <= set(cols(t))], len(pop))


@check("A03", 4, "Soft-deleted entities carry deleted_at and deleted_by", "delete_policy soft -> columns present", "soft table without deleted_at")
def _():
    pop = [t for t in T if t["delete_policy"] == "soft"]
    return res([t["name"] for t in pop if not {"deleted_at", "deleted_by"} <= set(cols(t))], len(pop))


@check("A04", 4, "Ledger and audit tables are append_only", "kind ledger/audit -> delete_policy append_only", "kind ledger/audit must be append_only")
def _():
    pop = [t for t in T if t["kind"] in ("ledger", "audit")]
    return res([t["name"] for t in pop if t["delete_policy"] != "append_only"], len(pop))


@check("A05", 4, "Append-only tables have no updated_* columns and no soft-delete columns", "append_only -> created_only audit", "append_only table must use audit: created_only")
def _():
    pop = [t for t in T if t["delete_policy"] == "append_only"]
    return res([t["name"] for t in pop if {"updated_at", "updated_by", "deleted_at"} & set(cols(t))], len(pop))


@check("A06", 4, "Business tables without updated_* are append-only (otherwise a recorded deviation)", "created_only tables that are not append_only (17 agg_* + gig_slot)", "NOT covered by lint (deviation DEV-03)", "deviation")
def _():
    bad = [t["name"] for t in T if t["audit"] == "created_only" and t["delete_policy"] != "append_only"]
    return res(bad, sum(1 for t in T if t["audit"] == "created_only"))


@check("A07", 4, "Every table states a retention rule", "retention text non-empty", "missing retention statement")
def _():
    return res([t["name"] for t in T if not t.get("retention")], len(T))


@check("A08", 4, "TTL declarations name an expiry (expire_after)", "ttl.expire_after present", "ttl needs expire_after")
def _():
    pop = [t for t in T if t.get("ttl")]
    return res([t["name"] for t in pop if "expire_after" not in t["ttl"]], len(pop))


@check("A09", 4, "TTL tables have no inbound FK that would block the TTL job (RESTRICT / NO ACTION from a surviving child)", "TTL table referenced by an FK of another table with on_delete RESTRICT (docs: job fails)", "NOT covered by lint (finding)", "finding")
def _():
    ttl = {t["name"] for t in T if t.get("ttl")}
    bad = []
    for t in T:
        for f in t["fks"]:
            if f["ref"] in ttl and f["on_delete"] in ("RESTRICT", "NO ACTION") and t["name"] != f["ref"]:
                bad.append(f"{t['name']}->{f['ref']}")
    return res(bad, len(ttl))


@check("A10", 4, "Append-only tables are covered by an INSERT/SELECT-only privilege statement (PRIVILEGE business rule or module policy)", "append_only table named in at least one PRIVILEGE/CHAIN_FK/TTL rule table list", "NOT covered by lint (finding: grants are module policy)", "finding")
def _():
    named = set()
    for r in M["rules"]:
        if r["mechanism_type"] in ("PRIVILEGE", "CHAIN_FK", "TTL", "TXN_LOCK"):
            named |= set(r.get("tables", []) or [])
    pop = [t for t in T if t["delete_policy"] == "append_only"]
    return res([t["name"] for t in pop if t["name"] not in named], len(pop))


# ---------------------------------------------------------------- group 5 nullability
@check("Z01", 5, "Every nullable column has a written justification (null_why)", "null true -> non-empty null_why", "nullable column needs null_why")
def _():
    pop = [(t, c) for t in T for c in t["columns"] if c.get("null")]
    return res([f"{t['name']}.{c['name']}" for t, c in pop if not str(c.get("null_why", "")).strip()], len(pop))


@check("Z02", 5, "No sentinel defaults ('', 'NA', 'unknown', 'none')", "default values", "sentinel default")
def _():
    bad = [f"{t['name']}.{c['name']}" for t in T for c in t["columns"] if c.get("default") is not None and str(c["default"]).strip().strip("'\"").lower() in ("", "na", "n/a", "unknown", "none")]
    return res(bad, sum(1 for t in T for c in t["columns"] if c.get("default") is not None))


@check("Z03", 5, "Money amounts are never defaulted to zero as 'missing'", "money columns with DEFAULT 0 that are also nullable-by-design", "independent")
def _():
    bad = [f"{t['name']}.{c['name']}" for t in T for c in t["columns"] if c["domain"] == "money" and str(c.get("default")) in ("0", "0.0", "0.00") and c.get("null")]
    return res(bad, sum(1 for t in T for c in t["columns"] if c["domain"] == "money"))


@check("Z04", 5, "Salary 'not disclosed' vs 'not applicable' vs amount is stored via a disclosure lookup", "tables with salary amounts also carry *_disclosure_id (candidate_profile, job_requirement)", "independent (D-09)")
def _():
    bad = []
    for n in ("candidate_profile", "job_requirement"):
        if not any(c.endswith("disclosure_id") for c in cols(TB[n])):
            bad.append(n)
    return res(bad, 2)


# ---------------------------------------------------------------- group 6 sensitivity
@check("S01", 6, "Every column is tagged Public/Internal/Personal/Sensitive", "sens in set", "sens must be one of ...")
def _():
    return res([f"{t['name']}.{c['name']}" for t in T for c in t["columns"] if c.get("sens") not in L.SENS], sum(len(t["columns"]) for t in T))


@check("S02", 6, "Personal and Sensitive columns state their treatment (pii note)", "non-empty pii", "column needs a 'pii' treatment note")
def _():
    pop = [(t, c) for t in T for c in t["columns"] if c["sens"] in ("Personal", "Sensitive")]
    return res([f"{t['name']}.{c['name']}" for t, c in pop if not str(c.get("pii", "")).strip()], len(pop))


@check("S03", 6, "Sensitive columns are hashed / tokenised / encrypted / masked (pii note names the mechanism)", "pii text contains hash|token|encrypt|mask|hmac|cipher|vault|argon|ref", "independent")
def _():
    pop = [(t, c) for t in T for c in t["columns"] if c["sens"] == "Sensitive"]
    kw = re.compile(r"hash|sha|aes|token|encrypt|mask|hmac|cipher|vault|argon|ref|kms|opaque|digest|vendor", re.I)
    return res([f"{t['name']}.{c['name']}" for t, c in pop if not kw.search(str(c.get("pii", "")))], len(pop))


@check("S04", 6, "No clear-text national id / card / bank column names exist", "column names containing aadhaar, ssn, passport_number, national_id, pan_number, card_number, cvv, iban, account_number, upi", "independent")
def _():
    pat = re.compile(r"aadhaar|(^|_)ssn(_|$)|passport_number|national_id|pan_number|card_number|(^|_)cvv|(^|_)iban|account_number|(^|_)upi(_|$)|bank_account")
    bad = [f"{t['name']}.{c['name']}" for t in T for c in t["columns"] if pat.search(c["name"])]
    return res(bad, sum(len(t["columns"]) for t in T))


@check("S05", 6, "Credential / secret / access-token columns are classed Sensitive consistently", "columns named *access_token*, *share_token*, *visitor_token*, *token_hash*, *secret*, *api_key* must be Sensitive (hashed contact identifiers are Personal by design)", "NOT covered by lint (finding: classification consistency)", "finding")
def _():
    pat = re.compile(r"(access_token|share_token|visitor_token|token_hash|secret|api_key)")
    pop = [(t, c) for t in T for c in t["columns"] if pat.search(c["name"])]
    return res([f"{t['name']}.{c['name']} ({c['sens']})" for t, c in pop if c["sens"] != "Sensitive"], len(pop))


# ---------------------------------------------------------------- group 7 multilingual
@check("L01", 7, "Every lookup stores name per language (name_i18n JSONB NOT NULL with CHECK containing 'en')", "lookup tables", "dictlib.expand_lookup")
def _():
    pop = [t for t in T if t["is_lookup"]]
    return res([t["name"] for t in pop if cols(t).get("name_i18n", {}).get("type") != "JSONB" or not has_check(t, "name_i18n", "'en'")], len(pop))


@check("L02", 7, "Every lookup value has an English name (default language)", "seed values contain en", "lookup value needs code and en")
def _():
    bad = [t["name"] for t in T if t["is_lookup"] for v in t["values"] if "code" not in v or "en" not in v]
    return res(bad, sum(len(t["values"]) for t in T if t["is_lookup"]))


@check("L03", 7, "UI strings are held by key with one translation row per key and language", "ui_label_translation UNIQUE (ui_label_id, language_id)", "independent")
def _():
    t = TB["ui_label_translation"]
    ok = any(k["type"] == "unique" and set(k["columns"]) == {"ui_label_id", "language_id"} for k in t["constraints"])
    return res([] if ok else ["missing"], 1)


@check("L04", 7, "Fallback to the default language is defined and terminates", "language: one default (partial unique), non-default has fallback CHECK, no self fallback", "independent (BR-MST-04)")
def _():
    t = TB["language"]
    ok = any(i.get("unique") and i["columns"] == ["is_default"] for i in t["indexes"]) and has_check(t, "fallback_language_id", "is_default") and has_check(t, "fallback_language_id", "id")
    return res([] if ok else ["missing"], 1)


@check("L05", 7, "User-entered long text records its language where known", "tables with a long_text column should have a *language_id column", "NOT covered by lint (partial)", "finding")
def _():
    pop, bad = [], []
    for t in T:
        lt = [c for c in t["columns"] if c["domain"] == "long_text"]
        if lt:
            pop.append(t["name"])
            if not any("language" in c["name"] and c["name"].endswith("_id") for c in t["columns"]):
                bad.append(t["name"])
    return res(bad, len(pop))


@check("L06", 7, "Text stored once in the language entered (no per-language duplicate content columns)", "no columns named *_en/_hi/*_ta etc.", "independent")
def _():
    bad = [f"{t['name']}.{c['name']}" for t in T for c in t["columns"] if re.search(r"_(en|hi|ta|bn|es|ar)$", c["name"])]
    return res(bad, sum(len(t["columns"]) for t in T))


# ---------------------------------------------------------------- group 8 physical (see also N16-N19)
@check("P01", 8, "Hash-sharded indexes declare bucket_count and are never inverted", "index flags", "hash index needs bucket_count / cannot be both hash and inverted")
def _():
    pop = [i for t in T for i in t["indexes"] if i.get("hash")]
    return res([i["name"] for i in pop if not i.get("bucket_count") or i.get("inverted")], len(pop))


@check("P02", 8, "High/very_high volume tables have a hash-sharded index or a no_hash_why", "volume flag", "high/very_high volume table has no hash-sharded index")
def _():
    pop = [t for t in T if t["volume"] in ("high", "very_high")]
    return res([t["name"] for t in pop if not any(i.get("hash") for i in t["indexes"]) and not t.get("no_hash_why")], len(pop))


@check("P03", 8, "Inverted (GIN) indexes on STRING columns name the gin_trgm_ops opclass", "inverted index on a STRING column whose columns/opclass do not contain gin_trgm_ops (CockroachDB requires it)", "NOT covered by lint (finding R-08)", "finding")
def _():
    bad, pop = [], 0
    for t in T:
        cc = cols(t)
        for i in t["indexes"]:
            if i.get("inverted"):
                first = i["columns"][0].split(" ")[0]
                if cc[first]["type"].startswith("STRING"):
                    pop += 1
                    if "gin_trgm_ops" not in " ".join(i["columns"]) and "gin_trgm_ops" not in str(i.get("opclass", "")):
                        bad.append(i["name"])
    return res(bad, pop)


@check("P04", 8, "Views reference existing tables", "view.tables", "unknown table in view")
def _():
    bad = [f"{v['name']}:{x}" for v in M["views"] for x in v.get("tables", []) or [] if x not in TB and x not in {w['name'] for w in M['views']}]
    return res(bad, len(M["views"]))


@check("P05", 8, "Business rules reference existing tables", "rule.tables", "unknown table in rule")
def _():
    bad = [f"{r['id']}:{x}" for r in M["rules"] for x in r.get("tables", []) or [] if x not in TB]
    return res(bad, len(M["rules"]))


@check("P06", 8, "Business rule ids are unique and carry a mechanism", "id uniqueness, mechanism present", "rule needs id, rule, mechanism_type, mechanism")
def _():
    c = collections.Counter(r["id"] for r in M["rules"])
    bad = [k for k, v in c.items() if v > 1] + [r["id"] for r in M["rules"] if not r.get("mechanism")]
    return res(bad, len(M["rules"]))


@check("P07", 8, "CHECKs use only operators verified for CockroachDB (num_nonnulls, ~ and jsonb ? need a smoke test on the target image)", "count of CHECKs using num_nonnulls / regex ~ / jsonb ?", "NOT covered by lint (risk R-05)", "info")
def _():
    n = sum(1 for t in T for k in t["constraints"] if k["type"] == "check" and re.search(r"num_nonnulls| ~ | \? ", str(k.get("expr"))))
    return 0, n, []


# ---------------------------------------------------------------- group 9 licensing
@check("G01", 9, "Sessions store device identifier, start time, last activity time and end time", "device_session columns", "independent")
def _():
    need = {"device_id", "started_at", "last_activity_at", "ended_at"}
    return res(sorted(need - set(cols(TB["device_session"]))), 4)


@check("G02", 9, "Expired or revoked sessions are not counted: counting predicate is ended_at IS NULL and revocation sets ended_at", "partial unique predicate + session_revocation/device_revocation tables", "independent (BR-LIC-08)")
def _():
    ok = all(n in TB for n in ("session_revocation", "device_revocation"))
    return res([] if ok else ["revocation tables missing"], 2)


@check("G03", 9, "A limit breach is recorded in the audit trail (device_limit_violation holds audit_event_id FK)", "FK to audit_event", "independent (BR-LIC-07)")
def _():
    ok = any(f["ref"] == "audit_event" for f in TB["device_limit_violation"]["fks"])
    return res([] if ok else ["missing"], 1)


@check("G04", 9, "Revoking a device or session is an auditable action (audit_event reference on both revocation tables)", "revocation tables reference audit_event", "independent")
def _():
    bad = [n for n in ("session_revocation", "device_revocation") if not any(f["ref"] == "audit_event" for f in TB[n]["fks"])]
    return res(bad, 2)


@check("G05", 9, "Licence seat allocation cannot exceed max_users (slot pre-creation + partial unique)", "uq_licence_seat_licence_seat_slot_id partial unique", "independent (BR-LIC-02)")
def _():
    ok = any(i.get("unique") and i["columns"] == ["licence_seat_slot_id"] for i in TB["licence_seat"]["indexes"])
    return res([] if ok else ["missing"], 1)


# ---------------------------------------------------------------- group 10 money
@check("M01", 10, "Ledgers are append-only (INSERT/SELECT) with a reversal reference instead of UPDATE", "wallet_ledger_entry append_only + UNIQUE reverses id", "kind ledger must be append_only; independent")
def _():
    t = TB["wallet_ledger_entry"]
    ok = t["delete_policy"] == "append_only" and any(k["type"] == "unique" and k["columns"] == ["reverses_wallet_ledger_entry_id"] for k in t["constraints"])
    return res([] if ok else ["missing"], 1)


@check("M02", 10, "Payment, refund and money-movement tables carry an idempotency key", "payment_order, refund, wallet_ledger_entry, profile_download have unique *idempotency_key", "independent")
def _():
    bad = [n for n in ("payment_order", "refund", "wallet_ledger_entry", "profile_download")
           if not any(k["type"] == "unique" and any(c.endswith("idempotency_key") for c in k["columns"]) for k in TB[n]["constraints"]) and
           not any(i.get("unique") and any(c.endswith("idempotency_key") for c in i["columns"]) for i in TB[n]["indexes"])]
    return res(bad, 4)


@check("M03", 10, "Subscription periods of a licence cannot overlap (CHAIN_FK constraints present)", "unique prior, composite FK, start>=prior end CHECK, partial root unique", "independent (BR-SUB-03)")
def _():
    t = TB["subscription_period"]
    ok = (any(k["type"] == "unique" and k["columns"] == ["prior_period_id"] for k in t["constraints"])
          and any(f["name"].endswith("prior_period") and len(f["columns"]) == 3 and f["on_update"] == "RESTRICT" for f in t["fks"])
          and has_check(t, "period_start_date", "prior_period_end_date")
          and any(i.get("unique") and "prior_period_id IS NULL" in str(i.get("where")) for i in t["indexes"]))
    return res([] if ok else ["missing"], 1)


@check("M04", 10, "Profile download history is append-only and carries actor, subject, timestamp and credit deduction", "profile_download columns/policy", "independent")
def _():
    t = TB["profile_download"]
    c = cols(t)
    names = set(c)
    ok = t["delete_policy"] == "append_only" and "wallet_ledger_entry_id" in names and "credits_charged" in names and any(x.startswith("candidate_profile") for x in names) and \
        any("downloaded" in x or "actor" in x or "app_user" in x for x in names)
    return res([] if ok else [f"profile_download columns: {sorted(names)}"], 1)


@check("M05", 10, "Profile view history is append-only with actor, subject and timestamp", "candidate_profile_view", "independent")
def _():
    t = TB["candidate_profile_view"]
    names = set(cols(t))
    ok = t["delete_policy"] == "append_only" and "viewed_at" in names and "candidate_profile_id" in names and any("viewer" in x or "app_user" in x for x in names)
    return res([] if ok else [f"candidate_profile_view columns: {sorted(names)}"], 1)


@check("M06", 10, "Refunds reference the original payment (no update of the original)", "refund has FK to payment_order and refund is not soft-deleted", "independent")
def _():
    t = TB["refund"]
    ok = any(f["ref"] == "payment_order" for f in t["fks"])
    return res([] if ok else ["missing"], 1)


_CACHE = []


def run_all():
    if _CACHE:
        return _CACHE
    out = []
    for id_, group, rule, how, lint, fn, sev in CHECK_RULES:
        v, pop, sample = fn()
        out.append(dict(id=id_, group=group, rule=rule, how=how, lint=lint, violations=v, population=pop, sample=sample, severity=sev))
    _CACHE.extend(out)
    return out


if __name__ == "__main__":
    for r in run_all():
        flag = "" if r["violations"] == 0 else "  <<<<<<"
        print(f"{r['id']:4} g{r['group']:<2} viol={r['violations']:<5} pop={r['population']:<6} {r['severity']:9} {r['rule'][:70]}{flag}")
        if r["violations"]:
            print("      sample:", r["sample"])

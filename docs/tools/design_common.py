"""Shared helpers for the design-documentation builders (build_adr.py, build_conventions.py,
build_compliance.py, build_justification.py).  Everything numeric is COMPUTED from docs/_staging via
dictlib.load_model(); builders must never hard-code a count."""
from __future__ import annotations
import os, re, subprocess, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dictlib import load_model, MODULES, MODULE_OWNER, ROOT  # noqa: E402

DOCS = os.path.join(ROOT, "docs")
TOOLS = os.path.join(DOCS, "tools")
TODAY = "2026-10-16"

DOCKET_MODULES = ["master_data", "profile", "jobs_applications", "administration", "organisation_account",
                  "gig_marketplace", "licences_devices", "sourcing_consultant", "subscriptions_payments",
                  "notifications_community", "analytics"]
TIER3 = ["tier3_reference"]
EXT = ["ext_network", "ext_matching", "ext_ads", "ext_learning", "ext_talent_ops"]
ALL_MODULES = DOCKET_MODULES + TIER3 + EXT

_MODEL = None


def model():
    global _MODEL
    if _MODEL is None:
        _MODEL = load_model()
    return _MODEL


def tables():
    return {t["name"]: t for t in model()["tables"]}


def view_names():
    return {v["name"] for v in model()["views"]}


def must_exist(*names):
    """Assert every name is a real table (or view for vw_/mv_ names).  Returns the names (for related_tables)."""
    tb, vw = tables(), view_names()
    for n in names:
        if n not in tb and n not in vw:
            raise SystemExit(f"UNKNOWN OBJECT referenced in documentation: {n}")
    return list(names)


def col_exists(table, col):
    t = tables()[table]
    return any(c["name"] == col for c in t["columns"])


def need_col(table, col):
    if not col_exists(table, col):
        raise SystemExit(f"UNKNOWN COLUMN {table}.{col}")
    return f"{table}.{col}"


def run_lint():
    """Run docs/tools/lint_dictionary.py as a subprocess; return (exit_code, summary_line)."""
    p = subprocess.run([sys.executable, os.path.join(TOOLS, "lint_dictionary.py")], capture_output=True, text=True,
                       encoding="utf-8")
    last = [l for l in p.stdout.strip().splitlines() if l.strip()][-1] if p.stdout.strip() else ""
    return p.returncode, last


def stats():
    m = model()
    T = m["tables"]
    S = collections.OrderedDict()
    S["tables"] = len(T)
    S["lookups"] = sum(1 for t in T if t["is_lookup"])
    S["non_lookups"] = S["tables"] - S["lookups"]
    S["lookup_values"] = sum(len(t.get("values", [])) for t in T if t["is_lookup"])
    S["lookup_values_hi"] = sum(1 for t in T if t["is_lookup"] for v in t["values"] if "hi" in v)
    S["lookups_fully_hi"] = sum(1 for t in T if t["is_lookup"] and all("hi" in v for v in t["values"]))
    S["columns"] = sum(len(t["columns"]) for t in T)
    S["columns_declared"] = sum(1 for t in T for c in t["columns"] if not c.get("injected"))
    S["views"] = sum(1 for v in m["views"] if v.get("kind", "view") != "materialized")
    S["mviews"] = sum(1 for v in m["views"] if v.get("kind") == "materialized")
    S["rules"] = len(m["rules"])
    S["rules_by_type"] = collections.Counter(r["mechanism_type"] for r in m["rules"])
    S["delete"] = collections.Counter(t["delete_policy"] for t in T)
    S["kinds"] = collections.Counter(t["kind"] for t in T)
    S["tiers"] = collections.Counter(str(t["tier"]) for t in T)
    S["volume"] = collections.Counter(t["volume"] for t in T)
    S["audit_mode"] = collections.Counter(t["audit"] for t in T)
    idx = [(t, i) for t in T for i in t["indexes"]]
    S["indexes"] = len(idx)
    S["hash_indexes"] = sum(1 for _, i in idx if i.get("hash"))
    S["hash_tables"] = len({t["name"] for t, i in idx if i.get("hash")})
    S["hash_buckets"] = collections.Counter(i.get("bucket_count") for _, i in idx if i.get("hash"))
    S["partial_indexes"] = sum(1 for _, i in idx if i.get("where"))
    S["partial_unique"] = sum(1 for _, i in idx if i.get("where") and i.get("unique"))
    S["unique_indexes"] = sum(1 for _, i in idx if i.get("unique"))
    S["gin_indexes"] = sum(1 for _, i in idx if i.get("inverted"))
    S["trgm_indexes"] = sum(1 for _, i in idx if "gin_trgm_ops" in " ".join(i.get("columns", []) + [str(i.get("opclass", ""))]))
    S["storing_indexes"] = sum(1 for _, i in idx if i.get("storing"))
    S["ttl_tables"] = sum(1 for t in T if t.get("ttl"))
    S["ttl_append_only"] = sum(1 for t in T if t.get("ttl") and t["delete_policy"] == "append_only")
    S["no_hash_why"] = sum(1 for t in T if t.get("no_hash_why"))
    S["hv_tables"] = sum(1 for t in T if t["volume"] in ("high", "very_high"))
    S["hv_no_hash"] = sum(1 for t in T if t["volume"] in ("high", "very_high") and not any(i.get("hash") for i in t["indexes"]))
    cons = [(t, k) for t in T for k in t["constraints"]]
    S["checks"] = sum(1 for _, k in cons if k["type"] == "check")
    S["unique_constraints"] = sum(1 for _, k in cons if k["type"] == "unique")
    fks = [(t, f) for t in T for f in t["fks"]]
    S["fks"] = len(fks)
    S["fks_composite"] = sum(1 for _, f in fks if len(f["columns"]) > 1)
    S["fk_delete"] = collections.Counter(f["on_delete"] for _, f in fks)
    S["fk_index_exempt"] = sum(1 for _, f in fks if f.get("index_exempt"))
    cols = [(t, c) for t in T for c in t["columns"]]
    S["sens"] = collections.Counter(c["sens"] for _, c in cols)
    S["nullable"] = sum(1 for _, c in cols if c.get("null"))
    S["domains"] = collections.Counter(c["domain"] for _, c in cols)
    S["external_ref_cols"] = sum(1 for _, c in cols if c.get("external_ref"))
    S["ref_cols"] = sum(1 for _, c in cols if c["name"].endswith("_ref"))
    S["computed_cols"] = sum(1 for _, c in cols if c.get("computed"))
    S["json_cols"] = sum(1 for _, c in cols if c["domain"] == "json")
    S["long_text_cols"] = sum(1 for _, c in cols if c["domain"] == "long_text")
    S["max_cols"] = max((len(t["columns"]), t["name"]) for t in T)
    S["avg_cols"] = round(S["columns"] / S["tables"], 1)
    S["avg_cols_nonlookup"] = round(sum(len(t["columns"]) for t in T if not t["is_lookup"]) / S["non_lookups"], 1)
    S["cols_gt30"] = sum(1 for t in T if len(t["columns"]) > 30)
    S["cols_gt40"] = sum(1 for t in T if len(t["columns"]) > 40)
    names = [(len(x["name"]), x["name"]) for t in T for x in t["indexes"] + t["constraints"] + t["fks"]]
    S["names_gt63"] = sum(1 for n, _ in names if n > 63)
    S["max_name"] = max(names)
    S["max_table_name"] = max((len(t["name"]), t["name"]) for t in T)
    S["max_col_name"] = max((len(c["name"]), c["name"]) for _, c in cols)
    S["idempotency_tables"] = sorted(t["name"] for t in T if any(c["name"].endswith("idempotency_key") for c in t["columns"]))
    S["hash_chain_tables"] = sorted(t["name"] for t in T if any(c["name"] == "row_hash" for c in t["columns"]))
    S["created_only"] = sum(1 for t in T if t["audit"] == "created_only")
    S["created_only_nonappend"] = sorted(t["name"] for t in T if t["audit"] == "created_only" and t["delete_policy"] != "append_only")
    # per module
    pm = collections.OrderedDict()
    for mod in ALL_MODULES:
        ts = [t for t in T if t["module"] == mod]
        d = dict(tables=len(ts), lookups=sum(1 for t in ts if t["is_lookup"]),
                 entities=sum(1 for t in ts if not t["is_lookup"]),
                 columns=sum(len(t["columns"]) for t in ts),
                 soft=sum(1 for t in ts if t["delete_policy"] == "soft"),
                 hard=sum(1 for t in ts if t["delete_policy"] == "hard"),
                 append_only=sum(1 for t in ts if t["delete_policy"] == "append_only"),
                 hash_idx=sum(1 for t in ts for i in t["indexes"] if i.get("hash")),
                 ttl=sum(1 for t in ts if t.get("ttl")),
                 sensitive=sum(1 for t in ts for c in t["columns"] if c["sens"] == "Sensitive"),
                 personal=sum(1 for t in ts for c in t["columns"] if c["sens"] == "Personal"),
                 nullable=sum(1 for t in ts for c in t["columns"] if c.get("null")),
                 fks=sum(len(t["fks"]) for t in ts),
                 views=sum(1 for v in m["views"] if v["module"] == mod),
                 rules=sum(1 for r in m["rules"] if r["module"] == mod),
                 very_high=sum(1 for t in ts if t["volume"] == "very_high"),
                 high=sum(1 for t in ts if t["volume"] == "high"))
        pm[mod] = d
    S["per_module"] = pm
    S["ext_tables"] = sum(pm[x]["tables"] for x in EXT)
    S["docket_tables"] = sum(pm[x]["tables"] for x in DOCKET_MODULES)
    return S


def reserved_lists():
    def words(fn):
        out = set()
        for line in open(os.path.join(TOOLS, fn), encoding="utf-8"):
            if line.startswith("#"):
                continue
            out |= set(line.split())
        return out
    return words("reserved_words.txt"), words("avoid_words.txt")


# CockroachDB identifiers that docs/_staging/crdb_capabilities.md row 22 says to avoid (grammar list itself is not
# enumerated there: unverified).  Used as an additional list in the reserved-keyword check.
CRDB_DOC_AVOID = ["user", "order", "group", "table", "check", "end", "from", "limit", "offset", "primary", "references",
                  "unique", "value"]


def write_md(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

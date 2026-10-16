#!/usr/bin/env python
"""Consolidation step: de-duplicate tables/lookups defined by several staging files and align enum_ref/identifier domains.
Idempotent.  Priority decides which module keeps a duplicated definition (lookup values of dropped copies are merged into the kept one).
Usage: python docs/tools/reconcile.py [--skip file.yaml ...]"""
import os, sys, glob, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dictlib import STAGING

PRIORITY = ["master_data", "administration", "organisation_account", "profile", "jobs_applications", "licences_devices", "sourcing_consultant",
            "subscriptions_payments", "gig_marketplace", "notifications_community", "analytics", "tier3_reference", "ext_network", "ext_matching",
            "ext_ads", "ext_learning", "ext_talent_ops"]


def dump(d, path):
    class D(yaml.SafeDumper):
        pass

    def strrep(dumper, data):
        if "\n" in data:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)
    D.add_representer(str, strrep)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(d, f, Dumper=D, allow_unicode=True, sort_keys=False, width=200, default_flow_style=False)


def main():
    skip = set()
    if "--skip" in sys.argv:
        skip = set(sys.argv[sys.argv.index("--skip") + 1:])
    files = {}
    for p in sorted(glob.glob(os.path.join(STAGING, "*.yaml"))):
        fn = os.path.basename(p)
        if fn == "REGISTRY.yaml" or fn in skip:
            continue
        files[p] = yaml.load(open(p, encoding="utf-8"), Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader))
    defs = {}
    for p, d in files.items():
        for kind in ("lookups", "tables"):
            for t in d.get(kind, []) or []:
                defs.setdefault(t["name"], []).append((p, d.get("module"), kind, t))
    rank = lambda m: PRIORITY.index(m) if m in PRIORITY else 99
    removed = []
    for name, lst in defs.items():
        if len(lst) < 2:
            continue
        lst.sort(key=lambda x: rank(x[1]))
        keep = lst[0]
        for p, mod, kind, t in lst[1:]:
            if kind == "lookups" and keep[2] == "lookups":
                have = {v["code"] for v in keep[3].get("values", [])}
                for v in t.get("values", []) or []:
                    if v["code"] not in have:
                        keep[3].setdefault("values", []).append(v)
            files[p][kind] = [x for x in files[p][kind] if x is not t]
            removed.append(f"{name}: dropped from {mod} ({kind}), kept in {keep[1]}")
    lookup_names = {t["name"] for d in files.values() for t in d.get("lookups", []) or []}
    # also lookups defined with kind: lookup inside tables? (not used)
    fixed = 0
    for p, d in files.items():
        for t in d.get("tables", []) or []:
            cols = {c["name"]: c for c in t.get("columns", []) or []}
            for fk in t.get("fks", []) or []:
                cs = fk.get("columns") or ([fk["column"]] if fk.get("column") else [])
                if len(cs) != 1 or cs[0] not in cols or (fk.get("ref_columns") or ["id"]) != ["id"]:
                    continue
                c = cols[cs[0]]
                if fk["ref"] in lookup_names and c.get("domain") != "enum_ref":
                    c["domain"] = "enum_ref"; fixed += 1
                elif fk["ref"] not in lookup_names and c.get("domain") == "enum_ref":
                    c["domain"] = "identifier"; fixed += 1
    for p, d in files.items():
        dump(d, p)
    print("\n".join(removed))
    print(f"removed {len(removed)} duplicate definitions; fixed {fixed} domains")


if __name__ == "__main__":
    main()

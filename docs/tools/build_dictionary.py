#!/usr/bin/env python
"""Build SSOT workbook docs/05_data_dictionary.xlsx, ER diagrams (docs/01_er/*.md) and docs/03_module_ownership.xlsx
from the merged staging model.  Run after lint passes:  python docs/tools/build_dictionary.py"""
import os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dictlib import load_model, MODULES, MODULE_OWNER, ROOT
from xl import new_wb, add_sheet

DOCS = os.path.join(ROOT, "docs")
SENS_ORDER = ["Public", "Internal", "Personal", "Sensitive"]


def table_sens(t):
    s = [c.get("sens", "Internal") for c in t["columns"]]
    return max(s, key=lambda x: SENS_ORDER.index(x)) if s else "Internal"


def pk_name(t):
    return f"pk_{t['name']}"


def main():
    m = load_model()
    tables = m["tables"]
    by = {t["name"]: t for t in tables}
    mod_order = list(MODULES)
    tables.sort(key=lambda t: (mod_order.index(t["module"]) if t["module"] in mod_order else 99, t["is_lookup"], t["name"]))

    wb = new_wb()
    # Tables
    hdr = ["table", "module", "owner", "kind", "delete_policy", "volume", "tier", "milestones", "max_sensitivity", "column_count",
           "audit_columns", "ttl", "partitioning", "retention", "description"]
    rows = []
    for t in tables:
        part = t.get("partitioning")
        part_s = (f"{part.get('strategy', '')}: {part.get('key', '')}" if isinstance(part, dict) else (part or ""))
        ttl = t.get("ttl")
        ttl_s = f"expire_after={ttl.get('expire_after')} column={ttl.get('column', '')}" if ttl else ""
        rows.append([t["name"], t["module"], MODULE_OWNER.get(t["module"], "Team"), t["kind"], t["delete_policy"], t["volume"], str(t["tier"]),
                     t["milestones"], table_sens(t), len(t["columns"]), t["audit"], ttl_s, part_s, t.get("retention", ""), t.get("description", "")])
    add_sheet(wb, "Tables", hdr, rows, widths=[34, 24, 10, 10, 13, 10, 6, 18, 14, 8, 13, 24, 28, 50, 70], wrap_cols=("retention", "description"),
              validations={"delete_policy": ["soft", "hard", "append_only"]}, freeze_cols=1)

    # Columns
    hdr = ["table", "ordinal", "column", "type", "domain", "nullable", "nullability_justification", "default", "sensitivity_class",
           "delete_policy", "pii_treatment", "computed_expr", "is_pk", "fk_ref", "standard_column", "description"]
    rows = []
    for t in tables:
        fkmap = {}
        for fk in t["fks"]:
            for c, rc in zip(fk["columns"], fk["ref_columns"]):
                fkmap[c] = f"{fk['ref']}.{rc}"
        for i, c in enumerate(t["columns"], 1):
            comp = c.get("computed")
            comp_s = (f"{comp} STORED" if c.get("stored", True) else f"{comp} VIRTUAL") if comp else ""
            rows.append([t["name"], i, c["name"], c["type"], c["domain"], "YES" if c.get("null") else "NO", c.get("null_why", "") or "",
                         "" if c.get("default") is None else str(c.get("default")), c.get("sens", "Internal"), t["delete_policy"],
                         c.get("pii", "") or "", comp_s, "YES" if c["name"] == "id" else "NO", fkmap.get(c["name"], ""),
                         "YES" if c.get("injected") else "NO", c.get("desc", "")])
    add_sheet(wb, "Columns", hdr, rows, widths=[32, 8, 28, 20, 14, 9, 44, 18, 12, 12, 40, 24, 6, 28, 9, 70], wrap_cols=("nullability_justification", "pii_treatment", "description"),
              validations={"sensitivity_class": SENS_ORDER}, freeze_cols=3)

    # Enums (long format)
    hdr = ["lookup_table", "code", "sort_order", "is_active", "language_tag", "display_name", "description"]
    rows = []
    for t in tables:
        if not t["is_lookup"]:
            continue
        for i, v in enumerate(t.get("values", []), 1):
            langs = {k: val for k, val in v.items() if k not in ("code", "sort", "desc", "active")}
            for lg, nm in langs.items():
                rows.append([t["name"], v["code"], v.get("sort", i * 10), "YES" if v.get("active", True) else "NO", lg, nm, v.get("desc", "")])
    add_sheet(wb, "Enums", hdr, rows, widths=[30, 30, 10, 9, 12, 40, 40], freeze_cols=2)

    # Indexes
    hdr = ["table", "index_name", "columns", "unique", "hash_sharded", "bucket_count", "inverted", "where_predicate", "storing", "kind", "description", "opclass"]
    rows = []
    for t in tables:
        for ix in t["indexes"]:
            rows.append([t["name"], ix["name"], ", ".join(ix.get("columns", [])), "YES" if ix.get("unique") else "NO", "YES" if ix.get("hash") else "NO",
                         ix.get("bucket_count", ""), "YES" if ix.get("inverted") else "NO", ix.get("where", "") or "", ", ".join(ix.get("storing", []) or []),
                         "partial" if ix.get("where") else ("hash" if ix.get("hash") else ("inverted" if ix.get("inverted") else "btree")), ix.get("desc", ""), ix.get("opclass", "") or ""])
    add_sheet(wb, "Indexes", hdr, rows, widths=[32, 46, 36, 8, 10, 8, 9, 36, 24, 9, 60, 14], wrap_cols=("description", "where_predicate"), freeze_cols=2)

    # Constraints (PK + unique + check)
    hdr = ["table", "constraint_name", "constraint_type", "columns", "expression", "description"]
    rows = []
    for t in tables:
        rows.append([t["name"], pk_name(t), "PRIMARY KEY", "id", "", "Surrogate UUID primary key."])
        for k in t["constraints"]:
            rows.append([t["name"], k["name"], k["type"].upper(), ", ".join(k.get("columns", []) or []), k.get("expr", "") or "", k.get("desc", "")])
    add_sheet(wb, "Constraints", hdr, rows, widths=[32, 46, 14, 30, 60, 60], wrap_cols=("expression", "description"), freeze_cols=2,
              validations={"constraint_type": ["PRIMARY KEY", "UNIQUE", "CHECK"]})

    # FKs
    hdr = ["table", "fk_name", "columns", "ref_table", "ref_columns", "on_delete", "on_update", "ref_module", "cross_module", "index_exempt", "description"]
    rows = []
    cross = []
    for t in tables:
        for fk in t["fks"]:
            rt = by.get(fk["ref"])
            rm = rt["module"] if rt else "?"
            rows.append([t["name"], fk["name"], ", ".join(fk["columns"]), fk["ref"], ", ".join(fk["ref_columns"]), fk["on_delete"], fk["on_update"], rm,
                         "YES" if rm != t["module"] else "NO", fk.get("index_exempt", "") or "", fk.get("desc", "")])
            if rm != t["module"]:
                cross.append([t["module"], t["name"], ", ".join(fk["columns"]), rm, fk["ref"], fk["on_delete"], fk.get("desc", "")])
    add_sheet(wb, "ForeignKeys", hdr, rows, widths=[32, 46, 30, 28, 14, 11, 11, 24, 8, 20, 60], wrap_cols=("description",), freeze_cols=2,
              validations={"on_delete": ["RESTRICT", "CASCADE", "SET NULL", "NO ACTION", "SET DEFAULT"]})

    # Views
    hdr = ["view_name", "kind", "module", "tables", "purpose", "security", "sql_sketch"]
    rows = [[v.get("name"), v.get("kind", "view"), v.get("module"), v.get("tables", []), v.get("purpose", ""), v.get("security", ""), v.get("sql_sketch", "")] for v in m["views"]]
    add_sheet(wb, "Views", hdr, rows, widths=[36, 14, 24, 40, 60, 50, 80], wrap_cols=("purpose", "security", "sql_sketch"), freeze_cols=1)

    # Partitioning / physical
    hdr = ["table", "module", "volume", "strategy", "key_or_columns", "naming_pattern", "notes"]
    rows = []
    for t in tables:
        part = t.get("partitioning")
        if isinstance(part, dict):
            rows.append([t["name"], t["module"], t["volume"], part.get("strategy", ""), part.get("key", ""), part.get("naming", f"{t['name']}_p_<key>"), part.get("desc", "")])
        for ix in t["indexes"]:
            if ix.get("hash"):
                rows.append([t["name"], t["module"], t["volume"], "hash-sharded index", ", ".join(ix["columns"]), ix["name"], f"bucket_count={ix.get('bucket_count')}; {ix.get('desc', '')}"])
        if t.get("ttl"):
            rows.append([t["name"], t["module"], t["volume"], "row-level TTL", t["ttl"].get("column", ""), f"{t['name']}_ttl", f"expire_after={t['ttl'].get('expire_after')}"])
    add_sheet(wb, "Partitioning", hdr, rows, widths=[32, 24, 10, 22, 36, 44, 70], wrap_cols=("notes",), freeze_cols=1)

    out = os.path.join(DOCS, "05_data_dictionary.xlsx")
    wb.save(out)

    # ----- ER diagrams -----
    os.makedirs(os.path.join(DOCS, "01_er"), exist_ok=True)
    edges = collections.Counter()
    for t in tables:
        for fk in t["fks"]:
            rt = by.get(fk["ref"])
            if rt and rt["module"] != t["module"]:
                edges[(t["module"], rt["module"])] += 1
    mm = ["# Module map", "", "High-level dependency map. An arrow `A --> B (n)` means A has n foreign keys pointing into tables owned by B.", "",
          "```mermaid", "flowchart LR"]
    for k, title in MODULES.items():
        n = sum(1 for t in tables if t["module"] == k)
        if n:
            mm.append(f'  {k}["{title}<br/>{n} tables"]')
    for (a, b), n in sorted(edges.items()):
        mm.append(f"  {a} -->|{n}| {b}")
    mm += ["```", "", "| Module | Owner | Tables | Lookups | Detailed ER |", "|---|---|---|---|---|"]
    for k, title in MODULES.items():
        ts = [t for t in tables if t["module"] == k]
        if ts:
            mm.append(f"| {title} | {MODULE_OWNER[k]} | {len(ts)} | {sum(1 for t in ts if t['is_lookup'])} | [{k}.md]({k}.md) |")
    open(os.path.join(DOCS, "01_er", "module_map.md"), "w", encoding="utf-8").write("\n".join(mm) + "\n")

    for k, title in MODULES.items():
        ts = [t for t in tables if t["module"] == k]
        if not ts:
            continue
        names = {t["name"] for t in ts}
        lines = [f"# ER diagram: {title}", "", f"Module key `{k}`, owner {MODULE_OWNER[k]}, {len(ts)} tables "
                 f"({sum(1 for t in ts if t['is_lookup'])} lookups). Generated from `05_data_dictionary.xlsx` (do not edit by hand). "
                 "Columns shown: primary key, foreign keys, unique/natural keys and a few business columns; see the dictionary for all columns. "
                 "Edges to lookup (enumeration) tables are listed under *Lookup references* rather than drawn.", ""]
        notes = m["notes"].get(k)
        if notes:
            lines += ["## Design notes", "", notes.strip(), ""]
        lines += ["## Diagram", "", "```mermaid", "erDiagram"]
        ext = set()
        rel = []
        lookup_refs = collections.defaultdict(list)
        for t in ts:
            for fk in t["fks"]:
                rt = by.get(fk["ref"])
                if not rt:
                    continue
                if rt["is_lookup"]:
                    lookup_refs[t["name"]].append(fk["ref"])
                    continue
                if rt["name"] not in names:
                    ext.add(rt["name"])
                col = next((c for c in t["columns"] if c["name"] == fk["columns"][0]), {})
                uniq = any(set(u.get("columns", [])) == set(fk["columns"]) for u in t["constraints"] if u["type"] == "unique") or \
                    any(i.get("unique") and not i.get("where") and set(i["columns"]) == set(fk["columns"]) for i in t["indexes"])
                nullable = col.get("null")
                par = "|o" if nullable else "||"
                card = "o|" if uniq else "o{"
                rel.append(f'  {rt["name"]} {par}--{card} {t["name"]} : "{fk["columns"][0]}"')
        for t in ts:
            if t["is_lookup"]:
                continue
            fkcols = {c for fk in t["fks"] for c in fk["columns"]}
            uqcols = {c for u in t["constraints"] if u["type"] == "unique" for c in u.get("columns", [])}
            lines.append(f"  {t['name']} {{")
            shown = 0
            for c in t["columns"]:
                n = c["name"]
                tag = "PK" if n == "id" else ("FK" if n in fkcols else ("UK" if n in uqcols else ""))
                if tag or (shown < 3 and not c.get("injected")):
                    ty = str(c["type"]).replace("(", "_").replace(")", "").replace(",", "_").replace(" ", "")
                    lines.append(f"    {ty} {n}{(' ' + tag) if tag else ''}")
                    if not tag:
                        shown += 1
            lines.append("  }")
        for e in sorted(ext):
            lines.append(f"  {e} {{\n    UUID id PK\n  }}")
        lines += sorted(set(rel))
        lines += ["```", ""]
        lk = [t for t in ts if t["is_lookup"]]
        if lk:
            lines += ["## Lookup tables in this module", "", "| Lookup | Values | Description |", "|---|---|---|"]
            for t in lk:
                lines.append(f"| `{t['name']}` | {len(t.get('values', []))} | {t.get('description', '')} |")
            lines.append("")
        if lookup_refs:
            lines += ["## Lookup references (enumeration FKs)", "", "| Table | Lookup tables referenced |", "|---|---|"]
            for tn, refs in sorted(lookup_refs.items()):
                lines.append(f"| `{tn}` | {', '.join('`' + r + '`' for r in sorted(set(refs)))} |")
            lines.append("")
        lines += ["## Tables", "", "| Table | Kind | Delete | Volume | Description |", "|---|---|---|---|---|"]
        for t in ts:
            lines.append(f"| `{t['name']}` | {t['kind']} | {t['delete_policy']} | {t['volume']} | {(t.get('description') or '').replace('|', '/')} |")
        open(os.path.join(DOCS, "01_er", f"{k}.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")

    # ----- ownership workbook -----
    wb2 = new_wb()
    rows = []
    for k, title in MODULES.items():
        ts = [t for t in tables if t["module"] == k]
        if ts:
            rows.append([k, title, MODULE_OWNER[k], "Common (Phase 1)" if k in ("master_data", "profile", "jobs_applications") else ("Optional stretch" if k == "tier3_reference" else "Individual/shared"),
                         len(ts), sum(1 for t in ts if t["is_lookup"]), sum(1 for t in ts if t["delete_policy"] == "soft"), sum(1 for t in ts if t["delete_policy"] == "append_only"),
                         sum(1 for t in ts if t["volume"] in ("high", "very_high")), sum(len(t["columns"]) for t in ts)])
    rows.append(["TOTAL", "", "", "", len(tables), sum(1 for t in tables if t["is_lookup"]), sum(1 for t in tables if t["delete_policy"] == "soft"),
                 sum(1 for t in tables if t["delete_policy"] == "append_only"), sum(1 for t in tables if t["volume"] in ("high", "very_high")), sum(len(t["columns"]) for t in tables)])
    add_sheet(wb2, "Modules", ["module_key", "module", "owner", "type", "tables", "lookup_tables", "soft_delete_tables", "append_only_tables", "high_volume_tables", "columns"], rows,
              widths=[24, 50, 10, 18, 8, 8, 10, 10, 10, 9])
    rows = [[t["module"], MODULE_OWNER[t["module"]], t["name"], t["kind"], t["delete_policy"], t["volume"], t["milestones"]] for t in tables]
    add_sheet(wb2, "Tables", ["module_key", "owner", "table", "kind", "delete_policy", "volume", "milestones"], rows, widths=[24, 10, 36, 10, 13, 10, 24], freeze_cols=3)
    add_sheet(wb2, "CrossModuleFK", ["from_module", "from_table", "fk_columns", "to_module", "to_table", "on_delete", "reason"], cross, widths=[24, 34, 30, 24, 28, 11, 60],
              wrap_cols=("reason",))
    rows = [[a, b, n] for (a, b), n in sorted(edges.items())]
    add_sheet(wb2, "ModuleDependencies", ["from_module", "to_module", "fk_count"], rows, widths=[26, 26, 9])
    wb2.save(os.path.join(DOCS, "03_module_ownership.xlsx"))
    print(f"wrote dictionary: {len(tables)} tables, {sum(len(t['columns']) for t in tables)} columns, ER for {len(MODULES)} modules")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Build docs/14_threat_model.md and docs/15_security_design_note.md from the data model.
usage: python docs/tools/build_security_docs.py   (run build_rbac.py first only if the matrix changed; this script recomputes what it needs)"""
import os, re, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(__file__))
import rbac_core as C
import rbac_text as X
import rbac_spec as S
from rbac_tests import TESTS
from dictlib import MODULES
import sec_doc14, sec_doc15

DOCS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LEVEL = {"Low": 1, "Medium": 2, "High": 3}


def method(col):
    p = (col.get("pii") or "").lower()
    if "argon2" in p: return "argon2id hash"
    if "aes" in p: return "AES-GCM envelope"
    if "hmac" in p: return "keyed HMAC"
    if "sha-256" in p or "hash" in p: return "token hash"
    if "vendor token" in p or "token" in p or "tokenis" in p: return "vendor token"
    if "vault reference" in p or "secret stored outside" in p: return "secret reference"
    if "mask" in p: return "masked"
    if "encrypt" in p: return "storage-layer encryption"
    return "ACL + audit"


def module_section(model, mod):
    sens, pers = [], defaultdict(list)
    n_ps = 0
    for t in model["tables"]:
        if t["module"] != mod:
            continue
        hit = False
        for c in t["columns"]:
            if c["sens"] == "Sensitive":
                sens.append(f"`{t['name']}.{c['name']}` ({method(c)})"); hit = True
            elif c["sens"] == "Personal":
                pers[t["name"]].append(c["name"]); hit = True
        n_ps += hit
    lines = [f"**{MODULES[mod]}** (`{mod}`): {n_ps} tables hold Personal or Sensitive columns."]
    lines.append("* Sensitive: " + ("; ".join(sens) if sens else "none"))
    if pers:
        lines.append("* Personal (default handling: access control, masked views for non-owners, audit on reveal, anonymise on erasure): " +
                     "; ".join(f"`{t}`: {', '.join(cs)}" for t, cs in pers.items()))
    else:
        lines.append("* Personal: none")
    return "\n".join(lines)


def main():
    model, T, rows = C.compute()
    tabs = model["tables"]
    cols = Counter(); per_mod = defaultdict(Counter)
    for t in tabs:
        for c in t["columns"]:
            cols[c["sens"]] += 1; per_mod[t["module"]][c["sens"]] += 1
    n_ps = sum(1 for t in tabs if any(c["sens"] in ("Personal", "Sensitive") for c in t["columns"]))
    n_ttl = sum(1 for t in tabs if t.get("ttl"))
    n_ao = sum(1 for t in tabs if t["delete_policy"] == "append_only")
    n_lookup = sum(1 for t in tabs if t["is_lookup"])
    storage_only = sum(1 for t in tabs for c in t["columns"] if c["sens"] == "Personal" and not re.search(r"aes|hmac|hash|token|vendor", (c.get("pii") or ""), re.I))
    n_aes = sum(1 for t in tabs for c in t["columns"] if c["sens"] in ("Personal", "Sensitive") and re.search(r"aes", c.get("pii") or "", re.I))
    codes = {v["code"] for v in next(t for t in tabs if t["name"] == "audit_action")["values"]}
    ents = {v["code"] for v in next(t for t in tabs if t["name"] == "audit_entity_type")["values"]}
    gap_codes = {r[2] for r in X.AUDIT_ROWS if r[2] and r[2] not in codes} | {c for k, c, w in X.AUDIT_RECOMMENDATIONS if k == "audit_action" and c not in codes}
    gap_ents = {r[3] for r in X.AUDIT_ROWS if r[3] and r[3] not in ents} | {c for k, c, w in X.AUDIT_RECOMMENDATIONS if k == "audit_entity_type" and c not in ents}
    n_masked = sum(1 for t in tabs if any(c["sens"] in ("Personal", "Sensitive") for c in t["columns"]))

    # class table
    hdr = "| module | tables | lookups | columns | Public | Internal | Personal | Sensitive | tables with Personal/Sensitive |\n|---|---|---|---|---|---|---|---|---|\n"
    body = []
    for k, name in MODULES.items():
        mt = [t for t in tabs if t["module"] == k]
        c = per_mod[k]
        body.append(f"| {name} (`{k}`) | {len(mt)} | {sum(1 for t in mt if t['is_lookup'])} | {sum(c.values())} | {c['Public']} | {c['Internal']} | {c['Personal']} | {c['Sensitive']} | "
                    f"{sum(1 for t in mt if any(x['sens'] in ('Personal', 'Sensitive') for x in t['columns']))} |")
    body.append(f"| **Total** | {len(tabs)} | {n_lookup} | {sum(cols.values())} | {cols['Public']} | {cols['Internal']} | {cols['Personal']} | {cols['Sensitive']} | {n_ps} |")
    class_table = hdr + "\n".join(body)

    b75 = "\n\n".join(module_section(model, m) for m in ("administration", "organisation_account", "gig_marketplace"))
    b76 = "\n\n".join(module_section(model, m) for m in ("licences_devices", "sourcing_consultant", "subscriptions_payments"))
    b77 = "\n\n".join(module_section(model, m) for m in ("notifications_community", "analytics", "master_data", "profile", "jobs_applications", "tier3_reference",
                                                         "ext_network", "ext_matching", "ext_ads", "ext_learning", "ext_talent_ops"))

    # threats
    th = "| id | abuse case | adversary | assets | attack path | controls (tables / columns / rules) | inherent | residual | tests |\n|---|---|---|---|---|---|---|---|---|\n"
    th += "\n".join("| " + " | ".join(str(x).replace("|", "/") for x in t) + " |" for t in sec_doc14.THREATS)
    res = Counter(t[7] for t in sec_doc14.THREATS); inh = Counter(t[6] for t in sec_doc14.THREATS)
    summ = (f"{len(sec_doc14.THREATS)} abuse cases: inherent High {inh['High']} / Medium {inh['Medium']} / Low {inh['Low']}; residual High {res['High']} / Medium {res['Medium']} / Low {res['Low']}.\n\n"
            "| residual | cases |\n|---|---|\n" + "\n".join(f"| {r} | {', '.join(t[0] for t in sec_doc14.THREATS if t[7] == r)} |" for r in ("High", "Medium", "Low")) +
            "\n\nMedium residuals are accepted only with an owner, a monitoring signal (security_event type or verifier job) and a review date at Gate 2. No residual is High provided findings F-01, F-03 and F-14 are implemented as recommended.")
    tt = "| id | area | actor | test steps | expected result | evidence | rule / BR |\n|---|---|---|---|---|---|---|\n"
    tt += "\n".join("| " + " | ".join(str(x).replace("|", "/") for x in t) + " |" for t in TESTS)

    common = {
        "N_TABLES": len(tabs), "N_LOOKUP": n_lookup, "N_AO": n_ao, "N_COLS": sum(cols.values()), "N_PERS_COLS": cols["Personal"], "N_SENS_COLS": cols["Sensitive"],
        "N_TABLES_PS": n_ps, "N_TTL": n_ttl, "N_AES": n_aes, "N_STORAGE_ONLY": storage_only, "N_MASKED": n_masked, "N_GAP_CODES": len(gap_codes), "N_GAP_ENTS": len(gap_ents),
        "N_AUDIT_ACTIONS": len(codes), "CHAINS": 32, "CLASS_TABLE": class_table, "B75_TABLE": b75, "B76_TABLE": b76, "B77_TABLE": b77,
        "THREAT_TABLE": th, "RESIDUAL_SUMMARY": summ, "TEST_TABLE": tt,
    }

    def render(tpl):
        out = tpl
        for k, v in common.items():
            out = out.replace("{{" + k + "}}", str(v))
        left = re.findall(r"\{\{[A-Z_0-9]+\}\}", out)
        assert not left, left
        return out

    d14 = render(sec_doc14.TEMPLATE)
    d15 = render(sec_doc15.TEMPLATE)
    with open(os.path.join(DOCS, "14_threat_model.md"), "w", encoding="utf-8", newline="\n") as f: f.write(d14)
    with open(os.path.join(DOCS, "15_security_design_note.md"), "w", encoding="utf-8", newline="\n") as f: f.write(d15)

    # ---- consistency checks
    rules = {r["id"] for r in model["rules"]}
    names = {t["name"] for t in tabs}
    colnames = {c["name"] for t in tabs for c in t["columns"]}
    views = {v["name"] for v in model["views"]}
    lookupcodes = {v["code"] for t in tabs if t["is_lookup"] for v in t.get("values", [])}
    extra = set(S.ACTORS) | set(S.SHORT.values()) | {"schema_owner", "app_service", "app_rw", "dba_break_glass", "changefeed_svc", "root", "admin", "public"}
    unknown = Counter()
    for doc in (d14, d15):
        missing = sorted(set(re.findall(r"BR-[A-Z]+-\d+", doc)) - rules)
        print("missing BR ids:", missing)
        for tok in re.findall(r"`([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?(?:/[a-z_]+)*)`", doc):
            for part in tok.split("/"):
                p = part.split(".")
                if len(p) == 2 and p[0] in names and p[1] in colnames: continue
                if part in names or part in views or part in colnames or part in lookupcodes or part in extra or part in codes or part in ents: continue
                if p[0] in names and len(p) == 2: unknown[tok] += 1; continue
                unknown[part] += 1
    print("backtick tokens not found in model (review):", sorted(unknown)[:150])
    print("wrote", len(d14), len(d15), "chars")


if __name__ == "__main__":
    main()

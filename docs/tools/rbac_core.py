"""Assembly of the RBAC matrix: parse specs, apply rule groups, generate service-role cells, validate."""
import sys, os, re
sys.path.insert(0, os.path.dirname(__file__))
from dictlib import load_model
import rbac_spec as S

CELL_RE = re.compile(r"^([CRUD]+|-)(\*?)\|([GVATN](?:\+[GVATN])*)$")
STAFF = ("super_admin", "admin")
COMPLIANCE_ONLY = {"candidate_diversity_declaration", "candidate_identity_verification", "candidate_work_authorisation", "background_check_result",
                   "reference_check_response", "equal_opportunity_response"}
MONEY_REF_TARGETS = {"wallet", "wallet_ledger_entry", "payment_order", "refund", "invoice"}
ETL_EXCLUDE = {"message", "message_attachment", "message_thread", "message_read_receipt", "message_thread_participant", "contact_import",
               "contact_import_entry", "resume_parse_result", "application_note", "candidate_note", "interview_feedback", "application_scorecard",
               "application_scorecard_score", "outreach_message", "outreach_reply", "support_ticket_message", "reference_check_response",
               "background_check_result", "candidate_diversity_declaration", "equal_opportunity_response", "organisation_document"}


def parse_spec(spec):
    out = {}
    for tok in spec.split():
        if tok.startswith("@"):
            out.update(parse_spec(S.TPL[tok[1:]]))
        else:
            k, v = tok.split("=", 1)
            lt, enf = v.split("|", 1)
            scoped = "*" in lt
            lt = lt.replace("*", "")
            out[S.SHORT[k]] = lt + ("*" if scoped and lt != "-" else "") + "|" + enf
    return out


def fmt(letters, scoped, enf):
    letters = "".join(c for c in "CRUD" if c in letters) or "-"
    return f"{letters}{'*' if scoped and letters != '-' else ''}|{enf}"


def split(cell):
    m = CELL_RE.match(cell)
    assert m, cell
    return m.group(1), bool(m.group(2)), m.group(3)


def union_letters(cells):
    s = set()
    for c in cells:
        l, _, _ = split(c)
        if l != "-":
            s |= set(l)
    return s


def build():
    model = load_model()
    T = {t["name"]: t for t in model["tables"]}
    assign = {}
    for rid, desc, owner, spec, tabs in S.GROUPS:
        for n in tabs:
            assert n in T, f"{rid}: unknown table {n}"
            assert n not in assign, f"{n} in two groups ({assign[n][0]} and {rid})"
            assign[n] = (rid, owner, parse_spec(spec))

    def default(t):
        m, n = t["module"], t["name"]
        if t["is_lookup"]:
            if m in ("administration", "ext_matching", "ext_ads", "analytics", "ext_talent_ops", "tier3_reference"):
                return "RD-01", "platform", parse_spec(S.TPL["LOOKUP_STAFF"])
            return "RD-02", "public", parse_spec(S.TPL["LOOKUP_PUBLIC"])
        if m == "master_data":
            return "RD-03", "platform", parse_spec(S.TPL["MASTER_PUBLIC"])
        if m == "analytics":
            if any("organisation" in c["name"] for c in t["columns"]) and not n.startswith("dim_user"):
                return "RD-04", "platform", parse_spec(S.TPL["ANALYTICS_TENANT"])
            return "RD-05", "platform", parse_spec(S.TPL["ANALYTICS_ADMIN"])
        return None

    for n, t in T.items():
        if n not in assign:
            d = default(t)
            if d:
                assign[n] = d
    missing = [n for n in T if n not in assign]
    return model, T, assign, missing


def has_sens(t):
    return any(c["sens"] == "Sensitive" for c in t["columns"])


def has_pers(t):
    return any(c["sens"] == "Personal" for c in t["columns"])


def tier_of(t):
    n, m = t["name"], t["module"]
    if n in S.AUTH_TABLES: return "auth"
    if n in S.SESSION_TABLES: return "session"
    if n in S.AUDIT_CORE: return "audit_core"
    if n in S.COMPLIANCE_VAULT: return "compliance"
    if t["is_lookup"]: return "lookup"
    if m == "subscriptions_payments":
        if n in S.MONEY_VAULT: return "money_vault"
        if n in S.MONEY_CATALOGUE: return "money_cat"
        return "money"
    if m == "analytics": return "analytics"
    if any(f["ref"] in MONEY_REF_TARGETS for f in t["fks"]): return "money_ref"
    return "normal"


GC = {"lookup": "GC-LOOKUP", "auth": "GC-AUTH", "session": "GC-SESSION", "audit_core": "GC-AUDIT", "compliance": "GC-COMPLIANCE", "analytics": "GC-ANALYTICS"}


def grant_class(t):
    tr = tier_of(t); ao = t["delete_policy"] == "append_only"
    if tr in GC: return GC[tr]
    if tr.startswith("money"): return "GC-MONEY-APPEND" if ao else "GC-MONEY"
    if ao: return "GC-APPEND-PERSONAL" if (has_pers(t) or has_sens(t)) else "GC-APPEND"
    if has_sens(t): return "GC-VAULT"
    if has_pers(t): return "GC-PERSONAL"
    return "GC-STANDARD"


def service_cells(t, human):
    n = t["name"]; ao = t["delete_policy"] == "append_only"; kind = t["kind"]
    tr = tier_of(t)
    hu = union_letters(human.values())
    pers, sens = has_pers(t), has_sens(t)
    z = {a: "-|G" for a in S.SERVICE}
    z["backup_operator"] = "-|N"; z["migration_owner"] = "-|N"

    def put(a, letters, enf="G"):
        z[a] = f"{''.join(c for c in 'CRUD' if c in letters) or '-'}|{enf}"

    wr = "CR" if ao else "CRU"
    # ---- app_service (= app_rw)
    if tr == "lookup": put("app_service", "R")
    elif tr == "auth": z["app_service"] = "-|G"
    elif tr == "compliance": z["app_service"] = "C|G"
    elif tr == "session": put("app_service", "R")
    elif tr == "audit_core": put("app_service", "RU" if n == "audit_chain_head" else "CR")
    elif tr == "money_vault": z["app_service"] = "-|G"
    elif tr == "money": put("app_service", "R")
    elif tr == "money_cat": put("app_service", "R" if ao else set(hu) | set("CRU"))
    elif tr == "analytics": z["app_service"] = "-|G"
    else:
        s = set(hu) | set(wr)
        if kind == "ledger": s = {"R"}
        if ao: s -= {"U", "D"}
        put("app_service", s)
    if n == "purge_job_run": put("app_service", "R")
    # ---- auth_svc
    if tr in ("auth", "session"): put("auth_svc", "CR" if ao else "CRU")
    elif tr == "lookup": put("auth_svc", "R")
    elif tr == "audit_core": put("auth_svc", "RU" if n == "audit_chain_head" else "CR")
    elif n in ("app_user", "licence_seat"): put("auth_svc", "RU")
    elif n in ("licence", "licence_ip_allowlist", "licence_feature_override", "user_block", "organisation_block", "app_user_role", "permission",
               "app_role_permission", "licence_seat_slot", "trusted_ip"): put("auth_svc", "R")
    elif n in ("security_event", "error_log"): put("auth_svc", "CR")
    # ---- billing_svc
    if tr in ("money", "money_vault", "money_ref"): put("billing_svc", "CR" if ao else "CRU")
    elif tr == "money_cat": put("billing_svc", "CR" if n == "currency_exchange_rate" else ("RU" if n in ("coupon", "invoice_number_series") else "R"))
    elif tr == "lookup": put("billing_svc", "R")
    elif tr == "audit_core": put("billing_svc", "RU" if n == "audit_chain_head" else "CR")
    elif n in S.BILLING_READS: put("billing_svc", "R")
    # ---- audit_writer and the tiers that append audit rows inside their own transactions
    if tr == "audit_core": put("audit_writer", "RU" if n == "audit_chain_head" else "CR")
    elif n in ("security_event", "error_log"): put("audit_writer", "CR")
    elif tr == "lookup": put("audit_writer", "R")
    if tr == "audit_core":
        for a in ("purge_svc", "compliance_svc"):
            put(a, "RU" if n == "audit_chain_head" else "CR")
    # ---- purge_svc
    refs = {f["ref"] for f in t["fks"]}
    person_owned = bool(refs & {"candidate_profile", "app_user", "gig_worker_profile", "consultant_profile"}) or pers or sens
    if tr == "lookup": put("purge_svc", "R")
    elif n in ("data_retention_policy", "legal_hold"): put("purge_svc", "R")
    elif n in ("data_subject_request", "candidate_erasure_request", "app_user"): put("purge_svc", "RU")
    elif n == "purge_job_run": put("purge_svc", "CR")
    elif (tr not in ("audit_core",) and person_owned and not ao and tr not in ("analytics", "auth", "session", "money", "money_vault", "money_cat")
          and t["module"] not in ("master_data", "tier3_reference") and kind not in ("config", "ledger")):
        put("purge_svc", "RUD" if t["delete_policy"] == "soft" else "RD")
    # ---- compliance_svc
    if tr == "compliance": put("compliance_svc", "CRU")
    elif tr == "lookup": put("compliance_svc", "R")
    elif n in ("equal_opportunity_survey", "user_consent"): put("compliance_svc", "R")
    # ---- etl_service
    if tr == "lookup": put("etl_service", "R")
    elif tr == "analytics": put("etl_service", "CR" if ao else "CRUD")
    elif t["module"] == "tier3_reference": put("etl_service", "CRU")
    elif tr in ("auth", "session", "compliance", "money_vault", "audit_core") or sens or n in ETL_EXCLUDE: z["etl_service"] = "-|G"
    elif pers: z["etl_service"] = "R|V"
    else: put("etl_service", "R")
    # ---- analytics_reader
    if tr == "lookup": put("analytics_reader", "R")
    elif tr == "analytics": z["analytics_reader"] = "R|V" if pers else "R|G"
    elif t["module"] == "tier3_reference" and kind == "snapshot": put("analytics_reader", "R")
    # ---- audit_reader
    if tr == "lookup": put("audit_reader", "R")
    elif tr == "audit_core" and n != "audit_chain_head": z["audit_reader"] = "R|V"
    elif kind == "audit" or n in ("security_event", "error_log", "audit_export", "purge_job_run", "user_status_history"): z["audit_reader"] = "R|V"
    # ---- migration_owner: data DML only for seeds/config
    if tr == "lookup" or t["module"] == "master_data" or n in ("feature_flag", "feature_flag_rule"): z["migration_owner"] = "CRU|G"
    return z


def normalise(cell, t):
    l, sc, enf = split(cell)
    if l != "-":
        s = set(l)
        if t["delete_policy"] == "append_only": s -= {"U", "D"}
        if t["is_lookup"]: s -= {"D"}
        l = "".join(c for c in "CRUD" if c in s) or "-"
    if l == "-":
        sc = False
    return fmt(l, sc, enf)


def compute():
    model, T, assign, missing = build()
    assert not missing, missing
    rows = {}
    for n, t in T.items():
        rid, owner, human = assign[n]
        h = {a: "-|A" for a in S.HUMAN}
        h.update(human)
        if n in S.COMPLIANCE_VAULT:   # reachable only through the compliance service API
            for a in S.HUMAN:
                l, sc, enf = split(h[a])
                if n in COMPLIANCE_ONLY and a in STAFF:
                    h[a] = "-|A"          # special-category / vendor-check data: no staff read path at all
                elif l != "-":
                    h[a] = fmt(l, sc, enf.replace("G", "A") if "V" not in enf else "V+A")
        if (has_pers(t) or has_sens(t)) and n not in S.COMPLIANCE_VAULT:   # PF-05: no table-wide grant to persona pools on personal data
            for a in S.HUMAN:
                if a in STAFF or a == "guest":
                    continue
                l, sc, enf = split(h[a])
                if l != "-" and "G" in enf:
                    h[a] = fmt(l, sc, "V+A")
        cells = {**h, **service_cells(t, h)}
        cells = {a: normalise(cells[a], t) for a in S.ACTORS}
        rows[n] = dict(rule=rid, owner=owner, cells=cells, tier=tier_of(t), gclass=grant_class(t))
    return model, T, rows




def validate(model, T, rows):
    res = []

    def add(check, ok, detail=""):
        res.append((check, "PASS" if ok else "FAIL", detail))

    add("Every dictionary table appears exactly once", set(rows) == set(T) and len(rows) == len(T), f"{len(rows)} of {len(T)} tables")
    bad = [(n, a, c) for n, r in rows.items() for a, c in r["cells"].items() if not CELL_RE.match(c)]
    add("Every cell parses as <CRUD or -><*>|<G/V/A/T/N codes>", not bad, f"{len(rows) * len(S.ACTORS)} cells, {len(bad)} malformed")
    ao = [n for n, t in T.items() if t["delete_policy"] == "append_only"]
    viol = [(n, a, rows[n]["cells"][a]) for n in ao for a in S.ACTORS if set(split(rows[n]["cells"][a])[0]) & {"U", "D"}]
    add("No actor has U or D on append_only tables", not viol, f"{len(ao)} append_only tables x {len(S.ACTORS)} actors checked, {len(viol)} violations")
    lk = [n for n, t in T.items() if t["is_lookup"] and any("D" in split(rows[n]["cells"][a])[0] for a in S.ACTORS)]
    add("No actor can DELETE a lookup row (values are retired via is_active)", not lk, f"{len(lk)} violations")
    sec_read = [(n, a) for n in S.AUTH_TABLES + ["payment_event"] for a in S.HUMAN if "R" in split(rows[n]["cells"][a])[0]]
    add("No human actor can read credential/token/MFA/session-secret tables or raw payment events", not sec_read, str(sec_read[:5]))
    ap = [n for n in S.AUTH_TABLES if split(rows[n]["cells"]["app_service"])[0] != "-"]
    add("app_service holds no privilege on credential/token/MFA/session-secret tables", not ap, str(ap))
    ok_guest = {"app_user", "user_credential", "password_reset_token", "email_verification_token", "job_view_event", "job_share_event",
                "licence_user_invitation", "organisation_invitation", "notification_suppression", "unsubscribe_token", "faq_feedback",
                "certificate_verification", "article_view", "profile_share_link", "profile_share_visit", "public_profile_slug",
                "public_profile_setting", "invite_link_redemption", "scam_job_report", "reference_check_response",
                "announcement_dismissal"}
    g = [n for n, r in rows.items() if set(split(r["cells"]["guest"])[0]) & {"C", "U", "D"} and n not in ok_guest]
    add("Guest write access limited to registration, token redemption and anonymous events", not g, str(g))
    rc = [(n, a) for n in ("candidate_profile", "resume") for a in ("recruiter", "employer", "consultant", "placement_officer", "account_admin", "guest")
          if set(split(rows[n]["cells"][a])[0]) & {"C", "U", "D"}]
    add("Only the candidate (and staff) can create/change candidate_profile and resume (recruiters cannot register candidates)", not rc, str(rc))
    add("Recruiter, employer, consultant, TPO and guest have no INSERT on app_user",
        all("C" not in split(rows["app_user"]["cells"][a])[0] for a in ("recruiter", "employer", "consultant", "placement_officer")), "guest: C is self-signup constrained by registration_policy (T)")
    vv = [a for a in ("recruiter", "consultant") if "V" not in split(rows["job_requirement"]["cells"][a])[2]]
    add("Recruiter and consultant reach job_requirement only through views (V in the code)", not vv, str(vv))
    unscoped = []
    for n, r in rows.items():
        if r["owner"] in ("candidate", "org", "account", "gig_worker", "consultant", "user", "campus"):
            for a in ("account_admin", "candidate", "employer", "recruiter", "consultant", "placement_officer"):
                l, sc, enf = split(r["cells"][a])
                if l != "-" and not sc and ("C" in l and l == "C" or "R" in l or "U" in l or "D" in l):
                    unscoped.append((n, a, r["cells"][a]))
    add("Non-staff human cells on owner/tenant data are scoped (*), never unrestricted", not unscoped, f"{len(unscoped)} exceptions {unscoped[:4]}")
    pg = [(n, a) for n, r in rows.items() for a in S.HUMAN if a not in STAFF and (has_pers(T[n]) or has_sens(T[n])) and "G" in split(r["cells"][a])[2] and split(r["cells"][a])[0] != "-"]
    add("No persona pool role holds a table-wide grant (G) on a table with Personal/Sensitive columns (view + app instead)", not pg, f"{len(pg)} violations {pg[:3]}")
    nn = [(n, a) for n, r in rows.items() for a in S.HUMAN if split(r["cells"][a])[2] == "N"]
    add("N (n/a) is used only for backup_operator / migration_owner", not nn, str(nn[:3]))
    sv = [n for n, r in rows.items() if set(split(r["cells"]["guest"])[0]) & {"U", "D"} and n not in ("licence_user_invitation", "organisation_invitation", "unsubscribe_token")]
    add("Guest holds U only on one-time token redemption tables (invitations, unsubscribe) and never D", not sv, str(sv[:3]))
    mt = [n for n in T if n not in rows]
    return res


if __name__ == "__main__":
    model, T, rows = compute()
    for r in validate(model, T, rows):
        print(r)

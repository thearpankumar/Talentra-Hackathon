"""Roles, masked/ETL views and grants generated from docs/13_rbac_matrix.xlsx + the data dictionary."""
import collections
import re

PERSONA = {  # RoleMatrix column -> SQL role
    "super_admin": "app_staff_super", "admin": "app_staff_admin", "account_admin": "app_account_admin",
    "guest": "app_guest", "candidate": "app_candidate", "employer": "app_employer", "recruiter": "app_recruiter",
    "consultant": "app_consultant", "placement_officer": "app_tpo",
}
SERVICE = {  # RoleMatrix column -> SQL role (backup_operator / migration_owner handled separately)
    "app_service": "app_service", "auth_svc": "auth_svc", "billing_svc": "billing_svc", "audit_writer": "audit_writer",
    "purge_svc": "purge_svc", "compliance_svc": "compliance_svc", "etl_service": "etl_service",
    "analytics_reader": "analytics_reader", "audit_reader": "audit_reader",
}
LOGINS = {  # login user -> group roles
    "pool_staff_super": ["app_staff_super"], "pool_staff_admin": ["app_staff_admin"],
    "pool_account_admin": ["app_account_admin"], "pool_guest": ["app_guest"], "pool_candidate": ["app_candidate"],
    "pool_employer": ["app_employer"], "pool_recruiter": ["app_recruiter"], "pool_consultant": ["app_consultant"],
    "pool_tpo": ["app_tpo"], "svc_worker": ["app_service"], "svc_auth": ["auth_svc"], "svc_billing": ["billing_svc"],
    "svc_purge": ["purge_svc"], "svc_compliance": ["compliance_svc"], "svc_etl": ["etl_service"],
    "changefeed_svc": ["etl_service"], "svc_bi": ["analytics_reader"], "svc_audit": ["audit_reader"],
    "svc_backup": ["backup_operator"],
}
# infra placeholder roles (infra/main-db/scripts/bootstrap.sh) get mapped onto the service tier
INFRA_MAP = {"talentra_app_rw": "app_service", "talentra_analyst": "analytics_reader",
             "talentra_audit_reader": "audit_reader", "talentra_app_ro": "app_read"}
CHANGEFEED_TABLES = ["audit_event", "wallet_ledger_entry", "notification", "outreach_message"]  # GR-25
GUARDS = {  # GR-29 resource guards (statement_timeout etc.)
    "app_guest": "5s", "app_candidate": "10s", "app_recruiter": "10s", "app_employer": "15s", "app_consultant": "10s",
    "app_tpo": "10s", "app_account_admin": "15s", "app_staff_admin": "30s", "app_staff_super": "30s",
    "analytics_reader": "120s", "audit_reader": "60s", "etl_service": "300s",
}


def roles_sql(m, dbname_placeholder=True):
    out = []
    out.append("CREATE ROLE IF NOT EXISTS schema_owner NOLOGIN;")
    for r in list(PERSONA.values()) + list(SERVICE.values()) + ["backup_operator", "app_read"]:
        out.append(f"CREATE ROLE IF NOT EXISTS {r} NOLOGIN;")
    for u in LOGINS:
        out.append(f"CREATE USER IF NOT EXISTS {u} WITH PASSWORD NULL;")
    for u, groups in LOGINS.items():
        for g in groups:
            out.append(f"GRANT {g} TO {u};")
    for t in ("app_service", "auth_svc", "billing_svc", "purge_svc", "compliance_svc"):
        out.append(f"GRANT audit_writer TO {t};")
    # infra placeholder roles keep working: they become members of the matching group
    for placeholder, grp in INFRA_MAP.items():
        out.append("-- optional\nGRANT %s TO %s;" % (grp, placeholder))
    # resource guards
    for r, t in GUARDS.items():
        out.append(f"ALTER ROLE {r} SET statement_timeout = '{t}';")
    for r in list(PERSONA.values()) + ["app_service", "auth_svc", "billing_svc", "purge_svc", "compliance_svc"]:
        out.append(f"ALTER ROLE {r} SET idle_in_transaction_session_timeout = '30s';")
    out.append("ALTER ROLE app_read SET default_transaction_read_only = on;")
    out.append("ALTER ROLE analytics_reader SET default_transaction_read_only = on;")
    out.append("ALTER ROLE audit_reader SET default_transaction_read_only = on;")
    return out


def db_privs_sql():
    """Run inside the target database (needs the database name via current_database())."""
    return [
        "GRANT CONNECT ON DATABASE {db} TO app_service, auth_svc, billing_svc, audit_writer, purge_svc, compliance_svc, "
        "etl_service, analytics_reader, audit_reader, app_read, " + ", ".join(PERSONA.values()) + ";",
        "GRANT ALL ON DATABASE {db} TO schema_owner;",
        "GRANT BACKUP, RESTORE ON DATABASE {db} TO backup_operator;",
        # GR-06: default deny, new objects start without grants (E-13)
        "ALTER DEFAULT PRIVILEGES FOR ROLE schema_owner IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;",
        "ALTER DEFAULT PRIVILEGES FOR ROLE schema_owner IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC;",
        "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;",
    ]


def parse_masked(expr):
    """'email -> expr; phone -> expr' -> {col: expr}"""
    out = {}
    if not expr:
        return out
    for part in re.split(r";\s+(?=\w+ -> )", expr.strip()):
        if " -> " in part:
            c, e = part.split(" -> ", 1)
            out[c.strip()] = e.strip().rstrip(";")
    return out


def masked_views(m, warnings):
    stmts, names = [], {}
    for r in m.R["MaskedViews"]:
        t = r["table"]
        if t not in m.cols:
            warnings.append(f"MaskedViews: unknown table {t}")
            continue
        inc = [c.strip() for c in r["included columns (Public/Internal)"].split(",") if c.strip()]
        masked = parse_masked(r["masked Personal columns (expression)"])
        colmap = {c["column"]: c for c in m.cols[t]}
        sel = []
        for c in m.cols[t]:
            n = c["column"]
            if n in inc:
                sel.append(n)
            elif n in masked:
                e = masked[n]
                if e.upper() == "NULL":
                    e = f"CAST(NULL AS {c['type']})"
                sel.append(f"{e} AS {n}")
        soft = "deleted_at" in colmap
        if not sel:
            warnings.append(f"MaskedViews: {t} has no selectable columns")
            continue
        where = " WHERE deleted_at IS NULL" if soft else ""
        v = r["persona/masked view"]
        stmts.append(f"CREATE OR REPLACE VIEW {v} AS SELECT {', '.join(sel)} FROM {t}{where};")
        names[v] = t
        ev = r["ETL view"]
        if ev:
            stmts.append(f"CREATE OR REPLACE VIEW {ev} AS SELECT {', '.join(c for c in inc if c in colmap)} FROM {t}{where};")
            names[ev] = t
    return stmts, names


def parse_cell(cell):
    if not cell or "|" not in str(cell):
        return "", "", False
    letters, code = str(cell).split("|", 1)
    scoped = letters.endswith("*")
    return letters.rstrip("*"), code, scoped


def privs_for(letters, delete_policy):
    p = []
    if "C" in letters:
        p.append("INSERT")
    if "R" in letters or "U" in letters:
        p.append("SELECT")
    if "U" in letters:
        p.append("UPDATE")
    if "D" in letters and delete_policy == "hard":
        p.append("DELETE")
    if delete_policy == "append_only":
        p = [x for x in p if x in ("INSERT", "SELECT")]
    return sorted(set(p), key=["SELECT", "INSERT", "UPDATE", "DELETE"].index)


def grants_sql(m, view_names, warnings):
    """view_names: set of existing view names. Returns statements."""
    roles = dict(PERSONA)
    roles.update(SERVICE)
    G = collections.defaultdict(lambda: collections.defaultdict(list))   # role -> privset -> tables
    V = collections.defaultdict(set)                                        # role -> views
    for row in m.R["RoleMatrix"]:
        t = row["table"]
        dp = m.tables[t]["delete_policy"]
        vl = [x.strip() for x in str(row.get("views_over_table") or "").split(",") if x.strip()]
        for actor, role in roles.items():
            letters, code, _ = parse_cell(row.get(actor))
            if not letters:
                continue
            codes = set(re.split(r"[+]", code))
            if "G" in codes:
                p = privs_for(letters, dp)
                if p:
                    G[role][tuple(p)].append(t)
            if "V" in codes and ("R" in letters or "U" in letters or "C" in letters):
                names = vl or ([f"vw_{t}_masked"] if f"vw_{t}_masked" in view_names else
                               ([f"vw_{t}_visible"] if f"vw_{t}_visible" in view_names else []))
                if actor == "etl_service" and f"vw_etl_{t}" in view_names:
                    names = [f"vw_etl_{t}"]
                elif actor == "etl_service" and not vl and f"vw_{t}_visible" not in view_names:
                    names = []
                if not names:
                    warnings.append(f"grant: {actor} has V on {t} but no view exists")
                for n in names:
                    if n in view_names:
                        V[role].add(n)
                    else:
                        warnings.append(f"grant: view {n} (for {actor} on {t}) is not defined")
    out = []
    for role in sorted(G):
        for privs, tables in sorted(G[role].items()):
            tl = sorted(set(tables))
            for i in range(0, len(tl), 60):
                out.append(f"GRANT {', '.join(privs)} ON TABLE {', '.join(tl[i:i + 60])} TO {role};")
    # views: one statement per view, tolerated by apply.py when the view does not exist yet ("-- optional"); views the
    # dictionary/overrides do not define at all never reach this point (listed in WARNINGS.txt instead)
    by_view = collections.defaultdict(set)
    for role, vs in V.items():
        for v in vs:
            by_view[v].add(role)
    for v in sorted(by_view):
        out.append(f"-- optional\nGRANT SELECT ON TABLE {v} TO {', '.join(sorted(by_view[v]))};")
    # GR-25 changefeed sources (CHANGEFEED needs SELECT as well); GR-24 purge helpers are matrix cells
    for t in CHANGEFEED_TABLES:
        if t in m.tables:
            out.append(f"GRANT SELECT, CHANGEFEED ON TABLE {t} TO etl_service;")
        else:
            warnings.append(f"grant: GR-25 changefeed table {t} is not in the dictionary")
    # read-only application role: lookups + masked views only
    lk = sorted(t for t, r in m.tables.items() if r["kind"] == "lookup")
    for i in range(0, len(lk), 60):
        out.append(f"GRANT SELECT ON TABLE {', '.join(lk[i:i + 60])} TO app_read;")
    secret_cls = {"GC-AUTH", "GC-SESSION", "GC-COMPLIANCE", "GC-VAULT"}
    secret_tables = {r["table"] for r in m.R["RoleMatrix"] if r["grant_class"] in secret_cls}

    def _src(n):
        mm = re.match(r"vw_(.+)_(masked|public)$", n)
        return mm.group(1) if mm else None
    mv = sorted(n for n in view_names if (n.endswith("_masked") or n.endswith("_public")) and _src(n) not in secret_tables)
    for v in mv:
        out.append(f"-- optional\nGRANT SELECT ON TABLE {v} TO app_read;")
    return out


def visible_views(m, existing):
    """Default read view for tables where the matrix gives V but no masked/business view exists:
    soft-deleted rows are hidden (the 'soft-delete default view')."""
    need = set()
    actors = list(PERSONA) + ["etl_service", "analytics_reader", "audit_reader"]
    for row in m.R["RoleMatrix"]:
        t = row["table"]
        vl = [x.strip() for x in str(row.get("views_over_table") or "").split(",") if x.strip()]
        if vl or f"vw_{t}_masked" in existing:
            continue
        for a in actors:
            letters, code, _ = parse_cell(row.get(a))
            if letters and "V" in set(code.split("+")) and ("R" in letters or "U" in letters):
                need.add(t)
    out, names = [], {}
    for t in sorted(need):
        colmap = {c["column"] for c in m.cols[t]}
        where = " WHERE deleted_at IS NULL" if "deleted_at" in colmap else ""
        out.append(f"CREATE OR REPLACE VIEW vw_{t}_visible AS SELECT * FROM {t}{where};")
        names[f"vw_{t}_visible"] = t
    return out, names

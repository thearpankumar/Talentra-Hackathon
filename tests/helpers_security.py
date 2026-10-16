"""Independent expectation model for the RBAC tests (does NOT import infra/schema/gen_security.py).

Everything here is derived from docs/13_rbac_matrix.xlsx (RoleMatrix, MaskedViews, Actors) and the Grants sheet
rules GR-08..GR-25, so a bug in the generator shows up as a diff.
"""
import re
from functools import lru_cache
from pathlib import Path

import openpyxl

DOCS = Path(__file__).resolve().parent.parent / "docs"

# RoleMatrix column -> SQL role (docs/13 Actors sheet, column 'CockroachDB SQL role mapping')
PERSONA = {"super_admin": "app_staff_super", "admin": "app_staff_admin", "account_admin": "app_account_admin",
           "guest": "app_guest", "candidate": "app_candidate", "employer": "app_employer", "recruiter": "app_recruiter",
           "consultant": "app_consultant", "placement_officer": "app_tpo"}
SERVICE = {k: k for k in ("app_service", "auth_svc", "billing_svc", "audit_writer", "purge_svc", "compliance_svc",
                          "etl_service", "analytics_reader", "audit_reader")}
ACTOR_ROLE = {**PERSONA, **SERVICE}
ALL_ROLES = list(ACTOR_ROLE.values())
ROLE_ACTOR = {v: k for k, v in ACTOR_ROLE.items()}
GROUP_ROLES = ALL_ROLES + ["backup_operator", "schema_owner", "app_read"]
# docs/13 Actors sheet: login -> group role (+ GR-05 audit_writer membership)
LOGIN_MEMBERSHIP = {
    "pool_staff_super": {"app_staff_super"}, "pool_staff_admin": {"app_staff_admin"},
    "pool_account_admin": {"app_account_admin"}, "pool_guest": {"app_guest"}, "pool_candidate": {"app_candidate"},
    "pool_employer": {"app_employer"}, "pool_recruiter": {"app_recruiter"}, "pool_consultant": {"app_consultant"},
    "pool_tpo": {"app_tpo"}, "svc_worker": {"app_service"}, "svc_auth": {"auth_svc"}, "svc_billing": {"billing_svc"},
    "svc_purge": {"purge_svc"}, "svc_compliance": {"compliance_svc"}, "svc_etl": {"etl_service"},
    "changefeed_svc": {"etl_service"}, "svc_bi": {"analytics_reader"}, "svc_audit": {"audit_reader"},
    "svc_backup": {"backup_operator"},
}
AUDIT_WRITER_MEMBERS = {"app_service", "auth_svc", "billing_svc", "purge_svc", "compliance_svc"}
ORDER = ["SELECT", "INSERT", "UPDATE", "DELETE"]


@lru_cache(maxsize=1)
def matrix():
    wb = openpyxl.load_workbook(DOCS / "13_rbac_matrix.xlsx", read_only=True, data_only=True)
    out = {}
    for ws in wb:
        rows = list(ws.iter_rows(values_only=True))
        h = [str(x) if x is not None else "" for x in rows[0]]
        out[ws.title] = [dict(zip(h, ["" if v is None else v for v in r])) for r in rows[1:]]
    return out


@lru_cache(maxsize=1)
def role_matrix():
    return {r["table"]: r for r in matrix()["RoleMatrix"]}


def cell(table, actor):
    """-> (letters, codes:set, scoped)"""
    v = str(role_matrix()[table].get(actor) or "")
    if "|" not in v:
        return "", set(), False
    letters, code = v.split("|", 1)
    return letters.rstrip("*"), set(code.split("+")), letters.endswith("*")


def table_privs(table, actor):
    """GR-08: C->INSERT, R->SELECT, U->UPDATE+SELECT, D->DELETE (hard-delete tables only; soft = UPDATE deleted_at).
    Append-only tables: never UPDATE/DELETE (GR-14)."""
    letters, codes, _ = cell(table, actor)
    if "G" not in codes or not letters:
        return set()
    dp = role_matrix()[table]["delete_policy"]
    p = set()
    if "C" in letters:
        p.add("INSERT")
    if "R" in letters or "U" in letters:
        p.add("SELECT")
    if "U" in letters:
        p.add("UPDATE")
    if "D" in letters and dp == "hard":
        p.add("DELETE")
    if dp == "append_only":
        p &= {"SELECT", "INSERT"}
    return p


def expected_table_grants(role):
    """{table: set(privs)} for the persona/service role (matrix G cells)."""
    actor = ROLE_ACTOR[role]
    out = {}
    for t in role_matrix():
        p = table_privs(t, actor)
        if p:
            out[t] = p
    return out


def view_cells(role):
    """tables on which the actor has a V cell with read intent."""
    actor = ROLE_ACTOR[role]
    out = []
    for t in role_matrix():
        letters, codes, _ = cell(t, actor)
        if letters and "V" in codes and ("R" in letters or "U" in letters or "C" in letters):
            out.append(t)
    return out


def allowed_views(role):
    """Upper bound of views the role may hold SELECT on (GR-10): matrix views_over_table of its V cells, the
    proposed vw_<t>_masked / vw_<t>_visible, vw_etl_<t>, plus the explicit GR-20/22 names."""
    s = set()
    for t in view_cells(role):
        for v in str(role_matrix()[t].get("views_over_table") or "").split(","):
            if v.strip():
                s.add(v.strip())
        s.update({f"vw_{t}_masked", f"vw_{t}_visible", f"vw_etl_{t}"})
    return s


def tables_of_class(*classes):
    return sorted(t for t, r in role_matrix().items() if r["grant_class"] in classes)


def tables_with_policy(policy):
    return sorted(t for t, r in role_matrix().items() if r["delete_policy"] == policy)


def sensitive_columns():
    return matrix()["SensitiveColumns"]


def masked_views():
    return matrix()["MaskedViews"]


def tests_b79():
    return {r["id"]: r for r in matrix()["Tests_B79"]}


def parse_masked(expr):
    out = {}
    if not expr:
        return out
    for part in re.split(r";\s+(?=\w+ -> )", str(expr).strip()):
        if " -> " in part:
            c, e = part.split(" -> ", 1)
            out[c.strip()] = e.strip().rstrip(";")
    return out

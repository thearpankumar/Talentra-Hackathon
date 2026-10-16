"""Shared loader/expander for the Talentra data dictionary staging files.

Staging files live in docs/_staging/*.yaml (one per module group).  `load_model()`
merges them, expands lookups (shorthand) into real tables and injects the standard
columns (id, audit columns, deleted_at) so every table is fully explicit afterwards.
"""
from __future__ import annotations
import glob, os, copy
import yaml
_LOADER = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)  # C loader: ~10x faster on the 3.6 MB staging set

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STAGING = os.path.join(ROOT, "docs", "_staging")

MODULES = {
    "master_data": "Master data",
    "profile": "Profile",
    "jobs_applications": "Jobs and Applications",
    "administration": "Administration",
    "organisation_account": "Organisation and account",
    "gig_marketplace": "Gig marketplace",
    "licences_devices": "Licences, seats, devices and sessions",
    "sourcing_consultant": "Sourcing and consultant requests",
    "subscriptions_payments": "Subscriptions, wallet, payments and download history",
    "notifications_community": "Notifications, preferences and community",
    "analytics": "Analytics layer and reporting marts",
    "tier3_reference": "Tier 3 reference domains (optional)",
    "ext_network": "Extension: Network, connections and graph",
    "ext_matching": "Extension: Recommendations and matching signals",
    "ext_ads": "Extension: Ads and sponsored placements",
    "ext_learning": "Extension: Learning and assessments marketplace",
    "ext_talent_ops": "Extension: Talent operations (onboarding, background checks, CRM, trust and safety)",
}
MODULE_OWNER = {
    "master_data": "Team", "profile": "Team", "jobs_applications": "Team",
    "administration": "Member A", "organisation_account": "Member B", "gig_marketplace": "Member C",
    "licences_devices": "Member A", "sourcing_consultant": "Member B", "subscriptions_payments": "Member C",
    "notifications_community": "Team", "analytics": "Team", "tier3_reference": "Team",
    "ext_network": "Team", "ext_matching": "Team", "ext_ads": "Team", "ext_learning": "Team", "ext_talent_ops": "Team",
}

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def _col(name, type_, domain, null=False, null_why="", default=None, sens="Internal", pii="", desc="", **kw):
    d = dict(name=name, type=type_, domain=domain, null=null, null_why=null_why, default=default,
             sens=sens, pii=pii, desc=desc, injected=True)
    d.update(kw)
    return d


def expand_lookup(lk: dict, module: str) -> dict:
    """Turn a `lookups:` shorthand entry into a full table dict."""
    name = lk["name"]
    t = dict(
        name=name, kind="lookup", delete_policy="hard", volume="low", tier=lk.get("tier", "1"),
        milestones=lk.get("milestones", []), retention="Reference data; retired via is_active, never deleted.",
        description=lk.get("description", f"Lookup (enumeration) of {name.replace('_', ' ')} values."),
        audit="full", module=module, is_lookup=True,
        columns=[
            dict(name="code", type="STRING(64)", domain="code", null=False, sens="Public",
                 desc="Stable machine code of the value (lowercase snake_case); never shown to users."),
            dict(name="name_i18n", type="JSONB", domain="json", null=False, sens="Public",
                 desc="Display name per supported UI language, {language_tag: text}; must contain the default language 'en'; "
                      "UI falls back along language.fallback chain to 'en'."),
            dict(name="sort_order", type="INT4", domain="quantity", null=False, default="0", sens="Public",
                 desc="Display order (ascending)."),
            dict(name="is_active", type="BOOL", domain="boolean", null=False, default="true", sens="Public",
                 desc="False = retired value; kept for historical rows."),
        ],
        constraints=[
            dict(name=f"uq_{name}_code", type="unique", columns=["code"], desc="Natural key of the lookup."),
            dict(name=f"ck_{name}_code_format", type="check", expr="code ~ '^[a-z0-9_]+$'", desc="Lowercase snake_case code."),
            dict(name=f"ck_{name}_name_i18n_en", type="check", expr="name_i18n ? 'en'", desc="Default language present."),
        ],
        indexes=[], fks=[],
        values=lk.get("values", []),
    )
    for extra in lk.get("extra_columns", []) or []:
        t["columns"].append(extra)
    for k in ("constraints", "indexes", "fks"):
        t[k].extend(lk.get(k, []) or [])
    return t


def inject_standard(t: dict) -> dict:
    cols = t.get("columns", []) or []
    names = {c["name"] for c in cols}
    std = [_col("id", "UUID", "identifier", default="gen_random_uuid()", sens="Internal",
                desc="Surrogate primary key (UUID v4).")]
    audit = t.get("audit", "full")
    if t.get("delete_policy") == "append_only" and "audit" not in t:
        audit = "created_only"
    t["audit"] = audit
    if audit in ("full", "created_only"):
        std.append(_col("created_at", "TIMESTAMPTZ", "timestamp", default="now()", sens="Internal",
                        desc="Row creation time (UTC)."))
        std.append(_col("created_by", "UUID", "identifier", default=None, sens="Internal",
                        desc=f"app_user.id of the creator; system actor {SYSTEM_USER_ID} for automated writes (no FK, see D-04)."))
    if audit == "full":
        std.append(_col("updated_at", "TIMESTAMPTZ", "timestamp", default="now()", sens="Internal",
                        desc="Last update time (UTC); set on every UPDATE by the application/ON UPDATE."))
        std.append(_col("updated_by", "UUID", "identifier", sens="Internal",
                        desc="app_user.id of the last updater (no FK, see D-04)."))
    if t.get("delete_policy") == "soft":
        std.append(_col("deleted_at", "TIMESTAMPTZ", "timestamp", null=True,
                        null_why="NULL means the row is live; non-NULL is the soft-delete instant (UTC).",
                        sens="Internal", desc="Soft-delete timestamp; default queries filter deleted_at IS NULL."))
        std.append(_col("deleted_by", "UUID", "identifier", null=True,
                        null_why="NULL while the row is live; set together with deleted_at.", sens="Internal",
                        desc="app_user.id that soft-deleted the row (no FK, see D-04)."))
    # std first, then declared columns (declared must not redefine std names)
    t["columns"] = std + [c for c in cols if c["name"] not in {s["name"] for s in std}]
    t["_declared_std_conflict"] = sorted(names & {s["name"] for s in std})
    return t


def load_files(paths=None):
    paths = paths or sorted(glob.glob(os.path.join(STAGING, "*.yaml")))
    docs = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            d = yaml.load(f, Loader=_LOADER) or {}
        d["_file"] = os.path.basename(p)
        docs.append(d)
    return docs


def load_model(paths=None):
    """Return dict(tables=[...], views=[...], rules=[...], notes={module: str}, errors=[...])."""
    docs = load_files(paths)
    model = dict(tables=[], views=[], rules=[], notes={}, errors=[], files=[d["_file"] for d in docs])
    for d in docs:
        f = d["_file"]
        default_mod = d.get("module")
        if default_mod:
            model["notes"].setdefault(default_mod, "")
            if d.get("notes"):
                model["notes"][default_mod] += f"\n\n<!-- {f} -->\n" + str(d["notes"])
        for lk in d.get("lookups", []) or []:
            mod = lk.get("module", default_mod)
            t = expand_lookup(lk, mod)
            t["_file"] = f
            model["tables"].append(inject_standard(t))
        for t in d.get("tables", []) or []:
            t = copy.deepcopy(t)
            t.setdefault("module", default_mod)
            t.setdefault("kind", "entity")
            t.setdefault("delete_policy", "soft")
            t.setdefault("volume", "medium")
            t.setdefault("tier", "1")
            t.setdefault("milestones", [])
            t.setdefault("constraints", [])
            t.setdefault("indexes", [])
            t.setdefault("fks", [])
            t["_file"] = f
            t["is_lookup"] = False
            model["tables"].append(inject_standard(t))
        for v in d.get("views", []) or []:
            v = dict(v); v.setdefault("module", default_mod); v["_file"] = f
            model["views"].append(v)
        for r in d.get("business_rules", []) or []:
            r = dict(r); r.setdefault("module", default_mod); r["_file"] = f
            model["rules"].append(r)
    for t in model["tables"]:
        for fk in t["fks"]:
            fk["columns"] = fk.get("columns") or ([fk["column"]] if fk.get("column") else [])
            fk["ref_columns"] = fk.get("ref_columns") or ["id"]
            fk.setdefault("on_update", "RESTRICT")
    return model

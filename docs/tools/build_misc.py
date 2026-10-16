#!/usr/bin/env python
"""Builds docs/06_business_rules.xlsx, docs/00_milestone_traceability.xlsx, docs/05_data_dictionary_schema.md (and keeps README index in sync).
Run after build_dictionary.py."""
import os, re, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dictlib import load_model, MODULES, MODULE_OWNER, ROOT
from xl import new_wb, add_sheet

DOCS = os.path.join(ROOT, "docs")
M = load_model()
T = {t["name"]: t for t in M["tables"]}


def mod_stats(k):
    ts = [t for t in M["tables"] if t["module"] == k]
    return len(ts), sum(1 for t in ts if t["is_lookup"])


def build_rules():
    wb = new_wb()
    rows = []
    for r in M["rules"]:
        rows.append([r["id"], r.get("module"), r["rule"], r["mechanism_type"], r["mechanism"], r.get("tables", []), r.get("milestone", ""),
                     "Yes" if r["mechanism_type"] in ("CHECK", "UNIQUE", "PARTIAL_UNIQUE", "FK", "CHAIN_FK", "SLOT_TABLE") else "Partial/Procedural"])
    rows.sort(key=lambda x: (str(x[1]), x[0]))
    add_sheet(wb, "Rules", ["rule_id", "module", "rule", "mechanism_type", "enforcement_mechanism", "tables", "milestone", "declarative_in_db"], rows,
              widths=[14, 24, 60, 18, 90, 40, 10, 14], wrap_cols=("rule", "enforcement_mechanism", "tables"), freeze_cols=1)
    cnt = collections.Counter((r["module"], r["mechanism_type"]) for r in M["rules"])
    types = sorted({r["mechanism_type"] for r in M["rules"]})
    mods = [k for k in MODULES if any(r["module"] == k for r in M["rules"])]
    rows = [[k] + [cnt[(k, ty)] for ty in types] + [sum(cnt[(k, ty)] for ty in types)] for k in mods]
    rows.append(["TOTAL"] + [sum(cnt[(k, ty)] for k in mods) for ty in types] + [len(M["rules"])])
    add_sheet(wb, "MechanismSummary", ["module"] + types + ["total"], rows, widths=[26] + [12] * (len(types) + 1))
    mt = [
        ["CHECK", "Row-level predicate, e.g. end >= start, min <= max, ranges, regex E.164, lower(email)", "Native CRDB CHECK; validated on write", "No cross-row logic; cannot call subqueries"],
        ["UNIQUE / PARTIAL_UNIQUE", "Natural keys, one-active-row rules (UNIQUE INDEX ... WHERE)", "Native; partial predicate must be deterministic", "Soft-delete tables must include deleted_at IS NULL"],
        ["FK / CHAIN_FK", "Referential integrity, composite same-tenant/same-parent FKs, period chains (P1)", "Native; composite FK needs non-partial UNIQUE on parent", "No deferrable FKs: order inserts; extra lock/IO per write"],
        ["SLOT_TABLE", "Non-overlap of windows via exploded slot rows with UNIQUE (P2)", "Native UNIQUE", "Row explosion; cleanup on cancel; TTL"],
        ["TXN_LOCK", "SELECT ... FOR UPDATE + check + write under SERIALIZABLE with 40001 retry (P4)", "Native; locking best-effort under SERIALIZABLE so correctness rests on SERIALIZABLE", "Retry loop required; contention hot rows"],
        ["PRIVILEGE", "GRANT/REVOKE to service roles; append-only = INSERT/SELECT only (P5)", "Native; no column-level privileges", "Masking via views"],
        ["VIEW", "Restricted views with predicates (tenant scope, block lists)", "Native views", "Viewer identity passed by parameter/function or session context"],
        ["TTL", "Row-level TTL for retention (P6)", "Native ttl_expire_after / ttl_expiration_expression", "TTL deletes are normal DELETEs: FKs apply"],
        ["JOB", "Scheduled jobs (purge, reconciliation, orphan checks)", "CRDB SCHEDULES or external scheduler", "Eventually consistent"],
        ["APP_LAYER", "Rules not expressible in DB", "Application/service", "Must be covered by tests"],
    ]
    add_sheet(wb, "MechanismCatalogue", ["mechanism_type", "used_for", "cockroachdb_support", "limits"], mt, widths=[24, 70, 50, 50], wrap_cols=("used_for", "cockroachdb_support", "limits"))
    wb.save(os.path.join(DOCS, "06_business_rules.xlsx"))
    return len(rows)


def parse_backlog():
    txt = open(os.path.join(ROOT, "talentra_docket.md"), encoding="utf-8").read()
    items = []
    for m in re.finditer(r"^\| (B\d+) \| (.*?) \| (.*?) \| (.*?) \|\s*$", txt, re.M):
        items.append(m.groups())
    return items


MOD_OF = {}  # B-number -> module keys for entity/rel/col/key milestones
for b, ks in {
    (4, 5, 8, 9, 12, 13, 16, 17, 20, 21): ["profile"], (6, 7, 10, 11, 14, 15, 18, 19, 22, 23): ["jobs_applications"],
    (24, 27, 30, 33): ["administration"], (25, 28, 31, 34): ["organisation_account"], (26, 29, 32, 35): ["gig_marketplace"],
    (36, 39, 42, 45): ["licences_devices"], (37, 40, 43, 46): ["sourcing_consultant"], (38, 41, 44, 47): ["subscriptions_payments"],
    (48, 51, 54, 57): ["notifications_community"], (49, 52, 55, 58): ["analytics"], (50, 53, 56, 59): ["administration"],
}.items():
    for n in b:
        MOD_OF[n] = ks


def step_of(n):
    # position within milestone triple/quad: entities, relationships, columns, keys
    if 4 <= n <= 23:
        return ["entities", "relationships", "columns", "design", "keys"][(n - 4) // 4]
    if 24 <= n <= 59:
        return ["entities", "relationships", "columns", "keys"][((n - 24) // 3) % 4]
    return ""


def artifact_for(n, text):
    mods = MOD_OF.get(n, [])
    ev = []
    step = step_of(n)
    if n == 1:
        return ("docs/02_adr_log.xlsx (ADR-001), docs/07_schema_justification.md (Engine)", "Done", "CockroachDB v26.2 chosen; PG dialect; deviations recorded")
    if n == 2:
        return ("docs/01_er/module_map.md, docs/03_module_ownership.xlsx (Modules)", "Done", f"{len([k for k in MODULES if mod_stats(k)[0]])} modules (11 docket + Tier 3 + 5 labelled extensions)")
    if n == 3:
        return ("docs/03_module_ownership.xlsx (Modules: Common)", "Done", "Profile and Jobs and Applications assigned to Team")
    if n in range(4, 24) or n in range(24, 60):
        files = [f"docs/01_er/{k}.md" for k in mods]
        sheet = {"entities": "05_data_dictionary.xlsx (Tables)", "relationships": "05_data_dictionary.xlsx (ForeignKeys), docs/03_module_ownership.xlsx (CrossModuleFK)",
                 "columns": "05_data_dictionary.xlsx (Columns)", "design": "05_data_dictionary.xlsx (all sheets)", "keys": "05_data_dictionary.xlsx (Constraints, Indexes), docs/06_business_rules.xlsx"}.get(step, "05_data_dictionary.xlsx")
        validated = "Validate" in text
        ev_s = []
        for k in mods:
            nt, nl = mod_stats(k)
            ev_s.append(f"{k}: {nt} tables ({nl} lookups)")
        art = f"{sheet}; {', '.join(files)}"
        if validated:
            art += "; validated by docs/tools/lint_dictionary.py (0 errors)"
        if step == "keys" and n in (33, 35, 45, 46, 47, 57, 58, 59):
            art += "; see mechanism rules in 06_business_rules.xlsx"
        return (art, "Done", "; ".join(ev_s))
    mp = {
        60: ("docs/12_seed_and_validation_plan.md (Seed design, load order)", "Design done; execution pending"),
        61: ("docs/12_seed_and_validation_plan.md", "Design done; execution pending"),
        62: ("docs/12_seed_and_validation_plan.md (incl. gig/overlap negative tests)", "Design done; execution pending"),
        63: ("docs/12_seed_and_validation_plan.md (licence, sourcing, subscription, refund, download)", "Design done; execution pending"),
        64: ("docs/12_seed_and_validation_plan.md", "Design done; execution pending"),
        65: ("docs/11_performance_indexing_plan.md (fixed query set, top-10 EXPLAIN)", "Design done; execution pending"),
        66: ("docs/12_seed_and_validation_plan.md (Section 5 validation SQL), docs/10_competitor_benchmark.xlsx (Gap register)", "Design done; execution pending"),
        67: ("docs/12_seed_and_validation_plan.md (module queries)", "Design done; execution pending"),
        68: ("docs/12_seed_and_validation_plan.md (module queries)", "Design done; execution pending"),
        69: ("docs/12_seed_and_validation_plan.md (module queries)", "Design done; execution pending"),
        70: ("docs/11_performance_indexing_plan.md (measurement method)", "Design done; execution pending"),
        71: ("docs/11_performance_indexing_plan.md, docs/11_index_catalogue.xlsx", "Design done; execution pending"),
        72: ("docs/11_performance_indexing_plan.md, docs/11_index_catalogue.xlsx", "Design done; execution pending"),
        73: ("docs/11_performance_indexing_plan.md (partitioning for application and notification tables)", "Design done; execution pending"),
        74: ("docs/11_performance_indexing_plan.md (extrapolation + first query to degrade)", "Design done; measurement pending"),
        75: ("docs/15_security_design_note.md (PII: Administration, Organisation, Gig)", "Done"),
        76: ("docs/15_security_design_note.md (PII: Licences, Sourcing, Subscriptions)", "Done"),
        77: ("docs/15_security_design_note.md (PII: Notifications, Analytics, shared)", "Done"),
        78: ("docs/14_threat_model.md", "Done"),
        79: ("docs/13_rbac_matrix.xlsx, docs/14_threat_model.md (test cases), docs/12_seed_and_validation_plan.md", "Design done; test execution pending"),
        80: ("docs/20_backup_restore_log.xlsx (template and procedure)", "Template done; measurement pending"),
        81: ("docs/15_security_design_note.md", "Done"),
        82: ("docs/00_milestone_traceability.xlsx (this file), docs/README.md", "Documentation gate done; execution gate pending"),
    }
    a, s = mp[n]
    return (a, s, "")


def build_trace():
    items = parse_backlog()
    wb = new_wb()
    rows = []
    for bid, text, owner, deliv in items:
        n = int(bid[1:])
        art, status, ev = artifact_for(n, text)
        phase = "Phase 1" if n <= 23 else ("Phase 2" if n <= 35 else ("Phase 3" if n <= 47 else ("Phase 4" if n <= 59 else "Phase 5")))
        rows.append([bid, phase, text, owner, deliv, art, status, ev])
    add_sheet(wb, "Traceability", ["backlog_id", "phase", "backlog", "owner", "deliverable", "artifact", "status", "evidence"], rows,
              widths=[9, 9, 70, 10, 38, 70, 30, 50], wrap_cols=("backlog", "deliverable", "artifact", "evidence"), status_col="status", freeze_cols=1,
              validations={"status": ["Done", "Design done; execution pending", "Template done; measurement pending", "Design done; measurement pending",
                                      "Design done; test execution pending", "Documentation gate done; execution gate pending", "In progress", "Pending"]})
    allm = sorted(k for k in MODULES if mod_stats(k)[0])
    rows = [[k, MODULES[k], MODULE_OWNER[k], *mod_stats(k)] for k in allm]
    add_sheet(wb, "Modules", ["module_key", "module", "owner", "tables", "lookup_tables"], rows, widths=[26, 56, 10, 8, 12])
    cov = [
        ["Tier 1", "Identity and accounts", "administration", "app_user, user_credential, app_role, identity_session, user_consent"],
        ["Tier 1", "Candidate profile core", "profile", "candidate_profile, candidate_education, candidate_experience, candidate_skill"],
        ["Tier 1", "Employer and client organisation", "organisation_account", "organisation, organisation_location, recruiter_organisation_mapping"],
        ["Tier 1", "Job requirement lifecycle", "jobs_applications", "job_requirement, job_position"],
        ["Tier 1", "Applications and pipeline", "jobs_applications", "job_application, job_application_status_history"],
        ["Tier 1", "Sourcing and consultant requests", "sourcing_consultant", "sourcing_request, outreach_batch, outreach_recipient"],
        ["Tier 1", "Wallet and credits", "subscriptions_payments", "wallet, wallet_ledger_entry"],
        ["Tier 1", "Audit and error logging", "administration", "audit_event, error_log"],
        ["Tier 1", "Master data", "master_data", "country, city, skill, qualification, industry, job_role"],
        ["Tier 1", "Notifications and alert preferences", "notifications_community", "notification, notification_delivery, job_alert_preference"],
        ["Tier 1", "Gig marketplace", "gig_marketplace", "gig_request, gig_worker_profile, gig_application, gig_assignment, gig_slot"],
        ["Tier 1", "Licences and seats", "licences_devices", "licence, licence_seat"],
        ["Tier 1", "Devices and sessions", "licences_devices", "device, device_session, device_slot, login_attempt"],
        ["Tier 1", "Subscriptions and plans", "subscriptions_payments", "subscription_plan, subscription, subscription_period"],
        ["Tier 1", "Payments and transaction history", "subscriptions_payments", "payment_order, invoice, refund"],
        ["Tier 1", "Account and organisation", "organisation_account", "account, account_admin, organisation_hierarchy"],
        ["Tier 1", "Preferences", "notifications_community", "notification_preference, language_preference, privacy_setting"],
        ["Tier 1", "Profile projects", "profile / gig_marketplace", "candidate_project, gig_worker_project"],
        ["Tier 1", "Profile download history", "subscriptions_payments", "profile_download"],
        ["Tier 2", "Resume versions and templates", "profile", "resume, resume_template"],
        ["Tier 2", "Community posts and reactions", "notifications_community", "community_post, community_reaction"],
        ["Tier 2", "Profile view history", "profile", "candidate_profile_view"],
        ["Tier 3", "Salary benchmarks, company ratings, trending, dashboards, feature flags", "tier3_reference", "optional module"],
        ["Extension", "Network, matching, ads, learning, talent operations", "ext_*", "product-suite scope increase; see ADR log"],
    ]
    add_sheet(wb, "Section4Coverage", ["tier", "domain", "module", "key_tables (verify in dictionary)"], cov, widths=[10, 56, 28, 80], wrap_cols=("key_tables (verify in dictionary)",))
    wb.save(os.path.join(DOCS, "00_milestone_traceability.xlsx"))
    return len(rows), cov


def missing_cov(cov):
    out = []
    for c in cov:
        for nm in re.findall(r"[a-z_]{4,}", c[3]):
            pass
    return out


def build_schema_md():
    lines = f"""# Data dictionary format (05_data_dictionary.xlsx)

`docs/05_data_dictionary.xlsx` is the single source of truth (SSOT) for the schema. It is generated from `docs/_staging/*.yaml` by `python docs/tools/build_dictionary.py` after `python docs/tools/lint_dictionary.py` passes with 0 errors. A DDL generator must read **only** this workbook (sheet names and header names below are stable; extra columns may be appended at the end but never inserted or renamed).

Engine: CockroachDB v26.2 (PostgreSQL dialect). All names are lowercase snake_case, never quoted. Booleans in sheets are `YES`/`NO`.

Current content: {len(M['tables'])} tables ({sum(1 for t in M['tables'] if t['is_lookup'])} lookups), {sum(len(t['columns']) for t in M['tables'])} columns, {len(M['views'])} views, {len(M['rules'])} business rules.

## Sheets

### Tables (one row per table)
`table, module, owner, kind, delete_policy, volume, tier, milestones, max_sensitivity, column_count, audit_columns, ttl, partitioning, retention, description`
* `kind`: entity|link|event|ledger|audit|snapshot|config|dim|fact|agg|bridge|staging|lookup.
* `delete_policy`: soft (table has `deleted_at`, `deleted_by`), hard, append_only (grant INSERT/SELECT only; no UPDATE/DELETE).
* `audit_columns`: `full` (created_at/by + updated_at/by) or `created_only`.
* `ttl`: empty or `expire_after=<interval> column=<timestamp column>` -> `WITH (ttl_expire_after = '<interval>')` when column is the implicit `crdb_internal_expiration`; when `column` is a timestamp column use `ttl_expiration_expression = '<column>'` (expire_after `0 seconds` means expression-only TTL).
* `volume`: low|medium|high|very_high (drives hash-sharding and seed sizes).
* Create tables in dependency order computed from the ForeignKeys sheet (topological sort; add FKs via `ALTER TABLE ... ADD CONSTRAINT` after all tables when cycles exist).

### Columns (one row per column, ordered by `ordinal`)
`table, ordinal, column, type, domain, nullable, nullability_justification, default, sensitivity_class, delete_policy, pii_treatment, computed_expr, is_pk, fk_ref, standard_column, description`
* `type` is a CockroachDB SQL type literal: `UUID`, `STRING(n)`, `STRING`, `BOOL`, `INT2/4/8`, `DECIMAL(p,s)`, `DATE`, `TIMESTAMPTZ`, `JSONB`, `BYTES`, `INTERVAL`, `INET`, `FLOAT4/8`, `TIME`.
* `nullable`: YES -> omit NOT NULL; NO -> `NOT NULL`. `default` is a SQL expression string (empty = none).
* `computed_expr`: empty or `<expr> STORED|VIRTUAL` -> `AS (<expr>) STORED|VIRTUAL`.
* `is_pk`: YES for `id` (`UUID NOT NULL DEFAULT gen_random_uuid()` PRIMARY KEY, named `pk_<table>`).
* `fk_ref`: `<table>.<column>` when the column participates in an FK (details in ForeignKeys).
* `domain` + `sensitivity_class` drive masking/grant generation; `pii_treatment` is free text for the security notes.
* `standard_column`: YES for auto-injected id/created_*/updated_*/deleted_*.

### Enums (lookup seed values, long format)
`lookup_table, code, sort_order, is_active, language_tag, display_name, description`
One row per (lookup, code, language). Seed each lookup table: `code`, `sort_order`, `is_active`, and `name_i18n` = JSON object {{language_tag: display_name}} built from all rows of the code. `en` is mandatory. Lookup table columns: `code STRING(64)`, `name_i18n JSONB`, `sort_order INT4`, `is_active BOOL` (see Columns sheet).

### Indexes
`table, index_name, columns, unique, hash_sharded, bucket_count, inverted, where_predicate, storing, kind, description, opclass`
* `columns`: comma-separated; entries may carry ` DESC`. Hash-sharded: `CREATE INDEX name ON t (cols) USING HASH WITH (bucket_count = n)`. Inverted: JSONB columns use `CREATE INVERTED INDEX` (plain, `opclass` empty); STRING columns are trigram indexes `CREATE INDEX ... USING GIN (col gin_trgm_ops)` and always carry `opclass = gin_trgm_ops`. `opclass` is the last column of the sheet. `where_predicate` -> partial index. `storing` -> `STORING (...)`.
* `unique = YES` entries are `CREATE UNIQUE INDEX uq_...` (partial unique rules live here).

### Constraints
`table, constraint_name, constraint_type, columns, expression, description`
`PRIMARY KEY` (always `id`), `UNIQUE` (non-partial; `columns`), `CHECK` (`expression`).

### ForeignKeys
`table, fk_name, columns, ref_table, ref_columns, on_delete, on_update, ref_module, cross_module, index_exempt, description`
Multi-column FKs use comma-separated `columns`/`ref_columns` in matching order and rely on a UNIQUE constraint on the referenced columns. Use `MATCH SIMPLE` (default) unless the description says otherwise.

### Views
`view_name, kind, module, tables, purpose, security, sql_sketch` -- `sql_sketch` is design intent (parameter names, predicates), to be completed by the DDL author; `kind` view|materialized.

### Partitioning
`table, module, volume, strategy, key_or_columns, naming_pattern, notes` -- hash-sharded indexes, TTL and any zone-config/partition design per table.

## Related files
* `docs/06_business_rules.xlsx`: rule -> enforcement mechanism (must be implemented by the DDL: CHECK/UNIQUE/FK/chain/slot/privileges/TTL).
* `docs/13_rbac_matrix.xlsx`: grants per role.
* `docs/_staging/FORMAT_AND_DECISIONS.md`: binding design decisions D-01..D-20.
"""
    open(os.path.join(DOCS, "05_data_dictionary_schema.md"), "w", encoding="utf-8").write(lines)


if __name__ == "__main__":
    nr = build_rules()
    n, cov = build_trace()
    build_schema_md()
    print(f"rules={len(M['rules'])} backlog_items={n}")

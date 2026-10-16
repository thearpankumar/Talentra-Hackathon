#!/usr/bin/env python
"""Build docs/08_rule_compliance.xlsx: docket Section 5 compliance table, atomic rule checks with live violation counts,
deviation register, open-findings register and the reserved-keyword result."""
import os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import design_common as dc
import rule_checks as rc
from xl import new_wb, add_sheet

S = dc.stats()
RES, AVOID = dc.reserved_lists()
CR = {r["id"]: r for r in rc.run_all()}


def v(i):
    return CR[i]["violations"]


def result(r):
    if r["severity"] == "info":
        return "Info"
    if r["violations"] == 0:
        return "Complies"
    return {"deviation": "Deviation (recorded)", "finding": "Open finding", "warn": "Warning", "rule": "Open finding"}[r["severity"]]


# --------------------------------------------------------------------------------------------- deviations (computed text)
DEVIATIONS = [
    ["DEV-01", "Docket engine line / B1 (PostgreSQL or MySQL)",
     "Engine is CockroachDB v26.2 using the PostgreSQL dialect and wire protocol, not stock PostgreSQL or MySQL.",
     "Horizontal write scale for the 10M profile / 100M application target, SERIALIZABLE by default, hash-sharded indexes, online schema change (ADR-001).",
     "Licence (multi-node cluster needs a key; throttled to 5 concurrent open txns without one), missing features (exclusion constraints, MERGE, CREATE DOMAIN in 26.2, column privileges, uuid_v7), panel may require stock PostgreSQL.",
     "Patterns P1-P6 replace missing features; portability list in 07 section 1; DDL kept in PG dialect; licence key applied at cluster init; Enterprise Free eligibility checked.", "Justified"],
    ["DEV-02", "Rule 2 (every foreign key enforced) / Rule 4 audit columns",
     f"created_by, updated_by, deleted_by hold app_user.id but are NOT foreign keys on any of {S['tables']} tables (D-04).",
     "Avoids an FK check and lock per write against the largest identity range, removes the app_user bootstrap cycle, keeps ledger/audit/notification inserts cheap (ADR-014).",
     "Orphan ids possible; analysts may trust a dangling id.", "Nightly orphan-check job; erasure anonymises app_user in place so ids stay valid; FKs that matter (actor_app_user_id, blocker, consent subject) are enforced.", "Justified"],
    ["DEV-03", "Rule 4 (every business table has created_at, created_by, updated_at, updated_by)",
     f"{S['created_only']} tables have created_* only: {S['delete']['append_only']} append_only tables plus {len(S['created_only_nonappend'])} others (17 agg_* aggregates that are upserted by grain and gig_slot which is insert/delete only).",
     "An append-only row is never updated, so updated_* would be permanently NULL or equal to created_*; aggregates carry last_refreshed_at/load run id instead; gig_slot rows are only inserted or deleted with their assignment.",
     "A reader scanning for 'last modified' must know the exception; aggregates have no updated_by.", "dictlib audit mode `created_only` is explicit per table; lint requires audit created_only for append_only; agg_* tables record load_dim_etl_run_id and last_refreshed_at.", "Justified"],
    ["DEV-04", "Rule 4 / Rule 10 (audit, download and view history append-only)",
     f"{S['ttl_append_only']} append_only tables (incl. audit_event, candidate_profile_view, candidate_activity_log, login_attempt) have row-level TTL: the database's own TTL job deletes expired rows.",
     "Retention/erasure rules (docket Rule 4 retention, GDPR/DPDP storage limitation) require physical expiry; application roles still have INSERT/SELECT only; audit_event TTL is gated by checkpoint sink confirmation and legal hold (BR-ADM-10).",
     "TTL jobs run as table owner so REVOKE does not stop them; an expired audit row is gone (mitigated by immutable sink).", "Changefeed to object-lock storage before TTL; ttl_pause on legal hold; rollups survive (notification_delivery_daily_summary).", "Justified"],
    ["DEV-05", "Rule 2 (every foreign key enforced)",
     f"{S['external_ref_cols'] - 1} analytics columns plus candidate_activity_log.subject_row_ref ({S['external_ref_cols']} columns flagged external_ref) copy OLTP ids without FK. Fact-to-dimension links are enforced.",
     "Analytics must survive OLTP deletes/erasure/partitioning and loads must not take FK checks against hot OLTP ranges (ADR-021).", "Orphan fact rows after OLTP corrections.", "Per-run reconciliation in agg_data_quality_result (blocking publish gate).", "Justified"],
    ["DEV-06", "Rule 1 (index name pattern) / portability",
     f"{S['names_gt63']} index/constraint/FK names exceed 63 characters (longest {S['max_name'][0]}); tables <= {S['max_table_name'][0]}, columns <= {S['max_col_name'][0]} characters are fine.",
     "Names spell out all key columns so constraint errors and plans are self-describing. CockroachDB accepts identifiers up to 128 bytes (max_identifier_length, not enforced).", "Stock PostgreSQL silently truncates identifiers at 63 bytes and may collide.", "Generate a rename map (hash suffix) before any PostgreSQL port; check names < 128 on CockroachDB.", "Justified"],
    ["DEV-07", "Rule 6 (Sensitive requires encryption, tokenisation or masking)",
     "candidate_diversity_declaration.has_disability and .is_veteran are classed Sensitive but stored as plain BOOL (special-category flags).",
     "Booleans must be filterable for consented inclusive-hiring programmes; the free-text sibling disability_description_encrypted IS field-encrypted (AES-GCM, KMS data key per row); the column notes restrict the flags to recruiters in inclusive-hiring programmes with explicit consent (user_consent_id FK) and forbid use in ranking (ADR-036).", "Storage-layer encryption only protects the two flags at rest.", "Consent FK, never used in ranking (APP_LAYER rule), reads through views; consider storing both flags inside an encrypted payload if the panel requires field-level protection.", "Justified"],
    ["DEV-08", "Rule 2 (key type applied consistently)",
     "dim_date and dim_time_of_day ids are deterministic UUIDv5 values instead of random UUIDv4 (still UUID type).",
     "Loaders resolve date/minute keys without a lookup; tables are tiny (days/minutes) so the hotspot concern does not apply (ADR-020).", "Non-random keys on a hot table would hotspot (not the case).", "Restricted to two pre-generated dimensions.", "Justified"],
    ["DEV-09", "Rule 2 (every foreign key enforced)",
     f"{S['ref_cols']} `*_ref` columns are intentionally not FKs: polymorphic or log references (audit_event.entity_ref, notification source_object_ref, vendor/external refs) paired with a type code (ADR-040).",
     "Targets are several tables or must outlive the referenced row; avoids an FK check per audit/notification write.", "Dangling references.", "Nightly orphan-check; naming convention keeps `*_id` strictly FK (lint error otherwise).", "Justified"],
    ["DEV-10", "Rule 10 convention 'facts are append-only'",
     "Four accumulating-snapshot facts (fact_application_pipeline, fact_gig_assignment, fact_support_ticket, fact_outreach_recipient) are updated in place until closed.",
     "An accumulating snapshot is by definition updated as milestones arrive; the same information is also kept append-only in the event fact (analytics notes).", "Restatement history of the snapshot is lost.", "Event facts kept append-only; reconciliation in agg_data_quality_result.", "Justified"],
    ["DEV-11", "Rule 3 (FK to a lookup uses enum_ref and a direct lookup FK)",
     "job_application_status_history.from_application_status_id (enum_ref) is enforced through the composite FK to application_status_transition rather than directly to application_status.",
     "The composite FK proves the transition is allowed (D-12 status chain).", "None beyond transitive enforcement (application_status_transition itself FKs the lookup).", "Documented in jobs notes (Key decision 1).", "Justified"],
    ["DEV-12", "Rule 1 no abbreviations that are not defined",
     "Abbreviations used in names (id, ip, url, kpi, mrr, ...) are defined in 04_design_conventions.xlsx (Abbreviations sheet) instead of in each module design; the analytics module also defines its own list.",
     "One central glossary is easier to review.", "A module read in isolation lacks the glossary.", "Abbreviations sheet is computed from the model (every <= 4 letter token is either an English word or defined).", "Justified"],
]

FINDINGS = [
    ["F-01", "TTL vs inbound foreign keys", ("RESOLVED in staging: " if not v('A09') else "OPEN: ") + f"{v('A09')} TTL tables are referenced by an FK with ON DELETE RESTRICT from another table (rule check A09). The 12 inbound FKs found earlier (audit_event, identity_session, device_session, login_attempt, payment_event, ad_performance_hourly parents) are now ON DELETE SET NULL (nullable, evidence rows survive) or CASCADE (session_activity_sample); decisions are in the notes of administration, licences_devices, subscriptions_payments, tier3_reference and ext_ads."
     + (" Remaining: " + "; ".join(CR["A09"]["sample"]) if v('A09') else ""), "High", "Keep rule check A09 at 0; any new TTL table must not be the target of a RESTRICT/NO ACTION FK."],
    ["F-02", "Duplicate view name", ("RESOLVED in staging: " if not v('N19') else "OPEN: ") + f"{v('N19')} view names are defined twice. vw_recruiter_job_requirement now has a single definition (organisation_account) with an explicit viewer argument (fn_recruiter_job_requirement); vw_candidate_visible_to_user documents that its viewer filter is mandatory.", "Medium", "Keep rule check N19 at 0."],
    ["F-03", "Inverted index opclass", ("RESOLVED in staging: " if not v('P03') else "OPEN: ") + f"{v('P03')} inverted indexes on STRING columns lack gin_trgm_ops. Every trigram index now carries `opclass: gin_trgm_ops` (last column of the Indexes sheet); JSONB inverted indexes have none; the lint enforces both."
     + (" Remaining: " + "; ".join(CR["P03"]["sample"]) if v('P03') else ""), "Medium", "Keep rule check P03 at 0."],
    ["F-04", "Classification consistency", ("RESOLVED in staging: " if not v('S05') else "OPEN: ") + f"{v('S05')} token-like columns are not classed Sensitive. article_view.visitor_token, reference_check_request.access_token_hash and community_share.share_token are now Sensitive with hash/token handling stated in pii."
     + (" Remaining: " + "; ".join(CR["S05"]["sample"]) if v('S05') else ""), "Low", "Keep rule check S05 at 0."],
    ["F-05", "UI label key guard", f"{v('D16')} ui_label_key columns (" + "; ".join(CR["D16"]["sample"][:3]) + " ...) hold keys with no FK or CHECK tying them to ui_label.label_key.", "Low", "Add a FK-like job check (nightly) or reference ui_label_id instead of the key string."],
    ["F-06", "Language of user-entered text", ("RESOLVED in staging: " if not v('L05') else "OPEN: ") + f"{v('L05')} tables with an unbounded text column have no language_id. The 15 tables found earlier now carry a nullable language_id (FK language, NULL = language unknown)."
     + (" Remaining: " + ", ".join(CR["L05"]["sample"][:4]) if v('L05') else ""), "Medium", "Keep rule check L05 at 0."],
    ["F-07", "Append-only privilege coverage", f"{v('A10')} of {CR['A10']['population']} append_only tables are not named in any PRIVILEGE/CHAIN_FK/TTL/TXN_LOCK business rule; INSERT/SELECT-only grants for them follow only from delete_policy: append_only.", "Low", "Generate GRANT/REVOKE statements for all append_only tables from the model at build time and test them in B79."],
    ["F-08", "Role name inconsistency", "Administration notes use analytics_ro; analytics notes use analytics_reader and analytics_etl.", "Low", "Pick one set of DB role names and update both modules."],
    ["F-09", "ext_ads notes block", "RESOLVED in staging: the ext_ads.yaml `notes` block holds written design notes (entity rationale, money and billing, retention and privacy, competitor coverage, extension points) instead of a generator script.", "Medium", "None."],
    ["F-10", "Unverified CockroachDB operators in CHECKs", f"{CR['P07']['population']} CHECKs use num_nonnulls, regex `~` or jsonb `?` (crdb_capabilities row 5: not covered by the fetched CHECK page; ext_talent_ops avoids num_nonnulls for that reason).", "Medium", "Smoke-test each operator on the pinned cockroachdb/cockroach:v26.2.x image in B65; fall back to CASE sums."],
]


def sheet_compliance():
    groups = [
        ("1: Naming conventions", "Partial",
         f"Tables/columns {CR['N01']['population']}/{CR['N02']['population']} snake_case with 0 violations; PK id on all {S['tables']} tables; {CR['N05']['population']} FK columns follow <table>_id; zero reserved words. "
         f"Deviation DEV-06: {S['names_gt63']} index/constraint names exceed 63 characters (CockroachDB allows 128). View names are unique (former finding F-02 resolved). Index names use the full column list, not only 'idx_table_column'."),
        ("2: Keys and constraints", "Partial",
         f"UUID surrogate PK everywhere; natural keys UNIQUE; {S['fks']} FKs with stated delete/update rules; {S['checks']} CHECKs; concurrency/licence limits by constraints + FOR UPDATE ({S['rules_by_type']['TXN_LOCK']} TXN_LOCK, {S['rules_by_type']['SLOT_TABLE']} SLOT_TABLE, {S['rules_by_type']['CHAIN_FK']} CHAIN_FK rules); device limit via slots (D-13). "
         "Deviations DEV-02 (created_by/updated_by/deleted_by not FK), DEV-05 (analytics source ids), DEV-09 (*_ref columns), DEV-01 (engine). Former finding F-01 (TTL vs RESTRICT children) resolved: child FKs are SET NULL/CASCADE."),
        ("3: Standard domains", "Yes",
         f"All {S['columns']} columns map to a standard domain with the allowed type (0 mismatches); money DECIMAL scale>=2 with currency; lat/long with range CHECK and geo_source_id; booleans NOT NULL with default; "
         f"{S['long_text_cols']} unbounded columns all justified. Notes: DEV-07 (two Sensitive boolean flags), DEV-08 (UUIDv5 date keys), DEV-11; open finding F-05 (label key guard)."),
        ("4: Audit and lifecycle", "Partial",
         f"created_* on {S['tables']} tables, updated_* on {S['audit_mode']['full']}, soft-delete columns on {S['delete']['soft']}; retention text on every table; {S['ttl_tables']} TTL tables. Deviations DEV-03 (no updated_* on {S['created_only']} tables), DEV-04 (TTL deletes on append_only tables). "
         "Open finding F-07 (grants generated from delete_policy); former finding F-01 (TTL vs RESTRICT children) resolved."),
        ("5: Nullability and unknown values", "Yes",
         f"All {S['nullable']} nullable columns carry null_why; no sentinel defaults ({CR['Z02']['population']} defaults checked); salary disclosure lookups for candidate_profile and job_requirement; analytics placeholder members distinguish unknown from not applicable."),
        ("6: Sensitive data classification", "Partial",
         f"All {S['columns']} columns classed (Public {S['sens']['Public']}, Internal {S['sens']['Internal']}, Personal {S['sens']['Personal']}, Sensitive {S['sens']['Sensitive']}); every Personal/Sensitive column has a pii note; no clear-text national id/card/bank column. "
         "Deviation DEV-07; former finding F-04 (token-like columns classed Sensitive) resolved."),
        ("7: Multilingual data", "Partial",
         f"UTF-8 everywhere; {S['lookups']} lookups with name_i18n (en mandatory); ui_label/ui_label_translation unique per key+language; fallback chain terminates by CHECKs; user content stored once in its language. "
         f"Former finding F-06 resolved: {v('L05')} tables with unbounded user text lack a language column. Hindi coverage {S['lookup_values_hi']} of {S['lookup_values']} lookup values."),
        ("8: Naming of physical objects", "Yes",
         f"Views vw_* ({S['views']}) and mv_* ({S['mviews']}), analytics dim_/fact_/agg_/bridge_ ({S['per_module']['analytics']['tables']} tables, lint-enforced); partition and zone names reserved as <table>_p_<key>/<table>_zone (no physical partitions defined, ADR-017)."),
        ("Reserved keyword check", "Yes",
         f"{len(RES)} reserved words (PostgreSQL + CockroachDB + MySQL 8 union) and {len(AVOID)} risky non-reserved words checked against {len({t['name'] for t in rc.T}) + len({c['name'] for t in rc.T for c in t['columns']}) + len({x['name'] for x in rc.M['views']})} distinct table, column and view names: 0 hits. "
         "The CockroachDB grammar keyword list itself is not enumerated in the research (unverified), so the union list plus the docs' avoid list is used."),
        ("9: Licensing, devices and sessions", "Yes",
         f"licence.max_users / max_devices_per_user with positive CHECKs; slots + FOR UPDATE enforce the limit at login; sessions store device, start, last activity, end; ended_at IS NULL counts; breach writes audit_event + device_limit_violation; revocation tables reference audit_event (RuleChecks G01-G05)."),
        ("10: Money, subscriptions and history", "Yes",
         "wallet_ledger_entry append-only with UNIQUE reversal reference, sequence and idempotency; subscription_period non-overlap by CHAIN_FK constraints (engine has no exclusion constraints; constraint-based as the rule allows); profile_download and candidate_profile_view append-only with actor, subject, time and credit deduction (RuleChecks M01-M06)."),
    ]
    rows = []
    for g, ok, text in groups:
        dev = [d for d in DEVIATIONS if (g.split(":")[0].strip() in ("1",) and d[0] in ("DEV-06",)) or False]
        rows.append([g, ok, text])
    # attach deviation text
    dmap = {
        "1: Naming conventions": "DEV-06 (names > 63 chars, portability only; CockroachDB allows 128). Reason: self-describing names; no impact on CockroachDB.",
        "2: Keys and constraints": "DEV-01 engine; DEV-02 audit columns without FK (avoid per-write FK checks); DEV-05 analytics source ids; DEV-09 *_ref polymorphic references. Risks and mitigations in 'Deviations'.",
        "3: Standard domains": "DEV-07 two Sensitive booleans; DEV-08 UUIDv5 ids on two dimensions; DEV-11 transitive lookup FK. Reasons in 'Deviations'.",
        "4: Audit and lifecycle": "DEV-03 updated_* omitted where rows are immutable or upserted by grain; DEV-04 TTL deletion on append-only tables (retention law, gated by sink confirmation).",
        "5: Nullability and unknown values": "None.",
        "6: Sensitive data classification": "DEV-07 (plain boolean special-category flags).",
        "7: Multilingual data": "Finding F-06 closed: free-text tables carry a nullable language_id (NULL = unknown).",
        "8: Naming of physical objects": "None (partition names reserved, none defined).",
        "Reserved keyword check": "None (0 hits).",
        "9: Licensing, devices and sessions": "None.",
        "10: Money, subscriptions and history": "None (non-overlap by constraint pattern, not exclusion constraint, because CockroachDB lacks it; DEV-10 for four accumulating facts in analytics).",
    }
    return ["Rule group", "Design complies", "Evidence (computed)", "Deviation with reason"], [[g, ok, text, dmap[g]] for g, ok, text in groups]


def sheet_rulechecks():
    rows = []
    for r in rc.run_all():
        rows.append([r["id"], f"Rule group {r['group']}", r["rule"], r["how"], r["lint"], r["population"], r["violations"], result(r), "; ".join(r["sample"][:3]) if r["violations"] else ""])
    return ["id", "Rule group", "Atomic rule", "How verified", "Lint rule (message in lint_dictionary.py)", "Population checked", "Live violations", "Result", "Sample (first 3)"], rows


def sheet_dev():
    return ["id", "Rule", "Deviation", "Reason", "Risk", "Mitigation", "Status"], DEVIATIONS


def sheet_findings():
    return ["id", "Topic", "Finding (computed)", "Severity", "Proposed fix (staging owner)"], FINDINGS


def sheet_reserved():
    names = collections.defaultdict(lambda: collections.Counter())
    for t in rc.T:
        names[t["name"]]["table"] += 1
        for c in t["columns"]:
            names[c["name"]]["column"] += 1
    for x in rc.M["views"]:
        names[x["name"]]["view"] += 1
    rows = []
    for n in sorted(names):
        kinds = names[n]
        role = "/".join(k for k in ("table", "column", "view") if kinds[k])
        uses = sum(kinds.values())
        rows.append([n, role, uses, "Yes" if n in RES else "No", "Yes" if n in AVOID else "No", "Yes" if n in dc.CRDB_DOC_AVOID else "No",
                     "Fail" if n in RES else ("Warn" if (n in AVOID or n in dc.CRDB_DOC_AVOID) else "Pass")])
    return ["Identifier", "Used as", "Occurrences", "In reserved list (PG+CRDB+MySQL)", "In avoid list (risky)", "In CockroachDB docs avoid list", "Result"], rows


def main():
    code, line = dc.run_lint()
    wb = new_wb()
    hdr, rows = sheet_compliance()
    add_sheet(wb, "Compliance", hdr, rows, widths=[34, 16, 110, 80], wrap_cols=["Evidence (computed)", "Deviation with reason", "Rule group"],
              validations={"Design complies": ["Yes", "Partial", "No"]}, status_col="Design complies")
    ws = wb["Compliance"]
    for r in range(2, ws.max_row + 1):
        ws.row_dimensions[r].height = 95
    hdr, rows = sheet_rulechecks()
    add_sheet(wb, "RuleChecks", hdr, rows, widths=[8, 14, 60, 60, 50, 14, 12, 20, 70], wrap_cols=["Atomic rule", "How verified", "Lint rule (message in lint_dictionary.py)", "Sample (first 3)"],
              status_col="Result")
    hdr, rows = sheet_dev()
    add_sheet(wb, "Deviations", hdr, rows, widths=[9, 38, 70, 70, 50, 60, 12], wrap_cols=hdr, validations={"Status": ["Justified", "Open gap"]})
    ws = wb["Deviations"]
    for r in range(2, ws.max_row + 1):
        ws.row_dimensions[r].height = 120
    hdr, rows = sheet_findings()
    add_sheet(wb, "Findings", hdr, rows, widths=[8, 30, 100, 10, 80], wrap_cols=hdr, validations={"Severity": ["High", "Medium", "Low"]})
    ws = wb["Findings"]
    for r in range(2, ws.max_row + 1):
        ws.row_dimensions[r].height = 90
    hdr, rows = sheet_reserved()
    add_sheet(wb, "ReservedKeywords", hdr, rows, widths=[46, 18, 12, 18, 16, 18, 10])
    allr = rc.run_all()
    summ = [
        ["lint_dictionary.py exit code", code], ["lint summary line", line[line.index("tables="):] if "tables=" in line else line],
        ["atomic checks", len(allr)], ["checks with 0 violations", sum(1 for r in allr if r["violations"] == 0)],
        ["recorded deviations (checks)", sum(1 for r in allr if r["violations"] and r["severity"] == "deviation")],
        ["open findings (checks)", sum(1 for r in allr if r["violations"] and r["severity"] in ("finding", "rule"))],
        ["generated by", "python docs/tools/build_compliance.py (uses rule_checks.py)"],
        ["model", f"{S['tables']} tables, {S['columns']} columns, {S['rules']} business rules"],
    ]
    add_sheet(wb, "Summary", ["item", "value"], summ, widths=[40, 120], wrap_cols=["value"])
    wb.save(os.path.join(dc.DOCS, "08_rule_compliance.xlsx"))
    print("written; lint:", code, line[-120:], "| checks", len(allr))


if __name__ == "__main__":
    main()

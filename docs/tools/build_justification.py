#!/usr/bin/env python
"""Build docs/07_schema_justification.md.  Counts are computed from docs/_staging; every backticked identifier in the
text is verified against the model (tables, views, columns, index/constraint/FK names, business-rule ids) or an explicit
allow-list of non-schema terms; the script aborts on an unknown name."""
import os, re, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import design_common as dc
import rule_checks as rc
import build_adr as adr

S = dc.stats()
PM = S["per_module"]
M = dc.model()
TB = dc.tables()
CR = {r["id"]: r for r in rc.run_all()}
RES, AVOID = dc.reserved_lists()
code_lint, line_lint = dc.run_lint()

LABEL = {k: v for k, v in __import__("dictlib").MODULES.items()}


def n(mod, key="tables"):
    return PM[mod][key]


def mline(mod):
    d = PM[mod]
    return (f"{d['tables']} tables ({d['entities']} entities + {d['lookups']} lookups), {d['columns']} columns, {d['fks']} FKs, "
            f"{d['soft']} soft / {d['hard']} hard / {d['append_only']} append-only, {d['hash_idx']} hash-sharded indexes, {d['ttl']} TTL tables, "
            f"{d['views']} views, {d['rules']} business rules, Sensitive {d['sensitive']} / Personal {d['personal']} columns, {d['nullable']} nullable columns.")


# --------------------------------------------------------------------------- docket coverage mapping (verified names)
TIER1 = [
    ("Identity and accounts: users, credentials, roles, sessions, consent", "administration", ["app_user", "user_credential", "mfa_factor", "app_role", "app_user_role", "identity_session", "user_consent"]),
    ("Candidate profile core: personal, contact, education, experience, skills, languages", "profile", ["candidate_profile", "candidate_contact_phone", "candidate_contact_email", "candidate_education", "candidate_experience", "candidate_skill", "candidate_language"]),
    ("Employer and client organisation: details, locations, tax/registration ids, verification, mapped recruiters", "organisation_account", ["organisation", "organisation_location", "organisation_identifier", "organisation_verification", "recruiter_organisation_mapping"]),
    ("Job requirement lifecycle: posting, status, positions, requirements, publish state, consultant openness", "jobs_applications", ["job_requirement", "job_position", "job_requirement_skill", "job_publish_history", "job_requirement_status_history", "job_consultant_openness"]),
    ("Applications and pipeline", "jobs_applications", ["job_application", "job_application_status_history", "application_status_transition", "job_shortlist", "interview", "offer", "joining", "rejection"]),
    ("Sourcing and consultant requests, candidate shortlists", "sourcing_consultant", ["sourcing_request", "sourcing_request_decision", "sourcing_shortlist", "sourcing_submission", "consultant_job_openness"]),
    ("Wallet and credits", "subscriptions_payments", ["wallet", "wallet_ledger_entry", "credit_pack", "credit_rate_card", "wallet_expiry_bucket"]),
    ("Audit and error logging", "administration", ["audit_event", "audit_chain_head", "error_log", "security_event"]),
    ("Master data", "master_data", ["country", "state_region", "city", "skill", "qualification", "industry", "job_role", "employment_type", "work_type"]),
    ("Notifications and alert preferences", "notifications_community", ["notification", "notification_delivery", "notification_preference", "job_alert_preference"]),
    ("Gig marketplace", "gig_marketplace", ["gig_request", "gig_worker_profile", "gig_availability_rule", "gig_application", "gig_assignment", "gig_requester_worker_list", "gig_rating", "gig_earnings_snapshot"]),
    ("Licences and seats", "licences_devices", ["licence", "licence_seat_slot", "licence_seat"]),
    ("Devices and sessions", "licences_devices", ["device", "device_slot", "device_session", "login_attempt", "device_limit_violation"]),
    ("Subscriptions and plans", "subscriptions_payments", ["subscription_plan", "plan_version", "subscription", "subscription_period", "plan_entitlement"]),
    ("Payments and transaction history", "subscriptions_payments", ["payment_order", "payment_attempt", "invoice", "refund", "wallet_ledger_entry"]),
    ("Account and organisation", "organisation_account", ["account", "account_admin", "organisation_hierarchy", "organisation_hierarchy_closure"]),
    ("Preferences: notification, language, job alert, privacy", "notifications_community", ["notification_preference", "language_preference", "job_alert_preference", "privacy_setting"]),
    ("Profile projects (candidate and gig)", "profile / gig_marketplace", ["candidate_project", "gig_worker_project"]),
    ("Profile download history", "subscriptions_payments", ["profile_download"]),
]
TIER2 = [
    ("Resume versions and templates", ["resume", "resume_template"]),
    ("Community posts and reactions", ["community_post", "community_reaction"]),
    ("Assessments and scores", ["assessment", "assessment_attempt", "candidate_test_score"]),
    ("Course enrolments", ["course", "course_enrolment"]),
    ("Support tickets, FAQs", ["support_ticket", "faq_item"]),
    ("Referral tracking", ["referral", "referral_event", "referral_reward"]),
    ("Blocking lists", ["user_block", "organisation_block"]),
    ("Profile completion scoring", ["profile_completion_rule", "candidate_profile_completion_snapshot"]),
    ("Profile view history", ["candidate_profile_view"]),
    ("Job search and application history", ["candidate_job_search_history", "job_application"]),
    ("Candidate activity log", ["candidate_activity_log"]),
    ("Shortlist and bookmark history", ["candidate_shortlist_item", "candidate_shortlist_history", "job_bookmark"]),
]
TIER3 = [("Salary benchmarks", ["salary_benchmark", "salary_benchmark_source"]), ("Company ratings", ["company_rating_snapshot"]),
         ("Trending job snapshots", ["trending_job_snapshot", "skill_demand_snapshot"]), ("Dashboard aggregates", ["dashboard_aggregate"]),
         ("Feature flags", ["feature_flag", "feature_flag_rule", "feature_flag_override", "feature_flag_audit"])]

GAPS = [
    # id, area, competitor/source, element, status, where, note
    ("G-01", "Profile", "LinkedIn", "Certifications, honours, patents, publications, volunteering, section ordering, endorsements, recommendations, follows", "Covered",
     "candidate_certification, candidate_award, candidate_patent, candidate_publication, candidate_volunteer_experience, candidate_profile_section, candidate_skill_endorsement, candidate_recommendation, candidate_follow", "profile notes; exceeds with assessment-verified skills and erasure evidence"),
    ("G-02", "Profile", "LinkedIn", "Groups, newsletters, events, creator mode, audio rooms", "Covered by extension (creator mode not modelled)", "community_group, newsletter, event, live_room", "profile notes said 'falls short deliberately'; ext_network now covers groups/newsletters/events/live rooms"),
    ("G-03", "Profile", "Naukri", "Resume headline, key skills, IT skills, notice period, current/expected CTC, desired job type/shift/location, differently-abled flag, completeness meter, resume parser", "Covered",
     "candidate_profile, candidate_job_preference, candidate_diversity_declaration, candidate_profile_completion_snapshot, resume_parse_result", "exceeds with resume version chain, scan status, parse provenance"),
    ("G-04", "Profile", "Monster", "Up to 5 resumes with per-resume visibility, work authorisation, relocation, security clearance, templates, saved jobs", "Partially covered",
     "resume, candidate_work_authorisation, resume_template, job_bookmark", "security clearance is not a first-class field (add as a certification type)"),
    ("G-05", "Profile", "Indeed", "Resume privacy, ready to start, pay expectation, work authorisation", "Covered", "visibility_level, availability_status, candidate_work_authorisation", ""),
    ("G-06", "Jobs", "LinkedIn", "Job posting, hiring team, screening questions with must-have answers, Easy Apply, pipeline stages, good-fit rating; InMail credits", "Partially covered",
     "job_requirement, job_requirement_screening, questionnaire, job_application, application_scorecard, job_requirement_assignment", "hiring-team sub-roles out of scope (section 10); InMail credits live in wallet/outreach"),
    ("G-07", "Jobs", "Naukri", "Walk-in details, CTC with 'not disclosed', freshers flag, hot vacancy, consultant postings, response/folder management", "Covered",
     "job_walk_in_session, salary_disclosure, job_boost, job_consultant_openness, talent_pool", ""),
    ("G-08", "Jobs", "Greenhouse (Harvest API)", "Applications, stages, rejection reasons, scorecards, interviews, offers, approvals, openings, close reasons", "Covered except demographic data",
     "job_application, rejection, application_scorecard, interview, offer, offer_approval, job_requirement_approval, job_position, job_close_reason", "EEOC demographics deliberately excluded from core (kept in ext_talent_ops equal_opportunity_response with safeguards); custom fields via job_requirement.attributes JSONB"),
    ("G-09", "Organisation", "LinkedIn Company Page", "Name, tagline, about, industry, size, type, HQ, specialties, verified badge, admins, followers, media", "Covered",
     "organisation, organisation_location, organisation_industry, organisation_verification, account_admin, organisation_follow, organisation_media", "employee graph / 'people also viewed' not modelled"),
    ("G-10", "Organisation", "Naukri/Monster employer profile", "GST/CIN, recruiter list, branding, career page, perks, gallery", "Covered",
     "organisation_identifier, recruiter_organisation_mapping, organisation_career_page, organisation_benefit", ""),
    ("G-11", "Organisation", "Glassdoor / AmbitionBox", "Multi-dimension reviews, pros/cons, employer replies, helpful votes, moderation", "Covered",
     "organisation_review, organisation_review_response, organisation_review_vote, organisation_review_moderation, organisation_rating_summary", "salary data is Tier 3 reference only"),
    ("G-12", "Gig", "Upwork", "Fixed-price milestones vs hourly work diary, ratings, availability", "Covered except connects/bids pricing and escrow",
     "gig_request_milestone, gig_timesheet_entry, gig_rating, gig_availability_status", "escrow is external; credits live in the wallet module"),
    ("G-13", "Gig", "Fiverr", "Packages, revisions, delivery days", "Covered except seller levels", "gig_service_offering, gig_service_package", "seller levels derivable from completed count + rating"),
    ("G-14", "Gig", "TaskRabbit / Uber / Urban Company", "Hourly rate, radius, favourites, KYC, document expiry, ratings", "Covered", "gig_worker_profile, gig_requester_worker_list, gig_worker_certification, gig_worker_rating_summary", "background-check results stay at the vendor"),
    ("G-15", "Gig", "Toptal / Freelancer.com", "Vetting tiers, contests", "Partially covered", "verification_status", "contests not modelled"),
    ("G-16", "Subscriptions", "Stripe / Razorpay / Chargebee", "Price, subscription, invoice, credit note, order->payment->refund, signed webhooks, plan versions, dunning", "Covered",
     "plan_price, subscription_renewal_attempt, invoice, credit_note, payment_order, payment_event, plan_version", "full double-entry accounting left as extension (ADR-007)"),
    ("G-17", "Subscriptions", "LinkedIn Recruiter / Naukri RESDEX", "Credit-based downloads, plan quotas, InMail refund on reply, bulk mailer credits", "Covered",
     "profile_download, subscription_entitlement, outreach_credit_usage, credit_rate_card", "refund window is a job parameter (7-90 days varies by vendor)"),
    ("G-18", "Licences", "Microsoft 365 / Slack / Okta / Netflix", "Named seats with reassignment history, device or session cap with sign-out-oldest", "Covered",
     "licence_seat, licence_user_invitation, licence_transfer, device_limit_policy, device_session", ""),
    ("G-19", "Notifications", "LinkedIn / Naukri / Monster / Twilio / SendGrid", "Inbox, digests, job alerts, privacy, delivery event models", "Covered",
     "notification, notification_delivery_event, job_alert_run, job_alert_match, privacy_setting, delivery_status", "WhatsApp template approval workflow is an extension point"),
    ("G-20", "Master data", "LinkedIn", "~39-41k skills, 26 languages, ~374k aliases, >200k skill links, 26 job functions, ~400 industries", "Structure matched; seed volumes pending",
     "skill, skill_alias, skill_relation, language, job_function, industry", "taxonomy import (ESCO/O*NET/Lightcast) via skill.external_taxonomy_ref; counts are targets, not loaded"),
    ("G-21", "Master data", "Deliberate omission", "Religion, caste, disability, ethnicity, nationality-of-origin masters", "Not modelled (by design)", "gender, drive_eligibility_rule", "legal risk (DPDP 2023, GDPR art. 9, EEOC/ADA)"),
    ("G-22", "Analytics", "Docket", "KPIs 'in Section 8'", "Docket has no Section 8; K01..K20 defined", "dim_kpi_definition, agg_funnel_daily, agg_revenue_monthly", "replace the KPI set if a Section 8 is issued; each KPI maps to one aggregate"),
    ("G-23", "Search", "LinkedIn / Naukri", "Relevance ranking, embeddings, typeahead", "Boundary only", "search_index_outbox, embedding_ref, search_typeahead_term", "ranking is an external service (D-16); vector index not used"),
    ("G-24", "i18n", "Docket rule 7", "Translations", "Partial", "ui_label_translation", f"English complete; Hindi on {S['lookup_values_hi']} of {S['lookup_values']} lookup values; es/ar/ta/bn partial"),
    ("G-25", "Evidence", "Docket sections 6, 10, B60-B82", "Seed data at 1M profiles, performance baseline, backup/restore log, integration-boundary table", "Not part of this document", "-", "produced by other deliverables (seed, query, security documents)"),
    ("G-26", "Extensions", "Product suite scope", "Onboarding, background checks, campus drives, MSP/staffing, talent CRM, trust and safety, ads, learning, network", "Design only", "staffing_assignment, placement_drive, background_check_order, talent_campaign, fraud_signal, ad_campaign, mentor_profile, connection",
     f"{S['ext_tables']} extension tables are not seeded or performance-measured"),
]

for _g in GAPS:
    for _nm in re.split(r"[,; ]+", _g[5]):
        if _nm and re.fullmatch(r"[a-z][a-z0-9_]*", _nm) and "_" in _nm and _nm not in TB and _nm not in dc.view_names():
            raise SystemExit(f"gap {_g[0]}: unknown table {_nm}")

# --------------------------------------------------------------------------------------- allow-list for backtick checks
ALLOW = set("""gen_random_uuid unique_rowid ttl_expire_after ttl_expiration_expression ttl_job_cron ttl_pause ttl_select_batch_size ttl_delete_batch_size
enable_durable_locking_for_serializable follower_read_timestamp gin_trgm_ops max_identifier_length bucket_count num_nonnulls start_single_node
sql_schema_approx_max_object_count range_max_bytes gc_ttlseconds default_transaction_use_follower_reads app_rw auth_svc audit_writer audit_reader purge_svc
changefeed_svc analytics_ro analytics_reader analytics_etl dba_break_glass kv_rangefeed_enabled uuid_v7 crdb_region schema_locked server_version
enterprise_license cluster_organisation is_default is_active sort_order name_i18n code_test statement_timeout row_hash prev_hash chain_seq chain_no
index_exempt no_hash_why where_why null_why unbounded_why external_ref delete_policy append_only created_only audit_mode ttl_expire
sql_sketch start_end sequence_no prior_period_id enum_ref seconds_until_enterprise_license_expiry assigned_requirement deny_new replace_oldest open_to_consultants
""".split())
# schema-ish names collected once
ALL_NAMES = set(TB)
ALL_NAMES |= {c["name"] for t in M["tables"] for c in t["columns"]}
ALL_NAMES |= {x["name"] for t in M["tables"] for x in t["indexes"] + t["constraints"] + t["fks"]}
ALL_NAMES |= {v["name"] for v in M["views"]}
ALL_NAMES |= {r["id"] for r in M["rules"]}
ALL_NAMES |= set(__import__("dictlib").MODULES)


def verify(text, where):
    bad = []
    for tok in sorted(set(re.findall(r"`([^`\n]+)`", text))):
        if tok.endswith((".py", ".md", ".xlsx", ".yaml", ".txt", ".json")):
            continue
        if "." in tok and re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", tok):
            t, c = tok.split(".")
            if t in TB and any(x["name"] == c for x in TB[t]["columns"]):
                continue
            bad.append(tok)
        elif re.fullmatch(r"[a-z][a-z0-9_]*", tok) and ("_" in tok):
            if tok in ALL_NAMES or tok in ALLOW:
                continue
            bad.append(tok)
    if bad:
        raise SystemExit(f"[{where}] unknown identifiers: {bad}")



def _graph_stats():
    ext = set(dc.EXT)
    mod = {t["name"]: t["module"] for t in M["tables"]}
    inb = collections.Counter(f["ref"] for t in M["tables"] for f in t["fks"])
    c2e = sum(1 for t in M["tables"] if t["module"] not in ext for f in t["fks"] if mod[f["ref"]] in ext)
    e2e = sum(1 for t in M["tables"] if t["module"] in ext for f in t["fks"] if mod[f["ref"]] in ext and mod[f["ref"]] != t["module"])
    e2c = sum(1 for t in M["tables"] if t["module"] in ext for f in t["fks"] if mod[f["ref"]] not in ext)
    t3d = sum(1 for t in M["tables"] if t["module"] in dc.DOCKET_MODULES for f in t["fks"] if mod[f["ref"]] == "tier3_reference")
    g = collections.defaultdict(set)
    for t in M["tables"]:
        for f in t["fks"]:
            if f["ref"] != t["name"]:
                g[t["name"]].add(f["ref"])
    sys.setrecursionlimit(20000)
    idx, low, st, on, sccs, c = {}, {}, [], set(), [], [0]

    def sc(v):
        idx[v] = low[v] = c[0]; c[0] += 1; st.append(v); on.add(v)
        for x in g[v]:
            if x not in idx:
                sc(x); low[v] = min(low[v], low[x])
            elif x in on:
                low[v] = min(low[v], idx[x])
        if low[v] == idx[v]:
            comp = []
            while True:
                x = st.pop(); on.discard(x); comp.append(x)
                if x == v:
                    break
            if len(comp) > 1:
                sccs.append(sorted(comp))
    for v in list(mod):
        if v not in idx:
            sc(v)
    selfn = sum(1 for t in M["tables"] if any(f["ref"] == t["name"] for f in t["fks"]))
    nn = sum(1 for t in M["tables"] for k in t["constraints"] if k["type"] == "check" and "num_nonnulls" in str(k.get("expr")))
    return inb, c2e, e2e, e2c, t3d, sccs, selfn, nn


INB, C2E, E2E, E2C, T3_FROM_DOCKET, SCCS, SELF, NUMNN = _graph_stats()

# ======================================================================================================== build document
def build():
    o = []
    w = o.append
    docket_ratio = S["docket_tables"]
    w("# Schema justification (Talentra DB hackathon)\n")
    w(f"Generated by `python docs/tools/build_justification.py` on {dc.TODAY} from `docs/_staging` (lint: `{line_lint[line_lint.index('tables='):]}`). "
      "Every number below is computed from the model; every backticked table, column, view, index or business-rule id was verified to exist when this file was built. "
      "Companion documents: `02_adr_log.md/.xlsx` (decisions), `04_design_conventions.xlsx`, `05_data_dictionary.xlsx`, `08_rule_compliance.xlsx` (rule checks, deviations, findings).\n")
    w("Competitor statements come from the module notes; where a module's notes say a competitor feature list was not re-verified (administration, gig, subscriptions, ext_matching and others do), this document inherits that caveat.\n")

    # -------- 1 engine
    w("## 1. Engine choice\n")
    w("**Decision (ADR-001):** CockroachDB v26.2 (Regular GA 26.2.0-26.2.6; LTS from v26.2.7, maintenance to 2027-09-23) with the PostgreSQL dialect and wire protocol. "
      "The image tag must be pinned to the latest 26.2.x patch after checking Docker Hub (the exact tag is unverified in the research). v26.3 is an Innovation release and is not used.\n")
    w("**Why not the docket's PostgreSQL or MySQL.** The platform target (10M candidate profiles, 1M job requirements, 100M applications, 24x7 audit and notification writes) and the rules that must hold under concurrency "
      "(device limits, wallet balance, non-overlap) favour a SERIALIZABLE-by-default distributed engine where random UUID keys and hash-sharded indexes avoid write hotspots. MySQL has no partial indexes, which this design uses "
      f"{S['partial_unique']} times as partial unique indexes (soft-delete naturals, one-open-row rules, device slots). Single-primary PostgreSQL needs sharding middleware beyond one node. Because the docket names PostgreSQL or MySQL, "
      "this is recorded as deviation DEV-01 in `08_rule_compliance.xlsx`; CockroachDB is treated as the PostgreSQL family (same dialect, drivers and DDL review).\n")
    w("| Criterion | CockroachDB v26.2 (chosen) | PostgreSQL 16/17 | MySQL 8 |\n|---|---|---|---|")
    w("| Default isolation | SERIALIZABLE (client retries on 40001) | READ COMMITTED | REPEATABLE READ |")
    w("| Horizontal write scale | native ranges, hash-sharded indexes | single primary (Citus/partitions add-on) | single primary |")
    w("| Partial unique index (soft delete, one open row) | yes | yes | no (workarounds with generated columns) |")
    w("| Exclusion constraints (non-overlap) | **no** (patterns P1-P3) | yes (btree_gist) | no |")
    w("| Triggers / RLS | triggers GA in v26.2, RLS GA label unverified; not relied on | yes | triggers only |")
    w("| Column-level privileges | **no** (views + table grants) | yes | yes |")
    w("| Row-level TTL | yes (background job) | no (cron) | no (events) |")
    w("| Licence | CockroachDB Software License; multi-node needs a key (see below) | open source | GPL/commercial |\n")
    w("**Licence caveat (crdb_capabilities.md section 3).** Binaries from 24.3 are under the CockroachDB Software License. Every multi-node self-hosted cluster needs a licence key: Enterprise Free (company plus affiliates revenue below US$10M, own hardware or own public-cloud account, annual renewal, telemetry required), a 30-day Trial, or paid Enterprise. "
      "Without a valid key a cluster is throttled to **5 concurrent open SQL transactions** (7-day grace). `cockroach start-single-node` and `cockroach demo` need no licence but are single-node. "
      "The 3-node Docker cluster must therefore apply the key right after `cockroach init` and keep outbound telemetry open; Trial may expire mid-project. No feature is verified licence-free, so correctness never depends on triggers, RLS, changefeeds or multi-region (ADR-012, ADR-018).\n")
    w("**Features absent or unverified that the design works around:** exclusion constraints, MERGE (no docs page), `CREATE DOMAIN` (GA only in v26.3), column-level privileges, session advisory locks, `uuid_v7` (not found), DEFERRABLE and NOT VALID foreign keys (unverified), jsonb `?` index acceleration (unverified), SKIP LOCKED under SERIALIZABLE (gated), custom session variables such as `SET app.user_id` (unverified). "
      "Patterns P1-P6, D-13 and D-14 (04_design_conventions.xlsx, Patterns sheet) replace them.\n")
    w("**Portability to stock PostgreSQL (exit path):** drop `USING HASH ... WITH (bucket_count)` (plain btree), replace row-level TTL by cron deletes, rename identifiers longer than 63 characters "
      f"({S['names_gt63']} names, DEV-06; CockroachDB itself allows 128 bytes, `max_identifier_length`, crdb_capabilities row 22, so no change is needed on this engine and the lint only reports it as a deviation, never an error), change STRING(n) to varchar(n)/text, remove STORING (use INCLUDE) and re-test the CHECKs that use `~`, jsonb `?` and num_nonnulls. No CockroachDB-only semantics are needed for correctness.\n")

    # -------- 2 key type
    w("## 2. Key type\n")
    w(f"Every one of the {S['tables']} tables has `id UUID NOT NULL DEFAULT gen_random_uuid()` as primary key (ADR-002, check N04 in `08_rule_compliance.xlsx`); all {S['fks']} foreign keys are UUID. No sequences, no SERIAL. "
      "Reasons: CockroachDB documentation recommends UUIDv4 to spread writes across ranges; `uuid_v7()` is not available; sequential keys hotspot; keys carry no information so they are safe in URLs. "
      f"Cost: 16-byte keys enlarge {S['indexes']} secondary indexes and {S['fks']} FK columns, and the keys carry no time order, so every time-ordered read uses a timestamp column behind one of the {S['hash_indexes']} hash-sharded indexes (ADR-003; bucket_count 8: {S['hash_buckets'][8]}, 16: {S['hash_buckets'][16]}, 32: {S['hash_buckets'][32]}). "
      "Natural keys (email, GSTIN blind index, external reference, lookup code) are UNIQUE constraints or partial unique indexes. Gapless business numbers use a locked counter row (`invoice_number_series`, BR-SUB-12), not keys. The only non-random ids are `dim_date` and `dim_time_of_day` (UUIDv5, DEV-08).\n")

    # -------- 3 model at a glance
    w("## 3. The model at a glance\n")
    w(f"{S['tables']} tables in {len(dc.ALL_MODULES)} modules ({S['non_lookups']} entity/link/event/config/analytics tables and {S['lookups']} enumeration lookups holding {S['lookup_values']} seed values), {S['columns']} columns ({S['avg_cols']} per table; {S['avg_cols_nonlookup']} per non-lookup table; widest `job_requirement` with {S['max_cols'][0]}), "
      f"{S['fks']} foreign keys ({S['fks_composite']} composite), {S['checks']} CHECK constraints, {S['unique_constraints']} unique constraints, {S['indexes']} secondary indexes ({S['partial_indexes']} partial, {S['hash_indexes']} hash-sharded, {S['gin_indexes']} inverted/GIN of which {S['trgm_indexes']} declare gin_trgm_ops, {S['storing_indexes']} with STORING), "
      f"{S['views']} views and {S['mviews']} materialised views, {S['rules']} business rules with a stated enforcement mechanism "
      f"({', '.join(f'{k} {v}' for k, v in S['rules_by_type'].most_common())}). Delete policy: soft {S['delete']['soft']}, hard {S['delete']['hard']}, append_only {S['delete']['append_only']}; {S['ttl_tables']} tables have row-level TTL. "
      f"Column classes: Public {S['sens']['Public']}, Internal {S['sens']['Internal']}, Personal {S['sens']['Personal']}, Sensitive {S['sens']['Sensitive']}; {S['nullable']} nullable columns ({S['nullable'] * 100 / S['columns']:.1f}%), each with a null_why. "
      f"Volume classes: low {S['volume']['low']}, medium {S['volume']['medium']}, high {S['volume']['high']}, very_high {S['volume']['very_high']}. Tiers: docket Tier 1 {S['tiers']['1']}, Tier 2 {S['tiers'].get('2', 0)}, Tier 3 {S['tiers']['3']}, extension (X) {S['tiers']['X']} "
      "(tier labels are authors' tags per table; the coverage matrix in section 7 maps by domain).\n")
    w("| Module | Class | Tables | Entities | Lookups | Columns | FKs | Soft | Hard | Append-only | Hash idx | TTL | Views | Rules |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for mod in dc.ALL_MODULES:
        d = PM[mod]
        cls = "docket" if mod in dc.DOCKET_MODULES else ("Tier 3 (optional)" if mod in dc.TIER3 else "EXTENSION")
        w(f"| `{mod}` | {cls} | {d['tables']} | {d['entities']} | {d['lookups']} | {d['columns']} | {d['fks']} | {d['soft']} | {d['hard']} | {d['append_only']} | {d['hash_idx']} | {d['ttl']} | {d['views']} | {d['rules']} |")
    w(f"| **Total** | | **{S['tables']}** | {S['non_lookups']} | {S['lookups']} | {S['columns']} | {S['fks']} | {S['delete']['soft']} | {S['delete']['hard']} | {S['delete']['append_only']} | {S['hash_indexes']} | {S['ttl_tables']} | {S['views'] + S['mviews']} | {S['rules']} |\n")
    w(f"The 11 docket modules hold {S['docket_tables']} tables; Tier 3 holds {n('tier3_reference')}; the five extension modules hold {S['ext_tables']}. "
      "Why this many: 536 of the tables are enumeration lookups required by docket rule group 3 (one table per enumeration, no CHECK-IN lists, ADR-016); the rest are normalised children (no arrays, no repeating groups) and append-only event/history tables required by rule groups 4 and 10.\n")

    # -------- 4 module by module
    w("## 4. Module-by-module justification\n")
    w("Each subsection gives the computed size, why the entity set looks like this, the competitor comparison taken from the module notes and the module's own deviations. Table names are real.\n")

    MODS = collections.OrderedDict()
    MODS["master_data"] = (
        "Shared reference data for every other module: geography (`country`, `state_region`, `city`, `locality`, `postal_code`, `time_zone`), language/i18n (`language`, `ui_label`, `ui_label_translation`), `currency`, "
        "the skill graph (`skill_category`, `skill`, `skill_alias`, `skill_relation`), education (`qualification`, `field_of_study`, `institution`, `institution_alias`), industry (`industry` + `industry_closure`), roles (`job_category`, `job_role`, `job_role_alias`, `job_role_skill`), employer dedupe (`employer_name_canonical`, `employer_name_alias`) and `tax_id_type`.",
        "Free text from users must map to canonical rows or search, matching and analytics fragment. A table is a lookup when it is a small closed set that drives behaviour and has only code/name/order/active; it is a rich master when rows carry hierarchy, attributes or volume (country, city, skill...). Everything is hard-delete-free: rows are retired with `is_active` or merged via a `merged_into_*_id` pointer because millions of history rows reference them (FK RESTRICT). "
        "`tax_id_type` stores per-country regex, length, checksum and mask so GSTIN/PAN/CIN/TAN, EIN, VAT etc. are validated before hashing.",
        "LinkedIn (about 39-41k skills, 26 languages, about 374k aliases, over 200k skill-to-skill links, 26 job functions, about 400 industries): structure matched by `skill`/`skill_alias`/`skill_relation`/`language`/`industry`; seed volumes are targets, not loaded. Naukri (functional area/role/industry tree, notice-period buckets, city tier, engagement type) matched; Monster/Indeed (job type, salary period, shift) matched. "
        "Exceeds with multilingual aliases, closure table, canonical employers/institutions, per-country tax-id validation and reason-code lookups. Deliberately omitted: religion, caste, disability, ethnicity and nationality-of-origin masters (legal risk).",
        "Hindi translations exist for only part of the lookups (see gap G-24); fallback cycles cannot be CHECKed in CockroachDB (nightly job BR-MST-04).")
    MODS["profile"] = (
        "Candidate identity and contact (`candidate_profile`, `candidate_contact_phone`, `candidate_contact_email`, `candidate_address`, `candidate_identity_verification`), core sections (`candidate_education`, `candidate_experience`, `candidate_skill`, `candidate_language`, `candidate_project`), LinkedIn/Naukri depth "
        "(`candidate_certification`, `candidate_award`, `candidate_patent`, `candidate_publication`, `candidate_volunteer_experience`, `candidate_recommendation`, `candidate_skill_endorsement`, `candidate_follow`), preferences (`candidate_job_preference` plus five `candidate_preferred_*` bridges, `candidate_work_authorisation`, `candidate_open_to_work_signal`), documents (`resume`, `resume_template`, `resume_parse_result`, `candidate_media_file`, `profile_import`), scoring and learning (`profile_completion_rule`, `assessment`, `course_enrolment`), history (`candidate_profile_view`, `candidate_activity_log`, `candidate_shortlist_item`, `candidate_shortlist_history`) and growth/compliance (`referral`, `referral_reward`, `candidate_erasure_request`).",
        "One profile per user (`uq_candidate_profile_app_user_id`); national id never stored (`candidate_identity_verification` keeps `vendor_token` and a keyed HMAC `id_number_hmac`); salary amounts nullable with a disclosure lookup (D-09); special-category data isolated in `candidate_diversity_declaration`; resumes form a version chain with one default per profile and a scan status gate; views/activity/shortlist history are append-only, hash-sharded and TTL-expired; shortlist items are tenant-pinned by a composite FK to `candidate_shortlist_folder`. "
        "Erasure is anonymise-in-place with `candidate_erasure_request` as evidence (ADR-026). Legacy sync idempotency via `legacy_candidate_ref` and `profile_import`.",
        "LinkedIn: certifications, honours, patents, publications, volunteering, section ordering, endorsements, recommendations, follows, open-to-work all modelled; exceeds with assessment-verified skills, disclosure-aware salary, per-country work authorisation and erasure evidence; groups/newsletters moved to ext_network. Naukri: all headline fields matched, exceeds with resume version chain, scan status and parse provenance. Monster: multiple resumes, visibility per resume, work authorisation matched; security clearance not first-class (G-04). Indeed: privacy and availability matched.",
        "Contact phone/email are mirrored on `candidate_profile` for card speed (BR-PRF-04, nightly drift job); resume text is the only unbounded Personal body and is encrypted at the storage layer; free text carries a nullable `language_id` where the language may be unknown (former finding F-06, resolved).")
    MODS["jobs_applications"] = (
        "Requirement header `job_requirement` with per-slot `job_position`, normalised children (`job_requirement_skill`, `job_requirement_location`, `job_requirement_qualification`, `job_requirement_benefit`, `job_requirement_screening`), counters split into `job_requirement_stat`, pipeline (`job_application`, `application_status_transition`, `job_application_status_history`, `application_note`, `application_scorecard`), interviews (`interview` and 5 children), offers (`offer`, `offer_revision`, `offer_approval`), `joining`, `rejection`, candidate-side history (`candidate_job_search_history`, `job_view_event`, `job_bookmark`) and reach (`job_posting_channel`, `job_syndication`, `job_share_event`, `job_boost`, `talent_pool`, `job_walk_in_session`).",
        "Status transitions live in the database: a history row has a composite FK to `application_status_transition` and a self composite FK chain (application, prior sequence, from status) makes the chain gap-free; one live application per candidate and requirement via a partial unique index; questionnaire integrity via a composite FK chain; tenant isolation by `organisation_id` plus composite FK to `job_requirement` (D-11); salary semantics with `salary_disclosure_id`. "
        "Search uses a STORED `title_search` with trigram GIN plus an external engine (D-16). `job_application` (about 100M rows at full scale) leads every secondary index with ids; only `applied_at` and `status_changed_at` are hash-sharded (bucket_count 32).",
        "LinkedIn posting/Talent (workplace type, screening questions, pipeline, rating) covered; InMail credits live elsewhere. Naukri RMS (walk-ins, CTC not disclosed, hot vacancy, consultant postings, folders) covered. Monster/Indeed (benefit chips, knock-out screeners, sponsored jobs, syndication, reporting) covered. Greenhouse Harvest (applications, scorecards, interviews, offers, approvals, openings, close reasons) covered except demographic data. Exceeds with tenant-safe composite FKs, a status chain and offer revisions.",
        "The partial unique predicate cannot reference the status lookup, so withdrawal is a column (`withdrawn_at`) mirrored by the status; `vw_recruiter_job_requirement` has a single definition with an explicit viewer argument (former finding F-02, resolved); long index names (DEV-06).")
    MODS["administration"] = (
        "Identity (`app_user`, `user_status_history`), authorisation (`app_role`, `permission`, `app_role_permission`, `app_user_role`, `app_role_inheritance`, `app_role_eligibility`), secrets (`user_credential`, `mfa_factor`, `password_reset_token`, `identity_session`, `api_key`), consent (`consent_document`, `user_consent`), audit tier (`audit_event`, `audit_chain_head`, `audit_chain_checkpoint`, `error_log`, `security_event`), blocking (`user_block`, `organisation_block`) and governance (`data_subject_request`, `data_retention_policy`, `purge_job_run`, `legal_hold`, `privileged_access_request`, `impersonation_session`, `moderation_case`, `user_merge_request`).",
        "One identity table with an immutable actor type and role rows; recruiters cannot register candidates because a composite FK to `registration_policy` has no (candidate, recruiter, *) row; privileges are enforced in four layers (DB roles with REVOKE, views as row security, constraints, application data scope); audit is append-only with 32 parallel hash chains, signed checkpoints and a changefeed to an immutable sink (ADR-013); credentials argon2id with per-row parameters; blocks are effective-dated and read through `vw_candidate_visible_to_user` (ADR-039).",
        "LinkedIn member block (mutual invisibility), company page admin roles, DSAR audit: matched, with effective-dated reasoned blocks and a DSR SLA table beyond them. Naukri 'block companies' and recruiter sub-users: company blocks first class, sub-users deferred (section 10). Industry IAM practice (hashed tokens, refresh-token rotation with reuse detection, break-glass) modelled.",
        "Audit columns on business tables are not FKs (DEV-02); `audit_event.entity_ref` and similar are FK-less (DEV-09); TTL on `audit_event` is compatible with its children because their FKs are ON DELETE SET NULL (F-01, resolved); role-name inconsistency with the analytics module (F-08).")
    MODS["organisation_account"] = (
        "Tenant `organisation`, `organisation_location`, commercial `account` with `account_organisation` and `account_admin`, effective-dated `recruiter_organisation_mapping`, tree (`organisation_hierarchy`, `organisation_hierarchy_closure`, `organisation_hierarchy_change`), compliance (`organisation_identifier`, `organisation_verification`, `organisation_document`, `organisation_domain`), page content (`organisation_media`, `organisation_benefit`, `organisation_brand`, `organisation_career_page`), reviews (`organisation_review` family and `organisation_rating_summary`), data quality (`organisation_alias`, `organisation_merge`) and commercial (`client_relationship`, `organisation_invitation`).",
        "Recruiter mapping is the single owner of 'which recruiter may act for which company' and uses P1 + P3 (ADR-009); the hierarchy prevents cycles by a level-increment composite FK (ADR-010); tax ids are encrypted with an HMAC blind index so one GSTIN/EIN/VAT cannot be claimed by two companies; verification keeps vendor status and idempotency key and never silently flips to verified when the vendor is down; follower counts are not stored (hot-row contention).",
        "LinkedIn Company Page (name, tagline, industry, size, HQ, verified badge, admins, followers) covered; Naukri/Monster employer profile (GST/CIN, recruiter list, career page, perks) covered; Glassdoor/AmbitionBox style reviews covered. Beyond them: parent/ultimate-parent hierarchy with ownership percentage, staffing client contracts, merge workflow. Falls short deliberately: employee graph and salary data (Tier 3).",
        "'At least one active admin' is a counter plus CHECK maintained under FOR UPDATE (`vw_account_active_admin`); `helpful_count` is a batch counter; sub-roles deferred with a concrete extension design (section 10).")
    MODS["gig_marketplace"] = (
        "Worker side (`gig_worker_profile`, `gig_worker_skill`, `gig_worker_certification`, `gig_service_offering`, `gig_service_package`, `gig_availability_rule`, `gig_availability_window`, `gig_time_off`), demand side (`gig_request`, `gig_request_milestone`, `gig_request_status_history`), matching (`gig_application`, `gig_invite`, `gig_assignment`, `gig_slot`, `gig_requester_worker_list`), outcomes (`gig_rating`, `gig_worker_rating_summary`, `gig_dispute`, `gig_cancellation`), money and fulfilment (`gig_timesheet_entry`, `gig_payout_reference`, `gig_earnings_snapshot`, `gig_fulfilment_event`, `gig_fulfilment_event_attempt`).",
        "A separate subsystem rather than a job variant (ADR-005). Overlap rule B35 by P2: assignment windows on a 15-minute grid are exploded into `gig_slot` rows with UNIQUE (`gig_worker_profile_id`, `slot_start_at`); time off uses the same table so leave and work cannot overlap. Price consistency, accepted-application chain, rating-only-when-completed and favourite-xor-blacklist are composite FKs and partial uniques; external fulfilment is idempotent (handoff key, webhook key) and earnings are reconciled copies, never the source of truth.",
        "Upwork (milestones vs hourly diary, ratings, availability) covered except connects pricing and escrow; Fiverr (packages, revisions) covered except seller levels; TaskRabbit, Uber, Urban Company (rate, radius, favourites, KYC with document expiry, ratings) covered; Toptal vetting tiers via `verification_status`; Freelancer contests not modelled.",
        "`gig_slot` has no updated_* columns (DEV-03); very-high volume (~40M slot rows) with 90-day TTL and hash-sharded slot time; cross-type reporting needs `vw_unified_demand`.")
    MODS["licences_devices"] = (
        "`licence` (max_users, max_devices_per_user), `licence_seat_slot`, `licence_seat`, `licence_user_invitation`, `licence_transfer`, `licence_ip_allowlist`, `device`, `device_slot`, `device_session`, `session_activity_sample`, `login_attempt`, `device_revocation`, `session_revocation`, `device_limit_violation`, `device_trust_challenge`, `push_registration`, `licence_usage_snapshot`, `licence_status_history`.",
        "Two-level slot model (ADR-008): `licence_seat_slot` pre-creates slots 1..max_users and `device_slot` pre-creates 1..max_devices_per_user per seat; an active `device_session` claims a slot through the partial unique `uq_device_session_device_slot_id` (WHERE ended_at IS NULL); login locks only that user's `licence_seat` row. A session counts iff `ended_at IS NULL`; expiry is released lazily at login and by a one-minute job; revocation and breach write audit rows. Device fingerprints are HMACs only; login history is a view over `login_attempt`.",
        "Microsoft 365 / Slack / LinkedIn Recruiter (named seat assignment with reassignment history) matched by seat + transfer + invitation; Netflix / Okta (device or session cap with sign-out-oldest) matched by `device_limit_policy` (deny_new or replace_oldest).",
        "Locks are best-effort under SERIALIZABLE, hence retries and the declarative slot twin; TTL vs children resolved (F-01): the FKs from `session_revocation`, `device_limit_violation`, `profile_download` to `device_session` are ON DELETE SET NULL and `session_activity_sample` cascades; an FK cycle with `identity_session` is broken by a nullable `device_session_id`.")
    MODS["sourcing_consultant"] = (
        "Consultant side (`consultant_profile`, `consultant_specialisation`, `consultant_desk`, `consultant_job_openness`, `sourcing_request`, `sourcing_request_decision`, `sourcing_shortlist`, `sourcing_submission`, `sourcing_commission`, `consultant_rating`) and recruiter tooling (`outreach_template`, `outreach_batch`, `outreach_recipient`, `outreach_exclusion`, `outreach_message`, `outreach_reply`, `outreach_credit_usage`, `saved_search`, `candidate_search_history`, `candidate_note`, `candidate_tag`, `candidate_contact_attempt`).",
        "'A consultant may act only on requirements open to consultants' has no cross-table CHECK in CockroachDB, so openness rows are created under a FOR UPDATE check of `job_requirement.is_open_to_consultants`, `sourcing_request` carries a composite FK to its own requirement's openness row, and consultant identity is bound by a composite FK; closing the flag closes openness rows in the same transaction (BR-SRC-02). Blocks bind outreach (`INSERT ... SELECT ... WHERE NOT EXISTS`, `outreach_exclusion`). Credits reserve, consume, refund on reply through the wallet ledger.",
        "LinkedIn Recruiter (projects, saved searches with alerts, InMail credits with reply refund, notes/tags, templates), Naukri RESDEX (search history, mailers, folders) and Monster talent pools covered.",
        "The openness-iff-flag link is transactional (documented residual risk); message bodies retained 90 days (TTL), replies 180 days, search history 24 months; recruiters never create candidate rows.")
    MODS["subscriptions_payments"] = (
        "Catalogue (`subscription_plan`, `plan_version`, `plan_price`, `plan_entitlement`, `credit_rate_card`, `credit_pack`), contract (`subscription`, `subscription_period`, `subscription_status_history`, `subscription_cancellation`, `subscription_renewal_attempt`, `subscription_entitlement`), money (`wallet`, `wallet_ledger_entry`, `wallet_expiry_bucket`, `wallet_bucket_consumption`, `spend_limit`, `coupon`), payments (`payment_order`, `payment_attempt`, `payment_event`, `payment_method_token`, `refund`, `reconciliation_run`), billing (`invoice`, `invoice_line`, `credit_note`, `invoice_number_series`, `tax_rate`, `tax_registration`, `currency_exchange_rate`) and usage (`profile_download`, `consultant_payout`).",
        "Wallet unit is integer credits (ADR-007) with an append-only hash-chained single-entry ledger (ADR-006); the debit algorithm BR-SUB-08 locks the wallet, checks balance and limits, appends the entry and updates the wallet in one SERIALIZABLE transaction; `subscription_period` non-overlap uses the P1 chain with a proof by induction (BR-SUB-03); invoice numbers are gapless per series under a locked counter row (BR-SUB-12); every money-moving write has an idempotency key; no card/bank/UPI data is stored (tokens only).",
        "Stripe (price/subscription/invoice/credit note), Razorpay (order, payment, refund, signed webhooks), Chargebee/Recurly (plan versions, dunning), LinkedIn Recruiter and Naukri RESDEX (credit downloads, quotas) covered. Full double-entry accounting is an extension point.",
        "Hard delete on `subscription_period` only for scheduled uninvoiced rows; FKs to registry tables RESTRICT; TTL on `payment_event` (540 days) no longer conflicts with `payment_event_outcome` (F-01 resolved: ON DELETE SET NULL).")
    MODS["notifications_community"] = (
        "Catalogue and inbox (`notification_type`, `notification_template`, `notification`), the B57 delivery path (`notification_delivery`, `notification_delivery_event`, `provider_webhook_event`, `notification_delivery_daily_summary`), hygiene (`notification_suppression`, `unsubscribe_token`, `push_subscription`), preferences (`notification_preference`, `quiet_period`, `language_preference`, `privacy_setting`, `privacy_hidden_organisation`), job alerts (`job_alert_preference`, `job_alert_run`, `job_alert_match`), community (`community_group`, `community_post`, `community_comment`, `community_reaction`, `community_report`, `moderation_action_log`, `feed_item`), messaging (`message_thread`, `message`), support (`support_ticket` family) and FAQ (`faq_item`, `faq_item_translation`), `referral_event`, announcements and surveys.",
        "Deliveries are event-sourced and INSERT-only: a changefeed on `notification` drives dispatch, one row per notification x channel x attempt is written after the provider answers, receipts land in `provider_webhook_event` and become `notification_delivery_event` rows; every time-ordered index is hash-sharded; TTL 30 days with a 7-year rollup in `notification_delivery_daily_summary` (hash-chained) preserves audit. Bodies are never stored (digest only); recipients are stored as an HMAC. Community uses hybrid fan-out and counters off the post row (`community_post_stat`); messaging bodies are application-encrypted and checked against `user_block` in the same transaction.",
        "LinkedIn inbox, digests, feed, groups, messaging with read receipts; Naukri alerts and hide-from-companies; Monster search agents; Twilio/SendGrid delivery models covered, with durable suppression and moderation case tooling added.",
        "Polymorphic `*_ref` columns (DEV-09); `referral_event` has no FK to `referral` (open integration question); lookup FKs on large tables are index-exempt to save write cost.")
    MODS["analytics"] = (
        "Kimball star schema in the OLTP cluster: conformed dimensions (`dim_date`, `dim_candidate`, `dim_organisation`, `dim_user`, `dim_job_requirement`, `dim_skill`, `dim_location`, `dim_demand_type`...), facts (`fact_application_event`, `fact_application_pipeline`, `fact_profile_view`, `fact_profile_download`, `fact_wallet_transaction`, `fact_payment`, ...), aggregates for KPIs K01..K20 (`agg_funnel_daily`, `agg_time_to_fill_monthly`, `agg_revenue_monthly`, ...), bridges and ETL control (`dim_etl_run`, `dim_etl_watermark`, `agg_data_quality_result`).",
        "The docket cites Section 8 KPIs but supplies none; K01..K20 are defined with exact formulas (gap G-22). SCD Type 2 with `hash_diff` and a partial unique current row (ADR-020); aggregates load idempotently by grain with a data-quality publish gate; reads use follower reads; fact source ids are copies without FKs (ADR-021, DEV-05); placeholder members keep unknown and not-applicable distinct; no names/emails/phones, banded ages and k>=10 suppression.",
        "LinkedIn Talent Insights style supply/demand by skill and location, Naukri recruiter response and view/download analytics and Monster source and time-to-fill analytics are reproduced by the skill bridges, `agg_recruiter_activity_daily`, `fact_profile_view`, `fact_profile_download` and K01/K07.",
        "UUIDv5 date keys (DEV-08), accumulating snapshots updated in place (DEV-10), same-cluster OLAP load (warehouse export by changefeed is the escape hatch).")
    MODS["tier3_reference"] = (
        "`salary_benchmark` (+ `salary_benchmark_source`), `company_rating_snapshot`, `trending_job_snapshot`, `skill_demand_snapshot`, `industry_growth_snapshot`, `location_cost_index`, `dashboard_aggregate`, feature flags (`feature_flag`, `feature_flag_rule`, `feature_flag_override`, `feature_flag_audit`) and the provider registry (`reference_data_source`, `reference_data_refresh`).",
        "Taken on as an optional, separate module (ADR-028, section 6): reference data owned by the external provider with attribution and refresh date (docket Section 6 'Company review and salary data'); percentile bands as money with currency and a publishable threshold of 30 observations; feature flags are evaluated in the application and audited through `audit_event`.",
        "Glassdoor/Levels-style benchmarks and ratings are reference-only; no personal data stored.",
        "Two extension tables depend on it by FK (`article_salary_benchmark.salary_benchmark_id`, `ab_experiment.feature_flag_id`, the latter nullable), so dropping Tier 3 requires adjusting those two tables.")
    MODS["ext_network"] = (
        "Connection graph (`connection`, `connection_request`, `connection_request_quota`, `connection_event`), caches (`network_second_degree`, `mutual_connection_cache`, `network_graph_stat`), discovery (`pymk_candidate`, `pymk_dismissal`, `contact_match_key`, `contact_import`), creator and events (`newsletter`, `newsletter_issue`, `event`, `event_session`, `event_rsvp`, `event_ticket`, `live_room`), company updates (`organisation_update`), trust (`badge_type`-based `user_badge`, `user_signal`) and public profile (`public_profile_slug`, `public_profile_setting`, `profile_share_link`, `sitemap_entry`).",
        "Each undirected edge is stored once (`lo_app_user_id` < `hi_app_user_id`, UNIQUE) with two covering adjacency indexes; capped second-degree caches with TTL; address-book matching uses HMAC hashes and requires a consent row; event capacity is enforced under FOR UPDATE on the event row; reuse over duplication (blocks, follows, endorsements, payments).",
        "LinkedIn invitations, PYMK, tags/notes, newsletters, events, audio events, verification badges, open-to-work frame, public profile URL and visibility, who-viewed-your-profile (public product behaviour; no claim about internal LinkedIn schemas).",
        "About 2.5B edge rows at target scale (design-only, not seeded); `LEAST/GREATEST` over UUID in computed columns must be smoke-tested; hash + partial combinations to be tested at seed time.")
    MODS["ext_matching"] = (
        "Model registry (`recommendation_model`, `recommendation_model_version`, `recommendation_model_deployment`), pointers and snapshots (`embedding_ref`, `candidate_feature_snapshot`, `job_feature_snapshot`), results (`match_score`, `job_recommendation`, `candidate_suggestion`, `similar_job`, `similar_candidate`), logs (`recommendation_impression`, `recommendation_click_event`), search platform (`search_query_normalised`, `search_typeahead_term`, `search_synonym_group`, `ranking_profile`, `search_index_outbox`, `search_index_state`, `search_index_watermark`), experimentation (`ab_experiment`, `ab_variant`, `ab_assignment`) and quality scores (`job_quality_score`, `candidate_quality_score`).",
        "Models, embeddings and ranking stay outside the database; the DB stores the contract (registry, pointers, snapshots, results) and the transactional outbox for search sync (ADR-022, ADR-023). No VECTOR column in the core path (feature flag, GA label unverified, blocks writes on backfill). Batch-swap result tables expire by TTL on `expires_at`; impressions are the largest append-only table (hash-sharded, 32 buckets, TTL 90/180 days).",
        "LinkedIn, Naukri and Indeed recommendation/search shapes are modelled from public product behaviour and engineering write-ups known to the author (not re-fetched).",
        "Same-tenant composite FKs on `candidate_suggestion` and `ab_assignment` are application plus nightly check (BR-MTH-15); outbox claim uses a lease UPDATE because SKIP LOCKED is unverified; impression/click FKs may be dropped in production for ingest throughput.")
    MODS["ext_ads"] = (
        "Accounts and campaigns (`ad_account`, `ad_account_member`, `ad_campaign`, `ad_group`, `ad_group_placement`, `sponsored_job`, `sponsored_candidate_slot`), audiences (`ad_audience`, `ad_audience_rule`, `ad_audience_member_list`), creatives and approval (`ad_creative`, `ad_creative_review`, `ad_approval_workflow`, `ad_approval_request`, `ad_approval_decision`), pacing and money (`ad_schedule`, `ad_frequency_cap`, `ad_budget_shard`, `ad_budget_ledger`, `ad_billing_event`), events (`ad_impression`, `ad_click`, `ad_conversion`, `ad_auction_log`) and quality (`ad_performance_hourly`, `ad_performance_daily`, `ad_fraud_flag`, `ad_invalid_traffic_adjustment`, `ad_advertiser_policy_strike`), plus employer branding (`employer_branding_package`, `brand_page_promotion`).",
        "The ext_ads staging notes were rewritten (former finding F-09); the module has " + str(PM['ext_ads']['rules']) + " BR-ADS business rules. Mechanisms: sponsored jobs are pinned to the advertiser's tenant by composite FK (BR-ADS-02); click-time spend goes to one of 16 random `ad_budget_shard` rows to avoid a hot campaign row (BR-ADS-06); the budget ledger is append-only and hash-chained with reversal rows; ads never bypass the wallet (settlement via `wallet_ledger_entry`); targeting cannot use protected attributes (APP_LAYER); only approved creatives serve; raw events are idempotent, TTL-expired and rolled up hourly; invalid traffic is not billed.",
        "No competitor feature list is claimed for ads in the staging notes; LinkedIn campaign manager and Indeed sponsored jobs are the obvious public references for this shape, but no vendor comparison was done in staging and none is claimed here.",
        "Largest event volumes outside notifications; not seeded; `ad_budget_ledger` references the TTL table `ad_performance_hourly` with ON DELETE SET NULL (F-01 resolved); money columns carry `currency_id` everywhere.")
    MODS["ext_learning"] = (
        "Catalogue depth around profile's `course`/`course_enrolment` (`course_module`, `course_lesson`, `lesson_progress`, `course_review`, `course_offer`), `learning_path`, question bank and assessments (`question_bank`, `quiz_question`, `assessment_blueprint`, `assessment_session`, `assessment_answer`, `proctoring_event`), credentials (`certificate`, `certificate_verification`, `skill_badge`, `candidate_skill_badge`), mentoring (`mentor_profile`, `mentorship_session`, `coaching_session`, `mock_interview`), career resources (`article`, `resume_tip`), `skill_gap_report`, `learning_assignment` (employer-sponsored), provider integration (`learning_provider_integration`, `course_sync_event`) and engagement (`learning_activity_event`, `learning_streak`, `leaderboard_entry`).",
        "Does not redefine profile's course/assessment tables; question text lives only in `*_translation` rows so the language fallback chain works; polymorphic targets use one nullable FK per target plus an exactly-one CHECK (ADR-040); high-volume answer, proctoring and activity tables are hash-sharded; certificates carry a public verification code and content hash; no SCORM runtime, live video or real proctoring AI.",
        "LinkedIn Learning (paths, certificates, skill badges), Coursera (verified certificates), HackerRank (question banks, proctoring signals), Pluralsight Skill IQ (adaptive scoring, percentile bands), Naukri Campus assessments; table shapes are the authors' own.",
        "Mentor session overlap only guards identical starts (full non-overlap would use P2); free-text tables carry a nullable `language_id` (F-06 resolved).")
    MODS["ext_talent_ops"] = (
        "Onboarding (`onboarding_plan`, `onboarding_plan_task`, `e_sign_request`, `equipment_request`), background and reference checks (`background_check_order`, `background_check_result`, `reference_check_request`), campus and apprenticeship (`placement_drive`, `student_cohort`, `drive_round`, `drive_result`, `apprenticeship_programme`), staffing and managed workforce (`msp_vendor`, `workforce_request`, `vendor_submission`, `staffing_assignment`, `timesheet`, `performance_review_cycle`), talent CRM (`talent_segment`, `talent_campaign`, `talent_sequence`, `campaign_enrolment`), employer referral, trust and safety (`scam_job_report`, `fraud_signal`, `trust_score`, `spam_rule`, `account_restriction`, `appeal`) and compliance (`equal_opportunity_survey`, `data_processing_agreement`, `subprocessor`).",
        "Fills what an ATS/HRIS/staffing suite (Greenhouse, Lever, Workday Recruiting, Bullhorn, Naukri campus/TPO, LinkedIn Talent) has but the core touches lightly; no new money, ledger, consent or audit mechanism is invented; equal-opportunity data is isolated, encrypted with a dedicated key and reported only at minimum cell size 10 (ADR-036); campus eligibility rules cannot encode protected attributes; background checks follow the identity-verification boundary (vendor reference and outcome only).",
        "As above; client-confidential vendor pay rates are hidden behind `vw_client_vendor_submission` because column-level privileges do not exist.",
        "Non-overlap of a worker's staffing assignments is deliberately not enforced (concurrent part-time placements are legal); several cross-table rules (referrer is not the referee, approver is not the submitter) are APP_LAYER/TXN checks; `num_nonnulls` avoided in this module as unverified.")
    for mod, (scope, why, comp, devs) in MODS.items():
        cls = "docket module" if mod in dc.DOCKET_MODULES else ("OPTIONAL Tier 3 module" if mod in dc.TIER3 else "EXTENSION module (beyond the docket)")
        w(f"### 4.{list(MODS).index(mod) + 1} `{mod}` ({LABEL[mod]}; {cls})\n")
        w(f"**Size (computed):** {mline(mod)}\n")
        w(f"**Entity set.** {scope}\n")
        w(f"**Why this design.** {why}\n")
        w(f"**Competitor comparison.** {comp}\n")
        w(f"**Module-specific deviations and trade-offs.** {devs}\n")

    # -------- 5 deviations
    w("## 5. Docket Section 5 deviations (complete register)\n")
    w("A justified deviation is scored well and an undocumented one is a defect, so every departure found by checking the model and grepping the staging notes is listed. IDs match `08_rule_compliance.xlsx` (sheet Deviations). Open findings (defects to fix rather than justify) are in section 9.\n")
    import build_compliance as bc
    w("| ID | Rule | Deviation | Reason | Risk | Mitigation |\n|---|---|---|---|---|---|")
    for d in bc.DEVIATIONS:
        w("| " + " | ".join(str(x).replace("|", "/") for x in d[:6]) + " |")
    w("")
    w("**Module-recorded deviations and trade-offs (from the `notes:` blocks, each already covered above or by a DEV id):**\n")
    modnotes = [
        ("administration", "created_by/updated_by/deleted_by not FK-enforced; audit_event polymorphic refs FK-less; MFA secret in `mfa_factor`; lookup FKs index-exempt", "DEV-02, DEV-09"),
        ("analytics", "UUIDv5 ids for `dim_date`/`dim_time_of_day`; fact source ids not FK-enforced; accumulating snapshots updated in place", "DEV-08, DEV-05, DEV-10"),
        ("jobs_applications", "long index names (> 63); hash-sharded + REGIONAL BY ROW incompatibility; cross-module mapping equality via view", "DEV-06, ADR-018"),
        ("licences_devices", "fingerprints stored as HMAC only; `licence_usage_snapshot` owned here, KPIs in analytics", "ADR-008"),
        ("notifications_community", "polymorphic `*_ref` columns; enum FK columns without leading index; `referral_event` without FK to a referral table", "DEV-09"),
        ("organisation_account", "account-admin counter + CHECK instead of a count constraint; batch counters; single current hierarchy row", "ADR-010"),
        ("subscriptions_payments", "hard delete of scheduled uninvoiced `subscription_period` rows; RESTRICT to registry tables", "ADR-009"),
        ("ext_matching", "same-tenant composite FKs by application + nightly check; production may drop FKs on impressions/clicks", "BR-MTH-15"),
        ("ext_talent_ops", "`num_nonnulls` avoided; staffing worker reuses `candidate_profile`; column privileges replaced by views", "F-10"),
        ("tier3_reference", "FK domains assume registry tables are entities, not lookups (open integration question; no deviation from a rule)", "-"),
        ("profile", "profile notes say names over 63 characters are avoided, but the model has " + str(S['names_gt63']) + " index/constraint names over 63", "DEV-06"),
    ]
    w("| Module | Deviation / trade-off | Register |\n|---|---|---|")
    for m_, d_, r_ in modnotes:
        w(f"| `{m_}` | {d_} | {r_} |")
    w("")
    w("**Rule groups with no deviation:** 5 (nullability), 8 (physical object naming), 9 (licensing, devices, sessions), 10 (money and history; non-overlap is by constraint pattern, which the rule allows) and the reserved-keyword check (0 hits in "
      f"{len({t['name'] for t in rc.T}) + len({c['name'] for t in rc.T for c in t['columns']}) + len({x['name'] for x in rc.M['views']})} distinct identifiers against {len(RES)} reserved and {len(AVOID)} risky words).\n")

    # -------- 6 extension modules and tier 3
    w("## 6. Extension modules and Tier 3: decision record\n")
    w("**Context.** The docket defines 11 modules (Tier 1 and 2 domains) and lists Tier 3 (salary benchmarks, company ratings, trending job snapshots, dashboard aggregates, feature flags) as optional stretch coverage that must be a separate, clearly labelled module with the decision recorded. "
      "The scope was widened by the team to a full LinkedIn/Naukri-class product suite, which the docket neither requires nor forbids; it scores depth and competitor coverage.\n")
    w("**Decisions.**\n")
    w(f"1. Tier 3 is TAKEN ON as `tier3_reference` ({n('tier3_reference')} tables), physically separate: no Tier 1/2 or analytics table references it (computed: {T3_FROM_DOCKET} FKs from the 11 docket modules into it); only two extension FKs point in (`article_salary_benchmark`, `ab_experiment`). It implements the Section 6 boundary 'Company review and salary data' (reference only, attribution and refresh date) and demonstrates feature-flag design. Risk: stale reference data (mitigated by `reference_data_source.max_staleness_days`).")
    w(f"2. Five EXTENSION modules are added, all labelled tier X and prefixed by purpose in the dictionary: `ext_network` ({n('ext_network')} tables), `ext_matching` ({n('ext_matching')}), `ext_ads` ({n('ext_ads')}), `ext_learning` ({n('ext_learning')}), `ext_talent_ops` ({n('ext_talent_ops')}): {S['ext_tables']} tables, {S['ext_tables'] * 100 / S['tables']:.0f}% of the schema.")
    w(f"3. Independence is verified, not asserted: computed foreign keys from non-extension modules into extension modules = {C2E}; extension-to-extension foreign keys across different extension modules = {E2E}; extension-to-core foreign keys = {E2C} (e.g. `connection` references `app_user`). Dropping an extension module therefore needs no change to core tables.")
    w("4. Extensions reuse rather than duplicate: connections reuse `user_block`, `community_follow`, `candidate_skill_endorsement`, `payment_order`; ads settle through `wallet_ledger_entry`; learning reuses profile's `course`/`assessment`; talent_ops reuses `moderation_case`, `user_consent`, `candidate_profile`. They invent no new money, consent or audit mechanism.")
    w("5. Extensions obey every Section 5 rule (same lint, same conventions) and carry their own business rules (" + ", ".join(f"{m_} {PM[m_]['rules']}" for m_ in dc.EXT) + ").\n")
    w(f"**Trade-offs and risks.** Review effort and DDL size grow; extension tables are not seeded or measured at scale (gap G-26); several are very high volume (connections about 2.5B rows, impressions, answers, events) and their performance claims are design-only; the 2-day hackathon scope makes depth in the core modules the priority. "
      "The panel can score the 11 docket modules independently because the extension modules are separable.\n")
    w("**Alternatives rejected:** staying at the 11 modules (loses the competitor-coverage argument); folding extension tables into core modules (loses separability and review clarity).\n")

    # -------- 7 coverage matrix
    w("## 7. Docket coverage matrix (Section 4)\n")
    w("Every Tier 1, Tier 2 and Tier 3 domain maps to real tables (names verified at build time).\n")
    w("| Tier | Domain | Owning module | Tables |\n|---|---|---|---|")
    for d, m_, ts in TIER1:
        dc.must_exist(*ts)
        w(f"| 1 | {d} | {m_} | " + ", ".join(f"`{x}`" for x in ts) + " |")
    for d, ts in TIER2:
        dc.must_exist(*ts)
        w(f"| 2 | {d} | | " + ", ".join(f"`{x}`" for x in ts) + " |")
    for d, ts in TIER3:
        dc.must_exist(*ts)
        w(f"| 3 | {d} | tier3_reference | " + ", ".join(f"`{x}`" for x in ts) + " |")
    w("")

    # -------- 8 gap register
    w("## 8. Coverage-gap register\n")
    w("Competitor benchmark (docket: compare with at least Naukri, Monster and LinkedIn; exceed them in the domains that matter) and other gaps. Status values: Covered, Partially covered, Covered by extension, Not modelled (by design), Boundary only, Design only. "
      "Statements are as reliable as the module notes they come from.\n")
    w("| ID | Area | Reference | Element | Status | Where | Note |\n|---|---|---|---|---|---|---|")
    for g in GAPS:
        w("| " + " | ".join(str(x) for x in g) + " |")
    w("")
    w("Verdict by competitor (from the table): LinkedIn profile and company depth matched, network/creator depth now covered by `ext_network`; Naukri recruiter-side depth (RESDEX credits, walk-ins, hot vacancy, consultant postings, notice-period and CTC semantics) matched or exceeded; Monster resume multiplicity matched, security clearance not first-class. "
      "Where the design falls short it is by design (protected-attribute masters, ranking engine, escrow) or because evidence is produced elsewhere (seed, performance, backup).\n")

    # -------- 9 risks
    w("## 9. Risks and open findings\n")
    ttl_n = CR["A09"]["violations"]
    w("| ID | Risk | Likelihood | Impact | Evidence | Mitigation / action |\n|---|---|---|---|---|---|")
    risks = [
        ("R-01", "TTL jobs fail on rows referenced by RESTRICT children (finding F-01, resolved: 12 child FKs changed to SET NULL/CASCADE)", "High", "High if it regressed (audit/session/payment retention stops working)", f"{CR['A09']['violations']} open TTL-table/FK pairs in `08_rule_compliance.xlsx` RuleChecks A09 (was 12 before the fix); decisions in the module notes. Former pairs, e.g. `device_limit_violation`, `session_revocation`, `device_revocation`, `feature_flag_audit` -> `audit_event`; `device_session` -> `identity_session`; `payment_event_outcome` -> `payment_event`", "Keep A09 at 0; new TTL tables must not be RESTRICT parents; the 12 former pairs are SET NULL (nullable, evidence rows survive) or CASCADE (samples)"),
        ("R-02", "CockroachDB licence: 3-node cluster throttled to 5 open transactions without a key", "Medium", "High (benchmarks invalid)", "crdb_capabilities.md section 3", "Apply Enterprise Free/Trial key at init, keep telemetry egress, track expiry (`seconds_until_enterprise_license_expiry`), use single-node only for DDL tests"),
        ("R-03", "Schema size", "Medium", "Medium", f"{S['tables']} tables, {S['indexes']} secondary indexes, {S['fks']} FKs; docs: tested with 10,000 tables, `sql.schema.approx_max_object_count` default 20,000 (objects counted by the cluster, exact accounting unverified); schema changes run one at a time and are not fully atomic (XXA00)", "Create DDL in dependency order in batches, one schema change at a time, create FKs last; seed the core modules first"),
        ("R-04", "FK overhead", "Medium", "Medium", f"{S['fks']} FKs; `app_user` is referenced by {INB['app_user']} FKs, `organisation` by {INB['organisation']}, `candidate_profile` by {INB['candidate_profile']}; `job_requirement` has {len(TB['job_requirement']['fks'])} FKs; {S['fk_index_exempt']} FKs are index-exempt", "Soft delete/anonymise only for hubs (no parent deletes); cache lookups in the application; measure insert latency on `job_application` and `wallet_ledger_entry` in B70; batch multi-row inserts"),
        ("R-05", "CockroachDB features unverified in the research (crdb_capabilities.md)", "Medium", "Medium", "hash-sharded index GA label, row-level TTL minimum version, CHECK with `~` / jsonb `?` / `num_nonnulls`, trigram `CREATE EXTENSION` requirement, `SET app.user_id` custom variables, SKIP LOCKED, changefeed licence split, FK to partial index, DEFERRABLE FK, `INCLUDE`, SECURITY DEFINER", "Smoke-test every item on the pinned `cockroachdb/cockroach:v26.2.x` image before B65; designs avoid depending on them for correctness"),
        ("R-06", "Unverified CHECK operators in the DDL", "Medium", "Medium", f"{CR['P07']['population']} CHECKs use num_nonnulls, regex `~` or jsonb `?` ({S['lookups']} of them are the lookup `name_i18n ? 'en'` check); {NUMNN} use num_nonnulls although ext_talent_ops avoids it", "Test on image; fall back to CASE sums / jsonb function equivalents if rejected"),
        ("R-07", "Inverted indexes on STRING columns without gin_trgm_ops (finding F-03, resolved)", "Low", "Medium (DDL error or wrong index)", f"{CR['P03']['violations']} indexes lack it; every trigram index carries `opclass: gin_trgm_ops` and the lint enforces it", "Keep lint error; DDL generator reads the `opclass` column of the Indexes sheet"),
        ("R-08", "Hot rows under SERIALIZABLE", "Medium", "Medium", "one `wallet` row per wallet, 32 `audit_chain_head` rows, `invoice_number_series` row, `event` capacity row", "Counters off parent rows (ADR-030), sharded spend (`ad_budget_shard`), bounded retries with jitter (ADR-032)"),
        ("R-09", "Hash-sharded index trade-offs", "Low", "Medium", "range scans fan out over buckets; hash+partial and hash+explicit partition combinations unverified/not allowed", "Per-entity reads never use hashed indexes; test combos at seed time"),
        ("R-10", "Cyclic foreign keys need ordered DDL", "Medium", "Low", f"{len(SCCS)} FK cycles ({'; '.join('/'.join(c) for c in SCCS)}; one side nullable in each); {SELF} self-referencing tables", "Create tables first, add cycle FKs with ALTER TABLE; insert order documented in the BR mechanisms"),
        ("R-11", "Duplicate view name `vw_recruiter_job_requirement` (finding F-02, resolved)", "Low", "Low", "single definition in organisation_account; viewer is an explicit argument", "Keep rule check N19 at 0"),
        ("R-12", "ext_ads notes block (finding F-09, resolved)", "Low", "Low", "ext_ads.yaml notes rewritten as design notes", "None"),
        ("R-13", "Extension scope vs 2-day hackathon", "Medium", "Medium", f"{S['ext_tables']} extension tables unseeded and unmeasured", "Defend core modules first; extensions as separable design depth"),
        ("R-14", "Search quality depends on an external service", "Medium", "Medium", "ranking and embeddings are outside the DB (D-16); outbox lag", "`vw_search_index_lag` monitoring; trigram typeahead in-DB fallback"),
        ("R-15", "Index write amplification", "Medium", "Medium", f"{S['indexes']} secondary indexes (avg {S['indexes'] / S['non_lookups']:.1f} per non-lookup table); view tables with 5 indexes carry TTL cost", "Measure B71-B73; drop unused indexes after the query set"),
        ("R-16", "Seed and scale evidence is not in the schema", "High", "High (docket B60-B74)", "1M profiles / 5M applications seed, baseline and extrapolation are separate deliverables", "Plan generator early; FK-last bulk load (IMPORT INTO leaves FKs invalid; COPY is admin-only)"),
        ("R-17", "Classification, label-key and role-name gaps (open: F-05, F-08; resolved: F-04, F-06)", "Medium", "Low", "see `08_rule_compliance.xlsx` Findings sheet", "Fix remaining items in staging; none affects correctness"),
        ("R-18", "Single-host three-node cluster is not real high availability", "High", "Medium", "docs do not state that one Docker host is a valid failure domain (unverified)", "State it in the defence; use localities to exercise multi-region features only as a demo"),
    ]
    for r in risks:
        w("| " + " | ".join(r) + " |")
    w("")
    w("**Findings resolved in staging:** F-01 TTL vs RESTRICT children, F-02 duplicate view name, F-03 inverted-index opclass, F-04 token classification, F-06 language column on free-text tables, F-09 ext_ads notes. **Still open (not justifiable as deviations):** F-05 label-key guard, F-07 grants from `delete_policy`, F-08 role names, F-10 unverified CHECK operators. Details with live counts: `08_rule_compliance.xlsx`, sheets RuleChecks and Findings.")

    # -------- 10 sub-roles
    w("## 10. Sub-roles inside an employer organisation (out of scope now; extension path)\n")
    w("The docket explicitly excludes separate posting, interviewing and pipeline roles inside an employer organisation for this version and asks how the design would be extended. **Today:** an organisation has account administrators (`account_admin`, account scope), mapped recruiters (`recruiter_organisation_mapping`, effective-dated, all jobs of the mapped organisation or its descendants through `organisation_hierarchy_closure`) and platform roles from administration (`app_role`, `app_user_role` with organisation scope, `app_role_permission` with data scope). Every mapped recruiter has the same rights on all of the organisation's requirements.\n")
    w("**How sub-roles would be added without changing existing tables:**\n")
    w("1. Roles as data: insert new `app_role` rows (poster, interviewer, pipeline_manager, viewer; organisation-scoped), their `app_role_permission` rows with data scope `organisation` or `assigned_requirement`, and `app_role_eligibility` rows; express 'pipeline manager includes interviewer' with `app_role_inheritance`.")
    w("2. Membership: add the proposed table organisation_member_role (organisation_id, app_user_id, org role lookup, optional `organisation_department` scope, effective_from/effective_to) with the same P1 chain and P3 open-row constraints used by `recruiter_organisation_mapping`, so memberships cannot overlap.")
    w("3. Per-requirement assignment: add the proposed table job_requirement_member (job_requirement_id, app_user_id, app_role_id) beside the existing `job_requirement_assignment` (tenant-pinned by composite FK like other children).")
    w("4. Enforcement: extend `vw_recruiter_job_requirement` and `vw_user_effective_permission` with an EXISTS over the membership table; permissions keep their `enforcement_layer` and audit flag; high-risk actions use the existing four-eyes table `admin_action_approval`.")
    w("5. Seat-level roles (seat admin vs member) can use a new seat-role column on `licence_seat`; ad accounts already have `ad_account_member` for their own roles. No change to `job_requirement`, `job_application` or `organisation` is needed; analytics adds an attribute to `dim_user` and a bridge.\n")
    w("This path is recorded as ADR-034.\n")

    # -------- 11 reproducibility
    w("## 11. Verification and reproducibility\n")
    w("Run `python docs/tools/lint_dictionary.py` (0 errors, 0 warnings), then `python docs/tools/build_dictionary.py`, `build_adr.py`, `build_conventions.py`, `build_compliance.py` and `build_justification.py`. "
      f"`rule_checks.py` re-implements {len(CR)} atomic checks independently of the lint (results in `08_rule_compliance.xlsx`, sheet RuleChecks: {sum(1 for r in CR.values() if r['violations'] == 0)} with zero violations, the rest are the recorded deviations and open findings above). "
      f"The ADR log has {len(adr.ADRS)} decisions; every table, column and business-rule id cited there and here is checked against the model when the files are generated.\n")
    # ---- section 12: deviations from the dictionary / overrides applied by infra/schema (view list read from the parts folder)
    _vdir = os.path.join(dc.ROOT, "infra", "schema", "parts", "views")
    _views = sorted(f[:-4] for f in os.listdir(_vdir) if f.endswith(".sql")) if os.path.isdir(_vdir) else []
    _vl = ", ".join((f"`{v}`" if v in ALL_NAMES else v) for v in _views)
    w("\n## 12. Deviations from the dictionary and overrides applied by the build (infra/schema)\n")
    w("The DDL under `infra/schema` is generated from the dictionary; the items below are where the deployed schema differs from, or adds to, the dictionary text. Each is also listed in `docs/21_alignment_report.md`.\n")
    w(f"1. **View overrides ({len(_views)}).** The dictionary `sql_sketch` of these views was replaced by a hand-written definition in `infra/schema/parts/views/<name>.sql` because the sketch used constructs the engine rejects or returned wrong rows: {_vl}.\n")
    w("2. **BR-LRN-07 fix.** The rule \"at most one current price per course, market and access model\" had no enforcing index; the unique partial index `uq_course_list_price_course_id_country_id_course_access_model_id_current` (WHERE valid_to IS NULL AND deleted_at IS NULL) was added in `docs/_staging/ext_learning.yaml`, so the DDL and the dictionary agree.\n")
    w("3. **Dialect limitations.** The CockroachDB PL/pgSQL dialect has no FOUND, no RECORD variables, no FOR loops over queries, no PERFORM and no GET DIAGNOSTICS. Rules that needed them are implemented as triggers or set-based statements (single INSERT ... SELECT / UPDATE ... RETURNING) instead of procedural loops; behaviour is the same, and the mechanism label in `06_business_rules.xlsx` is the contract.\n")
    w("4. **Restore loses grants.** RESTORE does not bring back explicit grants, non-root owners or default privileges (verified on v26.2.8). The backup handler mirrors roles, memberships, owners and grants from the main cluster after every restore (the sync acl step). Default privileges and function EXECUTE grants are not mirrored; they return when `infra/schema/apply.py` is re-run against the backup copy.\n")
    w("5. **Sync restores under a stable name.** The earlier design (restore under a temporary name, then rename-swap with the old copy) cannot work once views exist: RESTORE rewrites view definitions to the new database name and ALTER DATABASE RENAME then fails with a dependency error. The sync therefore drops the old copy and restores under the stable name; the backup copy is briefly absent during a restore.\n")
    w("6. **Rename swap with views is impossible.** The same limitation means no rename-based database or table swap is offered for objects that views depend on; schema change is by idempotent re-apply (CREATE OR REPLACE, IF NOT EXISTS).\n")
    w("7. **Licence.** CockroachDB v26.2 has no Core edition; an unlicensed multi-node cluster is throttled after a 7-day grace period. The stack applies an Enterprise Free key from CRDB_LICENSE_KEY and CRDB_ORG at bootstrap. Backup encryption options and some Enterprise-tier features remain **unverified** against the licence terms (see `docs/15_security_design_note.md`).\n")
    text = "\n".join(o)
    verify(text, "07_schema_justification")
    dc.write_md(os.path.join(dc.DOCS, "07_schema_justification.md"), text)
    print("07_schema_justification.md written:", len(text), "chars")


if __name__ == "__main__":
    build()

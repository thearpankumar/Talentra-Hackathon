#!/usr/bin/env python
"""Build docs/04_design_conventions.xlsx from the staging model (counts and examples are computed / verified)."""
import os, re, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import design_common as dc
from xl import new_wb, add_sheet

S = dc.stats()
M = dc.model()
TL = M["tables"]
TB = dc.tables()
RES, AVOID = dc.reserved_lists()


def objchk(name):
    ok = any(name == i["name"] for t in TL for i in t["indexes"] + t["constraints"] + t["fks"]) or name in TB or name in dc.view_names()
    if not ok:
        raise SystemExit(f"example object not found: {name}")
    return name


def real(table, col=None):
    dc.must_exist(table)
    if col:
        dc.need_col(table, col)
        return f"{table}.{col}"
    return table


def dom_example(dom, prefer=None):
    """First declared (non-injected) column of the domain in a non-lookup table; returns (table, col, type, checks, default, sens)."""
    cands = []
    if prefer:
        t = TB[prefer[0]]
        for c in t["columns"]:
            if c["name"] == prefer[1]:
                cands.append((t, c))
    else:
        for t in TL:
            if t["is_lookup"]:
                continue
            for c in t["columns"]:
                if c["domain"] == dom and not c.get("injected"):
                    cands.append((t, c))
                    break
            if cands:
                break
    t, c = cands[0]
    chk = [k["expr"] for k in t["constraints"] if k["type"] == "check" and re.search(r"\b" + re.escape(c["name"]) + r"\b", str(k.get("expr")))]
    return t["name"], c["name"], c["type"], chk[:2], c.get("default"), c["sens"]


def count_dom(dom):
    return S["domains"].get(dom, 0)


# ------------------------------------------------------------------------------------------------------------ Naming
def sheet_naming():
    n_idx = S["indexes"]
    ex = lambda a, b: real(a, b)
    rows = [
        ["Tables", "singular snake_case noun; lookups named exactly after the enumeration", "^[a-z][a-z0-9]*(_[a-z0-9]+)*$; last token not a plural -s (lint heuristic with allow-list)",
         real("candidate_profile"), "lint check_ident (snake_case, plural heuristic, reserved word)", S["tables"]],
        ["Columns", "snake_case; no spaces/special characters; abbreviations only from the Abbreviations sheet", "same regex", ex("candidate_profile", "given_name"), "lint check_ident", S["columns"]],
        ["Primary key", "always `id` UUID NOT NULL DEFAULT gen_random_uuid()", "id", ex("job_application", "id"), "dictlib.inject_standard (cannot be declared by authors)", S["tables"]],
        ["Foreign key column (to entity)", "`<referenced table>_id`; a role prefix is allowed (<role>_<table>_id)", "<ref>_id | <role>_<ref>_id", ex("recruiter_organisation_mapping", "recruiter_app_user_id"),
         "lint: fk column must end with '<ref>_id'", S["fks"]],
        ["Foreign key column (to lookup)", "`<lookup>_id`, domain enum_ref, FK ON DELETE RESTRICT", "<lookup>_id", ex("job_application", "application_status_id"), "lint: FK to lookup must be enum_ref; enum_ref must have FK", S["domains"]["enum_ref"]],
        ["External identifiers", "ids that point outside the database are named `*_ref` (or flagged external_ref); every other `*_id` must be an FK", "*_ref", ex("audit_event", "entity_ref"),
         "lint: *_id without FK is an error", S["ref_cols"]],
        ["Boolean flags", "prefix is_/has_/can_/allow_; NOT NULL with stated default", "is_<state>", ex("licence_seat_slot", "is_retired"), "lint: boolean NOT NULL + default", S["domains"]["boolean"]],
        ["Timestamps", "TIMESTAMPTZ (UTC), suffix _at", "*_at", ex("device_session", "last_activity_at"), "lint: *_at must be domain timestamp", S["domains"]["timestamp"]],
        ["Dates", "DATE, suffix _date", "*_date", ex("subscription_period", "period_start_date"), "lint: *_date must be domain date", S["domains"]["date"]],
        ["Range pairs", "start/end pair names from a fixed list with CHECK end >= start", "*_start_date/_end_date, starts_at/ends_at, valid_from/valid_to, effective_from/effective_to ...", ex("recruiter_organisation_mapping", "effective_to"),
         "lint: date range needs CHECK (Rule 3)", sum(1 for t in TL for k in t["constraints"] if k["type"] == "check" and re.search(r"(>=|<=|>)", str(k.get("expr"))))],
        ["Hashes and blind indexes", "*_hash (digest/token hash), *_hmac / *_lookup_hash (keyed HMAC-SHA256 blind index)", "*_hash | *_hmac", ex("candidate_identity_verification", "id_number_hmac"), "review (Sensitive class + pii note)",
         sum(1 for t in TL for c in t["columns"] if re.search(r"(_hash|_hmac)$", c["name"]))],
        ["Counts, amounts", "*_count (INT), *_amount (DECIMAL money with currency), *_percent/_ratio/_rate (range CHECK)", "*_count | *_amount", ex("job_requirement_stat", "application_count"), "lint: money companion currency, percentage range CHECK", S["domains"]["money"]],
        ["Indexes", "idx_<table>_<first column>[_<other columns>]; the suffix must start with the first key column", "^idx_<table>_<col>...", objchk("idx_device_session_app_user_id_started_at"), "lint: prefix + first-column check", S["indexes"] - S["unique_indexes"]],
        ["Unique constraints / unique indexes", "uq_<table>_<columns>; partial uniques end with the predicate meaning (_open, _live, _first)", "^uq_<table>_...", objchk("uq_device_session_device_slot_id"), "lint: uq prefix; soft-delete unique must be partial",
         S["unique_constraints"] + S["unique_indexes"]],
        ["Foreign key constraints", "fk_<table>_<column or role>", "^fk_<table>_...", objchk("fk_subscription_period_prior_period"), "lint: fk prefix", S["fks"]],
        ["Check constraints", "ck_<table>_<what>", "^ck_<table>_...", objchk("ck_licence_max_devices_positive"), "lint: ck prefix", S["checks"]],
        ["Analytics objects (Rule 8)", "dim_ (dimension), fact_ (fact), agg_ (aggregate), bridge_ (many-to-many); reserved for module analytics", "^(dim|fact|agg|bridge)_", "fact_application_event",
         "lint: analytics tables must carry the prefix; other modules must not", S["per_module"]["analytics"]["tables"]],
        ["Views / materialised views (Rule 8)", "vw_<purpose>, mv_<purpose>", "^(vw|mv)_", "vw_candidate_visible_to_user / mv_kpi_summary_monthly", "lint: view names", S["views"] + S["mviews"]],
        ["Partitions / zone configs (Rule 8)", "<table>_p_<key>, <table>_zone (no physical partitions are defined; names reserved)", "<table>_p_<key>", "job_application_p_<region> (reserved)", "design note ADR-017", 0],
        ["Maximum lengths", f"tables <= {S['max_table_name'][0]} chars ({S['max_table_name'][1]}); columns <= {S['max_col_name'][0]} ({S['max_col_name'][1]}); constraint/index names up to {S['max_name'][0]} chars",
         "CockroachDB identifier limit 128 bytes (max_identifier_length, not enforced); PostgreSQL 63", f"{S['names_gt63']} names exceed 63 characters (DEV-06)", "build_adr/08 RuleChecks NAME-LEN", S["names_gt63"]],
        ["Languages", "UI language tags BCP-47 lowercase primary (en, hi, ta, bn, es, ar)", "^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$", real("language", "language_tag"), "CHECK ck_language_tag_format", S["lookups"]],
    ]
    return ["Area", "Convention", "Pattern", "Example (real object)", "Enforced by", "Population"], rows


# ----------------------------------------------------------------------------------------------- Abbreviations
ABBR = {
    "id": "identifier", "i18n": "internationalisation (name_i18n = name per language)", "ip": "Internet Protocol address", "url": "uniform resource locator",
    "etl": "extract-transform-load", "dim": "dimension (analytics)", "agg": "aggregate (analytics)", "kpi": "key performance indicator", "mrr": "monthly recurring revenue",
    "sla": "service-level agreement", "csat": "customer satisfaction score", "dsr": "data subject request", "eeo": "equal employment opportunity", "dpa": "data processing agreement",
    "tpo": "training and placement officer", "ctc": "cost to company (annual compensation, India)", "msp": "managed service provider", "kyc": "know your customer", "ab": "A/B experiment",
    "ad": "advertisement", "cpa": "cost per action", "ctr": "click-through rate", "cta": "call to action", "ci": "confidence interval", "mfa": "multi-factor authentication", "api": "application programming interface",
    "faq": "frequently asked questions", "hq": "headquarters", "ms": "milliseconds", "px": "pixels", "km": "kilometres", "kib": "kibibytes", "os": "operating system", "ror": "Research Organization Registry id",
    "gst": "goods and services tax (India)", "hsn": "harmonised system of nomenclature (goods code)", "sac": "services accounting code (India GST)", "tds": "tax deducted at source (India)", "po": "purchase order",
    "pymk": "people you may know", "rsvp": "event reply (repondez s'il vous plait)", "seo": "search engine optimisation", "og": "open graph (link preview metadata)", "utc": "coordinated universal time",
    "dst": "daylight saving time", "iana": "IANA time-zone database", "iso": "International Organization for Standardization", "iso2": "ISO 3166 alpha-2 country code", "iso3": "ISO 3166 alpha-3 country code",
    "xp": "experience points (learning gamification)", "sms": "short message service", "http": "hypertext transfer protocol", "mime": "multipurpose internet mail extensions type", "fk": "foreign key",
    "lo": "lower-ordered user id of an undirected connection edge", "hi": "higher-ordered user id of an undirected connection edge (lo < hi)", "p10": "10th percentile", "p25": "25th percentile",
    "p50": "median", "p75": "75th percentile", "p90": "90th percentile", "p95": "95th percentile", "cgpa": "cumulative grade point average", "eea": "European Economic Area", "ui": "user interface",
    "hmac": "keyed-hash message authentication code", "e": "electronic (e_sign = electronic signature)", "no": "number (sequence_no, slot_no)", "ttl": "time to live", "avg": "average", "pct": "percent",
    "min": "minimum", "max": "maximum", "ids": "identifiers", "dau": "daily active users", "wau": "weekly active users", "mau": "monthly active users", "scd": "slowly changing dimension",
    "nrr": "net revenue retention", "p": "probability (p_value of an A/B experiment)", "gps": "global positioning system", "pii": "personally identifiable information", "dpdp": "Digital Personal Data Protection Act 2023 (India)",
    "gdpr": "General Data Protection Regulation", "crdb": "CockroachDB", "kms": "key management service",
}


def sheet_abbr():
    tokens = collections.Counter()
    ex = {}
    for t in TL:
        for tok in t["name"].split("_"):
            tokens[tok] += 1
            ex.setdefault(tok, t["name"])
        for c in t["columns"]:
            for tok in c["name"].split("_"):
                tokens[tok] += 1
                ex.setdefault(tok, f"{t['name']}.{c['name']}")
    rows = []
    for k in sorted(ABBR):
        if k in tokens:
            rows.append([k, ABBR[k], tokens[k], ex[k]])
    return ["Abbreviation (token)", "Meaning", "Occurrences in names (computed)", "First use"], rows


# ------------------------------------------------------------------------------------------------------------ Keys
def sheet_keys():
    rows = [
        ["Surrogate key type", "UUID (v4) on every table; gen_random_uuid() default; no sequences, no SERIAL", f"{S['tables']} tables", real("wallet_ledger_entry", "id"), "ADR-002, D-02"],
        ["Natural keys", "UNIQUE constraint or unique index; never the PK. Soft-delete tables use partial unique WHERE deleted_at IS NULL", f"{S['unique_constraints']} unique constraints, {S['unique_indexes']} unique indexes ({S['partial_unique']} partial)", objchk("uq_app_user_email_live"), "Rule 2, ADR-004"],
        ["Foreign keys", "every *_id column has an FK with stated ON DELETE and ON UPDATE; default ON UPDATE RESTRICT", f"{S['fks']} FKs: RESTRICT {S['fk_delete']['RESTRICT']}, CASCADE {S['fk_delete']['CASCADE']}, SET NULL {S['fk_delete']['SET NULL']}", real("licence_seat_slot", "licence_id"), "Rule 2, D-20"],
        ["FK to lookup", "RESTRICT always (lookup rows are retired, never deleted)", f"{S['domains']['enum_ref']} enum_ref columns", real("job_application", "application_status_id"), "ADR-016"],
        ["Composite FK", "target needs a non-partial UNIQUE over exactly the referenced columns; MATCH SIMPLE (NULL part skips the check)", f"{S['fks_composite']} composite FKs", "fk_job_application_requirement (organisation_id, job_requirement_id) -> job_requirement(organisation_id, id)", "D-19, ADR-029"],
        ["FK leading index", "every FK column leads some index/unique; lookup FKs of low-cardinality may declare index_exempt with a reason", f"{S['fk_index_exempt']} FKs index_exempt", objchk("idx_wallet_ledger_entry_payment_order_id"), "D-20 (lint error otherwise)"],
        ["Audit-column FKs", "created_by/updated_by/deleted_by are UUID without FK (D-04)", f"{S['tables']} tables carry created_by", real("audit_event", "created_by"), "ADR-014"],
        ["Hash-sharded indexes", "time-ordered indexes on high/very_high tables: USING HASH WITH (bucket_count = 8|16|32)", f"{S['hash_indexes']} indexes on {S['hash_tables']} tables; buckets 8={S['hash_buckets'][8]}, 16={S['hash_buckets'][16]}, 32={S['hash_buckets'][32]}", objchk("idx_job_application_applied_at_hash"), "ADR-003"],
        ["Idempotency keys", "unique caller-supplied key on every integration or money-moving write", f"{len(S['idempotency_tables'])} tables with *idempotency_key", real("payment_order", "idempotency_key"), "ADR-031"],
        ["Surrogate key exceptions", "analytics dim_date and dim_time_of_day: deterministic UUIDv5 of the ISO date/minute", "2 tables", real("dim_date", "id"), "ADR-020"],
        ["Identifier limits", "STRING(n) always bounded except long_text with unbounded_why", f"{S['long_text_cols']} unbounded columns", real("resume", "body_text") if any(c["name"] == "body_text" for c in TB["resume"]["columns"]) else real("community_post", "body_text"), "Rule 3"],
    ]
    return ["Topic", "Decision", "Computed scope", "Example (real object)", "Reference"], rows


# --------------------------------------------------------------------------------------------------------- Domains
DOMS = [
    ("identifier", "UUID", "FK to non-lookup table or PK", "NOT NULL unless the relation is optional (null_why)", "Internal", ("app_user", "phone_country_id")),
    ("enum_ref", "UUID", "FK to a lookup (no CHECK-IN lists)", "NOT NULL unless optional", "Internal", None),
    ("person_name", "STRING(150)", "no format assumption (any script/word order)", "NOT NULL for the primary display/given name; optional family name NULL = single-name persons", "Personal", None),
    ("free_text", "STRING(n)", "bounded n (<= 8000 or long_text)", "free", "Internal", None),
    ("long_text", "STRING", "only job descriptions, resume bodies, posts; needs unbounded_why", "free", "Personal/Internal", None),
    ("email", "STRING(254)", "col IS NULL OR col = lower(col)  (+ partial unique where it identifies one account)", "NULL only when another identifier exists (phone-only OTP accounts); null_why states it", "Personal", None),
    ("phone", "STRING(16)", "col ~ '^\\+[1-9][0-9]{6,14}$' (E.164) + companion <col>_country_id", "NULL = no phone; (phone IS NULL) = (country IS NULL) CHECK", "Personal", None),
    ("tax_id", "STRING(n)", "validated against tax_id_type (regex, length, checksum) BEFORE hashing; stored as masked value only", "NULL = not yet provided/verified", "Sensitive", None),
    ("money", "DECIMAL(18,4) or DECIMAL(12,2) (scale >= 2)", "col >= 0; companion currency_id/currency_code in same table; min <= max pair CHECK", "NULL = unknown/undisclosed, never 0; a *_disclosure_id lookup says why (D-09)", "Internal", None),
    ("currency_code", "STRING(3)", "col ~ '^[A-Z]{3}$'", "NOT NULL whenever a money amount is present", "Internal", None),
    ("percentage", "DECIMAL(5,2)", "col BETWEEN 0 AND 100", "NULL = not computed yet / zero denominator", "Internal", None),
    ("ratio", "DECIMAL(p,s)", "col >= 0 AND col <= 1 (or stated max)", "NULL = zero denominator (not 0%)", "Internal", None),
    ("date", "DATE", "end >= start for pairs; suffix _date", "NULL end = open-ended/ongoing (null_why)", "Internal", None),
    ("timestamp", "TIMESTAMPTZ", "UTC; suffix _at; end >= start for pairs", "NULL = event not happened yet / unknown (null_why)", "Internal", None),
    ("boolean", "BOOL", "NOT NULL with stated DEFAULT", "NOT NULL", "Internal", None),
    ("latitude", "DECIMAL(9,6)", "col BETWEEN -90 AND 90; geo_source_id companion with (lat IS NULL) = (lon IS NULL) AND source present", "Personal when a home/check-in point", "Personal", None),
    ("longitude", "DECIMAL(9,6)", "col BETWEEN -180 AND 180", "Personal when a home/check-in point", "Personal", None),
    ("ui_label_key", "STRING(150)", "col ~ '^[a-z0-9_.]+$'; never displayed", "NOT NULL", "Public", None),
    ("code", "STRING(n)", "lowercase snake_case (code ~ '^[a-z0-9_]+$') for lookups", "Public/Internal", "Public", None),
    ("url", "STRING(n)", "bounded; scheme validated in the application", "Internal", "Internal", None),
    ("json", "JSONB", "documented schema; lookups: name_i18n ? 'en'", "Internal", "Internal", None),
    ("secret_hash", "STRING(n) | BYTES", "argon2id PHC string or digest; never reversible", "NOT NULL for credentials", "Sensitive", None),
    ("token", "STRING(n) | BYTES", "vendor token, session/refresh token hash, blind index; never the raw secret", "NULL = not issued", "Sensitive (vendor/session tokens) or Personal (hashed query/identifier)", None),
    ("encrypted", "BYTES", "application-layer envelope encryption (AES-GCM) with key reference column", "NULL only when the secret is not set (CHECK pairs key ref)", "Sensitive", None),
    ("quantity", "INT2 | INT4 | INT8", "range CHECK where a business bound exists", "Internal", "Internal", None),
    ("measure", "DECIMAL(p,s) | FLOAT", "non-money numeric score/rate; prefer DECIMAL", "Internal", "Internal", None),
    ("duration", "INTERVAL | INT", "unit in the column name when INT (_minutes, _days)", "Internal", "Internal", None),
    ("ip_address", "INET", "-", "NULL = unknown/not captured", "Personal", None),
    ("time_zone", "STRING(64)", "IANA name; default 'UTC'", "Internal", "Internal", None),
    ("language_tag", "STRING(n)", "BCP-47: ^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$", "Public", "Public", None),
    ("blob", "BYTES", "bounded by application; object-store reference preferred", "Internal", "Internal", None),
    ("time_of_day", "TIME | TIMETZ", "-", "Internal", "Internal", None),
    ("vector", "VECTOR(n)", "AVOID: 0 columns (embedding_ref used instead, ADR-022)", "-", "-", None),
]


def sheet_domains():
    rows = []
    for dom, typ, chk, null, sens, pref in DOMS:
        cnt = count_dom(dom)
        if cnt:
            t, c, ty, checks, default, s_ = dom_example(dom, pref)
            example = f"{t}.{c} {ty}" + (f" DEFAULT {default}" if default else "")
            sample_check = checks[0] if checks else ""
        else:
            example, sample_check = "(not used)", ""
        rows.append([dom, typ, chk, null, sens, cnt, example, sample_check])
    return ["Domain", "CockroachDB type", "Check pattern / rule", "Nullability / null rule", "Default sensitivity", "Columns using it (computed)", "Example (real column)", "Real CHECK on that table"], rows


# ------------------------------------------------------------------------------------------------------------ Audit
def sheet_audit():
    ca = collections.Counter(t["audit"] for t in TL)
    rows = [
        ["id", "UUID PK injected on every table", "DEFAULT gen_random_uuid()", "database", S["tables"], "Rule 2"],
        ["created_at / created_by", "injected when audit is full or created_only", "created_at DEFAULT now(); created_by = app_user.id or system actor 00000000-0000-0000-0000-000000000001; no FK (D-04)", "application on INSERT", S["tables"], "Rule 4"],
        ["updated_at / updated_by", "injected when audit = full", "updated_at DEFAULT now(), set on every UPDATE; no FK", "application on UPDATE", ca["full"], "Rule 4"],
        ["deleted_at / deleted_by", "injected when delete_policy = soft", "NULL = live; default queries filter deleted_at IS NULL; unique natural keys are partial", "application / erasure job", S["delete"]["soft"], "Rule 4, ADR-004"],
        ["audit mode `full`", "business entities, lookups, config, hard-deleted links", "created_* + updated_*", "-", ca["full"], ""],
        ["audit mode `created_only`", "append_only tables (INSERT-only, no UPDATE grants) and a few insert/delete or upsert-by-grain tables", "created_* only. updated_* is omitted on purpose: an immutable row has no update to record",
         "-", ca["created_only"], f"{len(S['created_only_nonappend'])} tables are created_only but not append_only: 17 agg_* upserts (last_refreshed_at records the change) and gig_slot (insert/delete only), see DEV-03"],
        ["Append-only enforcement (P5)", "INSERT/SELECT grants only for application roles; corrections are new rows referencing the original", f"{S['delete']['append_only']} append_only tables", "PRIVILEGE rules (BR-ADM-08, ...)", S["delete"]["append_only"], "Rule 4, Rule 10"],
        ["Tamper evidence", "row_hash/prev_hash hash chain + checkpoints/changefeed to immutable sink", ", ".join(S["hash_chain_tables"]), "audit_writer / ledger txn", len(S["hash_chain_tables"]), "ADR-006, ADR-013"],
        ["Sensitive-action audit", "every permission with is_sensitive writes audit_event; limit breach writes audit_event + device_limit_violation", "audit_event append_only", "access layer", 1, "Section 3, Rule 9"],
        ["Retention", "mandatory `retention` text on every table; TTL tables carry `ttl`", f"{S['ttl_tables']} TTL tables ({S['ttl_append_only']} append_only)", "TTL job / purge_service", S["ttl_tables"], "Rule 4, ADR-025"],
    ]
    return ["Element", "Where applied", "Convention", "Who sets it", "Tables", "Reference"], rows


# ------------------------------------------------------------------------------------------------------ Nullability
def sheet_null():
    per = [[mod, d["columns"], d["nullable"], f"{d['nullable'] * 100 / d['columns']:.1f}%"] for mod, d in S["per_module"].items()]
    rows = [
        ["Default", "every column NOT NULL unless declared null: true with a null_why", f"{S['nullable']} of {S['columns']} columns nullable ({S['nullable'] * 100 / S['columns']:.1f}%)", "lint: nullable needs null_why", ""],
        ["NULL means unknown / not yet provided / open-ended", "stated per column in null_why", real("recruiter_organisation_mapping", "effective_to"), "NULL effective_to = open-ended mapping", ""],
        ["Not applicable is separate", "a lookup or boolean states why a value is absent (D-09)", real("candidate_profile", "current_salary_disclosure_id"), "salary_disclosure: disclosed/undisclosed/not_applicable/negotiable/as_per_industry_standard", ""],
        ["Sentinel values forbidden", "no 0, '', 'NA', 'unknown', 'none' meaning missing", "lint rejects those DEFAULTs", "notice_period_days 0 is genuine; missing is NULL", ""],
        ["Boolean", "NOT NULL with explicit DEFAULT", f"{S['domains']['boolean']} boolean columns", real("licence", "require_ip_allowlist"), ""],
        ["Pairs", "paired nullable columns use equality CHECKs: (a IS NULL) = (b IS NULL)", "e.g. phone / phone_country_id", "ck_app_user_phone_country", ""],
        ["Ratios", "NULL ratio = zero denominator (not 0%)", "analytics aggregates", "agg_funnel_daily", ""],
        ["Analytics unknown members", "facts never hold NULL dimension keys for unknown; placeholder rows differ for unknown vs not_applicable", "dim_placeholder_kind", "dim_candidate placeholder rows", ""],
    ]
    rows += [["Per-module nullable share", "", f"{r[2]} of {r[1]}", r[0], r[3]] for r in per]
    return ["Topic", "Convention", "Computed / source", "Example", "Share"], rows


# ------------------------------------------------------------------------------------------------------ Sensitivity
def sheet_sens():
    def exs(cls, n=4):
        out = []
        for t in TL:
            for c in t["columns"]:
                if c["sens"] == cls and not c.get("injected"):
                    out.append(f"{t['name']}.{c['name']}")
                    if len(out) >= n:
                        return ", ".join(out)
        return ", ".join(out)
    rows = [
        ["Public", "may be shown without restriction (labels, codes, translated names)", "none beyond integrity", "no special logging; cacheable", exs("Public"), S["sens"]["Public"]],
        ["Internal", "available to staff and to the owning organisation (default for business data, ids, counters)", "role-based access, tenant scoping (ADR-011)", "standard audit columns; sensitive actions audited", exs("Internal"), S["sens"]["Internal"]],
        ["Personal", "identifies a person: contact data, names, IPs, behavioural logs, free-text bodies", "access control + audit of reads on bulk paths; masking views for non-owners; storage-layer encryption; anonymise-in-place on erasure (ADR-026); `pii:` note mandatory",
         "reads through views (vw_candidate_profile_recruiter, vw_app_user_public); never in analytics except pseudonymous keys", exs("Personal"), S["sens"]["Personal"]],
        ["Sensitive", "national/tax identifiers, bank/payment references, credentials, MFA secrets, tokens, special-category answers", "hash/token/ciphertext only: argon2id, HMAC blind index, vendor token, AES-GCM envelope with key ref; `pii:` note mandatory; separate DB role (auth_svc / special-category role)",
         "no raw value in logs, audit before/after, changefeeds or analytics; reads audited", exs("Sensitive"), S["sens"]["Sensitive"]],
    ]
    return ["Class", "Definition", "Required handling", "Logging / exposure", "Examples (real columns)", "Columns (computed)"], rows


# ------------------------------------------------------------------------------------------------ Physical objects
def sheet_phys():
    hv = [v["name"] for v in M["views"]][:2]
    rows = [
        ["Table (entity/link/event)", "<singular_noun>", "candidate_education", "^[a-z][a-z0-9_]*$", S["non_lookups"] - S["per_module"]["analytics"]["tables"]],
        ["Lookup", "<enumeration_name> (singular), columns code/name_i18n/sort_order/is_active", "employment_type", "^[a-z][a-z0-9_]*$", S["lookups"]],
        ["Analytics dimension", "dim_<name>", "dim_candidate", "^dim_", sum(1 for t in TL if t["name"].startswith("dim_"))],
        ["Analytics fact", "fact_<name>", "fact_wallet_transaction", "^fact_", sum(1 for t in TL if t["name"].startswith("fact_"))],
        ["Analytics aggregate", "agg_<name>_<grain>", "agg_revenue_monthly", "^agg_", sum(1 for t in TL if t["name"].startswith("agg_"))],
        ["Analytics bridge", "bridge_<a>_<b>", "bridge_candidate_skill", "^bridge_", sum(1 for t in TL if t["name"].startswith("bridge_"))],
        ["View", "vw_<purpose>", "vw_recruiter_job_requirement", "^vw_", S["views"]],
        ["Materialised view", "mv_<purpose> (manual refresh by CREATE SCHEDULE; REFRESH not allowed inside a txn)", "mv_kpi_summary_monthly", "^mv_", S["mviews"]],
        ["Index", "idx_<table>_<col>[_<col>...]; hash-sharded indexes keep the same name (bucket_count in the dictionary)", objchk("idx_job_application_applied_at_hash"), "^idx_", S["indexes"] - S["unique_indexes"]],
        ["Unique constraint / index", "uq_<table>_<cols>", "uq_wallet_ledger_entry_idempotency_key", "^uq_", S["unique_constraints"] + S["unique_indexes"]],
        ["Foreign key", "fk_<table>_<col|role>", "fk_device_session_device_slot_id_licence_seat_id", "^fk_", S["fks"]],
        ["Check", "ck_<table>_<what>", "ck_gig_slot_grid", "^ck_", S["checks"]],
        ["Partition", "<table>_p_<key> (reserved; no explicit partitions, ADR-017)", "job_application_p_in", "<table>_p_<key>", 0],
        ["Zone configuration", "<table>_zone (reserved)", "job_application_zone", "<table>_zone", 0],
        ["Hash-sharded index parameter", "bucket_count in {8,16,32}", "bucket_count = 16", "-", S["hash_indexes"]],
        ["Row-level TTL", "ttl_expire_after / ttl_expiration_expression on table; ttl_job_cron >= gc.ttlseconds", "notification_delivery: 30 days on expires_at", "-", S["ttl_tables"]],
        ["DB roles", "app_rw, auth_svc, audit_writer, audit_reader, purge_svc, changefeed_svc, analytics_ro (administration notes), analytics_reader + analytics_etl (analytics notes: names differ, reconcile), dba_break_glass", "audit_writer: INSERT+SELECT audit_event", "-", 10],
        ["Duplicate view name check", "global uniqueness of view names (a defect when violated)", "vw_recruiter_job_requirement defined in 2 modules", "-", sum(1 for k, v in collections.Counter(v["name"] for v in M["views"]).items() if v > 1)],
    ]
    for r in rows:
        for x in (r[2],):
            m = re.fullmatch(r"[a-z_0-9]+", x.split(":")[0].split(" ")[0])
            if x.startswith(("vw_", "mv_", "dim_", "fact_", "agg_", "bridge_", "idx_", "uq_", "fk_", "ck_")) or r[0] in ("Table (entity/link/event)", "Lookup"):
                name = x.split(" ")[0]
                if name in TB or name in dc.view_names():
                    continue
                if name.startswith(("idx_", "uq_", "fk_", "ck_")):
                    found = any(name == i["name"] for t in TL for i in t["indexes"] + t["constraints"] + t["fks"])
                    if not found:
                        raise SystemExit(f"example object not found: {name}")
    return ["Object", "Naming pattern", "Example (real)", "Regex", "Count (computed)"], rows


# ------------------------------------------------------------------------------------------------------- Patterns
def sheet_patterns():
    rt = S["rules_by_type"]
    rows = [
        ["P1", "CHAIN_FK", "Non-overlapping consecutive periods without exclusion constraints",
         "1) columns sequence_no, prior_period_id, prior_period_end_date; 2) UNIQUE(id, licence_id, period_end_date) as FK target; 3) UNIQUE(prior_period_id) (<= 1 successor); 4) UNIQUE(licence_id, sequence_no); "
         "5) partial UNIQUE (licence_id) WHERE prior_period_id IS NULL (one root); 6) FOREIGN KEY (prior_period_id, licence_id, prior_period_end_date) REFERENCES subscription_period (id, licence_id, period_end_date) ON UPDATE RESTRICT ON DELETE RESTRICT; "
         "7) CHECK (sequence_no = 1) = (prior_period_id IS NULL); 8) CHECK prior_period_end_date IS NULL OR period_start_date >= prior_period_end_date; 9) prior_period_id excluded from UPDATE grants",
         "Each period has one root and <= 1 successor in the same licence, starts at/after its predecessor's end and that copy is frozen by ON UPDATE RESTRICT, so by induction any two periods are disjoint. Gaps allowed, overlap impossible.",
         "subscription_period, recruiter_organisation_mapping", f"{rt['CHAIN_FK']} CHAIN_FK rules", "Only the tail period may be shortened; MATCH SIMPLE skips the FK for the root; cycles excluded by sequence_no + prior <> id"],
        ["P2", "SLOT_TABLE", "Non-overlapping windows (gig assignments + time off) per worker",
         "1) window columns on gig_assignment on a 15-minute grid (CHECK epoch % 900 = 0), end > start, max 31 days; 2) child table gig_slot(gig_worker_profile_id, slot_start_at, gig_assignment_id | gig_time_off_id); "
         "3) UNIQUE(gig_worker_profile_id, slot_start_at); 4) composite FK (gig_assignment_id, gig_worker_profile_id) -> gig_assignment(id, gig_worker_profile_id); 5) CHECK exactly one owner (assignment XOR time off); "
         "6) one multi-row INSERT of the slots in the same transaction as the assignment; 7) nightly re-check count(slots) = expected_slot_count",
         "Any two overlapping windows share at least one 15-minute slot, so the second insert violates the UNIQUE and the transaction aborts.", "gig_slot (gig_assignment, gig_time_off)", f"{rt['SLOT_TABLE']} SLOT_TABLE rules",
         "<= 2,976 rows per assignment; very_high volume with 90-day TTL; hash-sharded slot_start_at index"],
        ["P3", "PARTIAL_UNIQUE", "At most one open / primary / live row", "CREATE UNIQUE INDEX uq_recruiter_organisation_mapping_recruiter_app_user_id_organisation_id_open ON recruiter_organisation_mapping (recruiter_app_user_id, organisation_id) WHERE effective_to IS NULL; same for is_primary, is_active, deleted_at IS NULL",
         "At most one row satisfies the predicate per key; predicates must be immutable (no now()).", "recruiter_organisation_mapping, user_credential, licence_seat, language, wallet", f"{rt['PARTIAL_UNIQUE']} PARTIAL_UNIQUE rules ({S['partial_unique']} partial unique indexes)",
         "A partial unique index cannot be an FK target nor the only arbiter of multi-arbiter ON CONFLICT"],
        ["P4", "TXN_LOCK", "Serialise a contended parent row, then check, then write", "BEGIN; SELECT ... FROM licence_seat ... FOR UPDATE; check counts; INSERT; COMMIT; retry the whole txn on SQLSTATE 40001 (5 attempts, jittered)",
         "Ordering and low retry rate; correctness still from SERIALIZABLE + unique index (FOR UPDATE is best-effort under SERIALIZABLE).", "licence_seat, licence, wallet, audit_chain_head, invoice_number_series, event", f"{rt['TXN_LOCK']} TXN_LOCK rules",
         "Optional enable_durable_locking_for_serializable; SKIP LOCKED is unverified/gated"],
        ["P5", "APPEND_ONLY (PRIVILEGE)", "Immutable history, ledger, audit", "REVOKE UPDATE, DELETE ... FROM app_rw, audit_writer (INSERT/SELECT only); row_hash = SHA-256(fields || prev_hash); CHECK (chain_seq = 1) = (prev_hash IS NULL); checkpoint + changefeed to object-lock sink",
         "Application roles cannot modify rows; tampering is detectable by the hash chain verifier.", ", ".join(S["hash_chain_tables"]), f"{rt['PRIVILEGE']} PRIVILEGE rules; {S['delete']['append_only']} append_only tables",
         "Owner/admin retain privileges; TTL job deletes rows as owner (DEV-04)"],
        ["P6", "TTL", "Retention of logs and caches", "ALTER TABLE t SET (ttl_expiration_expression = 'expires_at', ttl_job_cron = '@daily'); or ttl_expire_after = '30 days' with a timestamp column; leaf tables only",
         "Expired rows are deleted by a background job; readers filter on the expiration column in the meantime.", "notification_delivery, provider_webhook_event, identity_session, login_attempt, gig_slot", f"{rt['TTL']} TTL rules; {S['ttl_tables']} TTL tables",
         "Inbound FK RESTRICT makes the job fail; secondary indexes slow it; expire_after on an existing table rewrites it"],
        ["D-13", "Licence device limit", "Max devices per user enforced at login", "licence(max_users, max_devices_per_user CHECK > 0) -> licence_seat_slot(1..max_users) -> licence_seat -> device_slot(1..max_devices_per_user) -> device_session claims device_slot with partial unique uq_device_session_device_slot_id WHERE ended_at IS NULL; "
         "login txn: SELECT ... FOR UPDATE OF licence_seat; release expired; pick free slot or deny/replace; on breach INSERT device_limit_violation + audit_event + login_attempt(device_limit_denied) and COMMIT",
         "Slots make it impossible to hold more active sessions than slots even if the lock is skipped.", "licence, licence_seat_slot, licence_seat, device_slot, device_session, device_limit_violation", "BR-LIC-01..08, 14", "Slot rows must follow limit changes (retire, never delete)"],
        ["D-14", "Wallet ledger", "Append-only credits ledger with reversal and idempotency", "wallet(credit_balance >= 0, ledger_sequence); wallet_ledger_entry UNIQUE(wallet_id, sequence_no), UNIQUE(idempotency_key), UNIQUE(reverses_wallet_ledger_entry_id), "
         "composite self FK (wallet_id, prev_sequence_no) -> (wallet_id, sequence_no); txn: replay check, SELECT wallet FOR UPDATE, check balance/limits, INSERT entry, UPDATE wallet, COMMIT; retry on 40001",
         "Balance can never go negative (CHECK), a retried request returns the original row (UNIQUE idempotency_key), a row is reversed at most once.", "wallet, wallet_ledger_entry, wallet_expiry_bucket, wallet_bucket_consumption, profile_download", "BR-SUB-08", "Hot wallet row; one writer per wallet"],
        ["P-OUTBOX", "Transactional outbox", "Reliable events to search/notification services", "INSERT outbox row (dedupe_key UNIQUE, payload_version) in the same txn as the source row; relay claims with lease columns; changefeed alternative", "Atomic with source write; idempotent consumers.", "search_index_outbox", "ADR-023", "Lease claim by UPDATE (SKIP LOCKED unverified)"],
        ["P-COMPOSITE", "Composite FK pinning", "Child provably in same tenant/state/transition", "UNIQUE(organisation_id, id) on parent; FOREIGN KEY (organisation_id, job_requirement_id) REFERENCES job_requirement (organisation_id, id)", "Cross-tenant child rows are rejected by the FK.", "job_application, candidate_shortlist_item, gig_assignment", f"{S['fks_composite']} composite FKs", "extra unique index per parent"],
    ]
    # verify every named table
    for r in rows:
        for tname in re.split(r"[,()/ ]+", r[5]):
            if tname and "_" in tname and tname.islower() and not tname.startswith("br") and tname not in TB and tname not in ("adr_023",):
                if tname.startswith(("adr",)) or tname in ("p_outbox",):
                    continue
                raise SystemExit(f"pattern table unknown: {tname}")
    return ["Id", "Name", "Problem solved", "Recipe (DDL / transaction steps)", "Invariant / proof", "Used by (real tables)", "Business rules / counts", "Limits and cost"], rows


# --------------------------------------------------------------------------------------------------- Reserved words
def sheet_reserved():
    tnames = {t["name"] for t in TL}
    cnames = {c["name"] for t in TL for c in t["columns"]}
    vnames = {v["name"] for v in M["views"]}
    hits_r = sorted((tnames | cnames | vnames) & RES)
    hits_a = sorted((tnames | cnames | vnames) & AVOID)
    hits_c = sorted((tnames | cnames | vnames) & set(dc.CRDB_DOC_AVOID))
    rows = [
        ["reserved_words.txt (PG + CockroachDB + MySQL 8 union)", len(RES), "must not be used as identifiers (lint ERROR)", len(tnames) + len(cnames) + len(vnames), len(hits_r), ", ".join(hits_r) or "none"],
        ["avoid_words.txt (risky non-reserved)", len(AVOID), "lint WARN; qualify the name (event_date, subscription_plan)", len(tnames) + len(cnames) + len(vnames), len(hits_a), ", ".join(hits_a) or "none"],
        ["CockroachDB docs 'avoid' list (crdb_capabilities.md row 22)", len(dc.CRDB_DOC_AVOID), "user, order, group, table, check, end, from, limit, offset, primary, references, unique, value (grammar list itself not enumerated in the research: unverified)", len(tnames) + len(cnames) + len(vnames), len(hits_c), ", ".join(hits_c) or "none"],
        ["Distinct identifiers checked", len(tnames) + len(cnames) + len(vnames), f"{len(tnames)} table names + {len(cnames)} distinct column names + {len(vnames)} distinct view names", "", "", ""],
        ["Quoted identifiers", 0, "none required: no identifier needs double quotes; MySQL/PostgreSQL reserved-word union is checked, not only CockroachDB", "", "", ""],
    ]
    return ["List", "Size (words / identifiers)", "Treatment", "Identifiers checked", "Hits", "Hit identifiers"], rows


def main():
    wb = new_wb()
    for title, fn, wrap, widths in (
        ("Naming", sheet_naming, ["Convention", "Pattern", "Enforced by", "Example (real object)"], [28, 55, 45, 45, 40, 12]),
        ("Keys", sheet_keys, ["Decision", "Computed scope", "Example (real object)"], [26, 70, 50, 50, 24]),
        ("Domains", sheet_domains, ["Check pattern / rule", "Nullability / null rule", "Example (real column)", "Real CHECK on that table"], [16, 28, 60, 30, 18, 14, 40, 60]),
        ("Audit", sheet_audit, ["Where applied", "Convention", "Reference"], [28, 50, 60, 24, 10, 50]),
        ("Nullability", sheet_null, ["Convention", "Computed / source", "Example"], [34, 60, 40, 50, 10]),
        ("Sensitivity classes", sheet_sens, ["Definition", "Required handling", "Logging / exposure", "Examples (real columns)"], [14, 50, 60, 50, 60, 14]),
        ("Physical objects", sheet_phys, ["Naming pattern", "Example (real)"], [28, 60, 50, 22, 14]),
        ("Patterns", sheet_patterns, ["Problem solved", "Recipe (DDL / transaction steps)", "Invariant / proof", "Used by (real tables)", "Business rules / counts", "Limits and cost"], [10, 22, 36, 90, 50, 40, 28, 40]),
        ("Reserved words", sheet_reserved, ["Treatment", "Hit identifiers"], [50, 16, 70, 18, 8, 30]),
        ("Abbreviations", sheet_abbr, ["Meaning"], [20, 60, 18, 40]),
    ):
        hdr, rows = fn()
        ws = add_sheet(wb, title, hdr, rows, widths=widths, wrap_cols=wrap)
    ws = wb["Patterns"]
    for r in range(2, ws.max_row + 1):
        ws.row_dimensions[r].height = 190
    wb.save(os.path.join(dc.DOCS, "04_design_conventions.xlsx"))
    print("04_design_conventions.xlsx written; sheets:", wb.sheetnames)


if __name__ == "__main__":
    main()

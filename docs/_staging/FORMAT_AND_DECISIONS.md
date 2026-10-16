# Staging contract for module agents (READ FULLY)

Each module agent writes ONE OR MORE YAML files `docs/_staging/<module_key>.yaml` (do not edit other agents' files, REGISTRY.yaml,
or docs/tools/*; report tooling bugs in your final message instead). Run `python docs/tools/lint_dictionary.py docs/_staging/<your>.yaml`
until it reports **0 errors** (warnings: fix unless clearly justified). The lint is the arbiter of rules; this document explains them.
Docket = `talentra_docket.md` (Section 5 rules are mandatory). Engine = **CockroachDB (PostgreSQL dialect), v25.x/26.x**.

## 1. File format
```yaml
module: profile                    # one of the module keys in docs/tools/dictlib.py MODULES (master_data, profile, jobs_applications, administration,
                                   # organisation_account, gig_marketplace, licences_devices, sourcing_consultant, subscriptions_payments,
                                   # notifications_community, analytics, tier3_reference)
notes: |                           # markdown: design decisions, trade-offs, deviations, extension points for THIS module (feeds 07_schema_justification)
lookups:                           # enumerations (Rule 3). Expanded automatically to tables with code/name_i18n/sort_order/is_active
  - name: employment_type          # singular snake_case; the table is called exactly this
    description: ...
    values:                        # code lowercase snake_case; `en` mandatory; add `hi` (Hindi) and others when you can; sort optional
      - {code: permanent, en: Permanent, hi: स्थायी, sort: 10}
tables:
  - name: candidate_education      # singular snake_case, no reserved words (see docs/tools/reserved_words.txt)
    kind: entity                   # entity|link|event|ledger|audit|snapshot|config|dim|fact|agg|bridge|staging
    delete_policy: soft            # soft (deleted_at+deleted_by auto-added) | hard | append_only (ledger/audit/history; no UPDATE/DELETE grants)
    volume: medium                 # low|medium|high|very_high  (high >= ~10M rows at laptop scale -> needs a hash-sharded index or no_hash_why)
    tier: "1"                      # docket tier: "1","2","3" or "X" (extra beyond docket, e.g. LinkedIn/Naukri depth)
    milestones: [B4, B12]          # backlog IDs this table serves (B1..B82)
    retention: "...."              # REQUIRED: retention/deletion rule (erasure on request, inactivity purge, TTL, legal hold)
    description: "..."
    audit: full                    # default full; append_only defaults to created_only. NEVER declare id/created_*/updated_*/deleted_* yourself.
    ttl: {expire_after: "90 days", column: expires_at}   # optional CockroachDB row-level TTL
    columns:                       # declared (non-standard) columns only
      - name: institution_name
        type: STRING(200)          # CockroachDB type; STRING(n) always bounded unless domain long_text
        domain: free_text          # see 3. Domains
        null: false                # default false
        null_why: ""               # REQUIRED if null: true (state what NULL means: unknown vs not-applicable)
        default: null              # SQL expr string e.g. "'draft'" "false" "now()" "0"
        sens: Personal             # Public|Internal|Personal|Sensitive   (Personal/Sensitive need `pii:` treatment text)
        pii: ""                    # e.g. "access-controlled+audited; masked for non-owner recruiters", "AES-GCM app-layer encryption + HMAC blind index"
        desc: "..."
        computed: "lower(email)"   # optional computed column expr; stored: true (default) -> STORED, false -> VIRTUAL
        unbounded_why: ""          # required for domain long_text
        external_ref: false        # true only for *_id that points outside the DB (avoid; prefer name *_ref)
    constraints:                   # unique (non-partial) and check
      - {name: uq_candidate_education_x, type: unique, columns: [a, b], desc: ...}
      - {name: ck_candidate_education_dates, type: check, expr: "end_date IS NULL OR end_date >= start_date", desc: ...}
    indexes:                       # secondary & partial-unique indexes (a unique index with `where` goes here, name uq_...)
      - {name: idx_candidate_education_candidate_profile_id, columns: [candidate_profile_id], desc: ...}
      - {name: idx_x_created_at, columns: [created_at], hash: true, bucket_count: 16, desc: ...}   # hash-sharded (CRDB `USING HASH WITH (bucket_count=16)`)
      - {name: uq_x_email, columns: [email], unique: true, where: "deleted_at IS NULL", desc: ...}
      - {name: idx_x_attrs, columns: [attrs], inverted: true, desc: ...}                          # JSONB inverted index
      - {name: idx_x_a_b, columns: [a, "b DESC"], storing: [c], where: "...", desc: ...}
    fks:                           # EVERY *_id column must be an FK; state delete rule
      - {name: fk_candidate_education_candidate_profile_id, columns: [candidate_profile_id], ref: candidate_profile,
         ref_columns: [id], on_delete: CASCADE, on_update: RESTRICT, desc: "child data follows the profile", index_exempt: "reason (only if no leading index)"}
views:                             # optional
  - {name: vw_active_candidate, kind: view, tables: [candidate_profile], purpose: ..., security: "...", sql_sketch: "..."}   # names vw_* / mv_*
business_rules:                    # every enforceable rule of your module (id unique: BR-<MODULE-ABBR>-nn)
  - {id: BR-PRF-01, rule: "...", mechanism_type: CHECK|UNIQUE|PARTIAL_UNIQUE|FK|TXN_LOCK|PRIVILEGE|VIEW|TTL|JOB|SLOT_TABLE|CHAIN_FK|APP_LAYER,
     mechanism: "exact DDL-level description, implementable on CockroachDB", tables: [...], milestone: B20}
```
Tables referenced from other modules: use names in `docs/_staging/REGISTRY.yaml` exactly (those tables are owned by the listed module; if you OWN
a registry table you MUST define it with that exact name). You may add more tables freely, but names must be globally unique.
Every table you own appears in exactly one file. Natural keys -> unique constraints. Table count is a quality target: be **deep and realistic**.

## 2. Binding design decisions (do not re-decide; record module-specific consequences in `notes`)
* **D-01 Engine**: CockroachDB, PostgreSQL dialect. SERIALIZABLE default isolation. No exclusion constraints, no table inheritance, no
  PG ENUM types (we use lookup tables), no reliance on triggers/RLS for correctness (these exist in newer CRDB but are version-gated; may be mentioned as optional hardening).
* **D-02 Keys**: every table `id UUID NOT NULL DEFAULT gen_random_uuid()` as PK (auto-injected). Random UUIDv4 spreads writes across ranges (no hot range). FK columns UUID.
  Time-ordered / sequential-access secondary indexes on high volume tables (created_at, event time, status+time) use **hash-sharded indexes** (`hash: true, bucket_count: 8/16/32`).
  Natural keys (email, national id token, external ref, code) are UNIQUE constraints, never PK. No sequences/SERIAL.
* **D-03 Enumerations**: lookup tables (domain `enum_ref`, FK `<lookup>_id`), never CHECK-IN lists or free text. Lookups are listed under `lookups:`; i18n via auto-column `name_i18n JSONB`.
  Cross-DB-engine caveat: none. UI strings: `ui_label` + `ui_label_translation` (fallback chain: requested language -> language.fallback_language_id -> 'en').
* **D-04 Audit columns**: `created_by/updated_by/deleted_by` are UUID holding app_user.id (system actor `00000000-0000-0000-0000-000000000001`), deliberately **not FK-enforced** (deviation: avoids an FK check+lock per write against the huge app_user range, avoids circular bootstrap; compensated by nightly orphan-check job). Do NOT declare them in `fks`.
* **D-05 Delete policy**: soft = business entities that users/regulation may restore or that history references (deleted_at, deleted_by auto-added; unique natural keys become PARTIAL unique `WHERE deleted_at IS NULL`).
  hard = pure reference/link/derived/cache rows; append_only = history, ledger, audit, downloads, views, login attempts (INSERT-only; corrections are new rows). PII erasure (GDPR/DPDP) = anonymise-in-place job for soft entities (state in `retention`).
* **D-06 Money**: `DECIMAL(18,4)` amounts (`DECIMAL(12,2)` for small prices ok; scale >= 2), companion `currency_id` (FK currency) in the same table. Never FLOAT. Rates/percentages `DECIMAL(5,2)` with range CHECK 0..100.
* **D-07 Phone/email/names/timestamps**: phone `STRING(16)` E.164 + `<col>_country_id`; email `STRING(254)` lowercase CHECK `col = lower(col)` (+ partial unique where it identifies an account); person names `STRING(150)`;
  all timestamps TIMESTAMPTZ (UTC), `*_at`; display time zone as `time_zone` domain (IANA name STRING(64)) where it matters; dates DATE `*_date`.
* **D-08 Ranges**: any start/end pair gets a CHECK `end >= start` (lint enforces known pair names: `*_start_date/*_end_date`, `starts_at/ends_at`, `valid_from/valid_to`, `effective_from/effective_to`, `start_date/end_date`, `from_date/to_date`...). Open-ended = NULL end with null_why "NULL = open-ended/ongoing".
* **D-09 Unknown vs not applicable**: represent "not applicable" by a separate boolean/enum (e.g. `salary_disclosure_id` lookup: disclosed/undisclosed/not_applicable) rather than 0/''/'NA'. NULL = unknown/not yet provided; say so in null_why. Never use sentinel defaults.
* **D-10 Sensitive data classes**: Public/Internal/Personal/Sensitive per column. Sensitive (national id, tax id, bank/payment ref, credentials, MFA secrets, tokens): stored as hash/token/ciphertext only (`encrypted` BYTES with envelope encryption, or `secret_hash` argon2id, `token` vendor token) + `pii:` text saying which.
  Contact details (email/phone/address) = Personal.
* **D-11 Multi-tenancy**: tenant = `organisation`; recruiters act on a company's `job_requirement` through `recruiter_organisation_mapping`. Tenant-scoped tables carry `organisation_id` (and composite unique `(organisation_id,id)` where a child must provably belong to the same tenant -> composite FK).
* **D-12 Patterns for rules without exclusion constraints** (use these names in `mechanism`):
  * P1 CHAIN_FK (non-overlapping consecutive periods, e.g. subscription_period): each period has `sequence_no`, `prior_period_id` (UNIQUE, NULL only for first), `prior_period_end_date` and a composite FK `(prior_period_id, prior_period_end_date) -> (id, period_end_date)` with `ON UPDATE RESTRICT` plus CHECK `period_start_date >= prior_period_end_date` and a partial unique `(licence_id) WHERE prior_period_id IS NULL`; since each period has at most one successor and starts at/after the predecessor's end, overlap is impossible declaratively.
  * P2 SLOT_TABLE (non-overlapping windows e.g. gig_slot): window rows exploded into 15-minute slot rows `(gig_worker_profile_id, slot_start_at)` with UNIQUE; written in the same txn as the assignment; CHECK windows aligned to the 15-min grid and max length.
  * P3 PARTIAL_UNIQUE (one open row): e.g. one active mapping/default/primary: partial unique index `WHERE effective_to IS NULL`/`WHERE is_primary` .
  * P4 TXN_LOCK: `SELECT ... FOR UPDATE` on the parent/limit row inside the txn (CRDB supports FOR UPDATE under SERIALIZABLE), then check, then write. Combine with P2/P3 where a declarative twin exists.
  * P5 APPEND_ONLY: INSERT/SELECT-only grants for app roles + `row_hash`/`prev_hash` hash-chain columns for tamper evidence on audit/ledger + changefeed to immutable sink.
  * P6 TTL: CRDB row-level TTL (`ttl_expire_after`, `ttl_expiration_expression`) for session/notification/log retention.
* **D-13 Licence device limit**: `licence_seat` (one per user per licence) -> `device_slot` rows pre-created `slot_no 1..max_devices_per_user`; each active `device_session` claims a slot: partial unique `uq_device_session_device_slot_id WHERE ended_at IS NULL`; login txn does `SELECT licence_seat ... FOR UPDATE`, picks a free slot, inserts session; limit breach -> insert audit_event + login_attempt rejection. Session release = set ended_at (+end_reason) (expiry job/TTL, revoke).
* **D-14 Ledger**: wallet_ledger_entry append-only; correction = new row with `reverses_ledger_entry_id` (UNIQUE -> reversed at most once); `wallet` holds current balance + `ledger_sequence` updated in the same txn under `SELECT FOR UPDATE`; unique `(wallet_id, sequence_no)`; unique `idempotency_key`; CHECK balance >= 0.
* **D-15 Gig**: gigs are a SEPARATE subsystem (`gig_*`) sharing master data (`job_category`, `skill`, `country`, `currency`), `organisation`, `app_user`, wallet/payment and notifications; they do not share job_requirement/job_application (different cardinality, window/price semantics, bids one-direction, external fulfilment).
* **D-16 Search**: CockroachDB has no full-text search engine parity with Elasticsearch: use trigram (`USING GIN (col gin_trgm_ops)`) / inverted indexes on JSONB, STORED computed lowercase columns and prefix indexes; heavy ranking is an integration boundary (search service fed by changefeed) — state in module notes where relevant.
* **D-17 PII in free-form**: message bodies / resume text may hold PII: classify Personal, encrypted at rest at storage layer; field-level encryption only for Sensitive.
* **D-18 Naming of physical objects (Rule 8)**: tables singular snake_case; analytics tables `dim_*`, `fact_*`, `agg_*`, `bridge_*`; views `vw_*`; materialised views `mv_*`; partitions/zone configs `<table>_p_<key>` / `<table>_zone`; indexes `idx_<table>_<cols>`, uniques `uq_<table>_<cols>`, FKs `fk_<table>_<col>`, checks `ck_<table>_<what>`.
* **D-19 Composite FKs** to `(id, other_col)` require a non-partial UNIQUE constraint on the referenced table over exactly those columns (declare it).
* **D-20 FK leading index**: every FK column must lead some index/unique (CRDB does not auto-index referencing columns well); lint errors otherwise.

## 3. Domains (lint-checked type mapping) — use exactly
identifier=UUID (PK/FK to non-lookup) | enum_ref=UUID (FK to lookup) | person_name=STRING(150) | free_text=STRING(n) | long_text=STRING (needs unbounded_why) |
email=STRING(254) | phone=STRING(16) | tax_id=STRING(n) [Sensitive] | money=DECIMAL(p,s>=2) | currency_code=STRING(3) | percentage/ratio=DECIMAL(p,s) +range CHECK |
date=DATE (`*_date`) | timestamp=TIMESTAMPTZ (`*_at`) | boolean=BOOL NOT NULL +default | latitude/longitude=DECIMAL(9,6) +range CHECK + `geo_source_id` column |
ui_label_key=STRING(150) | code=STRING(n) | url=STRING(n) | json=JSONB | secret_hash=STRING(n)/BYTES | token=STRING(n)/BYTES | encrypted=BYTES | quantity=INT2/4/8 |
measure=DECIMAL/FLOAT (non-money numeric score/rate; prefer DECIMAL) | duration=INTERVAL/INT | ip_address=INET | time_zone=STRING(64) | language_tag=STRING(n) | blob=BYTES | time_of_day=TIME | vector=VECTOR(n) (avoid).

## 4. Quality expectations
* Realism: model like production LinkedIn/Naukri/Monster/Indeed schemas (web-research public data coverage, engineering blogs, API docs); capture the columns real systems keep.
* Every column: desc; every nullable column: null_why; every Personal/Sensitive column: pii.
* Per table 6-25 columns typical (beyond standard), meaningful indexes (not only FK indexes): search/filter/sort patterns, partial indexes for "active"/"open" rows, hash-sharded for high volume time ordered.
* `retention` is mandatory on every table (lookups included -> inherited text auto). `milestones` list the B-ids the table serves.
* Count target for your module is in your brief; hitting it with *meaningful* tables (not padding) is part of the bar.
* Keep `notes` to concrete decisions (<= 120 lines): entity list rationale, competitor coverage observations (LinkedIn/Naukri/Monster), deviations, extension points (e.g. sub-roles inside employer org).
* When finished, final message to caller: table counts (incl. lookups), lint result, list of registry names you rely on from other modules, open questions. <= 250 words.

#!/usr/bin/env python
"""Build docs/02_adr_log.xlsx and docs/02_adr_log.md from the staging model.

Every numeric statement is computed from docs/_staging (design_common.stats()); every table / view / column / business
rule named in an ADR is verified to exist (the script aborts otherwise).  Re-run after any staging change:
    python docs/tools/build_adr.py
"""
import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import design_common as dc
from xl import new_wb, add_sheet

S = dc.stats()
PM = S["per_module"]
RULES = {r["id"]: r for r in dc.model()["rules"]}
TB = dc.tables()
ADRS = []


def adr(id_, title, context, decision, alternatives, reason, trade_off, consequences, status, ms, tables, brs=(), cols=()):
    dc.must_exist(*tables)
    for c in cols:
        t, col = c.split(".")
        dc.need_col(t, col)
    for b in brs:
        if b not in RULES:
            raise SystemExit(f"unknown business rule {b}")
    assert status in ("Accepted", "Proposed", "Superseded")
    ADRS.append(dict(id=id_, title=title, context=context, decision=decision, alternatives=alternatives, reason=reason,
                     trade_off=trade_off, consequences=consequences, status=status, date=dc.TODAY,
                     milestones=", ".join(ms), related_tables=", ".join(tables),
                     related_rules=", ".join(brs)))


hb = S["hash_buckets"]
D = S["delete"]
fkd = S["fk_delete"]
rt = S["rules_by_type"]

# ---------------------------------------------------------------------------------------------------------------
adr("ADR-001", "Engine: CockroachDB v26.2 (PostgreSQL dialect) instead of PostgreSQL or MySQL",
    "Docket Section 1 and backlog B1 ask the team to fix a provider (PostgreSQL or MySQL) and justify it. The platform has a stated target of "
    "10M candidate profiles, 1M job requirements and 100M applications, concurrent logins that must respect device limits, an append-only "
    "money ledger and 24x7 notification/audit write streams.",
    "Use CockroachDB, PostgreSQL dialect/wire protocol, assumed target v26.2 (Regular GA 26.2.0-26.2.6; LTS from v26.2.7, maintenance to 2027-09-23). "
    "The image tag must be pinned to the latest 26.2.x patch after checking Docker Hub (exact tag unverified in crdb_capabilities.md). "
    "DDL stays in the PG dialect; CockroachDB-specific constructs are limited to: hash-sharded indexes, row-level TTL, STORING indexes, "
    "STRING/INT2/INT4/INT8 type spellings and `CREATE INDEX ... USING GIN`.",
    "(A) PostgreSQL 16/17 single primary with declarative partitioning (and Citus/read replicas for scale); (B) MySQL 8 InnoDB; "
    "(C) CockroachDB v26.3 (Innovation release, shorter support, no Assistance Support).",
    "SERIALIZABLE is the default isolation, which makes the rules without exclusion constraints (device limit, non-overlap, wallet) provable by "
    "unique indexes plus transactions; random UUID keys and hash-sharded indexes remove single-range write hotspots; online schema changes and "
    "multi-node survival match the 24x7 audit/notification workload; PG wire compatibility keeps drivers, ORMs and DDL review familiar. "
    "MySQL was rejected because it has no partial indexes (needed for soft-delete uniques, one-open-row rules and device slots), and PostgreSQL because "
    "write scale beyond one primary needs sharding middleware.",
    "LICENCE CAVEAT (crdb_capabilities.md section 3): binaries from 24.3 are under the CockroachDB Software License; every multi-node self-hosted cluster needs a "
    "licence key (Enterprise Free only if company+affiliates revenue < US$10M, own hardware or own public-cloud account, annual renewal, telemetry required; "
    "30-day Trial otherwise). Without a valid key a cluster is throttled to 5 concurrent open SQL transactions (after 7 days). `cockroach start-single-node` and "
    "`cockroach demo` need no licence. Missing features we design around: exclusion constraints, MERGE (no docs page), CREATE DOMAIN (GA only in v26.3), column-level "
    "privileges, session advisory locks, uuid_v7 (not found in docs); DEFERRABLE FKs and NOT VALID are unverified. The docket names PostgreSQL or MySQL; CockroachDB is "
    "treated as the PostgreSQL family and this is recorded as a deviation (DEV-01 in 08_rule_compliance.xlsx).",
    "Apply the licence key right after `cockroach init`, ensure telemetry egress, prefer Enterprise Free over Trial. Patterns P1-P6 replace missing features "
    f"({rt['CHAIN_FK']} CHAIN_FK, {rt['SLOT_TABLE']} SLOT_TABLE, {rt['PARTIAL_UNIQUE']} PARTIAL_UNIQUE, {rt['TXN_LOCK']} TXN_LOCK business rules). Portability list to stock PostgreSQL: "
    "drop `USING HASH` (use plain btree), replace TTL by a cron DELETE, rename STRING->text/varchar, remove STORING (INCLUDE). Several CHECKs use `~` and jsonb `?` "
    "(crdb_capabilities row 5: must be smoke-tested on the target image).",
    "Accepted", ["B1", "B2"], ["app_user", "licence", "device_slot", "gig_slot", "subscription_period"],
    brs=["BR-LIC-06", "BR-SUB-03", "BR-GIG-01"])

adr("ADR-002", "Primary keys: random UUIDv4 (gen_random_uuid) rather than bigint, UUIDv7 or hash-sharded PKs",
    "Rule group 2 requires a stated, consistently applied surrogate key type. Sequential keys (SERIAL/sequences, time-ordered UUIDs) create a hot range "
    "in a distributed key-value store.",
    f"Every one of the {S['tables']} tables has `id UUID NOT NULL DEFAULT gen_random_uuid()` as PK (injected by dictlib.inject_standard); every FK column is UUID. "
    "No SERIAL, no sequences. Natural keys (email, GSTIN hash, external ref, lookup code) are UNIQUE constraints/indexes, never PKs. Only analytics dim_date and "
    "dim_time_of_day use deterministic UUIDv5 ids (documented exception, see ADR-021).",
    "(A) INT8 `unique_rowid()` (docs: acceptable, not strictly ordered, 48-bit time prefix); (B) sequences with CACHE; (C) UUIDv7/ULID (time-ordered); "
    "(D) `PRIMARY KEY (id) USING HASH` on a bigint.",
    "Docs strongly recommend UUIDv4 and `gen_random_uuid()` spreads inserts over all ranges, so a table never becomes a single-range write hotspot at 100M rows. "
    "UUIDv7 is not available (`uuid_v7()` not found in the v26.2 docs) and docs warn other UUID layouts 'require performance testing to avoid hotspots'. "
    "Sequences are replicated writes and leave gaps on rollback. UUIDs are non-guessable in URLs (no tenant-id enumeration).",
    "16-byte keys inflate every secondary index and FK column versus 8-byte ints (index size and RAM cache hit ratio cost, measured in B70-B74). "
    "Keys carry no creation order, so every time-ordered read needs a real timestamp column and index. `gen_random_uuid()` skips uniqueness checks by default "
    "(`sql.optimizer.uniqueness_checks_for_gen_random_uuid.enabled` is off): relevant only if implicit partitioning (REGIONAL BY ROW) is adopted, see ADR-018.",
    "Gapless human-facing numbers (invoice number) cannot come from keys: invoice_number_series is a locked counter row (BR-SUB-12). Support-ticket numbers are "
    "random base32. Time-range scans always use hash-sharded timestamp indexes (ADR-003).",
    "Accepted", ["B20", "B22", "B33"], ["app_user", "candidate_profile", "job_application", "wallet_ledger_entry", "invoice_number_series", "dim_date"],
    brs=["BR-SUB-12"])

adr("ADR-003", "Hash-sharded secondary indexes for time-ordered columns on high-volume tables",
    f"{S['hv_tables']} tables are marked high/very_high volume. Indexes on created_at, applied_at, occurred_at, expires_at or status+time receive monotonically "
    "increasing keys and would all land in the last range.",
    f"Time-ordered indexes use `USING HASH WITH (bucket_count = n)`: {S['hash_indexes']} hash-sharded indexes on {S['hash_tables']} tables "
    f"(bucket_count 8: {hb[8]}, 16: {hb[16]}, 32: {hb[32]}). Per-entity access paths lead with the random entity id and are plain btrees. "
    f"High/very_high tables without a hash index ({S['hv_no_hash']}) must justify it in `no_hash_why` ({S['no_hash_why']} tables do) because their only access paths are random-id-led.",
    "(A) plain btree on timestamp (hotspot); (B) PK `USING HASH` (keys are already random); (C) explicit PARTITION BY RANGE(month) (no physical partitions chosen, ADR-017); "
    "(D) application-computed shard column.",
    "Documented CockroachDB mechanism; shard column is VIRTUAL (no backfill since v22.1); lint-enforced (warning when a high/very_high table has neither a hash index nor `no_hash_why`).",
    "Range scans over a hashed index fan out to every bucket (small performance hit per docs); more buckets than nodes gives diminishing returns "
    "(job_application uses 32 for an assumed 8-node cluster: tune at deployment); a hash-sharded index cannot carry explicit PARTITION BY key columns. "
    "Hash + partial combinations (e.g. idx_device_session_expires_at WHERE ended_at IS NULL) are flagged in notes to be tested at seed time.",
    "Reports needing a time range must keep the range narrow or read a rollup (agg_* tables, analytics). Retention TTL jobs scan the same hashed index.",
    "Accepted", ["B57", "B70", "B73", "B74"], ["job_application", "notification_delivery", "audit_event", "login_attempt", "device_session", "gig_slot"])

adr("ADR-004", "Delete policy per table: soft, hard or append_only",
    "Rule group 4 requires every entity to be classified soft- or hard-deleted, audit records append-only, and retention stated per entity.",
    f"Each table carries `delete_policy`: soft = {D['soft']} tables (deleted_at/deleted_by injected, natural uniques become partial `WHERE deleted_at IS NULL`); "
    f"hard = {D['hard']} (lookups, links, derived/cache rows, config, mutable snapshots); append_only = {D['append_only']} (history, ledgers, audit, views/downloads, login attempts, facts: "
    "INSERT/SELECT grants only, corrections are new rows). Retention text is mandatory on every table; lookups are retired by is_active, never deleted.",
    "(A) soft-delete everywhere; (B) hard delete + separate archive tables; (C) temporal tables/history-table per entity.",
    "Soft only where users or regulation may restore data or where history rows reference the entity (candidate_profile, job_requirement, job_application, organisation, "
    "wallet); everything else avoids dead rows that slow indexes and TTL. Append-only maps directly to docket rule groups 4 and 10.",
    "Soft tables need partial unique indexes and `deleted_at IS NULL` predicates everywhere (partial predicates cannot use now()); FKs cannot point at a partial unique index, "
    "so soft-deleted parents stay referenceable. 18 tables are `created_only` but not append_only (17 agg_* upserts and gig_slot): they intentionally lack updated_* (see 07, DEV-03).",
    "Physical deletion is performed only by erasure/retention jobs (purge_service role, data_retention_policy), by TTL, or by FK CASCADE from soft parents in bounded batches.",
    "Accepted", ["B20", "B22", "B33", "B59"], ["candidate_profile", "job_requirement", "job_application", "organisation", "wallet", "audit_event", "wallet_ledger_entry",
                                                  "data_retention_policy", "purge_job_run"])

adr("ADR-005", "Gigs are a separate subsystem (gig_*), not a job_requirement variant (D-15)",
    "The docket lists the gig marketplace as the primary revenue category but with different semantics from permanent/staffing jobs: a window and a price, "
    "one-direction bids, external fulfilment, and a non-overlap rule per worker (B35).",
    f"A separate subsystem of {PM['gig_marketplace']['tables']} tables ({PM['gig_marketplace']['entities']} non-lookup) prefixed gig_*, sharing only master data (job_category, skill, country, city, currency, language), "
    "organisation, app_user, wallet/payment and notifications. Reporting is unified by `vw_unified_demand` and analytics `dim_demand_type`/`vw_unified_demand_daily`.",
    "(A) single job_requirement with a job_kind discriminator; (B) shared work_item supertype with gig/job subtype tables (CockroachDB has no table inheritance); (C) separate subsystem (chosen).",
    "A shared table needs 10+ discriminator-dependent nullable columns and weak type-dependent CHECKs; the job pipeline (shortlist, interview, offer, join) is wrong for gigs "
    "(bid -> accept -> external fulfilment); gig-only integrity (price xor rate, rate only completed work, one list entry, slots) is expressible as declarative constraints on gig tables.",
    "Cross-type demand reports need a UNION (mitigated by the view); skill/category matching logic exists twice (mitigated by shared master tables); "
    "a third work type (managed remote workforce) would be a third subsystem and another UNION branch.",
    "Candidate projects reuse one candidate_project table with an exactly-one owner CHECK (candidate or gig worker) so completed gigs become verified projects.",
    "Accepted", ["B26", "B29", "B32", "B35"], ["gig_request", "gig_application", "gig_assignment", "gig_worker_profile", "job_requirement", "job_application", "candidate_project",
                                               "vw_unified_demand", "dim_demand_type"])

adr("ADR-006", "Wallet ledger: append-only, hash-chained, corrections by reversal rows (D-14)",
    "Rule group 10: payment and wallet ledgers are append-only and a correction is a new entry that references the original. Credits are consumed concurrently "
    "(downloads, outreach, postings) so the balance must never go negative and a retried request must not double-charge.",
    "wallet_ledger_entry is INSERT/SELECT-only for application roles. Columns: sequence_no (UNIQUE per wallet), idempotency_key (UNIQUE), "
    "reverses_wallet_ledger_entry_id (UNIQUE: a row can be reversed once), balance_after, prev_hash/row_hash chain. wallet holds credit_balance and ledger_sequence, updated in the same "
    "SERIALIZABLE transaction under `SELECT ... FOR UPDATE`; CHECK credit_balance >= 0 and ledger-entry balance_after >= 0 are the backstops. A composite self-FK "
    "(wallet_id, prev_sequence_no) -> (wallet_id, sequence_no) makes the sequence gap-free. A changefeed ships ledger rows to an immutable sink and a nightly job verifies chains and "
    "`vw_wallet_balance_drift`.",
    "(A) mutable balance + audit log; (B) event sourcing with balance computed on read; (C) ledger without chain; (D) double-entry journal (ADR-007).",
    "Append-only plus unique keys give an auditable, idempotent money trail; lock + CHECK + UNIQUE together mean correctness does not rest on the best-effort FOR UPDATE lock "
    "(docs: under SERIALIZABLE the lock may be lost on lease transfer and the txn then retries with 40001).",
    "The wallet row is a deliberately hot row (one writer per wallet); throughput per wallet is bounded by transaction latency. The hash chain is per wallet (verification cost grows with entries). "
    "Optional `enable_durable_locking_for_serializable` for the billing role.",
    "Download deductions point the other way (profile_download.wallet_ledger_entry_id UNIQUE) because there are no deferrable FKs; the row is created right after the ledger row in the same txn. "
    f"Hash-chain tables in the model: {', '.join(S['hash_chain_tables'])}.",
    "Accepted", ["B41", "B44", "B47", "B38"], ["wallet", "wallet_ledger_entry", "profile_download", "vw_wallet_balance_drift", "refund", "payment_order"],
    brs=["BR-SUB-08"])

adr("ADR-007", "Wallet unit is integer credits, single-entry ledger; double-entry accounting left as an extension",
    "Recruiter products (downloads, outreach messages, postings) are priced in credits that vary by plan and expire in lots; money appears only at purchase.",
    "wallet.credit_balance and wallet_ledger_entry.credits_delta are INT8 credits; one wallet per owner (organisation XOR user, CHECK ck_wallet_one_owner) and currency; "
    "money lives in payment_order/invoice as DECIMAL(18,4) + currency_id (D-06). Lots (wallet_expiry_bucket, wallet_bucket_consumption) give FIFO expiry. "
    "A ledger_account + journal_line pair for full double-entry accounting is an extension point; finance receives a nightly export.",
    "(A) money wallet with FX; (B) full double-entry in the OLTP schema; (C) credits only (chosen).",
    "Integer credits make balance CHECKs and hash chains exact (no rounding), and avoid converting balances across currencies (a pack bought in INR is refunded in INR).",
    "Revenue recognition (deferred revenue, breakage) is not queryable from the OLTP ledger alone; it needs the export or the proposed journal tables.",
    "Status Proposed for the double-entry extension only; the single-entry design is Accepted. Gig escrow and payouts reuse wallet/payment_order (D-15).",
    "Accepted", ["B38", "B44", "B47"], ["wallet", "wallet_expiry_bucket", "wallet_bucket_consumption", "payment_order", "invoice", "credit_rate_card"],
    brs=["BR-SUB-08"])

adr("ADR-008", "Device limit enforcement: pre-created slots plus a seat row lock at login (D-13)",
    "Rule groups 2 and 9: a user must not hold more active sessions than the licence allows devices, the limit must be enforced at login under concurrency, the breach must be "
    "audited, and expired/revoked sessions must not count.",
    "licence has max_users and max_devices_per_user (positive CHECKs). licence_seat_slot pre-creates slots 1..max_users per licence; device_slot pre-creates 1..max_devices_per_user per licence_seat. "
    "Each active device_session claims one device_slot: `uq_device_session_device_slot_id` partial unique WHERE ended_at IS NULL. Login runs in a SERIALIZABLE txn: "
    "`SELECT ... FROM licence JOIN licence_seat ... FOR UPDATE OF s` (locks only that user's seat), releases stale sessions, picks a free slot or applies "
    "device_limit_policy (deny_new or replace_oldest). A breach writes device_limit_violation + audit_event + login_attempt(device_limit_denied) in a txn that commits the evidence. "
    "A session counts iff ended_at IS NULL; revoke sets ended_at/end_reason and writes session_revocation/device_revocation + audit_event.",
    "(A) COUNT(*) check in the application; (B) lock only (no declarative twin); (C) counter column on the licence_seat; (D) exclusion/trigger based approach (unavailable or not relied upon).",
    "The slot unique index cannot be bypassed by an application bug, while the FOR UPDATE row lock gives ordered, low-retry execution and a clean place to record the violation; "
    "locks are per seat so different users never contend.",
    "Slot rows must be maintained when the licence limit changes (BR-LIC-14: the limit-change txn marks surplus slots is_retired, never deleting a held one); lock durability under SERIALIZABLE is best-effort so 40001 retries (max 5, jittered) are part of the protocol.",
    "device_session is very_high volume (TTL 13 months on ended_at; NULL ended_at never expires). Fingerprints are HMAC hashes only. Login history is the view vw_login_history over login_attempt, so there is one write path.",
    "Accepted", ["B36", "B39", "B42", "B45"], ["licence", "licence_seat_slot", "licence_seat", "device_slot", "device_session", "device_limit_violation", "login_attempt", "audit_event",
                                               "device_revocation", "session_revocation", "vw_login_history"],
    brs=["BR-LIC-01", "BR-LIC-02", "BR-LIC-05", "BR-LIC-07", "BR-LIC-14", "BR-LIC-06"])

adr("ADR-009", "Non-overlap without exclusion constraints: chain FK (P1), slot table (P2), partial unique (P3)",
    "Docket rules require non-overlapping subscription periods per licence (rule group 10, B47), non-overlapping gig windows per worker (B35) and effective-dated recruiter mapping (B34). "
    "CockroachDB has no exclusion constraints.",
    f"P1 CHAIN_FK for consecutive periods: sequence_no, prior id UNIQUE, copied prior end date, composite FK (prior_period_id, licence_id, prior_period_end_date) -> "
    "(id, licence_id, period_end_date) ON UPDATE RESTRICT, CHECK start >= prior end, partial unique root. Used by subscription_period and recruiter_organisation_mapping "
    f"({rt['CHAIN_FK']} CHAIN_FK rules in total). P2 SLOT_TABLE for windows: gig_assignment windows on a 15-minute grid are exploded into gig_slot rows UNIQUE(gig_worker_profile_id, slot_start_at) "
    f"in the same txn ({rt['SLOT_TABLE']} SLOT_TABLE rules). P3 partial unique `WHERE effective_to IS NULL` for one open row. P4 FOR UPDATE only adds friendly errors.",
    "(A) range column + application check (race-prone); (B) btree_gist exclusion (not in CockroachDB); (C) triggers (not relied upon, ADR-012); (D) serialisable-only logic.",
    "Each pattern is declarative: the proof for P1 is by induction (each period has <= 1 successor, starts at/after predecessor end, frozen by ON UPDATE RESTRICT); P2 collides on at least one slot "
    "for any overlap, so concurrency is handled by the unique index.",
    "P1 forbids editing a non-tail period end; shortening is allowed only on the tail; lengthening/back-dating uses cancel-and-reissue. P2 costs rows: a 31-day max assignment = 2,976 slots, ~40 slots per typical gig, "
    "TTL keeps 90 days (gig_slot is very_high volume with a hash-sharded slot_start_at index).",
    "gig_slot has no updated_* columns (insert/delete only). vw_subscription_period_overlap must return 0 rows and is part of validation (B66/B79). mentor session overlap is only start-time unique (extension noted).",
    "Accepted", ["B34", "B35", "B47"], ["subscription_period", "recruiter_organisation_mapping", "gig_slot", "gig_assignment", "vw_subscription_period_overlap"],
    brs=["BR-SUB-03", "BR-GIG-01", "BR-ORG-01"])

adr("ADR-010", "Organisation hierarchy cycles prevented by level-increment composite FK",
    "Organisation hierarchy (B28/B34) needs a tree without cycles; no triggers (ADR-012) and recursive-CTE checks race under concurrency.",
    "organisation_hierarchy keeps one row per organisation with hierarchy_level (0..7), parent_level, root_organisation_id and ancestor_path; a self composite FK "
    "(parent_organisation_id, parent_level) -> (child_organisation_id, hierarchy_level) and CHECK hierarchy_level = parent_level + 1 make every edge go down one level exactly. "
    "organisation_hierarchy_closure (ancestor, descendant, depth) is derived in the same transaction and re-checked nightly.",
    "(A) adjacency list + recursive CTE guard; (B) closure table only; (C) nested sets; (D) triggers.",
    "A cycle would need a level to equal itself plus k, which is unrepresentable; the closure gives O(1) subtree reads for vw_recruiter_job_requirement.",
    "Re-parenting a subtree changes descendants' levels: one multi-row UPDATE inside a SERIALIZABLE txn (FK checked at statement end); the 8-level cap bounds it.",
    "The same trick is reused for organisation_department.", "Accepted", ["B28", "B34"],
    ["organisation_hierarchy", "organisation_hierarchy_closure", "organisation_department", "organisation"])

adr("ADR-011", "Multi-tenancy by organisation with composite-FK tenant pinning and view-based recruiter scoping (D-11)",
    "Docket Section 3: a recruiter may only see and act on job requirements assigned to their company; consultants only on requirements open to consultants; privileges must be enforced in the database or access layer.",
    "Tenant = organisation. Tenant-scoped tables carry organisation_id and, where a child must provably belong to the same tenant, a composite FK to the parent's UNIQUE(organisation_id, id) "
    "(job_application -> job_requirement(organisation_id, id); candidate_shortlist_item -> candidate_shortlist_folder). Recruiter access = effective-dated recruiter_organisation_mapping read through "
    "the view vw_recruiter_job_requirement; application roles have no direct SELECT on job_requirement for recruiter personas. Consultants go through consultant_job_openness and "
    "vw_consultant_open_requirement. Authorisation data (app_role, permission, app_role_permission with data_scope) is in the administration module.",
    "(A) schema-per-tenant; (B) database-per-tenant; (C) shared tables + row-level security policies as the only guard; (D) shared tables + tenant column checked by application only.",
    "Shared tables scale to millions of tenants without DDL fan-out; composite FKs make cross-tenant child rows impossible even if the application has a bug; views give a stable, auditable "
    "access path.",
    "Schema-per-tenant isolation and per-tenant backups are lost; row filtering relies on views + grants because CockroachDB has no column-level privileges and RLS is only optional hardening (ADR-012). "
    "A view `vw_recruiter_job_requirement` is defined in both jobs_applications and organisation_account (duplicate name; one must be dropped at integration, see 07 risk R-11).",
    "Every new tenant table needs `uq_<table>_id_organisation_id` for children to pin to. Cross-module mapping/organisation equality is partly view + application enforced (open question recorded in jobs notes).",
    "Accepted", ["B28", "B34", "B33", "B79"], ["organisation", "recruiter_organisation_mapping", "job_requirement", "job_application", "candidate_shortlist_item", "candidate_shortlist_folder",
                                               "job_consultant_openness", "app_role", "permission", "app_role_permission", "app_user_role", "vw_recruiter_job_requirement", "vw_consultant_open_requirement"])

adr("ADR-012", "Triggers and row-level security are optional hardening, never the correctness layer",
    "CockroachDB v26.2 has row-level triggers (GA) and row-level security (GA label unverified), but the licence blanket, version gating and documented bypasses matter.",
    f"No business rule depends on a trigger or RLS policy. All {S['rules']} business rules use CHECK ({rt['CHECK']}), UNIQUE/PARTIAL_UNIQUE ({rt['UNIQUE']}+{rt['PARTIAL_UNIQUE']}), FK/CHAIN_FK/SLOT_TABLE "
    f"({rt['FK']}+{rt['CHAIN_FK']}+{rt['SLOT_TABLE']}), TXN_LOCK ({rt['TXN_LOCK']}), PRIVILEGE ({rt['PRIVILEGE']}), VIEW, TTL, JOB or APP_LAYER ({rt['APP_LAYER']}) mechanisms. "
    "Where a trigger or policy would be a natural fit it is named only as optional hardening in module notes.",
    "(A) triggers for status transitions/overlap; (B) RLS as primary tenant guard; (C) neither (chosen).",
    "RLS is bypassed by FK, UNIQUE and PK checks, TRUNCATE, backup/restore, CDC and LDR/PCR (docs); triggers are row-level only, PL/pgSQL only and TTL deletes fire them; "
    "no feature is verified licence-free in the post-2024 model. Declarative constraints are visible in the dictionary and portable.",
    f"{rt['APP_LAYER']} rules are application-enforced (residual risk register in 07); some cross-table rules need nightly reconciliation jobs ({rt['JOB']} JOB rules).",
    "Status transitions use a composite FK to application_status_transition (ADR-029) instead of a trigger; blocking uses views.",
    "Accepted", ["B33", "B59", "B78", "B79"], ["application_status_transition", "job_application_status_history", "user_block", "organisation_block", "vw_candidate_visible_to_user"])

adr("ADR-013", "Audit trail: append-only, 32 parallel hash chains, changefeed to an immutable sink",
    "Rule group 4: audit records are append-only and application users cannot update/delete them; docket Section 3 requires every sensitive action to be recorded; B59/B78 ask for tamper evidence.",
    "audit_event (very_high, append_only, created_only audit mode) is INSERT/SELECT-only for app_rw/audit_writer (`REVOKE UPDATE, DELETE`, BR-ADM-08). 32 parallel chains: the writer locks "
    "audit_chain_head(chain_no 0..31) FOR UPDATE, assigns chain_seq, copies prev_hash and computes row_hash = SHA-256(canonical fields || prev_hash); CHECK (chain_seq = 1) = (prev_hash IS NULL). "
    "audit_chain_checkpoint anchors signed segments in an object-lock bucket; a changefeed streams audit_event to that bucket; row-level TTL (400 days on occurred_at) runs only after the checkpoint "
    "sink_confirmed_at and when no legal_hold exists (BR-ADM-10, job pauses TTL otherwise).",
    "(A) single global chain (serialises all writes); (B) no chain, grants only; (C) database audit log feature only (not in the data model); (D) per-table audit shadow tables.",
    "32 chains bound contention while keeping per-chain tamper evidence; checkpoints give an external anchor; a single audit table keeps queries uniform (actor, entity, request).",
    "A verifier job is needed (security_event audit_chain_break); TTL deletes run as table owner so the app-role REVOKE does not block them; audit_event.entity_ref/organisation_ref/request_ref are deliberately FK-less (polymorphic or must survive the row).",
    "All modules write audit_event for sensitive actions (device_limit_violation holds audit_event_id FK). The time index idx_audit_event_occurred_at is hash-sharded (16 buckets). "
    "TTL-after-confirmation is a job rule, not a declarative one (limitation recorded in 07).",
    "Accepted", ["B33", "B59", "B78", "B79", "B81"], ["audit_event", "audit_chain_head", "audit_chain_checkpoint", "audit_export", "legal_hold", "security_event", "device_limit_violation"],
    brs=["BR-ADM-08", "BR-ADM-09", "BR-ADM-10"])

adr("ADR-014", "created_by / updated_by / deleted_by are UUIDs without FK enforcement (D-04)",
    "Rule group 4 requires created_by/updated_by on every business table; rule group 2 requires every foreign key to be enforced. A hard FK to app_user on "
    f"{S['tables']} tables would add a lookup per write against the largest identity range and create a bootstrap cycle.",
    f"These three columns are UUID holding app_user.id (system actor 00000000-0000-0000-0000-000000000001) and are not declared in `fks` (lint rejects an FK on them). "
    "FKs that matter for access control (actor_app_user_id, blocker, consent subject, registrar) are enforced. A nightly orphan-check job compensates.",
    "(A) FK on every audit column; (B) store usernames/emails; (C) trigger-populated columns.",
    "Avoids an FK check + lock per write on app_user, removes circular bootstrap (app_user.created_by), keeps hot insert paths (audit, notifications, ledger) cheap.",
    "Orphans are possible until the nightly job finds them; erasure must anonymise app_user rather than delete it so ids stay meaningful.",
    "Recorded as deviation DEV-02 (08_rule_compliance.xlsx). Tables with audit mode created_only carry only created_at/created_by.",
    "Accepted", ["B20", "B33"], ["app_user", "audit_event", "wallet_ledger_entry", "notification_delivery"])

adr("ADR-015", "Internationalisation: name_i18n JSONB on lookups plus ui_label / ui_label_translation with a fallback chain",
    "Rule group 7: UTF-8 end to end, UI strings by key with one row per key and language, defined fallback, user content stored once with its language.",
    f"Lookups carry `name_i18n JSONB` ({{language_tag: text}}, CHECK name_i18n ? 'en'). UI strings live in ui_label (key + source_text) and ui_label_translation UNIQUE(ui_label_id, language_id). "
    "Fallback chain: requested language -> language.fallback_language_id -> default language 'en' -> ui_label.source_text; CHECKs force every non-default language to have a fallback and exactly one default "
    "(partial unique uq_language_is_default). Supported UI languages: en (default), hi, ta, bn, es, ar (rtl). User-entered content is not translated; modules store language_id next to it. "
    f"Seed coverage: {S['lookup_values']} lookup values with English everywhere, Hindi on {S['lookup_values_hi']}.",
    "(A) one translation table per lookup; (B) gettext files outside the DB; (C) PG ENUM labels; (D) i18n JSON only for UI strings.",
    "One JSONB per lookup row keeps 536 lookups from needing 536 translation tables; a language row addition needs no DDL; the chain terminates by construction.",
    "JSONB names cannot be indexed per language cheaply (lookups are tiny); fallback cycles (a->b->a) cannot be CHECKed in CockroachDB: a nightly job and the language admin transaction walk the chain (BR-MST-04).",
    "Hindi is complete for "+f"{S['lookups_fully_hi']} of {S['lookups']} lookups only (coverage gap register in 07). Question/FAQ/notification templates use the same pattern via *_translation tables.",
    "Accepted", ["B1", "B60"], ["language", "ui_label", "ui_label_translation", "faq_item_translation", "notification_template_translation", "quiz_question_translation"],
    brs=["BR-MST-04"])

adr("ADR-016", "Enumerations are lookup tables (536) rather than PostgreSQL ENUM types or CHECK-IN lists (D-03)",
    "Rule group 3: any fixed set of values is held in a lookup table with code, display name per UI language, active flag and sort order; free-text enumerations are not allowed.",
    f"{S['lookups']} lookup tables with {S['lookup_values']} seed values are declared in YAML `lookups:` and expanded to tables with code, name_i18n, sort_order, is_active; "
    "referencing columns use domain enum_ref and a real FK. Rich masters (country, city, skill, qualification, industry, job_role...) are ordinary tables with identifier FKs. "
    "Lookup FKs are ON DELETE RESTRICT; values are retired, never deleted.",
    "(A) PG ENUM types (database-scoped, hard to translate, adding is DDL); (B) CHECK (col IN (...)); (C) free text with application lists.",
    "Adding a value is an INSERT, translations are data, history rows keep resolving, and BI can join labels. 'Closed set drives behaviour' flags can ride on the lookup row "
    "(application_status.stage_group_code, delivery_status.precedence, notice_period.max_days).",
    f"{S['lookups']} extra tables; {S['fk_index_exempt']} of {S['fks']} FKs are `index_exempt` (low-cardinality lookups never deleted, so no child scan needed) which trades FK-delete scans for write cost; "
    "behaviour keyed on lookup codes still needs seed discipline.",
    "A lookup join is one extra point read; hot paths store the FK uuid and cache the lookup table in the application.",
    "Accepted", ["B1", "B60"], ["employment_type", "work_type", "application_status", "gender", "notification_channel", "salary_disclosure", "verification_status"])

adr("ADR-017", "No explicit PARTITION BY; scale through range splitting, hash-sharded indexes and TTL; partition names reserved",
    "B73 asks for the partitioning strategy for application and notification tables; rule group 8 requires a stated pattern for partitions.",
    "No table is explicitly partitioned. CockroachDB splits ranges automatically (range_max_bytes 512 MiB default); high-volume time-ordered access uses hash-sharded indexes (ADR-003) and "
    "retention uses row-level TTL (ADR-025) instead of dropping partitions. If partitions/zone configs are introduced the naming pattern is `<table>_p_<key>` and `<table>_zone` (D-18); "
    "job_application_zone / job_application_p_<region> are the reserved names.",
    "(A) PARTITION BY RANGE(month) on applications and notifications; (B) PARTITION BY LIST(country/region); (C) application-level sharding.",
    "Docs: partition columns must be leading PK columns, single partitions cannot be dropped, partitioning examples need an Enterprise/trial licence, and an index with explicit PARTITION BY cannot be hash-sharded. "
    "TTL + hash-sharded indexes meet the same goals without those restrictions.",
    "No instant bulk drop of an old month (TTL deletes are normal DELETEs, rate-limited, slowed by secondary indexes); geographic data pinning needs ADR-018 later.",
    "Full-scale extrapolation (B74) names job_application board reads (job_requirement_id, application_status_id, applied_at DESC) as the first query to degrade; STORING and keyset pagination mitigate.",
    "Accepted", ["B73", "B74"], ["job_application", "job_application_status_history", "notification_delivery", "notification_delivery_event"])

adr("ADR-018", "REGIONAL BY ROW / multi-region deferred (licence, single-host topology)",
    "job_application and job_requirement could be pinned by country-derived crdb_region; CockroachDB multi-region is GA but regions come only from node localities, survival of region failure needs >= 3 regions and licence blanket applies.",
    "Single-region deployment (3 nodes, localities may be set on one Docker host for testing); REGIONAL BY ROW is a documented extension, not part of the DDL. "
    "When adopted: add crdb_region, switch hash-sharded time indexes to per-region plain indexes (hash-sharded cannot include the region column), and decide on uniqueness checks for gen_random_uuid.",
    "(A) RBR from day one; (B) global tables for reference data; (C) defer (chosen).",
    "Hackathon topology is a single host and the licence is unresolved (crdb_capabilities.md section 3); multi-region would complicate every unique index and FK (RBR FK cannot reference crdb_region).",
    "No data-residency story beyond application routing; cross-region latency and GDPR/DPDP localisation remain an extension.",
    "Proposed for later: uniqueness checks for gen_random_uuid must be enabled for implicitly partitioned tables.",
    "Proposed", ["B73", "B74"], ["job_application", "job_requirement", "country"])

adr("ADR-019", "Analytics in the same cluster (star schema) with follower reads; warehouse export by changefeed as the escape hatch",
    "B49-B58: dimensions, facts, aggregates for the KPIs; the docket has no Section 8 so the team defined K01..K20. An ETL tool is out of scope.",
    f"The star schema lives in the OLTP cluster ({PM['analytics']['tables']} tables: dim_*, fact_*, agg_*, bridge_*, plus {PM['analytics']['views']} views/mviews). ETL and dashboards read with "
    "`AS OF SYSTEM TIME follower_read_timestamp()` (about 4.2 s stale per docs) under a dedicated role and connection pool; aggregates are loaded idempotently by grain "
    "(`INSERT ... ON CONFLICT (grain) DO UPDATE`) behind a data-quality publish gate (agg_data_quality_result); materialised views are refreshed by CREATE SCHEDULE jobs (no incremental refresh in CockroachDB).",
    "(A) separate warehouse (BigQuery/ClickHouse) from day one; (B) analytics on the OLTP tables with ad-hoc queries; (C) same cluster, separate tables (chosen).",
    "One system to defend and no ETL tooling; follower reads keep scans off the write path; facts are append-only and keyed so a changefeed to a warehouse needs no schema change.",
    "OLAP load shares nodes and licence capacity with OLTP; follower reads availability by edition must be verified against the licence in use; mviews are refreshed manually. "
    "Export by changefeed once OLTP p99 is affected (Proposed trigger).",
    "Four accumulating-snapshot facts (fact_application_pipeline, fact_gig_assignment, fact_support_ticket, fact_outreach_recipient) are updated in place and therefore hard-delete / full audit.",
    "Accepted", ["B49", "B52", "B55", "B58"], ["fact_application_event", "fact_application_pipeline", "agg_funnel_daily", "agg_data_quality_result", "dim_etl_run", "dim_etl_watermark",
                                                "mv_kpi_summary_monthly", "vw_unified_demand_daily"])

adr("ADR-020", "Analytics dimensions: SCD2 with hash_diff, placeholder members, deterministic ids for date/time dimensions",
    "B58 asks for slowly changing dimension keys and aggregate refresh rules.",
    "dim_candidate, dim_organisation, dim_user, dim_job_requirement, dim_gig_worker, dim_plan are SCD Type 2 (version_no, valid_from, valid_to, is_current, hash_diff) with partial unique "
    "(natural key) WHERE is_current and CHECKs tying is_current to an open valid_to; facts reference reserved placeholder rows (unknown vs not_applicable are different members, Rule 5). "
    "dim_date and dim_time_of_day ids are deterministic UUIDv5 of the ISO date/minute so loaders resolve them without a lookup.",
    "(A) SCD1 only; (B) snapshot-per-day dimensions; (C) bitemporal tables.",
    "Facts need the version in force at event time; partial unique prevents a concurrent double current version; UUIDv5 ids keep the 'UUID PK everywhere' rule while avoiding lookups on the largest fact loads.",
    "UUIDv5 ids are not random (hot range risk) but the tables are tiny (days/minutes); late-arriving dimension changes split older versions.",
    "Deviation from ADR-002 limited to two tiny dimensions.", "Accepted", ["B58"],
    ["dim_candidate", "dim_organisation", "dim_user", "dim_job_requirement", "dim_gig_worker", "dim_plan", "dim_date", "dim_time_of_day", "dim_application_status"])

adr("ADR-021", "Analytics fact source ids are copies without FKs to OLTP tables",
    "Rule group 2 says every foreign key is enforced. Facts carry degenerate source ids (job_application_id, wallet_ledger_entry_id, ...) from OLTP tables.",
    f"{S['external_ref_cols']} columns are flagged external_ref (32 in analytics plus candidate_activity_log.subject_row_ref); fact-to-dimension and aggregate-to-dimension links ARE enforced (RESTRICT). "
    "Compensating control: per-run reconciliation in agg_data_quality_result (row-count reconciliation fact vs source, orphan dimension keys = 0, grain uniqueness).",
    "(A) FK every source id (locks and checks against hot OLTP ranges); (B) no source ids; (C) copies + reconciliation (chosen).",
    "Analytics must survive OLTP hard deletes, erasure and any future partitioning; loads must not take FK checks against hot ranges; facts are history that must stay stable when a source row is corrected.",
    "Orphans are possible and are found by the reconciliation job, not by the database.", "Recorded as deviation DEV-05.",
    "Accepted", ["B52", "B55", "B58"], ["fact_application_event", "fact_wallet_transaction", "fact_payment", "fact_gig_assignment", "agg_data_quality_result"])

adr("ADR-022", "Search strategy: in-database trigram/inverted indexes for typeahead, external search engine for ranking",
    "Candidate and job search is the most performance-sensitive read; CockroachDB has no Elasticsearch-parity engine (D-16).",
    f"In the database: STORED lower() computed columns with trigram GIN ({S['trgm_indexes']} indexes declare gin_trgm_ops; {S['gin_indexes']} inverted/GIN indexes in total incl. JSONB), B-tree prefix indexes, "
    "partial indexes WHERE is_live/is_searchable, denormalised is_live flags. Outside: ranking, full-text and embeddings are a search service fed by changefeed and by the transactional outbox "
    "(ADR-023); embeddings are referenced (embedding_ref) rather than stored as VECTOR columns.",
    "(A) all search inside CockroachDB (tsvector FTS available but missing ts_rank_cd/setweight/websearch_to_tsquery); (B) external engine only; (C) hybrid (chosen).",
    "Trigram GIN accelerates =, LIKE, ILIKE and % for terms >= 3 chars; heavy ranking needs an engine; blocked/hidden candidates are filtered at query time in the view, never copied to the index.",
    "Trigram has no POSIX-regex acceleration and no collated strings; GIN accelerates `@>` only (jsonb `?` unverified). Every inverted index on a STRING column declares `opclass: gin_trgm_ops` (Indexes sheet, last column; lint-enforced), JSONB inverted indexes have none.",
    "Search-service freshness depends on outbox lag (vw_search_index_lag). Vector index is not used (feature flag, GA label unverified).",
    "Accepted", ["B65", "B70", "B71"], ["job_requirement", "city", "skill", "skill_alias", "organisation", "saved_search", "search_index_state", "search_index_watermark", "vw_search_index_lag",
                                        "vw_candidate_search_document"])

adr("ADR-023", "Transactional outbox and changefeeds for integration events",
    "The search service, notification dispatcher, immutable audit sink and legacy sync need change events without dual writes; CockroachDB has no session advisory locks and SKIP LOCKED is version-gated.",
    "search_index_outbox (dedupe_key UNIQUE, monotonic payload_version, lease claim columns claimed_by_ref/claimed_until_at, partial hash-sharded index on pending rows) is written in the same txn as the source row. "
    "Notification dispatch is driven by a CHANGEFEED on notification inserts with no queue table. Audit and ledger rows ship by changefeed to object-lock storage. "
    "Gig fulfilment uses an outbox-style handoff with handoff_idempotency_key and an immutable inbound gig_fulfilment_event.",
    "(A) dual write app -> DB and app -> queue; (B) polling status columns; (C) outbox + changefeed (chosen); (D) message broker as source of truth.",
    "Atomicity with the business row, idempotent consumers (dedupe_key), no hot UPDATE-per-status loop.",
    "Changefeeds need `kv.rangefeed.enabled` and a licence; Enterprise-vs-core split of changefeeds is unverified; outbox claim uses lease UPDATE because SKIP LOCKED is unverified/gated.",
    "Consumers must be idempotent; outbox rows expire by TTL on expires_at.", "Accepted", ["B57", "B59", "B81"],
    ["search_index_outbox", "search_index_state", "notification", "notification_delivery", "gig_fulfilment_event", "gig_fulfilment_event_attempt", "provider_webhook_event"],
    brs=[])

adr("ADR-024", "PII protection: classes, vendor tokens, envelope encryption and HMAC blind indexes (D-10, D-17)",
    "Rule group 6 and B75-B77: every column classified Public/Internal/Personal/Sensitive; Sensitive data stored encrypted/tokenised/hashed with the approach stated; national ids never in clear text (Section 6).",
    f"Counts (computed): Sensitive {S['sens']['Sensitive']} columns, Personal {S['sens']['Personal']}, Internal {S['sens']['Internal']}, Public {S['sens']['Public']}. Sensitive = secret_hash/token/encrypted domains only. "
    "National identifiers are never stored: candidate_identity_verification keeps vendor_token + id_number_hmac (keyed HMAC-SHA256 blind index, pepper in KMS) + outcome; organisation_identifier keeps "
    "value_encrypted (AES-GCM envelope, key per tenant) + value_lookup_hash + masked_value; tax_registration likewise. Credentials are argon2id with per-row parameters; MFA secrets AES-GCM; tokens and recovery codes are hashed. "
    "Personal columns require access control and audit; message/resume/review bodies are Personal and encrypted at the storage layer (D-17); field-level encryption only for Sensitive data.",
    "(A) encrypt everything at field level; (B) plaintext with access control; (C) pgcrypto encrypt()/decrypt() in SQL (enterprise-only on CCL builds, unverified).",
    "Blind indexes give dedupe/lookup without decryption; vendor tokens keep the platform out of PCI/KYC scope; pgcrypto digest/hmac/crypt/gen_salt are available but encrypt/decrypt are not relied on, so encryption happens in the application with KMS.",
    "Key rotation and KMS availability become operational dependencies; blind indexes allow equality probing if the pepper leaks; no column-level GRANT exists so masking uses views.",
    "Candidate mirrors of phone/email on candidate_profile are Personal plaintext columns (E.164/lowercase CHECKs) for search-card speed (BR-PRF-04). Special-category data is isolated (ADR-036).",
    "Accepted", ["B75", "B76", "B77", "B78", "B81"], ["candidate_identity_verification", "organisation_identifier", "tax_registration", "user_credential", "mfa_factor", "payment_method_token",
                                                    "login_attempt", "candidate_profile", "tax_id_type"],
    cols=["candidate_identity_verification.id_number_hmac", "organisation_identifier.value_lookup_hash", "tax_registration.tax_number_hmac", "login_attempt.identifier_hmac"])

adr("ADR-025", "Retention through CockroachDB row-level TTL on leaf and log tables",
    "Rule group 4: retention and deletion on request/inactivity stated per entity and implemented in the schema or a scheduled process. Section 6: message bodies only as long as audit needs.",
    f"{S['ttl_tables']} tables declare `ttl` (expire_after on a named column, or ttl_expiration_expression on expires_at); {S['ttl_append_only']} of them are append_only. Used only on leaf tables with no inbound FKs "
    "(views, activity log, deliveries, webhook payloads, sessions, search/recommendation logs, gig_slot). Examples: notification_delivery 30 days, identity_session 7 days after expiry, login_attempt 24 months, gig_slot 90 days. "
    "Rollups survive TTL (notification_delivery_daily_summary, 7 years, hash-chained).",
    "(A) cron DELETE jobs; (B) drop partitions; (C) never delete.",
    "TTL is declared next to the table (visible in the dictionary), runs as a background job under the owner, and is documented as GA.",
    "Expired rows stay visible until the job deletes them (readers filter on the expiration column); inbound ON DELETE RESTRICT makes the job fail; secondary indexes slow it; "
    "`ttl_expire_after` on an existing table rewrites the table (use the expression form); TTL job cron should be >= gc.ttlseconds (4h default); TTL deletes fire triggers (26.3).",
    "Deviation DEV-04: system-job deletion on tables classed append_only (grants still deny application roles). Legal hold pauses TTL (ttl_pause) for audit_event.",
    "Accepted", ["B57", "B59", "B73"], ["notification_delivery", "provider_webhook_event", "identity_session", "login_attempt", "gig_slot", "candidate_profile_view", "candidate_activity_log",
                                        "notification_delivery_daily_summary", "audit_event"])

adr("ADR-026", "Erasure by anonymise-in-place with an evidence log",
    "GDPR/DPDP erasure on request and inactivity purge (rule group 4), while audit, ledger and facts reference people by id and must stay consistent.",
    "app_user rows are anonymised in place (erasure_state_id, erasure_requested_at, anonymised_at; id kept); soft entities have PII columns overwritten/NULLed and anonymised_at set; hard multi-value tables "
    "(contact, address, links, preferences) are deleted; append-only history keeps rows pointing at the tombstone with IPs NULLed. candidate_erasure_request is the evidence log (7 years, no content). "
    "Inactivity: last_active_at > 36 months -> 30-day warning -> inactivity_purge request. data_subject_request (30-day SLA), data_retention_policy, purge_job_run and legal_hold drive and gate the jobs. "
    "Analytics dimensions are anonymised in place (natural key replaced by random UUID, is_erased).",
    "(A) hard delete the user and cascade; (B) crypto-shredding with per-user keys; (C) soft delete only.",
    "Keeps FKs and ledgers valid, is idempotent and auditable, and avoids rewriting facts.",
    "Free-text fields in other tables must be covered by the purge list or they leak; backups still hold PII until they age out (RTO/RPO note); legal holds can delay erasure.",
    "ck_candidate_profile_anonymised_deleted ties anonymisation to soft delete; DSR queue index serves SLA.",
    "Accepted", ["B75", "B78", "B81"], ["app_user", "candidate_profile", "candidate_erasure_request", "data_subject_request", "data_retention_policy", "purge_job_run", "legal_hold", "dim_candidate"])

adr("ADR-027", "Five clearly labelled extension modules for a LinkedIn/Naukri-class product suite",
    "The scope was widened beyond the docket's 11 modules to a full product suite; the docket scores depth and competitor coverage but forbids nothing beyond the 11 modules.",
    f"Five modules, all tier X and prefixed by purpose: ext_network ({PM['ext_network']['tables']} tables), ext_matching ({PM['ext_matching']['tables']}), ext_ads ({PM['ext_ads']['tables']}), "
    f"ext_learning ({PM['ext_learning']['tables']}), ext_talent_ops ({PM['ext_talent_ops']['tables']}) = {S['ext_tables']} tables ({S['ext_tables']*100//S['tables']}% of {S['tables']}). They obey every Section 5 rule, reuse core tables "
    "instead of duplicating them (connections reuse user_block, wallet, payment_order, community_follow, candidate_skill_endorsement) and only point into the core; nothing in the 11 core modules depends on them.",
    "(A) stay at the 11 docket modules; (B) fold extension tables into core modules; (C) separate labelled modules (chosen).",
    "Competitor benchmark (LinkedIn, Naukri, Monster) rewards coverage; separation lets the panel review the docket core independently and lets the extensions be dropped as a unit.",
    "Table count and review effort grow; extension tables are not seeded or measured at 10M scale; ext_ads/ext_network contain very-high-volume event tables (impressions, clicks, connections ~2.5B rows target).",
    "07 contains the decision record; coverage-gap register marks extension depth that is design-only. The ext_ads staging `notes` block currently holds a generator script instead of design notes (R-12).",
    "Accepted", ["B2", "B16", "B49"], ["connection", "recommendation_impression", "ad_campaign", "certificate", "staffing_assignment", "user_block", "payment_order", "community_follow"])

adr("ADR-028", "Tier 3 reference domains taken on as a separate optional module (tier3_reference)",
    "Docket Section 7: Tier 3 (salary benchmarks, company ratings, trending snapshots, dashboard aggregates, feature flags) is optional but must be a separate, clearly labelled module with the decision recorded.",
    f"Module tier3_reference ({PM['tier3_reference']['tables']} tables) is taken on, physically separate: only FKs point out of it, so it can be dropped as a unit. It implements the Section 6 boundary 'Company review and salary data' (reference only, "
    "attribution_text, refreshed_at, max_staleness_days) and feature flags with audit through audit_event.",
    "(A) omit Tier 3; (B) place in analytics; (C) separate module (chosen).",
    "Cheap on top of the analytics layer, shows the integration-boundary design, demonstrates feature-flag design.",
    "Reference data can go stale and mislead (mitigated by attribution and staleness hiding); a few FKs assume registry tables are entities rather than lookups.",
    "No dim_/fact_/agg_ prefixes (Rule 8) because it is not analytics.", "Accepted", ["B49"],
    ["salary_benchmark", "company_rating_snapshot", "trending_job_snapshot", "dashboard_aggregate", "feature_flag", "reference_data_source", "reference_data_refresh"])

adr("ADR-029", "Composite foreign keys as the pattern for tenant pinning, state pinning and status transitions (D-19)",
    "Cross-row invariants (child in the same tenant, state belongs to country, history row follows an allowed transition) cannot use CHECKs across tables and triggers are not relied on.",
    f"{S['fks_composite']} composite FKs reference a non-partial UNIQUE over exactly the referenced columns (lint-enforced). Examples: job_application -> job_requirement(organisation_id, id); "
    "job_application_status_history -> application_status_transition (+ self composite FK chain); city -> state_region(country_id, id); device_session -> device_slot(licence_seat_id, id) and licence_seat(id, app_user_id); "
    "gig_assignment -> gig_application(application, request, worker, is_accepted=true); user_block -> block_policy.",
    "(A) triggers; (B) application checks; (C) wide denormalised columns without FK.",
    "Pure declarative integrity; MATCH SIMPLE skips the check when a part is NULL, which is exploited (first period, city-states) and documented.",
    "Referenced tables need extra UNIQUE constraints (index cost); MATCH PARTIAL is unsupported; FK to a partial unique index is impossible; DEFERRABLE FKs are unverified so mutual references use one-directional pointers.",
    "Tenant pinning needs `uq_<t>_id_organisation_id` on every parent.", "Accepted", ["B22", "B35", "B47"],
    ["job_application", "job_application_status_history", "city", "state_region", "device_session", "gig_assignment", "user_block", "block_policy"])

adr("ADR-030", "Column and table-width discipline: counters and statistics off hot rows",
    "Wide, hot rows suffer under SERIALIZABLE (contention, retries) and inflate every index that STORINGs them.",
    f"Average {S['avg_cols_nonlookup']} columns per non-lookup table (max {S['max_cols'][0]} on {S['max_cols'][1]}; {S['cols_gt30']} tables > 30 columns; {S['cols_gt40']} > 40). "
    "Counters live in separate stat tables refreshed in batches: job_requirement_stat, community_post_stat, organisation_rating_summary, gig_worker_rating_summary; "
    "follower counts are not stored (organisation_follow facts + analytics); large authors' feeds are merged on read. Ad spend is spread over 16 shards (ad_budget_shard). Multi-valued attributes are child tables, not arrays or JSONB.",
    "(A) counters on the parent row; (B) triggers maintaining counters; (C) arrays/JSONB for lists.",
    "Parent rows stay stable under concurrent updates; stats can be eventually consistent.",
    "Counts shown to users lag by the batch interval; more tables and joins.", f"{S['json_cols']} JSONB columns exist; each is for payloads/snapshots/criteria, not for entities that need FKs.",
    "Accepted", ["B12", "B14", "B73"], ["job_requirement", "job_requirement_stat", "community_post_stat", "organisation_rating_summary", "gig_worker_rating_summary", "ad_budget_shard", "organisation_follow"])

adr("ADR-031", "Idempotency keys on every integration or money-moving write",
    "Section 6 requires an idempotency answer per boundary; SERIALIZABLE retries (40001) re-run whole transactions.",
    f"{len(S['idempotency_tables'])} tables carry a unique *idempotency_key ({', '.join(S['idempotency_tables'][:8])} ...). Webhooks use UNIQUE(provider, provider_event_ref) / (payment_gateway_id, gateway_event_ref); "
    "loads use `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`; analytics facts carry source_ref and runs carry run_key (dim_etl_run).",
    "(A) application-level dedupe caches; (B) exactly-once queue.",
    "A unique index makes replays harmless and makes whole-transaction retries safe.",
    "ON CONFLICT DO UPDATE allows one arbiter only and expression indexes cannot be conflict targets (CockroachDB); keys must be stable and uniformly distributed.",
    "Duplicate = return the original row.", "Accepted", ["B47", "B57", "B63"],
    ["wallet_ledger_entry", "payment_order", "refund", "profile_download", "notification_delivery", "provider_webhook_event", "payment_event", "gig_fulfilment_event", "organisation_verification", "dim_etl_run"])

adr("ADR-032", "Isolation and retry policy: SERIALIZABLE everywhere, client retry loop on 40001, FOR UPDATE for ordering",
    "CockroachDB defaults to SERIALIZABLE; READ COMMITTED exists (enabled by default, version of introduction unverified) but allows lost updates and write skew.",
    "All business transactions run SERIALIZABLE. The client wraps every transaction in a retry loop on SQLSTATE 40001 (up to 5 attempts with jittered backoff, BR-LIC-06, BR-SUB-08); single-statement implicit transactions rely on server-side retry. "
    "SELECT ... FOR UPDATE is used for ordering contended rows (licence_seat, wallet, audit_chain_head, invoice_number_series, event row for capacity); correctness never depends on it alone.",
    "(A) READ COMMITTED to avoid retries; (B) advisory locks (session-scoped unsupported); (C) optimistic version columns only.",
    "Serializable plus unique indexes is the simplest proof of the no-overlap and wallet rules; locks make queues short.",
    "Lock durability is best-effort under SERIALIZABLE (lost on lease transfer/split/merge -> 40001); `enable_durable_locking_for_serializable` optional; DDL is not fully transactional (XXA00).",
    "All write paths in the seed generator and the API must implement retries; B79 tests must include concurrent logins and wallet debits.",
    "Accepted", ["B45", "B47", "B79"], ["licence_seat", "wallet", "audit_chain_head", "invoice_number_series", "event", "job_application"], brs=["BR-LIC-06", "BR-SUB-08"])

adr("ADR-033", "Naming of physical objects (D-18) and the 63-character index-name deviation",
    "Rule group 1 sets idx_table_column and uq_table_column; rule group 8 requires stated patterns for partitions, views, materialised views and analytics objects.",
    "Tables singular snake_case; analytics tables dim_/fact_/agg_/bridge_ (reserved for the analytics module, lint-enforced); views vw_*; materialised views mv_*; partitions/zones <table>_p_<key>/<table>_zone; "
    "indexes idx_<table>_<cols>, uniques uq_<table>_<cols>, FKs fk_<table>_<col>, checks ck_<table>_<what>. Index/constraint names are built from full column lists so names stay unambiguous.",
    "(A) generic idx_1, idx_2; (B) truncated names.",
    "Self-describing names make plans and constraint errors readable.",
    f"{S['names_gt63']} index/constraint/FK names exceed 63 characters (longest {S['max_name'][0]}). CockroachDB accepts identifiers up to 128 bytes (`max_identifier_length`, not enforced yet) but stock PostgreSQL truncates at 63: rename "
    "before porting (DEV-06). Table names max 37, column names max 39 characters.",
    "A generated rename map is the portability work item.", "Accepted", ["B16", "B20"],
    ["notification_delivery_daily_summary", "recruiter_organisation_mapping", "job_application"], cols=[])

adr("ADR-034", "Sub-roles inside an employer organisation are out of scope; extension path defined",
    "Docket Section 3 explicitly excludes posting, interviewing and pipeline sub-roles inside an employer organisation but requires the design to say how it would be extended.",
    "Today: account admins (account scope), mapped recruiters (all jobs of mapped companies), platform roles from administration (app_role, app_user_role with organisation_id and role scope). "
    "Extension (no change to existing tables): new app_role rows (is_organisation_scoped=true) + app_role_permission rows with data_scope organisation or assigned_requirement + app_role_eligibility rows; "
    "optionally organisation_member_role(organisation_id, app_user_id, org_role lookup {poster, interviewer, pipeline_manager, viewer}, department, effective_from/to) with P1/P3 non-overlap, and "
    "job_requirement_member (job_requirement_id, app_user_id, app_role_id) for per-requirement assignment; role hierarchy via app_role_inheritance; extend vw_recruiter_job_requirement with EXISTS member_role.",
    "(A) model sub-roles now; (B) single flat employer role (chosen for now).",
    "Matches the docket scope and the hooks already exist: organisation_department, job_requirement_assignment, app_role_inheritance.",
    "Until built, every mapped recruiter has the same rights on all jobs of the organisation.",
    "licence_seat.seat_role_id could carry seat-level sub-roles; ext_talent_ops/ext_ads use ad_account_member for their own roles.",
    "Accepted", ["B28", "B33"], ["app_role", "app_user_role", "app_role_permission", "app_role_inheritance", "app_role_eligibility", "organisation_department", "job_requirement_assignment",
                                  "recruiter_organisation_mapping", "licence_seat", "ad_account_member"])

adr("ADR-035", "Money and measures: DECIMAL(18,4) with companion currency_id; percentages and ratios with range CHECKs (D-06)",
    "Rule group 3: money is fixed-precision decimal with a companion currency; percentages and ratios are fixed precision with a stated range.",
    f"{S['domains']['money']} money columns use DECIMAL(p,s) with s >= 2 (lint-enforced) and a currency_id FK (a currency_code column with an ISO CHECK is also accepted by the lint; none remain); {S['domains']['percentage']} percentage and {S['domains']['ratio']} ratio columns carry range CHECKs. "
    "No FLOAT for money. Salary ranges carry min <= max CHECKs; amounts are NULL-able only with a disclosure lookup (ADR-037).",
    "(A) integer minor units; (B) FLOAT; (C) money type.",
    "DECIMAL is exact and readable; companion currency avoids implicit conversion; FX is a dated table (currency_exchange_rate).",
    "Arithmetic across currencies is explicit; scale choices differ (18,4 vs 12,2) by purpose.", "Gig and course prices use DECIMAL(12,2).",
    "Accepted", ["B38", "B44", "B47"], ["currency", "currency_exchange_rate", "payment_order", "invoice_line", "plan_price", "job_requirement", "gig_request"])

adr("ADR-036", "Special-category data isolated with consent, separate role and encryption",
    "Diversity, disability, veteran and equal-opportunity data are special-category (GDPR Art. 9, DPDP purpose limitation, Indian anti-discrimination law).",
    "candidate_diversity_declaration (candidate-side opt-in, linked to a user_consent row, free text encrypted) and equal_opportunity_survey/equal_opportunity_response (employer-side regulatory survey, answers_encrypted with a dedicated key, aggregate reporting with minimum cell size 10 via vw_eeo_aggregate_cell_count) "
    "are isolated tables: the candidate-side table is linked to a user_consent row with the free text field-encrypted and the flags restricted by column note to consented inclusive-hiring programmes; the employer-side survey uses a dedicated KMS key, a separate DB role, audited reads and no FK path from ranking/screening/recruiter views. Master data deliberately has no religion, caste, disability, ethnicity or nationality-of-origin master; drive_eligibility_rule kinds cannot encode protected attributes.",
    "(A) columns on candidate_profile; (B) not collected.",
    "Isolation allows separate retention, access and encryption; omission from master data prevents accidental use in matching.",
    "Reporting needs minimum cell sizes; the application must keep protected attributes out of search ranking (APP_LAYER rule).",
    "Gender and marital status remain optional, hidden by default and excluded from search/ranking.", "Accepted", ["B75", "B77", "B78"],
    ["candidate_diversity_declaration", "equal_opportunity_survey", "equal_opportunity_response", "user_consent", "drive_eligibility_rule", "vw_eeo_aggregate_cell_count"])

adr("ADR-037", "Unknown versus not applicable: disclosure lookups, no sentinel values (D-09)",
    "Rule group 5: nullable columns need written justification; a salary that was not disclosed is not zero; no 0/'NA'/'' sentinels.",
    f"{S['nullable']} of {S['columns']} columns are nullable, every one with null_why (lint). Where the distinction matters a lookup states it: salary_disclosure (disclosed/undisclosed/not_applicable...), "
    "boolean flags with explicit default, closed ranges with NULL = open-ended. Lint rejects sentinel defaults ('', 'na', 'unknown', 'none') and nullable booleans. Analytics uses distinct placeholder members for unknown vs not_applicable.",
    "(A) default 0 / 'NA'; (B) separate boolean per column.",
    "NULL keeps unknown distinct from zero; the lookup gives the reason and drives masking views.",
    "Queries must handle NULL (ratios NULL on zero denominator); more lookups.", "Notice period 0 is a genuine value.",
    "Accepted", ["B14", "B20", "B22"], ["salary_disclosure", "candidate_profile", "job_requirement", "job_application", "agg_funnel_daily"])

adr("ADR-038", "One identity table (app_user); registration policy prevents recruiters registering candidates",
    "Docket Section 3: recruiters cannot register candidates on a candidate's behalf; one person may have several roles; Guest has no account.",
    "app_user is the only identity table with immutable actor_type and role assignment in app_user_role. A composite FK (actor_type_id, registrar_actor_type_id, registration_channel_id) -> registration_policy "
    "means seeded rows permit (candidate, candidate, self_signup), (candidate, admin, admin_created), (candidate, service, legacy_migration) etc.; no (candidate, recruiter, *) row exists so such an INSERT fails with an FK violation. "
    "Email and phone are unique among live rows (partial unique) and may be NULL (phone-only OTP accounts).",
    "(A) separate user tables per actor; (B) application check.",
    "The rule holds even with an application bug; one id space simplifies audit, blocking and wallets.",
    "Row-level semantics of 'recruiter contacts candidate' live in sourcing (outreach), not in identity; registration_policy is seed data that must be reviewed.",
    "Placement-officer student invitations are an extension (policy rows + acceptance step).", "Accepted", ["B24", "B27", "B33"],
    ["app_user", "registration_policy", "actor_type", "app_user_role", "outreach_recipient"], brs=[])

adr("ADR-039", "Blocking and visibility enforced through effective-dated block tables and a visibility view",
    "Docket: a candidate may block employers, recruiters or consultants and a blocked party must not see that candidate (B50/B53/B59).",
    "user_block (blocker, blocked) and organisation_block (candidate <-> company, both directions) are effective-dated (open row = effective_to IS NULL, partial unique); block_policy composite FK pins allowed actor-type pairs. "
    "vw_candidate_visible_to_user is the single predicate: a viewer sees a candidate unless an open user_block or organisation_block applies; outreach recipients are inserted with INSERT ... SELECT ... WHERE NOT EXISTS (blocks) "
    "and recorded in outreach_exclusion; the search export excludes blocked viewers at query time so un-blocking is immediate.",
    "(A) copy blocks into the search index; (B) application filter only; (C) RLS policies.",
    "Point-lookups on partial unique indexes; one auditable predicate shared by search, outreach and messaging.",
    "Services must read candidate data through the view (grants on base tables withheld); high-volume paths do two NOT EXISTS probes.",
    "Messages check user_block in the same transaction as thread creation.", "Accepted", ["B50", "B53", "B59", "B79"],
    ["user_block", "organisation_block", "block_policy", "vw_candidate_visible_to_user", "outreach_exclusion", "outreach_recipient", "message_thread"])

adr("ADR-040", "Polymorphic references use one nullable FK per target plus an exactly-one CHECK; *_ref columns are deliberately FK-less",
    "Several rows point to one of many tables (certificate source, learning assignment target, ledger reference, audit entity). An untyped (type, id) pair cannot be an enforced FK (Rule 2).",
    f"Where the target set is small and stable, one nullable FK per target type and a CHECK that exactly one is set (num_nonnulls or (a IS NULL) <> (b IS NULL)): wallet_ledger_entry (ck_wallet_ledger_entry_single_reference), "
    f"candidate_project, gig_slot. Where the target set is open or must outlive the row (audit_event.entity_ref, notification source_object_ref, analytics source ids), a {S['ref_cols']}-column family of `*_ref` columns "
    "is intentionally not an FK and is paired with a type code; the lint treats `*_id` without FK as an error but `*_ref` as allowed.",
    "(A) polymorphic type+id everywhere; (B) a join table per target.",
    "Keeps every `*_id` enforced while allowing durable, cheap log references.",
    "`*_ref` columns can dangle; the nightly orphan check job covers them. `num_nonnulls` is avoided in ext_talent_ops because it is unverified in CockroachDB docs.",
    "07 lists the FK-less reference families as a Section 5 deviation.", "Accepted", ["B22", "B33", "B59"],
    ["wallet_ledger_entry", "candidate_project", "gig_slot", "audit_event", "notification", "candidate_activity_log"])

adr("ADR-041", "Reference and master data: rich masters with merge pointers, canonical employers and aliases",
    "Free-text input (skills, job titles, institutions, employers) must map to canonical rows for search, matching and analytics; merges must not break history.",
    "skill/skill_alias/skill_relation, job_role/job_role_alias/job_role_skill, institution/institution_alias, employer_name_canonical/employer_name_alias, industry + industry_closure, geography country > state_region > city > locality/postal_code. "
    "Aliases are unique on (language, lower(alias)); merges use merged_into_*_id self-FK pointers; rows are retired with is_active and are never deleted (FK RESTRICT). Tax identifier validation rules (regex, length, checksum, mask) live in tax_id_type.",
    "(A) free text; (B) external taxonomy only (ESCO/O*NET); (C) own canonical tables with external_taxonomy_ref (chosen).",
    "Deterministic parsing and typeahead (trigram on STORED lower columns), taxonomy import without DDL, history keeps resolving.",
    "Dedupe/merge workflows are operational; seed volumes are large (city ~150k, skill_alias ~350k, employer_name_canonical ~500k).",
    "No hash-sharded indexes needed (low write rate).", "Accepted", ["B4", "B6", "B60"],
    ["skill", "skill_alias", "skill_relation", "job_role", "institution", "employer_name_canonical", "industry", "industry_closure", "tax_id_type", "city"])

adr("ADR-042", "Append-only is enforced by privileges AND a row trigger; TTL and purge are the only deleters",
    "BR-ADM-08 and the RBAC matrix (GR-14) make 125 tables append-only by granting INSERT/SELECT only. Verification on the real v26.2.8 cluster (docs/crdb_smoke_results.md) showed that "
    "owner and admin bypass REVOKE, that row-level TTL deletes and FK ON DELETE CASCADE both fire row triggers, and that a plain always-raising trigger therefore makes the TTL job fail.",
    "Every append_only table has `trg_<table>_append_only BEFORE UPDATE OR DELETE FOR EACH ROW` (SQLSTATE 42501) in addition to the missing UPDATE/DELETE grants. "
    "UPDATE is always blocked. DELETE is allowed only (a) for rows already past their TTL expiry (tables with a TTL get a per-table function `fn_ao_<table>` using the same expression as the TTL) "
    "or (b) inside a purge run: current_user is a member of purge_svc AND `SET app.purge_run_id` is non-empty (`fn_is_purge_run()`), which lets retention/erasure cascades from a hard-deleted parent through. Everyone else, including the table owner and root, is blocked.",
    "(A) privileges only (bypassed by owner/admin); (B) trigger that always raises (breaks TTL and cascades, proven on the cluster); (C) no TTL on append-only tables (violates retention rules).",
    "Defence in depth that survives a later wide GRANT or an operator mistake, and keeps retention working. The TTL job and purge_svc keep the documented behaviour.",
    "Two extra functions per TTL table (42 tables); purge_svc must set app.purge_run_id and write purge_job_run; trigger cost applies only to UPDATE/DELETE, not INSERT.",
    "TRUNCATE is still possible for the owner/admin: owner is the NOLOGIN schema_owner (E-27). Tests: tests/test_catalog_conformance.py (append-only section), tests/test_rules_money.py.",
    "Accepted", ["B79", "B58"], ["audit_event", "wallet_ledger_entry", "user_credential_history"], ["BR-ADM-08"])

adr("ADR-043", "Viewer context stays an explicit argument; RLS and custom session variables are verified but optional",
    "The RBAC findings F-05/AT-24/AT-25 required verification on the pinned image of custom session variables, SET ROLE, RLS and view owner semantics.",
    "On v26.2.8: `SET app.x`, `SET LOCAL`, `set_config(...,true)` and `current_setting(name,true)` work; SET ROLE works for member roles; RLS with policies on current_setting works for non-owner roles incl. WITH CHECK; "
    "views run with owner privileges (base denied, view allowed). The contract remains the explicit viewer argument (pair views / `fn_*(viewer)` functions) because it is testable per call, cache-friendly and independent of connection pooling state. "
    "RLS can be switched on later as a second layer without changing callers.",
    "(A) RLS + session variable as primary mechanism; (B) explicit viewer argument (chosen); (C) application filtering only.",
    "No dependence on pooled-connection session state; one view/function contract for blocked-party and recruiter scoping.",
    "Callers must pass the viewer id on every call (AT-25 lint).", "RLS ignores FK/UNIQUE checks, backups and changefeeds, so it could not be the only barrier anyway.",
    "Accepted", ["B79"], ["user_block", "organisation_block", "recruiter_organisation_mapping", "candidate_profile"], ["BR-ADM-08"])

# ---------------------------------------------------------------------------------------------------------------
assert len(ADRS) >= 35, len(ADRS)
ids = [a["id"] for a in ADRS]
assert len(set(ids)) == len(ids)
FIELDS = ["id", "title", "context", "decision", "alternatives", "reason", "trade_off", "consequences", "status", "date",
          "milestones", "related_tables", "related_rules"]
HEAD = ["id", "title", "context", "decision", "alternatives", "reason", "trade_off", "consequences", "status", "date",
        "milestone refs", "related tables", "related business rules"]


def build_xlsx(path):
    wb = new_wb()
    rows = [[a[f] for f in FIELDS] for a in ADRS]
    add_sheet(wb, "ADR", HEAD, rows,
              widths=[10, 38, 55, 70, 45, 55, 55, 45, 11, 11, 18, 45, 22],
              wrap_cols=["title", "context", "decision", "alternatives", "reason", "trade_off", "consequences", "related tables", "milestone refs", "related business rules"],
              validations={"status": ["Accepted", "Proposed", "Superseded"]}, status_col="status", freeze_cols=2)
    ws = wb["ADR"]
    for r in range(2, ws.max_row + 1):
        ws.row_dimensions[r].height = 300
    cnt = {s: sum(1 for a in ADRS if a["status"] == s) for s in ("Accepted", "Proposed", "Superseded")}
    add_sheet(wb, "Summary", ["item", "value"],
              [["ADR count", len(ADRS)], ["Accepted", cnt["Accepted"]], ["Proposed", cnt["Proposed"]], ["Superseded", cnt["Superseded"]],
               ["Generated by", "python docs/tools/build_adr.py"], ["Model basis", f"{S['tables']} tables, {S['columns']} columns, {S['rules']} business rules (docs/_staging)"],
               ["Engine", "CockroachDB v26.2 (PostgreSQL dialect), see ADR-001"],
               ["Verification", "every table/view/column/business-rule name in this log is checked against the staging model at build time"]],
              widths=[24, 100])
    wb.save(path)


def build_md(path):
    out = ["# Architecture Decision Record log (Talentra)\n",
           f"Generated by `python docs/tools/build_adr.py` on {dc.TODAY} from `docs/_staging` ({S['tables']} tables, {S['columns']} columns, {S['rules']} business rules). "
           "Engine: CockroachDB v26.2, PostgreSQL dialect. Every table, view, column and business-rule id cited below is verified against the model when this file is built. "
           "The same content is in `docs/02_adr_log.xlsx`.\n",
           "## Index\n", "| ID | Title | Status |", "|---|---|---|"]
    for a in ADRS:
        out.append(f"| {a['id']} | {a['title']} | {a['status']} |")
    out.append("")
    for a in ADRS:
        out.append(f"## {a['id']}: {a['title']}\n")
        out.append(f"**Status:** {a['status']}  |  **Date:** {a['date']}  |  **Milestones:** {a['milestones']}\n")
        for lab, key in (("Context", "context"), ("Decision", "decision"), ("Alternatives considered", "alternatives"),
                         ("Reason", "reason"), ("Trade-off", "trade_off"), ("Consequences", "consequences")):
            out.append(f"**{lab}.** {a[key]}\n")
        out.append(f"**Related tables:** {a['related_tables']}\n")
        if a["related_rules"]:
            out.append(f"**Related business rules:** {a['related_rules']}\n")
    dc.write_md(path, "\n".join(out))


if __name__ == "__main__":
    build_xlsx(os.path.join(dc.DOCS, "02_adr_log.xlsx"))
    build_md(os.path.join(dc.DOCS, "02_adr_log.md"))
    print("ADRs:", len(ADRS))

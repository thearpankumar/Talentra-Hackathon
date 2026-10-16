# 11. Performance and indexing plan (B65, B70-B74)

Target engine: CockroachDB v26.2 (LTS line), PostgreSQL dialect, 3-node Docker cluster. Everything below is computed from the consolidated data dictionary (`docs/_staging/*.yaml`, built into `docs/05_data_dictionary.xlsx`) by `python docs/tools/gen_perf_plan.py` (inputs: `docs/tools/perf_model.py`, `perf_extrap.py`, `fixed_queries.py`). The companion workbook `docs/11_index_catalogue.xlsx` lists every index.

**Status of numbers.** Row counts, sizes, plan notes and latency predictions in this document are model outputs, not measurements. The docket (Section 6) requires a measured basis: sections 5-8 define the measurement protocol and templates; the predictions are the hypotheses it will test. Sizes use an uncompressed logical-bytes model and an assumed 0.6 compression factor; both are calibrated after the seed with `python docs/tools/perf_model.py --calibrate <factor>`.

Note on the docket: Section 6 refers to a 'method in Section 10' and Section 8 KPIs; the supplied docket has neither (it ends at Section 7). The measurement method (section 5) and the KPI set (analytics module notes K01..K20) were therefore defined by us.

## Contents

1. Model facts and assumptions
2. Index strategy per module
3. Sharding, partitioning, zone configuration, follower reads
4. Table-size estimates (laptop and full scale)
5. The twelve fixed queries (B65) and the measurement method (B70)
6. Hot-spot analysis
7. Index justification per module with before/after template (B71-B73)
8. Partitioning strategy for application and notification tables (B73)
9. Full-scale extrapolation and the query that degrades first (B74)
10. Node sizing, contention analysis, FK overhead
11. Findings and recommendations to the data-model owner

## 1. Model facts and assumptions

| item | value |
|---|---|
| Dictionary | 1306 tables (538 lookups), 2270 declared secondary indexes, 1031 unique constraints, 2951 foreign keys, 78 views, 17 module files (12 modules + 5 EXTENSION modules) |
| Primary keys | UUID v4 (`gen_random_uuid()`) on every table: writes spread over ranges, no hot PK range at any volume (D-02). |
| Time-ordered secondary indexes | 189 hash-sharded indexes (34 with 8, 117 with 16, 38 with 32 buckets) |
| Cluster | 3 CockroachDB nodes in Docker on one host, replication factor 3 (every range has a replica on every node); each node 8 GiB RAM limit, 2 vCPU minimum, `--cache=.25 --max-sql-memory=.25`; licence applied after `cockroach init` (see crdb_capabilities.md section 3). |
| Laptop dataset (docket) | 1,000,000 candidate profiles, 100,000 job requirements, 5,000,000 applications (fixed); other tables per seed plan (doc 12). |
| Full scale (docket) | 10,000,000 profiles, 1,000,000 jobs, 100,000,000 applications (extrapolated, not loaded). |
| Row model | key 22 B + MVCC 12 B + value header 5 B + per column (type width + 1.5 B overhead); NULL columns cost 0 B; nullable columns assumed 60% populated. Index entry = prefix 6 B + key columns + PK 17 B + MVCC 12 B + header 5 B + STORING columns; partial indexes scaled by predicate selectivity; trigram/JSONB indexes by entries per row. |
| Replication | Sizes are per replica; cluster disk = 3x on a 3-node RF3 cluster; assumed on-disk compression factor 0.6 (unverified until measured). |


Logical size (one replica, data + indexes): laptop **173 GiB** (85 data + 88 index); full scale **2,423 GiB** (1,227 + 1,196). On the 3-node laptop cluster that is about **311 GiB** of disk at the assumed 0.6 compression (docket asks for 512 GB free SSD). If measured usage exceeds 330 GiB, the seed plan's fallback (halve the non-docket event streams) applies.

## 2. Index strategy per module

Rules applied by the model authors and checked by the lint: (1) every FK column leads some index (D-20); (2) time-ordered indexes on high-volume tables are hash-sharded (D-02); (3) 'live/open/unread' query shapes use partial indexes; (4) read paths that must avoid an index join use STORING; (5) natural and idempotency keys are unique constraints/partial unique indexes; (6) free-text search uses trigram GIN, JSONB filters use inverted indexes (ranking is delegated to the search service through `search_index_outbox`).

| module | tables | indexes | + unique constraints | hash-sharded | partial | unique (partial) | trigram | inverted JSONB | covering |
|---|---|---|---|---|---|---|---|---|---|
| master_data | 70 | 85 | 76 | 0 | 21 | 9 | 8 | 0 | 0 |
| profile | 90 | 151 | 60 | 5 | 92 | 36 | 0 | 0 | 11 |
| jobs_applications | 99 | 137 | 79 | 14 | 65 | 33 | 1 | 1 | 4 |
| administration | 88 | 128 | 60 | 7 | 72 | 22 | 0 | 0 | 5 |
| organisation_account | 65 | 152 | 50 | 2 | 55 | 36 | 2 | 0 | 3 |
| gig_marketplace | 65 | 103 | 56 | 7 | 49 | 13 | 2 | 0 | 5 |
| licences_devices | 34 | 78 | 26 | 7 | 29 | 10 | 0 | 0 | 1 |
| sourcing_consultant | 42 | 111 | 28 | 10 | 51 | 15 | 0 | 1 | 0 |
| subscriptions_payments | 74 | 115 | 73 | 9 | 57 | 12 | 0 | 0 | 0 |
| notifications_community | 93 | 136 | 72 | 23 | 57 | 24 | 2 | 1 | 1 |
| analytics | 80 | 125 | 81 | 30 | 7 | 6 | 0 | 0 | 6 |
| tier3_reference | 24 | 15 | 22 | 0 | 7 | 4 | 0 | 0 | 2 |
| ext_network | 88 | 210 | 65 | 18 | 69 | 21 | 0 | 0 | 7 |
| ext_matching | 78 | 148 | 59 | 26 | 42 | 27 | 3 | 0 | 0 |
| ext_ads | 79 | 110 | 64 | 14 | 44 | 21 | 0 | 0 | 1 |
| ext_learning | 112 | 184 | 73 | 12 | 93 | 30 | 0 | 0 | 0 |
| ext_talent_ops | 125 | 282 | 87 | 5 | 47 | 41 | 0 | 1 | 0 |


Flagship indexes per module (real names; each is in the catalogue with estimated size):

| module | index | table | type | role |
|---|---|---|---|---|
| master_data | `idx_city_name_search` | city | trigram (inverted) | trigram typeahead |
| master_data | `idx_city_country_id_name_search` | city | partial | prefix typeahead (partial is_active) |
| master_data | `idx_skill_canonical_name_lower` | skill | trigram (inverted) | skill typeahead (trigram) |
| master_data | `idx_job_role_canonical_name_lower` | job_role | trigram (inverted) | role typeahead (trigram) |
| profile | `idx_candidate_profile_current_city_id_availability_status_id` | candidate_profile | partial, covering (STORING) | recruiter search entry (partial, covering) |
| profile | `idx_candidate_profile_last_active_at` | candidate_profile | hash-sharded/16 | hash-sharded activity time |
| profile | `idx_candidate_skill_skill_id_years_of_experience` | candidate_skill | partial, covering (STORING) | skill-to-candidate search (partial, covering) |
| profile | `idx_candidate_profile_view_candidate_profile_id_viewed_at` | candidate_profile_view | btree | who-viewed-me |
| profile | `idx_candidate_profile_view_viewed_at` | candidate_profile_view | hash-sharded/32 | hash-sharded 32 buckets, TTL scan |
| profile | `idx_candidate_activity_log_activity_type_id_occurred_at` | candidate_activity_log | covering (STORING) | activity feed (see hot-spot note) |
| jobs_applications | `idx_job_requirement_primary_city_id_published_at` | job_requirement | partial, covering (STORING) | Q1 partial covering |
| jobs_applications | `idx_job_requirement_title_search_trgm` | job_requirement | trigram (inverted) | Q2 trigram |
| jobs_applications | `idx_job_requirement_attributes` | job_requirement | inverted (JSONB) | JSONB filters |
| jobs_applications | `idx_job_application_job_requirement_id_status_applied` | job_application | covering (STORING) | Q3 pipeline board |
| jobs_applications | `idx_job_application_candidate_profile_id_applied_at` | job_application | covering (STORING) | Q4 history |
| jobs_applications | `uq_job_application_candidate_profile_id_job_requirement_id` | job_application | unique, partial | one live application |
| jobs_applications | `idx_job_application_applied_at_hash` | job_application | hash-sharded/32 | hash-sharded 32 |
| jobs_applications | `idx_job_application_status_history_job_requirement_id_funnel` | job_application_status_history | btree | Q11 funnel |
| administration | `uq_user_block_blocker_app_user_id_blocked_app_user_id_open` | user_block | unique, partial, covering (STORING) | open block (partial unique) |
| administration | `idx_user_block_blocked_app_user_id_blocker_app_user_id_open` | user_block | partial | reverse lookup |
| administration | `idx_audit_event_audit_entity_type_id_entity_ref_occurred_at` | audit_event | partial | entity history |
| administration | `idx_audit_event_occurred_at` | audit_event | hash-sharded/16 | hash-sharded 16 |
| administration | `uq_app_user_email_live` | app_user | unique, partial | login by email (partial unique) |
| administration | `uq_identity_session_refresh_token_hash` | identity_session | unique | refresh token lookup |
| organisation_account | `idx_organisation_legal_name_normalised` | organisation | trigram (inverted) | name search (GIN) |
| organisation_account | `uq_organisation_slug` | organisation | unique, partial | public URL |
| organisation_account | `idx_recruiter_organisation_mapping_organisation_id_recruiter_app_user_id` | recruiter_organisation_mapping | partial, covering (STORING) | open mappings (partial, covering) |
| organisation_account | `uq_organisation_identifier_country_id_tax_id_type_id_value_lookup_hash` | organisation_identifier | unique, partial | tax id uniqueness via HMAC |
| organisation_account | `idx_organisation_verification_organisation_id_verification_kind_id_requested_at` | organisation_verification | btree | latest verification |
| gig_marketplace | `uq_gig_slot_gig_worker_profile_id_slot_start_at` | gig_slot | unique constraint | non-overlap slot table |
| gig_marketplace | `idx_gig_request_gig_request_status_id_window_start_at` | gig_request | partial, covering (STORING) | open gig feed (partial, covering) |
| gig_marketplace | `uq_gig_assignment_handoff_idempotency_key` | gig_assignment | unique, partial | idempotent handoff |
| gig_marketplace | `idx_gig_assignment_gig_reconcile_state_id_status_reconciled_at` | gig_assignment | partial | reconciliation sweep (partial) |
| gig_marketplace | `idx_gig_fulfilment_event_received_at` | gig_fulfilment_event | hash-sharded/16 | hash-sharded 16 |
| licences_devices | `uq_device_session_device_slot_id` | device_session | unique, partial | device limit (partial unique) |
| licences_devices | `uq_device_session_device_id` | device_session | unique, partial | one live session per device |
| licences_devices | `idx_device_session_expires_at` | device_session | hash-sharded/16, partial | expiry sweeper (partial, hash-sharded) |
| licences_devices | `uq_licence_seat_licence_id_app_user_id` | licence_seat | unique, partial | one live seat per user |
| licences_devices | `uq_device_app_user_id_device_fingerprint_hash` | device | unique, partial | device identity |
| sourcing_consultant | `idx_candidate_contact_attempt_candidate_profile_id_attempted_at` | candidate_contact_attempt | btree | contact frequency cap |
| sourcing_consultant | `idx_outreach_message_rendered_at` | outreach_message | hash-sharded/16 | hash-sharded TTL scan |
| subscriptions_payments | `uq_wallet_owner_organisation_id_currency` | wallet | unique, partial | one wallet per org and currency |
| subscriptions_payments | `uq_wallet_ledger_entry_wallet_id_sequence_no` | wallet_ledger_entry | unique constraint | gap-free sequence |
| subscriptions_payments | `uq_wallet_ledger_entry_idempotency_key` | wallet_ledger_entry | unique constraint | replay safety |
| subscriptions_payments | `idx_payment_order_payment_order_status_id_expires_at` | payment_order | partial | expiry sweeper (partial) |
| subscriptions_payments | `uq_payment_event_gateway_event_ref` | payment_event | unique constraint | webhook dedupe |
| subscriptions_payments | `uq_subscription_period_licence_id_first` | subscription_period | unique, partial | chain head (partial unique) |
| subscriptions_payments | `idx_profile_download_organisation_id_candidate_profile_id_downloaded_at` | profile_download | btree | download history / free repeat |
| notifications_community | `idx_notification_recipient_app_user_id_created_at_unread` | notification | partial | Q10 unread (partial) |
| notifications_community | `uq_notification_delivery_idempotency_key` | notification_delivery | unique constraint | dispatch idempotency |
| notifications_community | `idx_notification_delivery_dispatched_at` | notification_delivery | hash-sharded/16 | hash-sharded 16 |
| notifications_community | `uq_provider_webhook_event_notification_provider_id_provider_event_ref` | provider_webhook_event | unique constraint | webhook dedupe |
| notifications_community | `uq_job_alert_match_job_alert_preference_id_job_requirement_id` | job_alert_match | unique constraint | no duplicate alert |
| notifications_community | `idx_job_alert_preference_keyword_list` | job_alert_preference | inverted (JSONB) | JSONB inverted |
| analytics | `idx_agg_funnel_daily_period_date` | agg_funnel_daily | hash-sharded/8 | hash-sharded 8 |
| analytics | `idx_fact_application_event_event_at` | fact_application_event | hash-sharded/32 | hash-sharded 32 |
| analytics | `uq_fact_application_event_source_ref` | fact_application_event | unique constraint | idempotent load |
| analytics | `idx_fact_application_event_dim_job_requirement_id_to_dim_application_status_id` | fact_application_event | btree | requirement funnel |
| tier3_reference | `idx_salary_benchmark_job_role_id_country_id_reference_period_start_date` | salary_benchmark | partial, covering (STORING) | latest benchmark (partial, covering) |
| tier3_reference | `idx_company_rating_snapshot_organisation_id_snapshot_date` | company_rating_snapshot | partial, covering (STORING) | latest rating (partial, covering) |


Extension modules follow the same rules; their largest non-unique index per module (the one to justify first):

| extension module | table | largest non-unique index (candidate to justify) | type | est. size at full scale | purpose (dictionary) |
|---|---|---|---|---|---|
| ext_network | sitemap_entry | `idx_sitemap_entry_shard_no_last_modified_at` | btree | 0.0 GiB | Sitemap file writer. |
| ext_matching | search_query_normalised | `idx_search_query_normalised_normalised_text` | inverted/trigram | 0.3 GiB | Trigram index (GIN gin_trgm_ops) for fuzzy query lookup (D-16). |
| ext_ads | ad_click | `idx_ad_click_serve_ref` | btree | 0.0 GiB | Impression to click join. |
| ext_learning | lesson_progress | `idx_lesson_progress_course_enrolment_id_learning_progress_status_id` | btree | 0.0 GiB | Enrolment completion roll-up. |
| ext_talent_ops | fraud_signal | `idx_fraud_signal_app_user_id_detected_at` | btree | 0.0 GiB | Access path on app_user_id, detected_at. |


## 3. Sharding, partitioning, zone configuration, follower reads

**Principle.** CockroachDB shards every table and index into ranges (default maximum 512 MiB) and splits automatically; there is no need to partition for size. Partitioning is only needed for data locality (multi-region), tiered storage or mass-delete convenience. We therefore rely on (a) random UUID primary keys, (b) hash-sharded time indexes, (c) row-level TTL for expiring streams and (d) zone configs for GC and locality. Partitioning documentation in crdb_capabilities.md: partition columns must be leading PK columns and examples require an Enterprise/Trial licence.

| mechanism | where used | rule |
|---|---|---|
| UUID v4 PK | all 1306 tables | no sequential PK anywhere; `unique_rowid()` and SERIAL are not used |
| hash-sharded index | 189 time-ordered indexes | 8 buckets for low-volume dims/aggregates, 16 default for events, 32 for application/history/view tables (up to 4x node count of an 8-node cluster; with 3 nodes more than 6-9 buckets only adds fan-out cost on range scans) |
| row-level TTL | notification, notification_delivery, notification_delivery_event, provider_webhook_event, job_alert_*, feed_item, identity_session, device_session, login_attempt, candidate_job_search_history, job_view_event, gig_slot, gig_fulfilment_event, payment_event, audit_event (400 d, after sink confirmation) | `ttl_expiration_expression` on an `expires_at` column; `ttl_job_cron='@daily'`; keep only the indexes the read path needs (secondary indexes slow the TTL job); no inbound FKs from live tables onto TTL tables |
| zone config | see below | applied after seed, before measurement |
| follower reads | analytics and public listing reads | see below |

Zone configuration (names follow Rule 8: `<table>_zone`; statements are DDL to run on the cluster, not part of the dictionary):

```sql
-- Expiring high-churn streams: shorter MVCC garbage window (TTL tombstones are removed sooner). Default gc.ttlseconds is 14400 (4 h).
ALTER TABLE notification_delivery        CONFIGURE ZONE USING gc.ttlseconds = 3600;   -- notification_delivery_zone
ALTER TABLE notification_delivery_event  CONFIGURE ZONE USING gc.ttlseconds = 3600;
ALTER TABLE provider_webhook_event       CONFIGURE ZONE USING gc.ttlseconds = 3600;
ALTER TABLE notification                 CONFIGURE ZONE USING gc.ttlseconds = 7200;
ALTER TABLE job_view_event               CONFIGURE ZONE USING gc.ttlseconds = 3600;
-- Keep the BACKUP/PITR requirement in mind: gc.ttlseconds below the incremental backup interval breaks AS OF SYSTEM TIME backups; schedule incrementals hourly.
-- Large append-mostly tables: bigger ranges mean fewer ranges to track (default range_max_bytes = 512 MiB).
ALTER TABLE job_application_status_history CONFIGURE ZONE USING range_max_bytes = 1073741824, range_min_bytes = 268435456;
-- Small, constantly read reference tables: no change (cached in every node).
-- Hot-row config tables (wallet, invoice_number_series, audit_chain_head): keep default 3 replicas; do not co-locate with scans.
```

**Multi-region option (not used on the laptop).** `job_application` and `job_requirement` can become `REGIONAL BY ROW` on a country-derived `crdb_region` for data residency: it changes the primary key to `(crdb_region, id)`, and hash-sharded indexes cannot be combined with an explicit PARTITION BY key, so the two time indexes become plain per-region indexes. Not applied; recorded in section 8.

**Follower reads** (`AS OF SYSTEM TIME follower_read_timestamp()`, ~4.8 s staleness; read-only; verified GA in crdb_capabilities.md row 15c):

| query / path | follower read? | reason |
|---|---|---|
| Q1 job search, Q2 title search | yes | listing freshness of a few seconds is acceptable; offloads leaseholders |
| Q3 pipeline board (recruiter) | no | recruiters must see a status change they just made |
| Q4 candidate history | no (primary) | read-your-writes after applying |
| Q10 unread notifications | no | marking read must not reappear |
| Q11 funnel, Q12 KPI trend, all `mv_*`/`agg_*` reads | yes | analytics ETL itself reads `AS OF SYSTEM TIME follower_read_timestamp()` (analytics notes) |
| salary_benchmark / company_rating_snapshot reads | yes | reference data refreshed daily |
| Q5 recruiter candidate search | yes, bounded (`with_max_staleness('10s')` cannot be used: multi-row and joins) | use `follower_read_timestamp()` and accept ~5 s staleness of block/privacy flags: NOT acceptable for the block check, so Q5 runs on the leaseholder |
| Q6-Q9 transactions | no | writes; locks |


On a single-host 3-node Docker cluster a follower read does not reduce network latency (all nodes are local); the benefit measured on the laptop is leaseholder CPU offload only. Latency benefit needs `--locality` regions (crdb_capabilities.md row 14).

## 4. Table-size estimates (laptop and full scale)

Row counts: explicit anchors for 211 tables (docket-fixed 1M / 100k / 5M plus derived volumes), long-tail rule for the rest (doc 12 section 3 is the authority): lookups = their value count; per volume class low 1,000 / medium 20,000 / high 150,000 / very_high 500,000 rows; analytics tail 1,000 / 20,000 / 100,000 / 500,000; extension modules 5% of those; full scale = laptop x driver factor (profiles 10, jobs 10, applications 20, tail 10).

| table | module | rows laptop | row B | index B/row | # idx+uq | laptop data GiB | laptop idx GiB | rows full | full data GiB | full idx GiB | full total GiB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `candidate_profile` | profile | 1,000,000 | 1037 | 686 | 13 | 0.97 | 0.64 | 10,000,000 | 9.7 | 6.4 | 16.0 |
| `candidate_skill` | profile | 8,000,000 | 212 | 222 | 3 | 1.58 | 1.65 | 80,000,000 | 15.8 | 16.5 | 32.3 |
| `candidate_experience` | profile | 2,500,000 | 555 | 469 | 8 | 1.29 | 1.09 | 25,000,000 | 12.9 | 10.9 | 23.8 |
| `candidate_education` | profile | 1,800,000 | 414 | 307 | 5 | 0.69 | 0.51 | 18,000,000 | 6.9 | 5.1 | 12.1 |
| `resume` | profile | 1,400,000 | 1279 | 287 | 7 | 1.67 | 0.37 | 14,000,000 | 16.7 | 3.7 | 20.4 |
| `candidate_profile_view` | profile | 5,000,000 | 200 | 265 | 6 | 0.93 | 1.23 | 50,000,000 | 9.3 | 12.3 | 21.6 |
| `candidate_activity_log` | profile | 6,000,000 | 278 | 407 | 7 | 1.55 | 2.27 | 60,000,000 | 15.5 | 22.7 | 38.3 |
| `job_requirement` | jobs_applications | 100,000 | 2438 | 4063 | 16 | 0.23 | 0.38 | 1,000,000 | 2.3 | 3.8 | 6.1 |
| `job_requirement_skill` | jobs_applications | 600,000 | 170 | 148 | 2 | 0.10 | 0.08 | 6,000,000 | 1.0 | 0.8 | 1.8 |
| `job_application` | jobs_applications | 5,000,000 | 574 | 585 | 9 | 2.67 | 2.72 | 100,000,000 | 53.5 | 54.5 | 107.9 |
| `job_application_status_history` | jobs_applications | 15,000,000 | 320 | 324 | 5 | 4.47 | 4.53 | 300,000,000 | 89.4 | 90.6 | 180.0 |
| `questionnaire_answer` | jobs_applications | 4,000,000 | 665 | 296 | 4 | 2.48 | 1.10 | 80,000,000 | 49.6 | 22.1 | 71.6 |
| `candidate_job_search_history` | jobs_applications | 6,000,000 | 307 | 151 | 3 | 1.72 | 0.84 | 120,000,000 | 34.3 | 16.9 | 51.2 |
| `job_view_event` | jobs_applications | 10,000,000 | 166 | 159 | 3 | 1.54 | 1.48 | 200,000,000 | 30.8 | 29.5 | 60.4 |
| `app_user` | administration | 1,150,000 | 381 | 532 | 11 | 0.41 | 0.57 | 11,500,000 | 4.1 | 5.7 | 9.8 |
| `audit_event` | administration | 4,000,000 | 930 | 287 | 6 | 3.47 | 1.07 | 40,000,000 | 34.7 | 10.7 | 45.4 |
| `identity_session` | administration | 2,000,000 | 443 | 413 | 7 | 0.83 | 0.77 | 20,000,000 | 8.3 | 7.7 | 15.9 |
| `device_session` | licences_devices | 1,500,000 | 260 | 296 | 9 | 0.36 | 0.41 | 15,000,000 | 3.6 | 4.1 | 7.8 |
| `login_attempt` | licences_devices | 3,000,000 | 343 | 444 | 9 | 0.96 | 1.24 | 30,000,000 | 9.6 | 12.4 | 22.0 |
| `wallet_ledger_entry` | subscriptions_payments | 6,000,000 | 393 | 497 | 11 | 2.20 | 2.78 | 120,000,000 | 43.9 | 55.5 | 99.5 |
| `profile_download` | subscriptions_payments | 3,000,000 | 274 | 533 | 10 | 0.77 | 1.49 | 60,000,000 | 15.3 | 29.8 | 45.1 |
| `payment_order` | subscriptions_payments | 300,000 | 363 | 559 | 11 | 0.10 | 0.16 | 3,000,000 | 1.0 | 1.6 | 2.6 |
| `invoice` | subscriptions_payments | 250,000 | 499 | 556 | 12 | 0.12 | 0.13 | 2,500,000 | 1.2 | 1.3 | 2.5 |
| `notification` | notifications_community | 5,000,000 | 626 | 356 | 6 | 2.91 | 1.66 | 50,000,000 | 29.1 | 16.6 | 45.7 |
| `notification_delivery` | notifications_community | 6,000,000 | 362 | 353 | 6 | 2.02 | 1.97 | 60,000,000 | 20.2 | 19.7 | 40.0 |
| `notification_delivery_event` | notifications_community | 10,000,000 | 152 | 176 | 3 | 1.41 | 1.64 | 100,000,000 | 14.1 | 16.4 | 30.5 |
| `provider_webhook_event` | notifications_community | 3,000,000 | 695 | 299 | 5 | 1.94 | 0.84 | 30,000,000 | 19.4 | 8.4 | 27.8 |
| `job_alert_match` | notifications_community | 3,000,000 | 148 | 290 | 5 | 0.41 | 0.81 | 30,000,000 | 4.1 | 8.1 | 12.2 |
| `feed_item` | notifications_community | 3,000,000 | 170 | 248 | 4 | 0.48 | 0.69 | 30,000,000 | 4.8 | 6.9 | 11.7 |
| `outreach_recipient` | sourcing_consultant | 2,000,000 | 204 | 293 | 5 | 0.38 | 0.55 | 40,000,000 | 7.6 | 10.9 | 18.5 |
| `gig_slot` | gig_marketplace | 2,000,000 | 119 | 185 | 4 | 0.22 | 0.35 | 20,000,000 | 2.2 | 3.5 | 5.7 |
| `gig_fulfilment_event` | gig_marketplace | 1,000,000 | 585 | 212 | 4 | 0.54 | 0.20 | 10,000,000 | 5.4 | 2.0 | 7.4 |
| `fact_application_event` | analytics | 5,000,000 | 302 | 313 | 5 | 1.41 | 1.46 | 100,000,000 | 28.2 | 29.2 | 57.3 |
| `fact_search_event` | analytics | 4,000,000 | 310 | 238 | 4 | 1.15 | 0.89 | 80,000,000 | 23.1 | 17.7 | 40.8 |
| `agg_funnel_daily` | analytics | 100,000 | 303 | 172 | 2 | 0.03 | 0.02 | 1,000,000 | 0.3 | 0.2 | 0.4 |
| `dim_candidate` | analytics | 1,100,000 | 345 | 438 | 8 | 0.35 | 0.45 | 11,000,000 | 3.5 | 4.5 | 8.0 |


Module totals (GiB, one replica):

| module | tables | laptop data | laptop index | laptop total | full data | full index | full total |
|---|---|---|---|---|---|---|---|
| master_data | 70 | 0.1 | 0.7 | 0.8 | 0.8 | 3.1 | 3.8 |
| profile | 90 | 16.2 | 13.2 | 29.4 | 162.2 | 131.7 | 293.9 |
| jobs_applications | 99 | 20.4 | 15.1 | 35.5 | 393.6 | 289.5 | 683.2 |
| administration | 88 | 6.8 | 3.6 | 10.5 | 68.2 | 36.4 | 104.5 |
| organisation_account | 65 | 0.5 | 0.5 | 1.0 | 5.2 | 4.8 | 10.1 |
| gig_marketplace | 65 | 1.8 | 1.5 | 3.3 | 18.0 | 14.9 | 32.9 |
| licences_devices | 34 | 2.2 | 2.5 | 4.7 | 22.2 | 24.7 | 46.9 |
| sourcing_consultant | 42 | 3.2 | 2.1 | 5.3 | 62.8 | 41.9 | 104.7 |
| subscriptions_payments | 74 | 4.6 | 5.6 | 10.2 | 77.7 | 101.6 | 179.3 |
| notifications_community | 93 | 14.8 | 29.8 | 44.7 | 148.3 | 298.2 | 446.5 |
| analytics | 80 | 13.6 | 12.7 | 26.3 | 262.8 | 244.0 | 506.8 |
| tier3_reference | 24 | 0.0 | 0.0 | 0.0 | 0.3 | 0.2 | 0.5 |
| ext_network | 88 | 0.2 | 0.2 | 0.4 | 1.8 | 1.9 | 3.6 |
| ext_matching | 78 | 0.1 | 0.2 | 0.2 | 0.9 | 1.6 | 2.5 |
| ext_ads | 79 | 0.0 | 0.0 | 0.1 | 0.5 | 0.4 | 0.9 |
| ext_learning | 112 | 0.1 | 0.1 | 0.2 | 1.3 | 0.7 | 2.0 |
| ext_talent_ops | 125 | 0.0 | 0.0 | 0.1 | 0.4 | 0.4 | 0.8 |


Index overhead is 0.9-1.0x the data on the three biggest OLTP tables because of 7-8 secondary indexes on `job_application`; the per-index breakdown for any table is in the catalogue sheet `Index catalogue` (column 'est. size').

Range counts (ranges split at 512 MiB; modelled average fill 60%):

| table | scale | GiB (data+idx) | ranges (all indexes) | ranges (primary index only) |
|---|---|---|---|---|
| `job_application` | laptop | 5.4 | 18 | 9 |
| `job_application` | full | 107.9 | 360 | 179 |
| `job_application_status_history` | laptop | 9.0 | 30 | 15 |
| `job_application_status_history` | full | 180.0 | 600 | 299 |
| `candidate_profile` | laptop | 1.6 | 6 | 4 |
| `candidate_profile` | full | 16.0 | 54 | 33 |
| `candidate_skill` | laptop | 3.2 | 11 | 6 |
| `candidate_skill` | full | 32.3 | 108 | 53 |
| `notification_delivery` | laptop | 4.0 | 14 | 7 |
| `notification_delivery` | full | 40.0 | 134 | 68 |
| `wallet_ledger_entry` | laptop | 5.0 | 17 | 8 |
| `wallet_ledger_entry` | full | 99.5 | 332 | 147 |
| `audit_event` | laptop | 4.5 | 16 | 12 |
| `audit_event` | full | 45.4 | 152 | 116 |


## 5. The twelve fixed queries (B65) and the measurement method (B70)

All statements were parsed and column-checked against the dictionary by `python docs/tools/fixed_queries.py` (sqlglot, PostgreSQL dialect; INSERTs are also checked for NOT NULL columns without default; `created_by`/`updated_by` are omitted from the INSERT text for brevity and are set by the application per D-04). `$n` are bind parameters. The plan notes are **predictions** to be confirmed or refuted with `EXPLAIN ANALYZE`. The ten flagged queries are the 'top ten' of B65/B70.

| id | kind | title | EXPLAIN (top ten) | target p95 ms (laptop) |
|---|---|---|---|---|
| Q1 | lookup | Live job search by city, newest first (keyset pagination) | yes | 15 |
| Q2 | lookup | Job title substring search (trigram) | yes | 60 |
| Q3 | performance | Employer pipeline board: applications of one requirement in one status | yes | 25 |
| Q4 | functional | Candidate application history with job title | yes | 15 |
| Q5 | performance | Recruiter candidate search: city + skill + experience, honouring blocks and privacy | yes | 250 |
| Q6 | transactional | Apply to a job (idempotent, one live application per candidate and requirement) | yes | 40 |
| Q7 | transactional | Move an application to the next pipeline status (optimistic version) | yes | 40 |
| Q8 | transactional | Profile download paid from the wallet (ledger append + balance, idempotent) | yes | 60 |
| Q9 | transactional | Login: claim a free device slot under the licence device limit | no (captured, not in baseline) | 40 |
| Q10 | lookup | Unread in-app notifications for a user | no (captured, not in baseline) | 10 |
| Q11 | performance | Requirement funnel: transitions per status in a time window | yes | 40 |
| Q12 | performance | Organisation-agnostic funnel KPI trend by job category (analytics aggregate) | yes | 120 |


### Q1 - Live job search by city, newest first (keyset pagination) (lookup, EXPLAIN)

Parameters: $1 city id, $2 keyset cursor (published_at of last row seen). Index(es): `idx_job_requirement_primary_city_id_published_at`.

```sql
SELECT jr.id, jr.title, jr.job_category_id, jr.employment_type_id, jr.published_at
FROM job_requirement AS jr
WHERE jr.is_live
  AND jr.primary_city_id = $1
  AND jr.published_at < $2
ORDER BY jr.published_at DESC
LIMIT 20;
```

Expected plan: Expected: one `scan job_requirement@idx_job_requirement_primary_city_id_published_at` with constraint (primary_city_id = $1 AND published_at < $2) in REVERSE direction and LIMIT 20 pushed down (LIMITED SCAN, ~20 rows). Index is partial (WHERE is_live) and STORING (job_category_id, employment_type_id, title) so no index join. Fails the check if the plan shows `FULL SCAN` or `index join`. Invariant relied on: is_live is false for soft-deleted rows (validation V-JOB-03).


### Q2 - Job title substring search (trigram) (lookup, EXPLAIN)

Parameters: $1 user search text (lower-cased by the app). Index(es): `idx_job_requirement_title_search_trgm`.

```sql
SELECT jr.id, jr.title, jr.primary_city_id, jr.published_at
FROM job_requirement AS jr
WHERE jr.is_live
  AND jr.title_search LIKE '%' || lower($1) || '%'
ORDER BY jr.published_at DESC
LIMIT 20;
```

Expected plan: Expected: `inverted filter` scan on idx_job_requirement_title_search_trgm (trigram GIN, requires USING GIN (title_search gin_trgm_ops); needs >= 3 characters), then index join to job_requirement, filter is_live, top-k sort on published_at. Rows fetched = posting-list size of the rarest trigram, so cost grows with number of jobs (the extrapolation target). The inverted index is NOT partial, so non-live jobs are also fetched then filtered.


### Q3 - Employer pipeline board: applications of one requirement in one status (performance, EXPLAIN)

Parameters: $1 job requirement id, $2 application status id. Index(es): `idx_job_application_job_requirement_id_status_applied`, `candidate_profile primary key`.

```sql
SELECT ja.id, ja.applied_at, ja.match_score, cp.id AS candidate_profile_id, cp.display_name, cp.headline
FROM job_application AS ja
JOIN candidate_profile AS cp ON cp.id = ja.candidate_profile_id
WHERE ja.job_requirement_id = $1
  AND ja.application_status_id = $2
  AND ja.deleted_at IS NULL
ORDER BY ja.applied_at DESC
LIMIT 50;
```

Expected plan: Expected: scan job_application@idx_job_application_job_requirement_id_status_applied (job_requirement_id, application_status_id, applied_at DESC) STORING (candidate_profile_id, match_score), constraint on the first two columns, LIMIT 50; deleted_at is not in the index, so a filter after an index join (50 PK lookups) appears unless deleted_at is added to STORING (recommendation R-03); then lookup join into candidate_profile by primary key (50 point reads). Row order comes from the index, no sort node.


### Q4 - Candidate application history with job title (functional, EXPLAIN)

Parameters: $1 candidate profile id. Index(es): `idx_job_application_candidate_profile_id_applied_at`.

```sql
SELECT ja.id, ja.applied_at, ja.application_status_id, jr.id AS job_requirement_id, jr.title, jr.organisation_id
FROM job_application AS ja
JOIN job_requirement AS jr ON jr.id = ja.job_requirement_id
WHERE ja.candidate_profile_id = $1
  AND ja.deleted_at IS NULL
ORDER BY ja.applied_at DESC
LIMIT 20;
```

Expected plan: Expected: scan job_application@idx_job_application_candidate_profile_id_applied_at (candidate_profile_id, applied_at DESC) STORING (application_status_id, job_requirement_id) with LIMIT, lookup join job_requirement (PK) for title/organisation. A candidate has ~5 applications on average (5M/1M) so the scan is tiny; deleted_at filter needs an index join as for Q3.


### Q5 - Recruiter candidate search: city + skill + experience, honouring blocks and privacy (performance, EXPLAIN)

Parameters: $1 city id, $2 skill id, $3/$4 experience months range, $5 recruiter app_user id, $6 recruiter organisation id. Index(es): `idx_candidate_profile_current_city_id_availability_status_id`, `idx_candidate_skill_skill_id_years_of_experience`, `uq_user_block_blocker_app_user_id_blocked_app_user_id_open`, `uq_privacy_hidden_organisation (see dictionary)`, `privacy_setting unique on app_user_id`.

```sql
SELECT cp.id, cp.display_name, cp.headline, cp.current_job_title, cp.total_experience_months, cp.last_active_at
FROM candidate_profile AS cp
JOIN candidate_skill AS cs
  ON cs.candidate_profile_id = cp.id AND cs.skill_id = $2 AND cs.deleted_at IS NULL
JOIN privacy_setting AS ps ON ps.app_user_id = cp.app_user_id
WHERE cp.current_city_id = $1
  AND cp.deleted_at IS NULL
  AND cp.is_searchable
  AND ps.is_searchable_by_recruiters
  AND cp.total_experience_months BETWEEN $3 AND $4
  AND NOT EXISTS (SELECT 1 FROM user_block AS ub
                  WHERE ub.blocker_app_user_id = cp.app_user_id
                    AND ub.blocked_app_user_id = $5
                    AND ub.effective_to IS NULL)
  AND NOT EXISTS (SELECT 1 FROM privacy_hidden_organisation AS h
                  WHERE h.app_user_id = cp.app_user_id AND h.organisation_id = $6)
ORDER BY cp.last_active_at DESC NULLS LAST
LIMIT 50;
```

Expected plan: Expected: two candidate sets are built and joined: (a) candidate_profile via the partial index (current_city_id, availability_status_id) WHERE deleted_at IS NULL AND is_searchable STORING (current_job_title, total_experience_months) - prefix scan on current_city_id; (b) candidate_skill via idx_candidate_skill_skill_id_years_of_experience STORING (candidate_profile_id, proficiency_level_id). Optimizer chooses hash join or lookup join by estimated cardinality; anti-joins to user_block and privacy_hidden_organisation are point lookups per surviving candidate. last_active_at is NOT in any index used here, so ORDER BY ... LIMIT is a top-k sort over ALL matches - cost grows linearly with profiles in the city. This is the primary degradation candidate (see B74).


### Q6 - Apply to a job (idempotent, one live application per candidate and requirement) (transactional, EXPLAIN)

Parameters: $1 job requirement id, $2 candidate profile id, $3 organisation id, $4 job source id, $5 initial status id, $6 actor kind id, $7 idempotency key, $8 resume id. Index(es): `uq_job_application_candidate_profile_id_job_requirement_id`, `uq_job_application_status_history_idempotency_key`, `uq_job_application_status_history_job_application_id_sequence_no`.

```sql
SELECT jr.is_live, jr.max_applications, jr.organisation_id
FROM job_requirement AS jr
WHERE jr.id = $1;
```
```sql
INSERT INTO job_application (candidate_profile_id, organisation_id, job_requirement_id, job_source_id, application_status_id,
                             status_sequence_no, status_changed_at, applied_at, is_screened_out, resume_id)
VALUES ($2, $3, $1, $4, $5, 1, now(), now(), false, $8)
ON CONFLICT (candidate_profile_id, job_requirement_id) WHERE withdrawn_at IS NULL AND deleted_at IS NULL
DO NOTHING
RETURNING id;
```
```sql
INSERT INTO job_application_status_history (job_application_id, job_requirement_id, sequence_no, to_application_status_id, actor_kind_id, idempotency_key)
SELECT ja.id, $1, 1, $5, $6, $7
FROM job_application AS ja
WHERE ja.candidate_profile_id = $2 AND ja.job_requirement_id = $1 AND ja.withdrawn_at IS NULL AND ja.deleted_at IS NULL
ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING;
```

Expected plan: Expected: 3 statements in one transaction. INSERT writes the primary row plus 7 secondary index entries (uq candidate+requirement, requirement/status/applied storing, candidate/applied storing, organisation/status/changed_at, two hash-sharded time indexes, partial resume_id) and performs FK checks to candidate_profile, organisation, job_requirement (composite organisation_id,id), job_source, application_status, resume (see FK overhead). Arbiter is the partial unique index: a replay inserts 0 rows and RETURNING is empty (idempotent). No hot row: the requirement counters are updated asynchronously into job_requirement_stat.


### Q7 - Move an application to the next pipeline status (optimistic version) (transactional, EXPLAIN)

Parameters: $1 application id, $2 expected status_sequence_no, $3 new status id, $4 old status id, $5 actor app_user id, $6 actor kind id, $7 idempotency key. Index(es): `job_application primary key`, `uq_job_application_status_history_job_application_id_sequence_no_to_application_status_id`.

```sql
UPDATE job_application
SET application_status_id = $3, status_sequence_no = status_sequence_no + 1, status_changed_at = now(), updated_at = now(), updated_by = $5
WHERE id = $1 AND status_sequence_no = $2 AND deleted_at IS NULL
RETURNING status_sequence_no, job_requirement_id;
```
```sql
INSERT INTO job_application_status_history (job_application_id, job_requirement_id, sequence_no, prior_sequence_no, from_application_status_id, to_application_status_id, actor_kind_id, idempotency_key)
VALUES ($1, $8, $2 + 1, $2, $4, $3, $6, $7);
```

Expected plan: Expected: UPDATE by PK (point), 0 rows when another recruiter already moved it (status_sequence_no mismatch) -> client re-reads; else rewrites 4 secondary entries (status in idx_..._requirement_status_applied and candidate_applied storing, organisation_status_changed_at, status_changed_at_hash). History insert checks the composite FK to application_status_transition (from,to) and the self composite FK to the prior history row. $8 = job_requirement_id returned by the UPDATE.


### Q8 - Profile download paid from the wallet (ledger append + balance, idempotent) (transactional, EXPLAIN)

Parameters: $1 wallet id, $2 credits, $3 idempotency key, $4 actor app_user id, $5 organisation id, $6 candidate profile id, $7 ledger entry type id, $8 download kind id, $9 download source id, $10 resume id. Index(es): `wallet primary key (row lock)`, `uq_wallet_ledger_entry_wallet_id_sequence_no`, `uq_wallet_ledger_entry_idempotency_key`, `uq_profile_download_idempotency_key`, `uq_profile_download_wallet_ledger_entry_id`.

```sql
SELECT w.credit_balance, w.ledger_sequence, w.wallet_status_id
FROM wallet AS w
WHERE w.id = $1
FOR UPDATE;
```
```sql
INSERT INTO wallet_ledger_entry (wallet_id, sequence_no, prev_sequence_no, ledger_entry_type_id, credits_delta, is_debit, balance_after, idempotency_key, actor_app_user_id, has_fk_reference, row_hash)
VALUES ($1, $11 + 1, $11, $7, -$2, true, $12 - $2, $3, $4, false, $13)
RETURNING id;
```
```sql
UPDATE wallet
SET credit_balance = credit_balance - $2, ledger_sequence = ledger_sequence + 1, last_entry_at = now(), updated_at = now()
WHERE id = $1 AND credit_balance >= $2;
```
```sql
INSERT INTO profile_download (organisation_id, actor_app_user_id, candidate_profile_id, resume_id, download_kind_id, download_source_id, wallet_ledger_entry_id, credits_charged, downloaded_at, idempotency_key)
VALUES ($5, $4, $6, $10, $8, $9, $14, $2, now(), $3);
```

Expected plan: Expected: SELECT ... FOR UPDATE takes an exclusive lock on one wallet row, serialising all spenders of that wallet (hot-row analysis, ceiling ~ 1 / (txn latency)). Ledger insert enforces sequence gap-freedom via uq (wallet_id, sequence_no) and replay safety via uq idempotency_key; $11 = ledger_sequence read in statement 1, $12 = credit_balance, $13 = SHA-256 chain value computed by the app, $14 = id returned by statement 2. A replay violates uq_wallet_ledger_entry_idempotency_key and the app returns the first result. 4 statements, 1 commit, expected 2 consensus rounds (parallel commits).


### Q9 - Login: claim a free device slot under the licence device limit (transactional)

Parameters: $1 app_user id, $2 device id, $3 refresh token hash, $4 auth method id, $5 ip, $6 user agent. Index(es): `uq_licence_seat_licence_id_app_user_id`, `uq_device_slot_licence_seat_id_slot_no`, `uq_device_session_device_slot_id (partial WHERE ended_at IS NULL)`, `uq_device_session_device_id (partial)`.

```sql
SELECT ls.id, ls.licence_id
FROM licence_seat AS ls
WHERE ls.app_user_id = $1 AND ls.revoked_at IS NULL AND ls.deleted_at IS NULL
FOR UPDATE;
```
```sql
SELECT ds.id
FROM device_slot AS ds
WHERE ds.licence_seat_id = $7 AND NOT ds.is_retired
  AND NOT EXISTS (SELECT 1 FROM device_session AS s WHERE s.device_slot_id = ds.id AND s.ended_at IS NULL)
ORDER BY ds.slot_no
LIMIT 1;
```
```sql
INSERT INTO identity_session (app_user_id, refresh_token_hash, token_family_ref, auth_method_id, mfa_satisfied, issued_at, expires_at, absolute_expires_at, ip_address, user_agent)
VALUES ($1, $3, gen_random_uuid(), $4, false, now(), now() + interval '30 minutes', now() + interval '12 hours', $5, $6)
RETURNING id;
```
```sql
INSERT INTO device_session (identity_session_id, app_user_id, device_id, licence_seat_id, device_slot_id, started_at, last_activity_at, expires_at, ip_address, authentication_method_id)
VALUES ($8, $1, $2, $7, $9, now(), now(), now() + interval '30 minutes', $5, $4);
```

Expected plan: Expected: lock on the user's licence_seat row (per user, so no cross-user contention), pick the lowest free slot with an anti-join on the partial unique index uq_device_session_device_slot_id, insert. If two concurrent logins pick the same slot the partial unique index rejects the second (40001/23505): the database, not the application, enforces the limit. No free slot: instead of statements 3-4 insert device_limit_violation + audit_event + login_attempt (rejection) in the same transaction. $7 = seat id, $8 = identity_session id, $9 = slot id from the earlier statements.


### Q10 - Unread in-app notifications for a user (lookup)

Parameters: $1 app_user id. Index(es): `idx_notification_recipient_app_user_id_created_at_unread`.

```sql
SELECT n.id, n.notification_type_id, n.payload, n.created_at
FROM notification AS n
WHERE n.recipient_app_user_id = $1
  AND n.read_at IS NULL
  AND n.expires_at > now()
ORDER BY n.created_at DESC
LIMIT 20;
```

Expected plan: Expected: scan notification@idx_notification_recipient_app_user_id_created_at_unread (partial WHERE read_at IS NULL), constraint recipient_app_user_id = $1, reverse order, LIMIT 20; payload and notification_type_id are not stored in that partial index, so an index join of 20 rows is expected (the non-partial index STORING (notification_type_id, read_at) does not cover payload either). Rows past expires_at (TTL not yet run) are filtered.


### Q11 - Requirement funnel: transitions per status in a time window (performance, EXPLAIN)

Parameters: $1 job requirement id, $2 window start, $3 window end. Index(es): `idx_job_application_status_history_job_requirement_id_funnel`.

```sql
SELECT h.to_application_status_id, count(*) AS transitions
FROM job_application_status_history AS h
WHERE h.job_requirement_id = $1
  AND h.created_at >= $2 AND h.created_at < $3
GROUP BY h.to_application_status_id;
```

Expected plan: Expected: covered scan on idx_job_application_status_history_job_requirement_id_funnel (job_requirement_id, to_application_status_id, created_at): span per status value, streaming group-by with no sort. Rows scanned = history rows of ONE requirement in the window: ~200 on average (4 transitions x 50 applications), but mega-requirements (Zipf tail, 100k+ applicants) read hundreds of thousands: the long tail is what degrades.


### Q12 - Organisation-agnostic funnel KPI trend by job category (analytics aggregate) (performance, EXPLAIN)

Parameters: $1 window start date, $2 window end date, $3 dim_job_category id. Index(es): `idx_agg_funnel_daily_period_date (hash-sharded, 8 buckets)`, `PROPOSED: idx_agg_funnel_daily_dim_job_category_id_period_date STORING (applications, shortlisted, joined)`.

```sql
SELECT f.period_date, sum(f.applications) AS applications, sum(f.shortlisted) AS shortlisted, sum(f.joined) AS joined
FROM agg_funnel_daily AS f
WHERE f.period_date >= $1 AND f.period_date < $2
  AND f.dim_job_category_id = $3
GROUP BY f.period_date
ORDER BY f.period_date;
```

Expected plan: Expected with the existing schema: the only usable index is idx_agg_funnel_daily_period_date, hash-sharded (8 buckets): the range predicate becomes 8 spans, each followed by an index join to the primary index to read dim_job_category_id and the measures (the index stores none of them), then hash/sort aggregate: cost proportional to ALL rows in the date window across ALL categories. The unique grain (applied_dim_date_id UUIDv5, dim_job_category_id, ...) cannot serve a date range because the date key is not ordered. Proposed index (category, period_date) STORING the three measures turns it into a covered range scan (see section 7 analytics justification).


### 5.1 Measurement method (B70)

| item | protocol |
|---|---|
| Hardware (record in the report) | Host: >= 8 cores, 32 GB RAM, NVMe SSD with >= 512 GB free; record CPU model, RAM, SSD model, OS, Docker version, Docker CPU/RAM limits. Cluster: 3 x `cockroachdb/cockroach:v26.2.<LTS patch>` containers, each limited to 2 vCPU and 8 GiB (`--cache=.25 --max-sql-memory=.25`), one named volume per node, bridge network, licence applied, `kv.rangefeed.enabled=false`, no changefeeds, no other workloads. Load generator on the same host with CPU pinned away from the nodes. |
| Dataset | Seeded per doc 12 at laptop scale (1M profiles, 100k jobs, 5M applications; row counts verified by `SELECT count(*)` against the doc 12 targets); statistics refreshed (`CREATE STATISTICS` or wait for auto stats; `SHOW STATISTICS FOR TABLE job_application` captured); `ANALYZE`-equivalent done after the last load; no schema change running; `SHOW JOBS` clean. |
| Query text | Exactly the SQL in this section, parameters drawn from a fixed random stream (seed 20261015) with skew: city by Zipf(1.0) over cities, skill by Zipf(1.0) over skills, requirement by the application distribution (doc 12 section 4) so that both typical and heavy requirements are exercised; the same parameter sequence is replayed for before/after runs. |
| Warm-up | Cold run (restart the 3 containers, `SET CLUSTER SETTING` unchanged) for 1 pass of 200 executions recorded separately as 'cold'; then 2,000 untimed warm-up executions per query. |
| Runs | Each query: 5 independent runs x 10,000 timed executions single-client (latency), then 4/16/64 concurrent clients for 120 s each (throughput and tail). Transactional queries Q6-Q9 use a retry loop on SQLSTATE 40001; retries are counted and reported, latency is end-to-end including retries. |
| Statistics reported | p50, p95, p99, max, mean, stddev of latency in ms per run and pooled (HDR histogram, 1 microsecond resolution; coordinated-omission-safe open-loop mode for the concurrency tests); throughput; error and retry counts; rows returned. Report the median of the 5 run p95 values with min/max; reject a run when p99/p50 > 50 and re-run once. |
| EXPLAIN ANALYZE capture | For each flagged query: `EXPLAIN ANALYZE (VERBOSE)` text and `EXPLAIN ANALYZE (DISTSQL)` URL after warm-up, 3 samples (typical, heavy, empty parameter), stored in `perf/b70/<qid>/`. Record plan hash, `FULL SCAN` presence, rows read, KV bytes read, KV round trips, contention time, `SHOW RANGES FROM TABLE ... WITH DETAILS` for the tables touched. |
| Harness design | Python (psycopg 3 pool) or Go (pgx) driver reading a parameter file, one process per client, `statement_timeout=5s`, `application_name='b70'`; per-execution timestamps written to Parquet; a controller script runs the matrix (query x clients x run) and writes `perf/b70/results.csv` with columns `query_id, run, clients, n, p50_ms, p95_ms, p99_ms, max_ms, qps, retries, errors, rows_read_avg`. `crdb_internal.node_statement_statistics` is sampled before/after as a cross-check of server-side latency. |
| Acceptance | Pass = p95 <= target in the table above on the warm laptop run; any miss gets a recorded cause and either an index change (B71-B73) or a justified waiver. |
| Calibration after seed | Compute actual bytes/row from `SHOW RANGES FROM TABLE t WITH DETAILS` for 10 tables, derive the correction factor, re-run `perf_model.py --calibrate` and refresh sections 4, 9, 10. |


## 6. Hot-spot analysis

* **UUID primary keys**: all 1306 PKs are random v4; inserts are uniformly distributed over the table's ranges. Cost: no insert locality (each insert touches a random leaf), and secondary-index entries carry the 16-byte UUID (17 B encoded). `gen_random_uuid()` skips uniqueness checks by default, which is safe for v4 (collision probability negligible) and avoids one read per insert.
* **Hash-sharded time indexes**: 189 indexes (34/117/38 at 8/16/32 buckets). A plain index on `created_at` of an append table concentrates every insert on the last range (single leaseholder, ~one core); a hash-sharded index turns that into N independent write streams. Price: range scans on the index fan out over all N buckets (reads of a time window = N spans), so per-entity reads lead with the entity id instead (e.g. `(candidate_profile_id, viewed_at DESC)`), and the hash-sharded index is used for TTL/time-window jobs only.
* **Bucket count**: more buckets than nodes gives diminishing returns (crdb_capabilities.md row 2). With 3 nodes the useful ceiling is ~9; 16/32 are kept so that the dictionary does not need to change when the cluster grows to 8 nodes (32 = 4 x 8). Measured trade-off to record in B73: insert throughput vs 16/32-bucket scan latency for `idx_job_application_applied_at_hash`.

* **Remaining sequential-key candidates** found by scanning all 2270 indexes of high/very_high tables (9 leading-timestamp indexes without hashing, 14 leading low-cardinality lookup + timestamp):

| pattern | table | index | volume | partial predicate |
|---|---|---|---|---|
| timestamp-led | app_user | idx_app_user_last_login_at | high | deleted_at IS NULL AND anonymised_at IS NULL |
| timestamp-led | user_credential | idx_user_credential_breach_detected_at | high | breach_detected_at IS NOT NULL AND is_active |
| timestamp-led | connection_note | idx_connection_note_remind_at_due | very_high | remind_at IS NOT NULL |
| timestamp-led | newsletter_issue | idx_newsletter_issue_scheduled_at_due | high | scheduled_at IS NOT NULL AND published_at IS NULL |
| timestamp-led | event | idx_event_starts_at_upcoming | high | deleted_at IS NULL |
| timestamp-led | organisation_update | idx_organisation_update_scheduled_at_due | high | published_at IS NULL AND scheduled_at IS NOT NULL |
| timestamp-led | user_badge | idx_user_badge_expires_at_due | very_high | revoked_at IS NULL AND expires_at IS NOT NULL |
| timestamp-led | public_profile_slug | idx_public_profile_slug_redirect_expires_at_due | high | NOT is_current |
| timestamp-led | offer | idx_offer_expires_at | high | is_open AND expires_at IS NOT NULL |
| enum+timestamp | app_user | idx_app_user_erasure_state_id_erasure_requested_at | high | erasure_state_id IS NOT NULL AND anonymised_at IS NULL |
| enum+timestamp | proctoring_event | idx_proctoring_event_proctoring_severity_id_occurred_at | very_high | proctoring_severity_id IS NOT NULL |
| enum+timestamp | course_sync_event | idx_course_sync_event_provider_sync_status_id_received_at | high | provider_sync_status_id IS NOT NULL AND processed_at IS NULL |
| enum+timestamp | event | idx_event_event_status_id_starts_at | high | - |
| enum+timestamp | user_badge | idx_user_badge_badge_type_id_awarded_at | very_high | - |
| enum+timestamp | campaign_enrolment | idx_campaign_enrolment_campaign_enrolment_status_id_next_step_at | high | exited_at IS NULL |
| enum+timestamp | gig_request | idx_gig_request_gig_request_status_id_window_start_at | high | deleted_at IS NULL |
| enum+timestamp | gig_assignment | idx_gig_assignment_gig_reconcile_state_id_status_reconciled_at | high | NOT is_completed AND deleted_at IS NULL |
| enum+timestamp | job_syndication | idx_job_syndication_syndication_status_id_last_synced_at | high | syndication_status_id IS NOT NULL |
| enum+timestamp | candidate_media_file | idx_candidate_media_file_scan_status_id_created_at | very_high | deleted_at IS NULL |
| enum+timestamp | profile_import | idx_profile_import_import_status_id_created_at | high | finished_at IS NULL |
| enum+timestamp | candidate_activity_log | idx_candidate_activity_log_activity_type_id_occurred_at | very_high | - |
| enum+timestamp | course_enrolment | idx_course_enrolment_enrolment_status_id_enrolled_at | high | deleted_at IS NULL AND completed_at IS NULL |
| enum+timestamp | payment_order | idx_payment_order_payment_order_status_id_expires_at | high | paid_at IS NULL |


Assessment: the timestamp-led partial indexes (`*_due`, `expires_at_due`, `remind_at_due`) index a small due-set and are acceptable. **Needs action:** `idx_app_user_last_login_at` (high volume, WHERE deleted_at IS NULL AND anonymised_at IS NULL): every login rewrites the entry to the head of the index, a continuously hot range at login rate; make it hash-sharded or drop it in favour of the existing activity data. `idx_candidate_activity_log_activity_type_id_occurred_at` (very_high), `idx_user_badge_badge_type_id_awarded_at`, `idx_proctoring_event_proctoring_severity_id_occurred_at`, `idx_candidate_media_file_scan_status_id_created_at`: a handful of leading enum values with a rising timestamp concentrate writes on a few key prefixes (each prefix is one contiguous span, split into at most a few ranges); acceptable below ~1,000 writes/s per value, otherwise leading with a hash of the entity is required. Work-queue indexes such as `idx_payment_order_payment_order_status_id_expires_at` stay small because rows leave the partial predicate (paid_at IS NULL) quickly.

## 7. Index justification per module with before/after template (B71-B73)

One index per module, the one whose absence changes a plan from scan to point/range access. 'Before' = index made invisible (`ALTER INDEX ... NOT VISIBLE`, to be verified on the v26.2 image; otherwise DROP and re-CREATE) on the same dataset; 'After' = index visible. The query text is run through the B70 harness (single client, 5 x 10,000 executions, same parameter stream); `EXPLAIN ANALYZE` captured for both. The analytics entry proposes a new index (not in the dictionary): record it as change request R-01 in section 11.

#### 7.1 Master data (master_data): `idx_city_name_search` on `city`

* Backlog: B70/B71 (common modules, team). Table rows: 150,000 laptop, 150,000 full.
* DDL: `CREATE INDEX idx_city_name_search ON city USING GIN (name_search gin_trgm_ops)`
* Query:
```sql
SELECT id, display_name, state_region_id FROM city WHERE country_id = $1 AND is_active AND name_search LIKE '%' || lower($2) || '%' ORDER BY is_popular DESC, population DESC LIMIT 10;
```
* Before (index invisible or dropped), predicted: Without the trigram index the optimizer can only use idx_city_country_id_name_search for the country prefix and filter the infix match: scans every active city of the country (up to 150k rows at laptop scale, ~7,000 for a typical country).
* After, predicted: Inverted-index scan over the trigram posting lists of the typed text (3+ characters), index join of the few hundred matches, top-10 sort.
* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):

| metric | before | after | delta |
|---|---|---|---|
| p50 ms |  |  |  |
| p95 ms |  |  |  |
| p99 ms |  |  |  |
| rows read (EXPLAIN ANALYZE) |  |  |  |
| KV bytes read |  |  |  |
| plan shape (scan / index join / sort) |  |  |  |
| index size on disk (SHOW RANGES ... WITH INDEXES) | - |  |  |
| write cost: INSERT p95 ms on the table |  |  |  |

#### 7.2 Profile (profile): `idx_candidate_profile_current_city_id_availability_status_id` on `candidate_profile`

* Backlog: B70/B71 (common modules, team). Table rows: 1,000,000 laptop, 10,000,000 full.
* DDL: `CREATE INDEX idx_candidate_profile_current_city_id_availability_status_id ON candidate_profile (current_city_id, availability_status_id) STORING (current_job_title, total_experience_months) WHERE deleted_at IS NULL AND is_searchable`
* Query:
```sql
SELECT cp.id, cp.current_job_title, cp.total_experience_months FROM candidate_profile cp WHERE cp.current_city_id = $1 AND cp.deleted_at IS NULL AND cp.is_searchable AND cp.total_experience_months BETWEEN 36 AND 96;
```
* Before (index invisible or dropped), predicted: Only single-column FK index idx_candidate_profile_current_country_id / state exist; city filter becomes a FULL SCAN of 1M profiles (about 1 KB each).
* After, predicted: Partial covering index scan: prefix constraint on current_city_id, STORING provides current_job_title and total_experience_months so no index join; only searchable, live profiles are indexed.
* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):

| metric | before | after | delta |
|---|---|---|---|
| p50 ms |  |  |  |
| p95 ms |  |  |  |
| p99 ms |  |  |  |
| rows read (EXPLAIN ANALYZE) |  |  |  |
| KV bytes read |  |  |  |
| plan shape (scan / index join / sort) |  |  |  |
| index size on disk (SHOW RANGES ... WITH INDEXES) | - |  |  |
| write cost: INSERT p95 ms on the table |  |  |  |

#### 7.3 Jobs and Applications (jobs_applications): `idx_job_application_job_requirement_id_status_applied` on `job_application`

* Backlog: B70/B71 (common modules, team). Table rows: 5,000,000 laptop, 100,000,000 full.
* DDL: `CREATE INDEX idx_job_application_job_requirement_id_status_applied ON job_application (job_requirement_id, application_status_id, applied_at DESC) STORING (candidate_profile_id, match_score)`
* Query:
```sql
SELECT ja.id, ja.applied_at, ja.match_score, cp.id AS candidate_profile_id, cp.display_name, cp.headline
FROM job_application AS ja
JOIN candidate_profile AS cp ON cp.id = ja.candidate_profile_id
WHERE ja.job_requirement_id = $1
  AND ja.application_status_id = $2
  AND ja.deleted_at IS NULL
ORDER BY ja.applied_at DESC
LIMIT 50;
```
* Before (index invisible or dropped), predicted: Fallback idx_job_application_organisation_id_application_status_id_status_changed_at cannot serve a per-requirement filter: full or tenant-wide scan then sort 5M rows.
* After, predicted: Constrained scan on (job_requirement_id, application_status_id) in applied_at DESC order with LIMIT 50: no sort, ~50 rows read.
* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):

| metric | before | after | delta |
|---|---|---|---|
| p50 ms |  |  |  |
| p95 ms |  |  |  |
| p99 ms |  |  |  |
| rows read (EXPLAIN ANALYZE) |  |  |  |
| KV bytes read |  |  |  |
| plan shape (scan / index join / sort) |  |  |  |
| index size on disk (SHOW RANGES ... WITH INDEXES) | - |  |  |
| write cost: INSERT p95 ms on the table |  |  |  |

#### 7.4 Administration (administration): `uq_user_block_blocker_app_user_id_blocked_app_user_id_open` on `user_block`

* Backlog: B71. Table rows: 40,000 laptop, 400,000 full.
* DDL: `CREATE UNIQUE INDEX uq_user_block_blocker_app_user_id_blocked_app_user_id_open ON user_block (blocker_app_user_id, blocked_app_user_id) STORING (block_reason_id) WHERE effective_to IS NULL`
* Query:
```sql
SELECT 1 FROM user_block WHERE blocker_app_user_id = $1 AND blocked_app_user_id = $2 AND effective_to IS NULL;
```
* Before (index invisible or dropped), predicted: Falls back to idx_user_block_blocker_app_user_id_effective_from: scans all blocks of the candidate (heavy blockers have thousands) and filters.
* After, predicted: Unique point lookup (2 key columns, partial WHERE effective_to IS NULL): 1 KV read; also guarantees at most one open block per pair.
* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):

| metric | before | after | delta |
|---|---|---|---|
| p50 ms |  |  |  |
| p95 ms |  |  |  |
| p99 ms |  |  |  |
| rows read (EXPLAIN ANALYZE) |  |  |  |
| KV bytes read |  |  |  |
| plan shape (scan / index join / sort) |  |  |  |
| index size on disk (SHOW RANGES ... WITH INDEXES) | - |  |  |
| write cost: INSERT p95 ms on the table |  |  |  |

#### 7.5 Organisation and account (organisation_account): `idx_recruiter_organisation_mapping_organisation_id_recruiter_app_user_id` on `recruiter_organisation_mapping`

* Backlog: B71. Table rows: 60,000 laptop, 600,000 full.
* DDL: `CREATE INDEX idx_recruiter_organisation_mapping_organisation_id_recruiter_app_user_id ON recruiter_organisation_mapping (organisation_id, recruiter_app_user_id) STORING (mapping_scope_id, effective_from) WHERE effective_to IS NULL`
* Query:
```sql
SELECT recruiter_app_user_id, mapping_scope_id, effective_from FROM recruiter_organisation_mapping WHERE organisation_id = $1 AND effective_to IS NULL;
```
* Before (index invisible or dropped), predicted: Falls back to the unique (recruiter_app_user_id, organisation_id) indexes, which cannot be entered by organisation: full scan of the mapping table (60k rows laptop, 600k full).
* After, predicted: Covered partial index scan (open mappings only): one span per organisation, no index join (STORING mapping_scope_id, effective_from).
* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):

| metric | before | after | delta |
|---|---|---|---|
| p50 ms |  |  |  |
| p95 ms |  |  |  |
| p99 ms |  |  |  |
| rows read (EXPLAIN ANALYZE) |  |  |  |
| KV bytes read |  |  |  |
| plan shape (scan / index join / sort) |  |  |  |
| index size on disk (SHOW RANGES ... WITH INDEXES) | - |  |  |
| write cost: INSERT p95 ms on the table |  |  |  |

#### 7.6 Gig marketplace (gig_marketplace): `uq_gig_slot_gig_worker_profile_id_slot_start_at` on `gig_slot`

* Backlog: B71. Table rows: 2,000,000 laptop, 20,000,000 full.
* DDL: `-- declared as UNIQUE constraint uq_gig_slot_gig_worker_profile_id_slot_start_at on gig_slot`
* Query:
```sql
SELECT count(*) FROM gig_slot WHERE gig_worker_profile_id = $1 AND slot_start_at >= $2 AND slot_start_at < $3;
```
* Before (index invisible or dropped), predicted: Only idx_gig_slot_slot_start_at (hash-sharded) leads on time: the check becomes a scan across 16 buckets for the window then filter by worker (2M rows laptop). The uniqueness (overlap prevention) could not be enforced at all.
* After, predicted: Range scan inside one worker's key prefix: the same index that enforces non-overlap answers 'is this window free' in O(slots in window) = 4 per hour.
* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):

| metric | before | after | delta |
|---|---|---|---|
| p50 ms |  |  |  |
| p95 ms |  |  |  |
| p99 ms |  |  |  |
| rows read (EXPLAIN ANALYZE) |  |  |  |
| KV bytes read |  |  |  |
| plan shape (scan / index join / sort) |  |  |  |
| index size on disk (SHOW RANGES ... WITH INDEXES) | - |  |  |
| write cost: INSERT p95 ms on the table |  |  |  |

#### 7.7 Licences, seats, devices and sessions (licences_devices): `uq_device_session_device_slot_id` on `device_session`

* Backlog: B72. Table rows: 1,500,000 laptop, 15,000,000 full.
* DDL: `CREATE UNIQUE INDEX uq_device_session_device_slot_id ON device_session (device_slot_id) WHERE ended_at IS NULL`
* Query:
```sql
SELECT 1 FROM device_session s WHERE s.device_slot_id = $1 AND s.ended_at IS NULL;
```
* Before (index invisible or dropped), predicted: idx_device_session_licence_seat_id (partial WHERE ended_at IS NULL) answers per seat, but slot exclusivity under concurrent logins is not enforced by it.
* After, predicted: Partial unique point lookup; concurrent second claim of the same slot fails with a unique violation: limit enforced in the database.
* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):

| metric | before | after | delta |
|---|---|---|---|
| p50 ms |  |  |  |
| p95 ms |  |  |  |
| p99 ms |  |  |  |
| rows read (EXPLAIN ANALYZE) |  |  |  |
| KV bytes read |  |  |  |
| plan shape (scan / index join / sort) |  |  |  |
| index size on disk (SHOW RANGES ... WITH INDEXES) | - |  |  |
| write cost: INSERT p95 ms on the table |  |  |  |

#### 7.8 Sourcing and consultant requests (sourcing_consultant): `idx_candidate_contact_attempt_candidate_profile_id_attempted_at` on `candidate_contact_attempt`

* Backlog: B72. Table rows: 1,000,000 laptop, 20,000,000 full.
* DDL: `CREATE INDEX idx_candidate_contact_attempt_candidate_profile_id_attempted_at ON candidate_contact_attempt (candidate_profile_id, attempted_at DESC)`
* Query:
```sql
SELECT count(*) FROM candidate_contact_attempt WHERE candidate_profile_id = $1 AND attempted_at > now() - interval '7 days';
```
* Before (index invisible or dropped), predicted: Only the hash-sharded attempted_at index leads on time: 16-bucket scan for 7 days of ALL candidates, then filter (about 1M rows per week at laptop scale).
* After, predicted: Prefix scan on one candidate then the newest rows first: reads only that candidate's attempts of the last 7 days (usually 0-5 rows).
* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):

| metric | before | after | delta |
|---|---|---|---|
| p50 ms |  |  |  |
| p95 ms |  |  |  |
| p99 ms |  |  |  |
| rows read (EXPLAIN ANALYZE) |  |  |  |
| KV bytes read |  |  |  |
| plan shape (scan / index join / sort) |  |  |  |
| index size on disk (SHOW RANGES ... WITH INDEXES) | - |  |  |
| write cost: INSERT p95 ms on the table |  |  |  |

#### 7.9 Subscriptions, wallet, payments and download history (subscriptions_payments): `idx_payment_order_payment_order_status_id_expires_at` on `payment_order`

* Backlog: B72. Table rows: 300,000 laptop, 3,000,000 full.
* DDL: `CREATE INDEX idx_payment_order_payment_order_status_id_expires_at ON payment_order (payment_order_status_id, expires_at) WHERE paid_at IS NULL`
* Query:
```sql
SELECT id FROM payment_order WHERE payment_order_status_id = $1 AND expires_at < now() AND paid_at IS NULL ORDER BY expires_at LIMIT 500;
```
* Before (index invisible or dropped), predicted: Without the partial index the sweeper scans idx_payment_order_organisation_id_created_at or the full table (300k rows laptop, 3M full) every run.
* After, predicted: Partial index (WHERE paid_at IS NULL) contains only unsettled orders; the scan starts at the oldest expires_at and stops at LIMIT 500.
* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):

| metric | before | after | delta |
|---|---|---|---|
| p50 ms |  |  |  |
| p95 ms |  |  |  |
| p99 ms |  |  |  |
| rows read (EXPLAIN ANALYZE) |  |  |  |
| KV bytes read |  |  |  |
| plan shape (scan / index join / sort) |  |  |  |
| index size on disk (SHOW RANGES ... WITH INDEXES) | - |  |  |
| write cost: INSERT p95 ms on the table |  |  |  |

#### 7.10 Notifications, preferences and community (notifications_community): `idx_notification_recipient_app_user_id_created_at_unread` on `notification`

* Backlog: B73. Table rows: 5,000,000 laptop, 50,000,000 full.
* DDL: `CREATE INDEX idx_notification_recipient_app_user_id_created_at_unread ON notification (recipient_app_user_id, created_at DESC) WHERE read_at IS NULL`
* Query:
```sql
SELECT n.id, n.notification_type_id, n.payload, n.created_at
FROM notification AS n
WHERE n.recipient_app_user_id = $1
  AND n.read_at IS NULL
  AND n.expires_at > now()
ORDER BY n.created_at DESC
LIMIT 20;
```
* Before (index invisible or dropped), predicted: idx_notification_recipient_app_user_id_created_at scans the user's whole recent history (read and unread, up to 180 days) to find unread rows.
* After, predicted: Partial index holds unread rows only: reads at most the latest 20 unread entries of the user.
* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):

| metric | before | after | delta |
|---|---|---|---|
| p50 ms |  |  |  |
| p95 ms |  |  |  |
| p99 ms |  |  |  |
| rows read (EXPLAIN ANALYZE) |  |  |  |
| KV bytes read |  |  |  |
| plan shape (scan / index join / sort) |  |  |  |
| index size on disk (SHOW RANGES ... WITH INDEXES) | - |  |  |
| write cost: INSERT p95 ms on the table |  |  |  |

#### 7.11 Analytics layer and reporting marts (analytics): `idx_agg_funnel_daily_dim_job_category_id_period_date` on `agg_funnel_daily`

* Backlog: B73. Table rows: 100,000 laptop, 1,000,000 full.
* DDL: `CREATE INDEX idx_agg_funnel_daily_dim_job_category_id_period_date ON agg_funnel_daily (dim_job_category_id, period_date) STORING (applications, shortlisted, joined)`
* Query:
```sql
SELECT f.period_date, sum(f.applications) AS applications, sum(f.shortlisted) AS shortlisted, sum(f.joined) AS joined
FROM agg_funnel_daily AS f
WHERE f.period_date >= $1 AND f.period_date < $2
  AND f.dim_job_category_id = $3
GROUP BY f.period_date
ORDER BY f.period_date;
```
* Before (index invisible or dropped), predicted: Existing idx_agg_funnel_daily_period_date is hash-sharded (8 buckets) and not covering: 8 range spans + index join per row for the category filter and the measures.
* After, predicted: CREATE INDEX idx_agg_funnel_daily_dim_job_category_id_period_date ON agg_funnel_daily (dim_job_category_id, period_date) STORING (applications, shortlisted, joined): one covered range scan, ~90 rows for a 90-day window. Table is an aggregate (not append-hot), so the index needs no hash sharding.
* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):

| metric | before | after | delta |
|---|---|---|---|
| p50 ms |  |  |  |
| p95 ms |  |  |  |
| p99 ms |  |  |  |
| rows read (EXPLAIN ANALYZE) |  |  |  |
| KV bytes read |  |  |  |
| plan shape (scan / index join / sort) |  |  |  |
| index size on disk (SHOW RANGES ... WITH INDEXES) | - |  |  |
| write cost: INSERT p95 ms on the table |  |  |  |

#### 7.12 Tier 3 reference domains (optional) (tier3_reference): `idx_salary_benchmark_job_role_id_country_id_reference_period_start_date` on `salary_benchmark`

* Backlog: B70/B71 (common modules, team). Table rows: 20,000 laptop, 200,000 full.
* DDL: `CREATE INDEX idx_salary_benchmark_job_role_id_country_id_reference_period_start_date ON salary_benchmark (job_role_id, country_id, reference_period_start_date DESC) STORING (p25_amount, p50_amount, p75_amount, is_publishable) WHERE is_publishable`
* Query:
```sql
SELECT p25_amount, p50_amount, p75_amount FROM salary_benchmark WHERE job_role_id = $1 AND country_id = $2 AND is_publishable ORDER BY reference_period_start_date DESC LIMIT 1;
```
* Before (index invisible or dropped), predicted: idx_salary_benchmark_country_id then filter by job_role and sort: scans every benchmark of the country.
* After, predicted: Partial covering index: latest published period of (role, country) in one KV read.
* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):

| metric | before | after | delta |
|---|---|---|---|
| p50 ms |  |  |  |
| p95 ms |  |  |  |
| p99 ms |  |  |  |
| rows read (EXPLAIN ANALYZE) |  |  |  |
| KV bytes read |  |  |  |
| plan shape (scan / index join / sort) |  |  |  |
| index size on disk (SHOW RANGES ... WITH INDEXES) | - |  |  |
| write cost: INSERT p95 ms on the table |  |  |  |


#### 7.13 Extension modules

Use the same template; candidates by size (section 2 table). Priority order if time is short: `ext_matching` (`search_index_outbox`), `ext_ads` (`ad_budget_shard` write path), others optional.

## 8. Partitioning strategy for application and notification tables (B73)

**Decision: no declarative partitioning at laptop scale or at full scale in a single region; physical distribution relies on UUID PKs, hash-sharded indexes, TTL and range splitting.** Reasons (all from the dictionary and crdb_capabilities.md): (1) partition columns must lead the primary key, but the PK is `id UUID` by D-02 and changing it to a time-led key would create exactly the hot spot we avoid; (2) a table with an explicit `PARTITION BY` cannot use hash-sharded indexes on the same key columns; (3) partitioning docs require a licence; (4) dropping a partition is not supported (cannot drop a single partition), so partitions do not give cheap archival.

| table group | volume (laptop -> full rows) | strategy | retention mechanism | named objects |
|---|---|---|---|---|
| `job_application` | 5,000,000 -> 100,000,000 | Single table, random UUID PK. Secondary indexes lead with entity ids (requirement, candidate, organisation) so reads are range-local; the two time indexes are hash-sharded (32). At 100M rows (about 108 GiB incl. indexes) the table is ~360 ranges (data + indexes): no partitioning needed for size. | Soft delete + anonymise-in-place on erasure; no TTL (business record). Archive of closed requirements > 7 years by changefeed to cloud storage, then delete in bounded batches. | `idx_job_application_applied_at_hash`, `idx_job_application_status_changed_at_hash` |
| `job_application_status_history` | 15,000,000 -> 300,000,000 | Single table; hash-sharded created_at (32); funnel index leads with requirement id. Larger ranges (1 GiB) to reduce range count. | Append-only; export closed requirements older than 24 months and prune in batches (retention job). | `job_application_status_history_zone` |
| `notification` | 5,000,000 -> 50,000,000 | Single table; per-user index leads with the random recipient id; hash-sharded created_at/expires_at (16). | Row-level TTL 180 days on `expires_at` (daily job, batch 500). This is time-based expiry without partition management. | `notification_zone` (gc.ttlseconds 7200) |
| `notification_delivery`, `_event`, `provider_webhook_event` | 6,000,000 -> 60,000,000 (deliveries) | Event-sourced insert-only path (B57): batches of 500-1000 rows per txn, `ON CONFLICT DO NOTHING`; no updates; hash-sharded time indexes (16); TTL 30 d / 30 d / 7 d. | TTL; before expiry the daily rollup writes `notification_delivery_daily_summary` (7 years). | `notification_delivery_zone` (gc.ttlseconds 3600) |
| design-rate stress case | 50M deliveries/day x 30 days = 1.5 billion live rows (notifications notes) | Same design, but the 30-day TTL window would hold about 1.5e9 x 715 B = ~1.0 TiB per replica; at that rate move delivery rows to a 7-day window (about 250 GiB) and keep the 7-year daily summary, or shard by `crdb_region`. Decision threshold: if measured TTL job throughput (rows/s deleted) < ingest rate x 1.2, shorten the window. | TTL delete rate is the limiting factor: tune `ttl_select_batch_size`, `ttl_delete_batch_size` (500), `ttl_delete_rate_limit`. | - |


**Optional multi-region / residency variant** (documented, not built): `ALTER DATABASE ... PRIMARY REGION`, `ALTER TABLE job_application SET LOCALITY REGIONAL BY ROW`; the implicit `crdb_region` column is derived from the requirement's country; partition names `job_application_p_<region>`; hash-sharded time indexes become per-region plain indexes (volume is split per region anyway). Requires >= 3 regions for `SURVIVE REGION FAILURE` and a licence; a single Docker host can emulate regions with distinct `--locality` flags for functional tests only.

**Before/after for B73** (template, to fill): insert throughput of `job_application` with (a) hash-sharded applied_at 32 buckets, (b) 8 buckets, (c) plain index; p95 of Q3 and of a 'last 24 h applications' scan in each case; TTL job duration and rows/s for `notification_delivery` at 30 days of seeded rows.

## 9. Full-scale extrapolation (B74)

**Method.** Measure at laptop scale (B70), then project with an explicit model whose parameters are re-fitted from the measurements: (1) rows touched per query as a function of the scale drivers (profiles P, jobs J, applications A: x10, x10, x20); (2) index depth growth `depth = 1 + ceil(log_F(N / leaf entries))` with F the internal fan-out (32 KiB block / (key bytes + 16)); (3) cache hit ratio under Zipf(s=1) access: `h = ln(1 + C) / ln(1 + N)` where C and N are the cache and the query working set in 4 KiB blocks; (4) range count = bytes / (512 MiB x 0.6); (5) plan changes (hash join vs lookup join, sort spill) when estimated rows cross thresholds. Latency = fixed + scan rows x c_scan + hash rows x c_hash + sorted rows x c_sort + lookups x (c_lookup + miss x c_miss + extra levels x c_level).

Index depth and size of the indexes relied on by the fixed queries (32 KiB blocks):

| index | table | entries laptop | entries full | bytes/row | depth laptop | depth full | GiB laptop | GiB full |
|---|---|---|---|---|---|---|---|---|
| `idx_job_requirement_primary_city_id_published_at` | job_requirement | 100,000 | 1,000,000 | 53 | 2 | 3 | 0.00 | 0.05 |
| `idx_job_requirement_title_search_trgm` | job_requirement | 100,000 | 1,000,000 | 3006 | 3 | 3 | 0.28 | 2.80 |
| `idx_job_application_job_requirement_id_status_applied` | job_application | 5,000,000 | 100,000,000 | 106 | 3 | 3 | 0.49 | 9.87 |
| `idx_job_application_candidate_profile_id_applied_at` | job_application | 5,000,000 | 100,000,000 | 103 | 3 | 3 | 0.48 | 9.59 |
| `idx_candidate_profile_current_city_id_availability_status_id` | candidate_profile | 1,000,000 | 10,000,000 | 79 | 3 | 3 | 0.07 | 0.73 |
| `idx_candidate_skill_skill_id_years_of_experience` | candidate_skill | 8,000,000 | 80,000,000 | 93 | 3 | 3 | 0.69 | 6.91 |
| `idx_job_application_status_history_job_requirement_id_funnel` | job_application_status_history | 15,000,000 | 300,000,000 | 83 | 3 | 4 | 1.16 | 23.19 |
| `idx_notification_recipient_app_user_id_created_at_unread` | notification | 5,000,000 | 50,000,000 | 33 | 3 | 3 | 0.15 | 1.54 |


Because leaf blocks hold ~218 entries and fan-out is >200, depth grows by at most one level (laptop 3 to full 4); the extra level costs ~0.02 ms per lookup, which is negligible next to the cache effect. LSM levels per node: laptop 4 (about 104 GiB on disk per node) to full 5 (1,454 GiB per node); bloom filters keep point-read amplification near 1 for hits.

Cache model: query working set = the 8 indexes above + 10% of the three hot primary tables: **3.7 GiB** laptop and **61.2 GiB** full scale; total block cache 6 GiB (3 x 2 GiB). Predicted miss ratio: **2.0%** laptop, **14.0%** full scale (7.0x more cache misses per lookup).

Predicted latency (ms, single client, warm, mean of the model; NOT a measurement) and the scale at which each query first exceeds its SLO (lam = position on the path laptop 1 -> full 10; profiles 1M x lam, jobs 100k x lam, applications 5M x 2 lam):

| query | rows model (laptop) | SLO ms | laptop ms | full ms | growth | first breach |
|---|---|---|---|---|---|---|
| Q1 | LIMIT 20 on (city, published_at DESC) | 15 | 1.6 | 1.6 | 1.0x | does not breach up to full scale |
| Q2 | posting list of rarest trigram = 0.15% of jobs, index join each | 60 | 10.7 | 177.7 | 16.6x | 4.2x (~4.2M profiles / 421k jobs / 42M applications) |
| Q3 | 50 rows + deleted_at index join + candidate_profile lookups | 25 | 7.2 | 12.8 | 1.8x | does not breach up to full scale |
| Q4 | ~5 applications per candidate (5M/1M) | 15 | 2.2 | 2.9 | 1.3x | does not breach up to full scale |
| Q5 | city set 16k + skill set 10k index entries, 160 matches x 3 lookups | 250 | 119.9 | 1453.9 | 12.1x | 2.0x (~2.0M profiles / 203k jobs / 20M applications) |
| Q6 | 1 insert + 7 index entries + 7 FK checks | 40 | 2.3 | 3.2 | 1.4x | does not breach up to full scale |
| Q7 | update + 4 index rewrites + history insert + FK checks | 40 | 2.2 | 2.8 | 1.3x | does not breach up to full scale |
| Q8 | wallet lock + ledger + wallet + download insert | 60 | 2.3 | 3.1 | 1.3x | does not breach up to full scale |
| Q9 | seat lock, slot anti-join, 2 inserts | 40 | 2.1 | 2.6 | 1.3x | does not breach up to full scale |
| Q10 | 20 unread rows + payload index join | 10 | 2.7 | 3.8 | 1.4x | does not breach up to full scale |
| Q11 (avg requirement) | 4 transitions x apps per requirement (A/J doubles) | 40 | 2.0 | 2.5 | 1.2x | does not breach up to full scale |
| Q11 (largest requirement) | largest requirement holds 0.1% of applications x 4 transitions (p99 tail) | 400 | 101.5 | 1001.5 | 9.9x | 4.0x (~4.0M profiles / 399k jobs / 40M applications) |
| Q12 (existing index) | 90-day window, ~55 grain rows per day, index join per row | 120 | 299.0 | 5776.5 | 19.3x | already above SLO at laptop scale |
| Q12 (proposed index) | covered (category, period_date) range: 90 rows | 120 | 1.7 | 1.7 | 1.0x | does not breach up to full scale |


Parameters (re-fit them from the B70 baseline; one run of `gen_perf_plan.py` regenerates this table): fixed_ms=1.5, c_scan=0.0025, c_hash=0.001, c_sort=0.003, c_lookup=0.05, c_miss=0.3, c_level=0.02, block_bytes=4096, zipf_s=1.0.

### Prediction: which query degrades first and why

1. **Q5, recruiter candidate search (city + skill + experience, block/privacy anti-joins, ORDER BY last_active_at).** It is the only fixed query whose work is proportional to the number of profiles in a city and with a skill (sets of ~26,000 index entries at 1M profiles, ~260,000 at 10M) and whose sort key `last_active_at` is in none of the indexes used, so the engine must read both candidate sets and top-k sort every match; there is no LIMIT pushdown. Predicted 120 ms to 1,454 ms (x12); it crosses its 250 ms SLO at about 2.0M profiles, i.e. soon after the laptop scale. The plan can also change: when the estimated skill set exceeds the city set the optimizer may switch from lookup join to hash join with higher memory (`distsql` temp storage spill beyond the SQL memory budget) - watch for `spilled to disk` in EXPLAIN ANALYZE. Mitigation if confirmed: add `last_active_at DESC` as the second key of a per-city partial index (`(current_city_id, last_active_at DESC) STORING (...) WHERE deleted_at IS NULL AND is_searchable`) so that the city side streams in order with LIMIT pushdown, and drive skill filtering by lookup; or delegate ranking to the search service (D-16) and keep the SQL for filters only.
2. **Q2, trigram title search** second: posting-list size and the number of index-joined rows grow linearly with jobs (x10); predicted 11 ms to 178 ms, breach at ~4.2x laptop jobs (421k). The inverted index is not partial (`is_live`), so closed jobs add to the posting list and are filtered after the join.
3. **Q11 on the largest requirement (p99 tail, not the average)**: average cost only doubles (applications per requirement 50 to 100) but the largest requirement's history grows x20; predicted 102 ms to 1,002 ms, breach at ~4.0x. The docket jobs notes expected the pipeline board for mega-requirements (Q3) to degrade first; Q3 is protected by LIMIT 50 and the covering index, while Q11's aggregation has no limit, so Q11's tail degrades earlier. Mitigation: read the pre-aggregated `job_requirement_stat` / `fact_application_pipeline` for large requirements.
4. **Q12 on the existing schema** already exceeds its SLO at laptop scale (299 ms predicted vs 120 ms) because the only date index is hash-sharded and not covering; it is a present defect, fixed by the proposed index (section 7.11), after which it is flat.
5. Point/lookup and transactional queries (Q1, Q3, Q4, Q6-Q10) are predicted to grow by less than 2x: they are bounded by LIMIT or key lookups; their growth is cache misses and one more index level. The exception under concurrency is Q8 (wallet row lock), see section 10.

**How the prediction will be tested (falsifiable).** At laptop scale run Q5 and Q2 at 25%, 50%, 100% of the seeded profiles/jobs (delete-free subsetting by `id` hash, or three loads): the measured p95 vs rows must be linear with the fitted slope; extrapolate to 10x; the prediction is wrong if Q5's slope is below Q2's or Q11-max's. Optional bonus run (docket): one query set on a cloud cluster at 10M profiles to compare.

## 10. Node sizing, contention analysis, FK overhead

### 10.1 Node sizing for the 3-node Docker cluster at 100M-row tables

* **100M-application subset** (tables `job_application`, `job_application_status_history`, `candidate_profile`, `candidate_skill`, `job_requirement`, `job_requirement_skill`, `questionnaire_answer`, `job_application_screening_result`): 24 GiB laptop -> **431 GiB** per replica at full scale (259 GiB on disk after compression). With RF3 on 3 nodes every node stores the whole subset.
* **Whole database at full scale**: 2,423 GiB logical = 1.4 TiB on disk per node (RF3, 3 nodes): not feasible on a laptop; this is why only the subset (or one query set) is run in the cloud (bonus).

| resource | laptop run (1M/100k/5M) | 100M-application subset run | reasoning |
|---|---|---|---|
| per-node disk (data volume) | 104 GiB used, provision >= 155 GiB | 259 GiB used, provision >= 569 GiB | schema changes and index backfills need up to 3x the range size free (online-schema-changes); compaction headroom 30%; NVMe/SSD only |
| per-node RAM | 8 GiB container (cache 2 GiB, SQL 2 GiB, rest Go heap + OS) | 16-32 GiB container (cache 4-8 GiB) | cache must cover the hot index set (61 GiB full vs 6 GiB total today): at 3 x 8 GiB cache the predicted miss ratio is 6% |
| per-node CPU | 2-3 vCPU | 4-8 vCPU | 8-core laptop shared by 3 nodes + load generator: leave >= 2 cores to the client; hash-sharded fan-out and trigram scans are CPU-bound |
| ranges | ~576 ranges in the database | ~1,438 ranges in the subset | each range = a Raft group; keep < 100k ranges/cluster on this hardware; 512 MiB split size |
| file descriptors / memory | ulimit -n >= 65535 | same | LSM files per node grow with data |
| network | docker bridge (local) | same | RF3 replication traffic stays on the host; no WAN latency (use `--locality` + tc netem to emulate 5-60 ms for multi-region tests) |


### 10.2 Contention analysis (single-row serialisation points)

Model: a transaction holding a row lock for S ms gives a ceiling of 1000/S transactions per second per row; with Poisson arrivals at rate lambda the mean queue wait is `Wq = rho S / (2 (1 - rho))`, rho = lambda S / 1000 (M/D/1). S is estimated as (statements - 1) x 1.5 ms round trips + 6 ms commit (one consensus round + parallel commit) and must be **measured** in B70 (statement trace of Q8/Q9).

| serialisation point | mechanism | S ms | ceiling | load A | load B | load C | analysis and mitigation |
|---|---|---|---|---|---|---|---|
| wallet (Q8 debit/credit) | `wallet` row: `SELECT ... FOR UPDATE` then `UPDATE`; 4 statements | 12.0 | 83/s | 5/s: 0.4 ms (rho 0.06) | 20/s: 1.9 ms (rho 0.24) | 60/s: 15.4 ms (rho 0.72) | one writer per wallet by design (BR-SUB-08); organisation wallets shared by many recruiters: 60 downloads/s on ONE wallet is unrealistic (credits are bought in packs), but bulk outreach (`outreach_credit_usage`) can burst: batch the debit (one ledger row per batch) instead of per recipient |
| device slot / licence seat (Q9 login) | `licence_seat` row per user; slots per seat | 12.0 | 83/s | 0.2/s: 0.0 ms (rho 0.00) | 1/s: 0.1 ms (rho 0.01) | 3/s: 0.2 ms (rho 0.04) | locks are per user, so users never contend with each other; same user's concurrent logins queue; ceiling 1000/12 = 83 logins/s PER USER, far above any real rate |
| invoice series | `invoice_number_series` row per (seller_country_id, series_document_kind_id, prefix, fiscal_year_label), `UPDATE next_number+1` in the issuing txn (gap-free) | 10.5 | 95/s | 5/s: 0.3 ms (rho 0.05) | 20/s: 1.4 ms (rho 0.21) | 80/s: 27.6 ms (rho 0.84) | ONE row per series is global to the country+year: month-end invoice runs (300/s) saturate it. Mitigations: issue invoices from one worker in batches, assign N numbers in one UPDATE (`next_number = next_number + N`), or split the series by issuing entity (prefix); numbering stays gap-free because the number is assigned in the issuing txn |
| ad budget pacing | `ad_budget_shard` (16 random shards per campaign) + hourly accrual into `ad_campaign.spent_amount/ledger_sequence` | 9.0 | 111/s | 200/s: saturated | 1000/s: saturated | 5000/s: saturated | click-time charges pick a random shard: per-shard rate = total/16; 16 shards x 111/s = ~1,800 charges/s per campaign; flush job locks the campaign row once per hour. Hot campaigns above that need 64 shards (CHECK shard_no BETWEEN 0 AND 15 must be widened) |
| audit chain heads | `audit_chain_head` row per chain (32 chains), FOR UPDATE per audit write | 10.5 | 95/s | 20/s: 1.4 ms (rho 0.21) | 100/s: saturated | 400/s: saturated | 32 chains x 95/s = ~3,000 audit events/s ceiling; the audit write is in the business txn, so a slow audit chain slows the business action: monitor chain queueing |
| job counters | `job_requirement_stat` row per requirement (view/apply counters) | 9.0 | 111/s | 1/s: 0.0 ms (rho 0.01) | 10/s: 0.4 ms (rho 0.09) | 50/s: 3.7 ms (rho 0.45) | viral job at 50 applications/s would saturate one row; counters are updated asynchronously in batches by a worker (BR), never in the apply txn (Q6 does not touch it) |


Under SERIALIZABLE, `FOR UPDATE` locks are best-effort (lost on lease transfer/range split) so a 40001 retry is possible even when the lock queue is short; the billing role can set `enable_durable_locking_for_serializable = true` (crdb_capabilities.md row 16b). B70 records retry counts per hot row at 4/16/64 clients.

### 10.3 Foreign-key overhead

2951 foreign keys over 757 tables; 882 of them point at lookup tables (tiny, permanently cached: ~0.05 ms) and 2069 at entity tables (a point read of the parent primary key, possibly on another node: ~0.3-1 ms, executed after the mutation, in parallel batches per statement). Every FK also needs a leading index on the child column (lint D-20), which is where most of the 2270 secondary indexes come from.

| table | FKs | to lookups | to entities | indexes + unique | FK read cost per insert (serial worst / parallel best, ms) | write amplification |
|---|---|---|---|---|---|---|
| `job_requirement` | 25 | 12 | 13 | 16 | 8.4 / 0.7 | 1 + 16 KV writes |
| `fact_application_pipeline` | 16 | 0 | 16 | 6 | 9.6 / 0.7 | 1 + 6 KV writes |
| `candidate_profile` | 16 | 8 | 8 | 13 | 5.2 / 0.7 | 1 + 13 KV writes |
| `sponsored_job` | 15 | 4 | 11 | 7 | 6.8 / 0.7 | 1 + 7 KV writes |
| `staffing_assignment` | 13 | 4 | 9 | 14 | 5.6 / 0.7 | 1 + 14 KV writes |
| `gig_request` | 13 | 7 | 6 | 11 | 3.9 / 0.7 | 1 + 11 KV writes |
| `fact_gig_assignment` | 12 | 0 | 12 | 6 | 7.2 / 0.7 | 1 + 6 KV writes |
| `fact_outreach_recipient` | 12 | 0 | 12 | 6 | 7.2 / 0.7 | 1 + 6 KV writes |
| `ad_audience_rule` | 12 | 4 | 8 | 3 | 5.0 / 0.7 | 1 + 3 KV writes |
| `dim_job_requirement` | 11 | 1 | 10 | 12 | 6.0 / 0.7 | 1 + 12 KV writes |
| `fact_application_event` | 11 | 0 | 11 | 5 | 6.6 / 0.7 | 1 + 5 KV writes |
| `fact_support_ticket` | 11 | 0 | 11 | 4 | 6.6 / 0.7 | 1 + 4 KV writes |


Findings: (a) high-volume insert paths pay the entity-FK checks (Q6: job_application has FKs to candidate_profile, organisation, job_requirement (composite), job_position, resume, ...). The composite FK `(organisation_id, job_requirement_id)` adds tenant integrity at the price of one extra parent read. (b) FK checks on parents that are TTL tables are avoided by design (no inbound FKs). (c) `ON DELETE RESTRICT` parents deleted by retention jobs scan child indexes: all children have leading indexes. (d) FKs to lookups are cheap and keep Section 5 rule 3 (no free-text enums); do not drop them for speed. (e) Bulk load: `IMPORT INTO` leaves FKs unvalidated; the seed uses `COPY`/batched INSERT in FK order (doc 12) and runs `VALIDATE CONSTRAINT` or the orphan checks of doc 12 section 6.

## 11. Findings and recommendations to the data-model owner

These are observations from the model; this document does not edit the staging files.

| id | finding | recommendation |
|---|---|---|
| R-01 | `agg_funnel_daily` has no index that serves a category + date-range query; the hash-sharded date index is not covering (Q12). | Add `idx_agg_funnel_daily_dim_job_category_id_period_date ON agg_funnel_daily (dim_job_category_id, period_date) STORING (applications, shortlisted, joined)`; same pattern for other `agg_*` tables. |
| R-02 | Trigram indexes: 18 inverted indexes on STRING columns carry `opclass: gin_trgm_ops` in the dictionary (Indexes sheet, last column); 0 lack it. A plain inverted index on a STRING column is not the trigram index the queries need. | The DDL generator emits `USING GIN (col gin_trgm_ops)` from the `opclass` column; JSONB inverted indexes have an empty `opclass` and use plain `CREATE INVERTED INDEX`. |
| R-03 | Covering indexes on `job_application` (and others) do not store `deleted_at`, so every soft-delete-aware query (Q3, Q4) adds an index join per returned row. | Add `deleted_at` to STORING of `idx_job_application_job_requirement_id_status_applied` and `idx_job_application_candidate_profile_id_applied_at`, or make them partial on `deleted_at IS NULL`. |
| R-04 | `idx_app_user_last_login_at` is a sequential-key index on a high-volume table (hot range at login rate). | Hash-shard it or remove it; see section 6. |
| R-05 | `idx_job_requirement_title_search_trgm` is not partial, so closed/deleted jobs enlarge the posting lists (Q2). | Make it partial `WHERE is_live` (trigram + partial is supported) or accept and filter. |
| R-06 | Q5 sorts by `last_active_at`, which is in no usable index. | Add `(current_city_id, last_active_at DESC) STORING (current_job_title, total_experience_months) WHERE deleted_at IS NULL AND is_searchable` if the B70 baseline confirms the prediction. |
| R-07 | Viewer-scoped views (`vw_recruiter_job_requirement`, `vw_candidate_visible_to_user`) are pair views: without the viewer predicate they fan out over all viewers. Custom session variables are unverified on v26.2 (crdb_capabilities.md row 10), so the viewer is an explicit function/query argument (`fn_recruiter_job_requirement(viewer)`, `viewer_app_user_id = $1`). | Keep the viewer predicate mandatory (CI lint rejects unfiltered references, statement_timeout per pool role) and probe the viewer-filtered plans in B70; both views have a single definition. |
| R-08 | `idx_candidate_activity_log_activity_type_id_occurred_at` and similar (enum, timestamp) indexes on very_high tables concentrate writes on few prefixes (section 6). | Hash-shard or lead with `candidate_profile_id`. |

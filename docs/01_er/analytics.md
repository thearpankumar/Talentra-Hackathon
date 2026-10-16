# ER diagram: Analytics layer and reporting marts

Module key `analytics`, owner Team, 80 tables (5 lookups). Generated from `05_data_dictionary.xlsx` (do not edit by hand). Columns shown: primary key, foreign keys, unique/natural keys and a few business columns; see the dictionary for all columns. Edges to lookup (enumeration) tables are listed under *Lookup references* rather than drawn.

## Design notes

<!-- analytics.yaml -->
## Analytics layer and reporting marts: design notes (milestones B49, B52, B55, B58, B77)

**Scope.** The docket refers to "the KPIs in Section 8" but the supplied docket has no Section 8 (grep for KPI/Section 8 finds only the module table and B49/B55). The team therefore defines the KPI set below (K01..K20). Replace it if a Section 8 is issued later; each KPI maps to one aggregate table, so a change only touches `dim_kpi_definition` rows and the aggregate columns.

**Abbreviations defined here (Rule 1):** ETL = extract-transform-load; KPI = key performance indicator; MRR = monthly recurring revenue; SCD = slowly changing dimension; DAU/WAU/MAU = daily/weekly/monthly active users; SLA = service-level agreement.

### Architecture (Kimball star schema, same cluster)
* Prefixes (Rule 8, D-18): `dim_` dimensions (also the 5 small lookups and the audit dimension), `fact_` facts, `agg_` aggregates, `bridge_` many-to-many resolvers, `mv_`/`vw_` views. Tier of the module: required (milestones B49/B52/B55/B58/B77); it is not one of the optional Tier 3 domains.
* Conformed dimensions are shared by every fact: `dim_date` (role-playing: applied, viewed, shortlisted ... milestone dates), `dim_candidate`, `dim_organisation`, `dim_user`, `dim_job_requirement`, `dim_skill`, `dim_location`, `dim_job_category`, `dim_source`, `dim_channel`, `dim_application_status`, `dim_device_platform`, `dim_demand_type` (job vs gig). `dim_application_status.stage_group_code` maps gig application statuses onto the same funnel stages, so job and gig funnels are comparable.
* Surrogate keys: UUID v4 (D-02) except `dim_date` and `dim_time_of_day`, whose ids are deterministic UUIDv5 of the ISO date / minute so loaders resolve them without a lookup (deviation from random UUIDs, safe because the tables are tiny and pre-generated). Because UUID keys are not time-ordered, time-range scans use real date/timestamp columns (`snapshot_date`, `period_date`, `event_at`) with hash-sharded indexes; the FK key is used only for joins.
* Facts carry copies of source ids (`job_application_id`, `wallet_ledger_entry_id`, ... marked external_ref) as degenerate dimensions and an idempotency `source_ref`. **They are deliberately not FK-enforced to the OLTP tables** (deviation from Rule 2, recorded): (1) analytics must survive OLTP hard deletes, erasure and partitioning; (2) loads must not take FK checks or locks against hot OLTP ranges; (3) facts are append-only history that must remain stable if a source row is later corrected. Compensating control: the per-run reconciliation job in `agg_data_quality_result`. Every fact-to-dimension and aggregate-to-dimension link IS enforced (RESTRICT).
* Unknown members (Kimball): facts never hold NULL dimension keys for "unknown"; they point to reserved placeholder rows flagged by `dim_placeholder_kind_id` = unknown or not_applicable (two different rows, Rule 5). The only NULL keys are milestone dates in accumulating snapshots and optional role keys, each with a null_why.
* Time zone: `dim_date`/`dim_time_of_day` are in the business time zone Asia/Kolkata; `event_at` stays UTC. Fiscal year runs April-March.

### Fact table types
* **Transaction/event grain** (append-only): fact_application_event, fact_profile_view, fact_profile_download, fact_search_event, fact_notification_delivery, fact_wallet_transaction, fact_payment, fact_subscription_period, fact_session_activity, fact_user_signup, fact_sourcing_request_event.
* **Periodic snapshot** (append-only, one row per entity per day): fact_job_posting_daily, fact_gig_request_daily, fact_subscription_daily_snapshot, fact_candidate_activity_daily, fact_licence_seat_daily.
* **Accumulating snapshot** (updated in place until closed, delete_policy hard): fact_application_pipeline (apply -> view -> shortlist -> interview -> offer -> accept -> join with lag columns), fact_gig_assignment, fact_support_ticket, fact_outreach_recipient. This is the one place where "facts are append-only" is relaxed: an accumulating snapshot is by definition updated as milestones arrive; the same information is also kept append-only in the event fact (fact_application_event).
* **Factless-style bridge/event** use: fact_sourcing_request_event and fact_user_signup have few measures; they exist for counting.
* Gigs vs jobs: gigs stay a separate OLTP subsystem (D-15) but are unified for reporting through `dim_demand_type`, `dim_job_category` and `vw_unified_demand_daily` (UNION ALL of fact_job_posting_daily and fact_gig_request_daily).

### KPI definitions (exact formulas)
| Id | KPI | Formula | Served by |
|---|---|---|---|
| K01 | Time-to-fill (days) | mean over requirements whose last position was filled in the month of (fill date - first publish date); p50/p90 also stored | agg_time_to_fill_monthly |
| K02 | Time-to-hire (days) | mean of lag_applied_to_offer_accepted_days for applications whose offer was accepted in the month | agg_time_to_fill_monthly |
| K03 | Funnel conversion | stage_k count / stage_(k-1) count within the application-date cohort (view, shortlist, interview, offer, acceptance) | agg_funnel_daily |
| K04 | Application-to-hire rate | joined / applications in the cohort | agg_funnel_daily |
| K05 | Offer acceptance rate | accepted / (accepted + declined + expired) for offers decided in the month | agg_offer_acceptance_monthly |
| K06 | Cost-per-hire | value of credits consumed on postings, downloads and outreach attributed to requirements with a hire in the month / hires | agg_cost_per_hire_monthly |
| K07 | Source effectiveness | hires / applications and hires / signups per source; cost per hire per source | agg_source_effectiveness_monthly |
| K08 | Candidate activation (7-day) | sign-ups who reach profile completion >= 80% AND submit an application within 7 days / sign-ups in the week | agg_candidate_activation_weekly |
| K09 | Candidate retention | active_users in month n after sign-up / cohort_size | agg_candidate_retention_cohort |
| K10 | DAU, WAU, MAU, stickiness | distinct active candidates per day / trailing 7 / trailing 30 days; stickiness = DAU / MAU | agg_active_user_daily |
| K11 | Recruiter response rate | applications whose first recruiter action is within 48 h / applications received | agg_recruiter_activity_daily |
| K12 | Outreach reply rate | replies / outreach messages sent | agg_recruiter_activity_daily |
| K13 | Wallet burn | credits debited per day; days of cover = closing balance / mean daily burn of the trailing 7 days | agg_wallet_burn_daily |
| K14 | MRR, churn, NRR | MRR = sum of monthly-normalised price of active subscriptions at month end; churn = churned MRR / opening MRR; NRR = (opening + expansion - contraction - churn) / opening | agg_revenue_monthly |
| K15 | Net revenue | captured gross - refunds - chargebacks - tax | agg_revenue_monthly |
| K16 | Notification performance | delivery = delivered/sent; open = opened/delivered; click-through = clicked/delivered | agg_notification_performance_daily |
| K17 | Gig fill rate | gig requests with all positions accepted / requests posted in the week; hours to first assignment | agg_gig_fill_rate_weekly |
| K18 | Search quality | zero-result rate, click-through rate, mean first-click position, reformulation rate, p95 latency | agg_search_quality_daily |
| K19 | Support SLA attainment | tickets with first response within target / tickets opened | agg_support_sla_daily |
| K20 | Licence utilisation | seats active / seats licensed; device-limit rejections | agg_licence_utilisation_daily |
All rates are stored as ratios 0..1 (NRR 0..10); a NULL ratio means a zero denominator (not 0%).

### SCD Type 2 (B58)
`dim_candidate`, `dim_organisation`, `dim_user`, `dim_job_requirement`, `dim_gig_worker`, `dim_plan` carry: natural key (copy), `version_no`, `valid_from`, `valid_to`, `is_current`, `hash_diff`. Keys: `uq (natural key, version_no)`, partial unique `(natural key) WHERE is_current` (at most one current version), CHECK `valid_to IS NULL OR valid_to >= valid_from`, CHECK is_current agrees with open-ended valid_to. Maintenance algorithm, per batch, in one transaction per natural key (SERIALIZABLE; retry on 40001): (1) read the source row AS OF SYSTEM TIME; (2) compute `hash_diff` over the Type-2 attributes only (Type-1 attributes such as display name are overwritten in place); (3) `SELECT ... FOR UPDATE` the current version; (4) if hashes are equal: no-op; (5) else `UPDATE` current SET `valid_to = change_time, is_current = false` then `INSERT` a new row with `version_no + 1`, `valid_from = change_time`, `valid_to = NULL`; the partial unique index makes a concurrent double-insert fail. Facts resolve the dimension key with the as-of lookup `valid_from <= event_at AND (valid_to IS NULL OR event_at < valid_to)` using `idx (natural key, valid_from DESC)`. Late-arriving facts therefore get the version in force at event time; a late-arriving dimension change that predates the current version splits the older version (insert-between, renumber not required because `version_no` is only unique, order is by `valid_from`).

### Aggregate refresh rules (B58)
1. **Incremental by watermark:** each pipeline reads `dim_etl_watermark.last_watermark_at - late_arrival_window` (default 48 h) up to a snapshot instant taken with `AS OF SYSTEM TIME follower_read_timestamp()` and registers a `dim_etl_run` row (idempotent `run_key`).
2. **Idempotent upsert by grain:** every aggregate has `uq_<table>_grain`; the load is `INSERT ... ON CONFLICT (grain) DO UPDATE` from a re-aggregation of the affected grain cells (never a delta add), so re-running a window is safe.
3. **Late-arriving facts:** a fact whose business date is older than the window re-computes only its grain cell(s) (via the touched-key set), so closed days/months are corrected automatically.
4. **Restatement:** corrections to closed periods (more than 48 h) are run as `dim_etl_run_type = restatement` with `restated_dim_etl_run_id` pointing at the superseded run; aggregates are rewritten for the affected cells and `last_refreshed_at`/`load_dim_etl_run_id` change. Financial aggregates (agg_revenue_monthly) are restated only through an approved run and the previous values are exported to the warehouse first.
5. **Publish gate:** results of `agg_data_quality_result` with `is_blocking` must pass (row-count reconciliation fact vs source, orphan dimension keys = 0, grain uniqueness) before dashboards read the new run (views read only rows whose run status = succeeded).
6. **Cohort aggregates** (funnel, retention) are restated for the cohort cells that progress.

### Same cluster, follower reads, warehouse option (B52, B58)
* Analytics lives in the OLTP cluster. ETL and dashboards read with `AS OF SYSTEM TIME follower_read_timestamp()` (reads data a few seconds in the past; CockroachDB docs cite at least about 4.2 s for exact-staleness reads; availability by edition must be checked against the licence in use) so scans do not block or contend with writes; `REFRESH MATERIALIZED VIEW` also accepts `AS OF SYSTEM TIME`. Materialised views are not auto-refreshed and CockroachDB has no refresh-on-commit and (per docs) no documented incremental refresh, so `mv_*` are refreshed by scheduled jobs (`CREATE SCHEDULE`) after the aggregate publish gate. `CONCURRENTLY` is accepted but is the default.
* Isolation of load: analytics ETL runs under a dedicated SQL role with a separate connection pool and `SET statement_timeout`; the huge facts use hash-sharded indexes (`bucket_count` 8-32) so append-heavy time order does not create a hot range; row-level TTL (`ttl`) bounds search, session, view and notification facts.
* Option: a changefeed on the fact and aggregate tables to Kafka/cloud storage feeds a warehouse (BigQuery/Snowflake/ClickHouse) when OLAP load exceeds what the OLTP cluster should carry; the same `source_ref`/`load_dim_etl_run_id` give the sink exactly-once de-duplication. Decision: start in-cluster (single system to defend, no ETL tool), export by changefeed once p99 OLTP latency is affected; no schema change is needed because facts are append-only and keyed.

### PII and security (B77)
No names, emails, phones, addresses or dates of birth in analytics. Candidate/user keys are pseudonymous UUID copies classed Personal; banded attributes (age, experience, salary) replace exact values; gender is optional with k>=10 suppression in aggregates; search keywords are stored as a SHA-256 only. Erasure: dimension rows are anonymised in place (natural key replaced by a random UUID, `is_erased`), so facts need no rewrite. Access: the `analytics_reader` role gets SELECT on `dim_/fact_/agg_/bridge_/mv_/vw_` only; `analytics_etl` gets INSERT/UPDATE on analytics tables and SELECT on OLTP; nobody but `analytics_etl` writes; facts have INSERT/SELECT grants only (append-only, D-12 P5) except the four accumulating snapshots.

### Extension points
Sub-roles inside employers would add a `dim_user` attribute and a bridge; a warehouse export needs no change; conformed `dim_skill` allows a future skill-gap fact. Competitor coverage: LinkedIn Talent Insights style supply/demand by skill and location is reproduced by the skill bridges plus fact_job_posting_daily and fact_candidate_activity_daily; Naukri-style recruiter response and profile-view/download analytics by fact_profile_view/download and agg_recruiter_activity_daily; Monster-style source and time-to-fill analytics by K01/K07.

## Diagram

```mermaid
erDiagram
  agg_active_user_daily {
    UUID id PK
    UUID dim_date_id FK
    UUID dim_user_role_id FK
    UUID dim_device_platform_id FK
    INT8 daily_active_users
    INT8 weekly_active_users
    INT8 monthly_active_users
    UUID load_dim_etl_run_id FK
  }
  agg_candidate_activation_weekly {
    UUID id PK
    UUID week_start_dim_date_id FK
    UUID dim_source_id FK
    UUID dim_device_platform_id FK
    INT8 signups
    INT8 profile_completed
    INT8 first_application
    UUID load_dim_etl_run_id FK
  }
  agg_candidate_retention_cohort {
    UUID id PK
    UUID cohort_month_start_dim_date_id FK
    UUID dim_source_id FK
    INT2 months_since_signup UK
    INT8 cohort_size
    INT8 active_users
    DECIMAL_9_6 retention_rate
    UUID load_dim_etl_run_id FK
  }
  agg_cost_per_hire_monthly {
    UUID id PK
    UUID month_start_dim_date_id FK
    UUID dim_job_category_id FK
    UUID currency_id FK
    INT8 hires
    INT8 credits_consumed
    DECIMAL_18_4 spend_amount
    UUID load_dim_etl_run_id FK
  }
  agg_data_quality_result {
    UUID id PK
    UUID checked_dim_etl_run_id FK
    STRING_64 check_code UK
    STRING_64 target_table_code UK
    INT8 rows_checked
    INT8 rows_failed
    BOOL is_blocking
  }
  agg_funnel_daily {
    UUID id PK
    UUID applied_dim_date_id FK
    UUID dim_job_category_id FK
    UUID dim_location_id FK
    UUID dim_source_id FK
    UUID dim_job_type_id FK
    INT8 applications
    INT8 viewed
    INT8 shortlisted
    UUID load_dim_etl_run_id FK
  }
  agg_gig_fill_rate_weekly {
    UUID id PK
    UUID week_start_dim_date_id FK
    UUID dim_job_category_id FK
    UUID dim_location_id FK
    UUID currency_id FK
    INT8 requests_posted
    INT8 requests_filled
    INT8 requests_expired
    UUID load_dim_etl_run_id FK
  }
  agg_licence_utilisation_daily {
    UUID id PK
    UUID dim_date_id FK
    UUID dim_plan_id FK
    INT8 licences_active
    INT8 seats_licensed
    INT8 seats_assigned
    UUID load_dim_etl_run_id FK
  }
  agg_notification_performance_daily {
    UUID id PK
    UUID dim_date_id FK
    UUID dim_channel_id FK
    UUID dim_notification_type_id FK
    UUID currency_id FK
    INT8 sent
    INT8 delivered
    INT8 failed
    UUID load_dim_etl_run_id FK
  }
  agg_offer_acceptance_monthly {
    UUID id PK
    UUID month_start_dim_date_id FK
    UUID dim_job_category_id FK
    UUID dim_job_type_id FK
    UUID currency_id FK
    INT8 offers_extended
    INT8 offers_accepted
    INT8 offers_declined
    UUID load_dim_etl_run_id FK
  }
  agg_recruiter_activity_daily {
    UUID id PK
    UUID dim_date_id FK
    UUID recruiter_dim_user_id FK
    UUID dim_organisation_id FK
    INT8 applications_received
    INT8 applications_actioned
    INT8 first_response_within_sla
    UUID load_dim_etl_run_id FK
  }
  agg_revenue_monthly {
    UUID id PK
    UUID month_start_dim_date_id FK
    UUID dim_plan_id FK
    UUID dim_payment_method_id FK
    UUID currency_id FK
    DECIMAL_18_4 gross_revenue_amount
    DECIMAL_18_4 refund_amount
    DECIMAL_18_4 net_revenue_amount
    UUID load_dim_etl_run_id FK
  }
  agg_search_quality_daily {
    UUID id PK
    UUID dim_date_id FK
    UUID dim_search_surface_id FK
    UUID dim_device_platform_id FK
    INT8 searches
    INT8 zero_result_searches
    DECIMAL_9_6 zero_result_rate
    UUID load_dim_etl_run_id FK
  }
  agg_source_effectiveness_monthly {
    UUID id PK
    UUID month_start_dim_date_id FK
    UUID dim_source_id FK
    UUID dim_job_category_id FK
    UUID currency_id FK
    INT8 signups
    INT8 applications
    INT8 hires
    UUID load_dim_etl_run_id FK
  }
  agg_support_sla_daily {
    UUID id PK
    UUID dim_date_id FK
    UUID dim_ticket_category_id FK
    UUID dim_ticket_priority_id FK
    INT8 tickets_opened
    INT8 tickets_resolved
    INT8 first_response_within_sla
    UUID load_dim_etl_run_id FK
  }
  agg_time_to_fill_monthly {
    UUID id PK
    UUID month_start_dim_date_id FK
    UUID dim_job_category_id FK
    UUID dim_location_id FK
    UUID dim_job_type_id FK
    INT8 requirements_filled
    INT8 requirements_expired_unfilled
    DECIMAL_12_2 avg_days_to_fill
    UUID load_dim_etl_run_id FK
  }
  agg_wallet_burn_daily {
    UUID id PK
    UUID dim_date_id FK
    UUID dim_wallet_entry_type_id FK
    UUID currency_id FK
    INT8 entries
    INT8 credits_debited
    INT8 credits_credited
    UUID load_dim_etl_run_id FK
  }
  bridge_candidate_skill {
    UUID id PK
    UUID dim_candidate_id FK
    UUID dim_skill_id FK
    DECIMAL_9_6 weighting_factor
    DECIMAL_4_1 years_of_experience
    STRING_24 proficiency_level_code
    UUID load_dim_etl_run_id FK
  }
  bridge_date_country_holiday {
    UUID id PK
    UUID dim_date_id FK
    UUID country_id FK
    STRING_150 holiday_name
    BOOL is_national
  }
  bridge_gig_worker_skill {
    UUID id PK
    UUID dim_gig_worker_id FK
    UUID dim_skill_id FK
    DECIMAL_9_6 weighting_factor
    UUID load_dim_etl_run_id FK
  }
  bridge_job_requirement_skill {
    UUID id PK
    UUID dim_job_requirement_id FK
    UUID dim_skill_id FK
    DECIMAL_9_6 weighting_factor
    BOOL is_mandatory
    UUID load_dim_etl_run_id FK
  }
  dim_application_status {
    UUID id PK
    STRING_64 application_status_code UK
    STRING_150 application_status_name
    STRING_64 stage_group_code
    INT4 pipeline_order
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_candidate {
    UUID id PK
    UUID candidate_profile_id UK
    INT4 version_no UK
    TIMESTAMPTZ valid_from
    TIMESTAMPTZ valid_to
    BOOL is_current
    UUID current_dim_industry_id FK
    UUID home_dim_location_id FK
    UUID acquisition_dim_source_id FK
    UUID signup_dim_date_id FK
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_channel {
    UUID id PK
    STRING_64 channel_code UK
    STRING_150 channel_name
    STRING_64 provider_group_code
    BOOL is_paid_channel
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_date {
    UUID id PK
    DATE full_date UK
    INT4 year_number
    INT2 quarter_number
    INT2 month_number
  }
  dim_delivery_status {
    UUID id PK
    STRING_64 delivery_status_code UK
    STRING_150 delivery_status_name
    BOOL is_terminal
    BOOL is_failure
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_demand_type {
    UUID id PK
    STRING_64 demand_type_code UK
    STRING_150 demand_type_name
    BOOL is_gig
    INT4 sort_order
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_device_platform {
    UUID id PK
    STRING_64 device_platform_code UK
    STRING_150 device_platform_name
    STRING_64 os_family_code
    BOOL is_mobile
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_etl_run {
    UUID id PK
    STRING_64 pipeline_code
    STRING_150 run_key UK
    UUID dim_etl_run_type_id FK
    UUID dim_etl_run_status_id FK
    TIMESTAMPTZ started_at
    TIMESTAMPTZ finished_at
    UUID restated_dim_etl_run_id FK
  }
  dim_etl_watermark {
    UUID id PK
    STRING_64 pipeline_code UK
    STRING_64 source_table_code
    TIMESTAMPTZ last_watermark_at
    UUID last_successful_dim_etl_run_id FK
    INTERVAL late_arrival_window
  }
  dim_gig_worker {
    UUID id PK
    UUID gig_worker_profile_id UK
    INT4 version_no UK
    TIMESTAMPTZ valid_from
    TIMESTAMPTZ valid_to
    BOOL is_current
    UUID home_dim_location_id FK
    UUID primary_dim_job_category_id FK
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_industry {
    UUID id PK
    UUID industry_id UK
    STRING_150 industry_name
    STRING_150 sector_name
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_job_category {
    UUID id PK
    UUID job_category_id UK
    STRING_150 job_category_name
    UUID parent_dim_job_category_id FK
    INT2 level_number
    STRING_40 job_family_code
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_job_requirement {
    UUID id PK
    UUID job_requirement_id UK
    INT4 version_no UK
    TIMESTAMPTZ valid_from
    TIMESTAMPTZ valid_to
    BOOL is_current
    UUID dim_organisation_id FK
    UUID posted_by_dim_user_id FK
    UUID dim_job_category_id FK
    UUID dim_job_type_id FK
    UUID dim_location_id FK
    UUID dim_demand_type_id FK
    UUID published_dim_date_id FK
    UUID expiry_dim_date_id FK
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
    UUID currency_id FK
  }
  dim_job_type {
    UUID id PK
    STRING_64 job_type_code UK
    STRING_150 job_type_name
    STRING_64 employment_type_code
    STRING_64 work_type_code
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_kpi_definition {
    UUID id PK
    STRING_40 kpi_code UK
    STRING_150 kpi_name
    STRING_2000 definition
    STRING_2000 formula
    UUID dim_kpi_unit_id FK
    UUID dim_kpi_grain_id FK
  }
  dim_location {
    UUID id PK
    STRING_80 location_key UK
    UUID city_id
    UUID state_region_id
    UUID country_id
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_login_outcome {
    UUID id PK
    STRING_64 login_outcome_code UK
    STRING_150 login_outcome_name
    BOOL is_success
    BOOL is_device_limit
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_notification_type {
    UUID id PK
    STRING_64 notification_type_code UK
    STRING_150 notification_type_name
    BOOL is_marketing
    BOOL is_transactional
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_organisation {
    UUID id PK
    UUID organisation_id UK
    INT4 version_no UK
    TIMESTAMPTZ valid_from
    TIMESTAMPTZ valid_to
    BOOL is_current
    UUID primary_dim_industry_id FK
    UUID hq_dim_location_id FK
    UUID onboarded_dim_date_id FK
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_outreach_type {
    UUID id PK
    STRING_64 outreach_type_code UK
    STRING_150 outreach_type_name
    BOOL is_bulk
    INT4 sort_order
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_payment_method {
    UUID id PK
    STRING_64 payment_method_code UK
    STRING_150 payment_method_name
    BOOL is_online
    INT4 sort_order
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_payment_status {
    UUID id PK
    STRING_64 payment_status_code UK
    STRING_150 payment_status_name
    BOOL is_terminal
    BOOL is_successful
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_plan {
    UUID id PK
    UUID subscription_plan_id UK
    INT4 version_no UK
    TIMESTAMPTZ valid_from
    TIMESTAMPTZ valid_to
    BOOL is_current
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
    UUID currency_id FK
  }
  dim_search_surface {
    UUID id PK
    STRING_64 search_surface_code UK
    STRING_150 search_surface_name
    BOOL is_candidate_side
    INT4 sort_order
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_signup_method {
    UUID id PK
    STRING_64 signup_method_code UK
    STRING_150 signup_method_name
    BOOL is_social
    INT4 sort_order
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_skill {
    UUID id PK
    UUID skill_id UK
    STRING_150 skill_name
    UUID skill_category_id
    STRING_150 skill_category_name
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_source {
    UUID id PK
    STRING_64 source_code UK
    STRING_150 source_name
    STRING_64 source_group_code
    BOOL is_paid
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_sourcing_status {
    UUID id PK
    STRING_64 sourcing_status_code UK
    STRING_150 sourcing_status_name
    BOOL is_terminal
    INT4 sort_order
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_ticket_category {
    UUID id PK
    STRING_64 ticket_category_code UK
    STRING_150 ticket_category_name
    STRING_64 sla_group_code
    INT4 sort_order
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_ticket_priority {
    UUID id PK
    STRING_64 ticket_priority_code UK
    STRING_150 ticket_priority_name
    INT4 sla_target_minutes
    INT4 sort_order
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_time_of_day {
    UUID id PK
    TIME time_of_day UK
    INT2 hour_number
    INT2 minute_number
    STRING_16 day_part_code
  }
  dim_user {
    UUID id PK
    UUID app_user_id UK
    INT4 version_no UK
    TIMESTAMPTZ valid_from
    TIMESTAMPTZ valid_to
    BOOL is_current
    UUID dim_user_role_id FK
    UUID dim_organisation_id FK
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_user_role {
    UUID id PK
    STRING_64 user_role_code UK
    STRING_150 user_role_name
    BOOL is_supply_side
    INT4 sort_order
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  dim_wallet_entry_type {
    UUID id PK
    STRING_64 wallet_entry_type_code UK
    STRING_150 wallet_entry_type_name
    STRING_64 direction_code
    BOOL is_consumption
    UUID dim_placeholder_kind_id FK
    UUID load_dim_etl_run_id FK
  }
  fact_application_event {
    UUID id PK
    TIMESTAMPTZ event_at
    UUID dim_date_id FK
    UUID dim_time_of_day_id FK
    UUID load_dim_etl_run_id FK
    UUID job_application_id
    UUID dim_candidate_id FK
    UUID dim_job_requirement_id FK
    UUID dim_organisation_id FK
    UUID from_dim_application_status_id FK
    UUID to_dim_application_status_id FK
    UUID actor_dim_user_id FK
    UUID dim_source_id FK
    UUID dim_device_platform_id FK
    INT8 seconds_in_previous_status
    STRING_100 source_ref UK
  }
  fact_application_pipeline {
    UUID id PK
    UUID job_application_id UK
    UUID dim_candidate_id FK
    UUID dim_job_requirement_id FK
    UUID dim_organisation_id FK
    UUID owner_dim_user_id FK
    UUID dim_source_id FK
    UUID dim_job_type_id FK
    UUID current_dim_application_status_id FK
    UUID applied_dim_date_id FK
    UUID viewed_dim_date_id FK
    UUID shortlisted_dim_date_id FK
    UUID interviewed_dim_date_id FK
    UUID offered_dim_date_id FK
    UUID offer_accepted_dim_date_id FK
    UUID joined_dim_date_id FK
    UUID closed_dim_date_id FK
    INT4 lag_applied_to_viewed_days
    INT4 lag_applied_to_shortlisted_days
    INT4 lag_shortlisted_to_interviewed_days
    UUID load_dim_etl_run_id FK
  }
  fact_candidate_activity_daily {
    UUID id PK
    UUID dim_date_id FK
    UUID dim_candidate_id FK
    UUID primary_dim_device_platform_id FK
    UUID load_dim_etl_run_id FK
    INT4 login_count
    INT4 search_count
    INT4 job_view_count
  }
  fact_gig_assignment {
    UUID id PK
    UUID gig_assignment_id UK
    UUID gig_request_id
    UUID dim_gig_worker_id FK
    UUID dim_organisation_id FK
    UUID dim_job_category_id FK
    UUID dim_location_id FK
    UUID requested_dim_date_id FK
    UUID applied_dim_date_id FK
    UUID accepted_dim_date_id FK
    UUID started_dim_date_id FK
    UUID completed_dim_date_id FK
    UUID rated_dim_date_id FK
    INT4 lag_requested_to_accepted_hours
    INT4 lag_accepted_to_completed_hours
    UUID load_dim_etl_run_id FK
    UUID currency_id FK
  }
  fact_gig_request_daily {
    UUID id PK
    UUID dim_date_id FK
    UUID gig_request_id UK
    UUID dim_organisation_id FK
    UUID dim_job_category_id FK
    UUID dim_location_id FK
    UUID dim_demand_type_id FK
    UUID load_dim_etl_run_id FK
    INT4 positions_open
    INT8 applications_in_day
    INT8 applications_cumulative
    UUID currency_id FK
  }
  fact_job_posting_daily {
    UUID id PK
    UUID dim_date_id FK
    UUID dim_job_requirement_id FK
    UUID dim_organisation_id FK
    UUID dim_job_category_id FK
    UUID dim_location_id FK
    UUID load_dim_etl_run_id FK
    INT4 days_open
    INT4 positions_open
    INT4 positions_filled_cumulative
  }
  fact_licence_seat_daily {
    UUID id PK
    UUID dim_date_id FK
    UUID licence_id UK
    UUID dim_organisation_id FK
    UUID dim_plan_id FK
    UUID load_dim_etl_run_id FK
    INT4 seats_licensed
    INT4 seats_assigned
    INT4 seats_active
  }
  fact_notification_delivery {
    UUID id PK
    TIMESTAMPTZ event_at
    UUID dim_date_id FK
    UUID dim_time_of_day_id FK
    UUID load_dim_etl_run_id FK
    UUID notification_id
    UUID dim_channel_id FK
    UUID dim_notification_type_id FK
    UUID dim_delivery_status_id FK
    UUID dim_candidate_id FK
    UUID recipient_dim_user_id FK
    INT4 provider_latency_ms
    STRING_100 source_ref UK
    UUID currency_id FK
  }
  fact_outreach_recipient {
    UUID id PK
    UUID outreach_recipient_id UK
    UUID outreach_batch_id
    UUID dim_candidate_id FK
    UUID sender_dim_user_id FK
    UUID dim_organisation_id FK
    UUID dim_job_requirement_id FK
    UUID dim_outreach_type_id FK
    UUID dim_channel_id FK
    UUID sent_dim_date_id FK
    UUID delivered_dim_date_id FK
    UUID opened_dim_date_id FK
    UUID replied_dim_date_id FK
    UUID applied_dim_date_id FK
    INT4 reply_lag_minutes
    INT4 credits_consumed
    UUID load_dim_etl_run_id FK
  }
  fact_payment {
    UUID id PK
    TIMESTAMPTZ event_at
    UUID dim_date_id FK
    UUID dim_time_of_day_id FK
    UUID load_dim_etl_run_id FK
    UUID payment_order_id
    UUID dim_organisation_id FK
    UUID dim_plan_id FK
    UUID dim_payment_status_id FK
    UUID dim_payment_method_id FK
    DECIMAL_18_4 gross_amount
    STRING_100 source_ref UK
    UUID currency_id FK
  }
  fact_profile_download {
    UUID id PK
    TIMESTAMPTZ event_at
    UUID dim_date_id FK
    UUID dim_time_of_day_id FK
    UUID load_dim_etl_run_id FK
    UUID dim_candidate_id FK
    UUID downloader_dim_user_id FK
    UUID dim_organisation_id FK
    UUID dim_job_requirement_id FK
    INT4 credits_deducted
    UUID wallet_ledger_entry_id
    STRING_100 source_ref UK
  }
  fact_profile_view {
    UUID id PK
    TIMESTAMPTZ event_at
    UUID dim_date_id FK
    UUID dim_time_of_day_id FK
    UUID load_dim_etl_run_id FK
    UUID dim_candidate_id FK
    UUID viewer_dim_user_id FK
    UUID dim_organisation_id FK
    UUID origin_dim_search_surface_id FK
    UUID dim_device_platform_id FK
    UUID dim_job_requirement_id FK
    INT4 credits_consumed
    INT4 view_seconds
    STRING_100 source_ref UK
  }
  fact_search_event {
    UUID id PK
    TIMESTAMPTZ event_at
    UUID dim_date_id FK
    UUID dim_time_of_day_id FK
    UUID load_dim_etl_run_id FK
    UUID dim_search_surface_id FK
    UUID dim_device_platform_id FK
    UUID dim_candidate_id FK
    UUID dim_user_id FK
    UUID dim_location_id FK
    UUID dim_job_category_id FK
    STRING_64 query_hash
    INT4 result_count
    STRING_100 source_ref UK
  }
  fact_session_activity {
    UUID id PK
    TIMESTAMPTZ event_at
    UUID dim_date_id FK
    UUID dim_time_of_day_id FK
    UUID load_dim_etl_run_id FK
    UUID dim_login_outcome_id FK
    UUID dim_device_platform_id FK
    UUID dim_candidate_id FK
    UUID dim_user_id FK
    INT8 session_seconds
    INT4 page_view_count
    STRING_100 source_ref UK
  }
  fact_sourcing_request_event {
    UUID id PK
    TIMESTAMPTZ event_at
    UUID dim_date_id FK
    UUID load_dim_etl_run_id FK
    UUID sourcing_request_id
    UUID dim_organisation_id FK
    UUID consultant_dim_user_id FK
    UUID dim_job_requirement_id FK
    UUID dim_sourcing_status_id FK
    INT4 shortlist_candidate_count
    STRING_100 source_ref UK
  }
  fact_subscription_daily_snapshot {
    UUID id PK
    UUID dim_date_id FK
    UUID load_dim_etl_run_id FK
    UUID subscription_id UK
    UUID dim_organisation_id FK
    UUID dim_plan_id FK
    DECIMAL_18_4 monthly_recurring_value_amount
    INT4 seats_licensed
    INT4 seats_assigned
    UUID currency_id FK
  }
  fact_subscription_period {
    UUID id PK
    TIMESTAMPTZ event_at
    UUID dim_date_id FK
    UUID load_dim_etl_run_id FK
    UUID subscription_period_id UK
    UUID subscription_id
    UUID dim_organisation_id FK
    UUID dim_plan_id FK
    UUID period_start_dim_date_id FK
    UUID period_end_dim_date_id FK
    INT4 sequence_no
    STRING_100 source_ref UK
    UUID currency_id FK
  }
  fact_support_ticket {
    UUID id PK
    UUID support_ticket_id UK
    UUID dim_ticket_category_id FK
    UUID dim_ticket_priority_id FK
    UUID dim_channel_id FK
    UUID dim_candidate_id FK
    UUID dim_user_id FK
    UUID dim_organisation_id FK
    UUID opened_dim_date_id FK
    UUID first_responded_dim_date_id FK
    UUID resolved_dim_date_id FK
    UUID closed_dim_date_id FK
    INT4 first_response_minutes
    INT4 resolution_minutes
    INT4 sla_target_minutes
    UUID load_dim_etl_run_id FK
  }
  fact_user_signup {
    UUID id PK
    TIMESTAMPTZ event_at
    UUID dim_date_id FK
    UUID dim_time_of_day_id FK
    UUID load_dim_etl_run_id FK
    UUID app_user_id
    UUID dim_user_role_id FK
    UUID dim_signup_method_id FK
    UUID dim_source_id FK
    UUID dim_device_platform_id FK
    UUID dim_candidate_id FK
    UUID dim_location_id FK
    UUID referrer_dim_candidate_id FK
    BOOL is_verified_within_24h
    STRING_100 source_ref UK
  }
  fact_wallet_transaction {
    UUID id PK
    TIMESTAMPTZ event_at
    UUID dim_date_id FK
    UUID dim_time_of_day_id FK
    UUID load_dim_etl_run_id FK
    UUID wallet_ledger_entry_id
    UUID dim_organisation_id FK
    UUID dim_wallet_entry_type_id FK
    UUID actor_dim_user_id FK
    UUID dim_job_requirement_id FK
    UUID dim_candidate_id FK
    INT8 credits_delta
    STRING_100 source_ref UK
    UUID currency_id FK
  }
  country {
    UUID id PK
  }
  currency {
    UUID id PK
  }
  country ||--o{ bridge_date_country_holiday : "country_id"
  currency ||--o{ agg_cost_per_hire_monthly : "currency_id"
  currency ||--o{ agg_gig_fill_rate_weekly : "currency_id"
  currency ||--o{ agg_notification_performance_daily : "currency_id"
  currency ||--o{ agg_offer_acceptance_monthly : "currency_id"
  currency ||--o{ agg_revenue_monthly : "currency_id"
  currency ||--o{ agg_source_effectiveness_monthly : "currency_id"
  currency ||--o{ agg_wallet_burn_daily : "currency_id"
  currency ||--o{ dim_job_requirement : "currency_id"
  currency ||--o{ dim_plan : "currency_id"
  currency ||--o{ fact_gig_assignment : "currency_id"
  currency ||--o{ fact_gig_request_daily : "currency_id"
  currency ||--o{ fact_notification_delivery : "currency_id"
  currency ||--o{ fact_payment : "currency_id"
  currency ||--o{ fact_subscription_daily_snapshot : "currency_id"
  currency ||--o{ fact_subscription_period : "currency_id"
  currency ||--o{ fact_wallet_transaction : "currency_id"
  dim_application_status |o--o{ fact_application_event : "from_dim_application_status_id"
  dim_application_status ||--o{ fact_application_event : "to_dim_application_status_id"
  dim_application_status ||--o{ fact_application_pipeline : "current_dim_application_status_id"
  dim_candidate |o--o{ fact_notification_delivery : "dim_candidate_id"
  dim_candidate |o--o{ fact_search_event : "dim_candidate_id"
  dim_candidate |o--o{ fact_session_activity : "dim_candidate_id"
  dim_candidate |o--o{ fact_support_ticket : "dim_candidate_id"
  dim_candidate |o--o{ fact_user_signup : "dim_candidate_id"
  dim_candidate |o--o{ fact_user_signup : "referrer_dim_candidate_id"
  dim_candidate |o--o{ fact_wallet_transaction : "dim_candidate_id"
  dim_candidate ||--o{ bridge_candidate_skill : "dim_candidate_id"
  dim_candidate ||--o{ fact_application_event : "dim_candidate_id"
  dim_candidate ||--o{ fact_application_pipeline : "dim_candidate_id"
  dim_candidate ||--o{ fact_candidate_activity_daily : "dim_candidate_id"
  dim_candidate ||--o{ fact_outreach_recipient : "dim_candidate_id"
  dim_candidate ||--o{ fact_profile_download : "dim_candidate_id"
  dim_candidate ||--o{ fact_profile_view : "dim_candidate_id"
  dim_channel ||--o{ agg_notification_performance_daily : "dim_channel_id"
  dim_channel ||--o{ fact_notification_delivery : "dim_channel_id"
  dim_channel ||--o{ fact_outreach_recipient : "dim_channel_id"
  dim_channel ||--o{ fact_support_ticket : "dim_channel_id"
  dim_date |o--o{ dim_job_requirement : "expiry_dim_date_id"
  dim_date |o--o{ dim_job_requirement : "published_dim_date_id"
  dim_date |o--o{ dim_organisation : "onboarded_dim_date_id"
  dim_date |o--o{ fact_application_pipeline : "closed_dim_date_id"
  dim_date |o--o{ fact_application_pipeline : "interviewed_dim_date_id"
  dim_date |o--o{ fact_application_pipeline : "joined_dim_date_id"
  dim_date |o--o{ fact_application_pipeline : "offer_accepted_dim_date_id"
  dim_date |o--o{ fact_application_pipeline : "offered_dim_date_id"
  dim_date |o--o{ fact_application_pipeline : "shortlisted_dim_date_id"
  dim_date |o--o{ fact_application_pipeline : "viewed_dim_date_id"
  dim_date |o--o{ fact_gig_assignment : "accepted_dim_date_id"
  dim_date |o--o{ fact_gig_assignment : "applied_dim_date_id"
  dim_date |o--o{ fact_gig_assignment : "completed_dim_date_id"
  dim_date |o--o{ fact_gig_assignment : "rated_dim_date_id"
  dim_date |o--o{ fact_gig_assignment : "started_dim_date_id"
  dim_date |o--o{ fact_outreach_recipient : "applied_dim_date_id"
  dim_date |o--o{ fact_outreach_recipient : "delivered_dim_date_id"
  dim_date |o--o{ fact_outreach_recipient : "opened_dim_date_id"
  dim_date |o--o{ fact_outreach_recipient : "replied_dim_date_id"
  dim_date |o--o{ fact_support_ticket : "closed_dim_date_id"
  dim_date |o--o{ fact_support_ticket : "first_responded_dim_date_id"
  dim_date |o--o{ fact_support_ticket : "resolved_dim_date_id"
  dim_date ||--o{ agg_active_user_daily : "dim_date_id"
  dim_date ||--o{ agg_candidate_activation_weekly : "week_start_dim_date_id"
  dim_date ||--o{ agg_candidate_retention_cohort : "cohort_month_start_dim_date_id"
  dim_date ||--o{ agg_cost_per_hire_monthly : "month_start_dim_date_id"
  dim_date ||--o{ agg_funnel_daily : "applied_dim_date_id"
  dim_date ||--o{ agg_gig_fill_rate_weekly : "week_start_dim_date_id"
  dim_date ||--o{ agg_licence_utilisation_daily : "dim_date_id"
  dim_date ||--o{ agg_notification_performance_daily : "dim_date_id"
  dim_date ||--o{ agg_offer_acceptance_monthly : "month_start_dim_date_id"
  dim_date ||--o{ agg_recruiter_activity_daily : "dim_date_id"
  dim_date ||--o{ agg_revenue_monthly : "month_start_dim_date_id"
  dim_date ||--o{ agg_search_quality_daily : "dim_date_id"
  dim_date ||--o{ agg_source_effectiveness_monthly : "month_start_dim_date_id"
  dim_date ||--o{ agg_support_sla_daily : "dim_date_id"
  dim_date ||--o{ agg_time_to_fill_monthly : "month_start_dim_date_id"
  dim_date ||--o{ agg_wallet_burn_daily : "dim_date_id"
  dim_date ||--o{ bridge_date_country_holiday : "dim_date_id"
  dim_date ||--o{ dim_candidate : "signup_dim_date_id"
  dim_date ||--o{ fact_application_event : "dim_date_id"
  dim_date ||--o{ fact_application_pipeline : "applied_dim_date_id"
  dim_date ||--o{ fact_candidate_activity_daily : "dim_date_id"
  dim_date ||--o{ fact_gig_assignment : "requested_dim_date_id"
  dim_date ||--o{ fact_gig_request_daily : "dim_date_id"
  dim_date ||--o{ fact_job_posting_daily : "dim_date_id"
  dim_date ||--o{ fact_licence_seat_daily : "dim_date_id"
  dim_date ||--o{ fact_notification_delivery : "dim_date_id"
  dim_date ||--o{ fact_outreach_recipient : "sent_dim_date_id"
  dim_date ||--o{ fact_payment : "dim_date_id"
  dim_date ||--o{ fact_profile_download : "dim_date_id"
  dim_date ||--o{ fact_profile_view : "dim_date_id"
  dim_date ||--o{ fact_search_event : "dim_date_id"
  dim_date ||--o{ fact_session_activity : "dim_date_id"
  dim_date ||--o{ fact_sourcing_request_event : "dim_date_id"
  dim_date ||--o{ fact_subscription_daily_snapshot : "dim_date_id"
  dim_date ||--o{ fact_subscription_period : "dim_date_id"
  dim_date ||--o{ fact_subscription_period : "period_end_dim_date_id"
  dim_date ||--o{ fact_subscription_period : "period_start_dim_date_id"
  dim_date ||--o{ fact_support_ticket : "opened_dim_date_id"
  dim_date ||--o{ fact_user_signup : "dim_date_id"
  dim_date ||--o{ fact_wallet_transaction : "dim_date_id"
  dim_delivery_status ||--o{ fact_notification_delivery : "dim_delivery_status_id"
  dim_demand_type ||--o{ dim_job_requirement : "dim_demand_type_id"
  dim_demand_type ||--o{ fact_gig_request_daily : "dim_demand_type_id"
  dim_device_platform |o--o{ fact_application_event : "dim_device_platform_id"
  dim_device_platform ||--o{ agg_active_user_daily : "dim_device_platform_id"
  dim_device_platform ||--o{ agg_candidate_activation_weekly : "dim_device_platform_id"
  dim_device_platform ||--o{ agg_search_quality_daily : "dim_device_platform_id"
  dim_device_platform ||--o{ fact_candidate_activity_daily : "primary_dim_device_platform_id"
  dim_device_platform ||--o{ fact_profile_view : "dim_device_platform_id"
  dim_device_platform ||--o{ fact_search_event : "dim_device_platform_id"
  dim_device_platform ||--o{ fact_session_activity : "dim_device_platform_id"
  dim_device_platform ||--o{ fact_user_signup : "dim_device_platform_id"
  dim_etl_run |o--o{ dim_etl_run : "restated_dim_etl_run_id"
  dim_etl_run |o--o{ dim_etl_watermark : "last_successful_dim_etl_run_id"
  dim_etl_run ||--o{ agg_active_user_daily : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_candidate_activation_weekly : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_candidate_retention_cohort : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_cost_per_hire_monthly : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_data_quality_result : "checked_dim_etl_run_id"
  dim_etl_run ||--o{ agg_funnel_daily : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_gig_fill_rate_weekly : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_licence_utilisation_daily : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_notification_performance_daily : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_offer_acceptance_monthly : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_recruiter_activity_daily : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_revenue_monthly : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_search_quality_daily : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_source_effectiveness_monthly : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_support_sla_daily : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_time_to_fill_monthly : "load_dim_etl_run_id"
  dim_etl_run ||--o{ agg_wallet_burn_daily : "load_dim_etl_run_id"
  dim_etl_run ||--o{ bridge_candidate_skill : "load_dim_etl_run_id"
  dim_etl_run ||--o{ bridge_gig_worker_skill : "load_dim_etl_run_id"
  dim_etl_run ||--o{ bridge_job_requirement_skill : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_application_status : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_candidate : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_channel : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_delivery_status : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_demand_type : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_device_platform : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_gig_worker : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_industry : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_job_category : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_job_requirement : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_job_type : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_location : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_login_outcome : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_notification_type : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_organisation : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_outreach_type : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_payment_method : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_payment_status : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_plan : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_search_surface : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_signup_method : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_skill : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_source : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_sourcing_status : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_ticket_category : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_ticket_priority : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_user : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_user_role : "load_dim_etl_run_id"
  dim_etl_run ||--o{ dim_wallet_entry_type : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_application_event : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_application_pipeline : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_candidate_activity_daily : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_gig_assignment : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_gig_request_daily : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_job_posting_daily : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_licence_seat_daily : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_notification_delivery : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_outreach_recipient : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_payment : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_profile_download : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_profile_view : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_search_event : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_session_activity : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_sourcing_request_event : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_subscription_daily_snapshot : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_subscription_period : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_support_ticket : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_user_signup : "load_dim_etl_run_id"
  dim_etl_run ||--o{ fact_wallet_transaction : "load_dim_etl_run_id"
  dim_gig_worker ||--o{ bridge_gig_worker_skill : "dim_gig_worker_id"
  dim_gig_worker ||--o{ fact_gig_assignment : "dim_gig_worker_id"
  dim_industry |o--o{ dim_candidate : "current_dim_industry_id"
  dim_industry |o--o{ dim_organisation : "primary_dim_industry_id"
  dim_job_category |o--o{ dim_gig_worker : "primary_dim_job_category_id"
  dim_job_category |o--o{ dim_job_category : "parent_dim_job_category_id"
  dim_job_category |o--o{ fact_search_event : "dim_job_category_id"
  dim_job_category ||--o{ agg_cost_per_hire_monthly : "dim_job_category_id"
  dim_job_category ||--o{ agg_funnel_daily : "dim_job_category_id"
  dim_job_category ||--o{ agg_gig_fill_rate_weekly : "dim_job_category_id"
  dim_job_category ||--o{ agg_offer_acceptance_monthly : "dim_job_category_id"
  dim_job_category ||--o{ agg_source_effectiveness_monthly : "dim_job_category_id"
  dim_job_category ||--o{ agg_time_to_fill_monthly : "dim_job_category_id"
  dim_job_category ||--o{ dim_job_requirement : "dim_job_category_id"
  dim_job_category ||--o{ fact_gig_assignment : "dim_job_category_id"
  dim_job_category ||--o{ fact_gig_request_daily : "dim_job_category_id"
  dim_job_category ||--o{ fact_job_posting_daily : "dim_job_category_id"
  dim_job_requirement |o--o{ fact_outreach_recipient : "dim_job_requirement_id"
  dim_job_requirement |o--o{ fact_profile_download : "dim_job_requirement_id"
  dim_job_requirement |o--o{ fact_profile_view : "dim_job_requirement_id"
  dim_job_requirement |o--o{ fact_wallet_transaction : "dim_job_requirement_id"
  dim_job_requirement ||--o{ bridge_job_requirement_skill : "dim_job_requirement_id"
  dim_job_requirement ||--o{ fact_application_event : "dim_job_requirement_id"
  dim_job_requirement ||--o{ fact_application_pipeline : "dim_job_requirement_id"
  dim_job_requirement ||--o{ fact_job_posting_daily : "dim_job_requirement_id"
  dim_job_requirement ||--o{ fact_sourcing_request_event : "dim_job_requirement_id"
  dim_job_type ||--o{ agg_funnel_daily : "dim_job_type_id"
  dim_job_type ||--o{ agg_offer_acceptance_monthly : "dim_job_type_id"
  dim_job_type ||--o{ agg_time_to_fill_monthly : "dim_job_type_id"
  dim_job_type ||--o{ dim_job_requirement : "dim_job_type_id"
  dim_job_type ||--o{ fact_application_pipeline : "dim_job_type_id"
  dim_location |o--o{ dim_candidate : "home_dim_location_id"
  dim_location |o--o{ dim_gig_worker : "home_dim_location_id"
  dim_location |o--o{ dim_job_requirement : "dim_location_id"
  dim_location |o--o{ dim_organisation : "hq_dim_location_id"
  dim_location |o--o{ fact_gig_assignment : "dim_location_id"
  dim_location |o--o{ fact_gig_request_daily : "dim_location_id"
  dim_location |o--o{ fact_search_event : "dim_location_id"
  dim_location |o--o{ fact_user_signup : "dim_location_id"
  dim_location ||--o{ agg_funnel_daily : "dim_location_id"
  dim_location ||--o{ agg_gig_fill_rate_weekly : "dim_location_id"
  dim_location ||--o{ agg_time_to_fill_monthly : "dim_location_id"
  dim_location ||--o{ fact_job_posting_daily : "dim_location_id"
  dim_login_outcome ||--o{ fact_session_activity : "dim_login_outcome_id"
  dim_notification_type ||--o{ agg_notification_performance_daily : "dim_notification_type_id"
  dim_notification_type ||--o{ fact_notification_delivery : "dim_notification_type_id"
  dim_organisation |o--o{ dim_user : "dim_organisation_id"
  dim_organisation |o--o{ fact_support_ticket : "dim_organisation_id"
  dim_organisation ||--o{ agg_recruiter_activity_daily : "dim_organisation_id"
  dim_organisation ||--o{ dim_job_requirement : "dim_organisation_id"
  dim_organisation ||--o{ fact_application_event : "dim_organisation_id"
  dim_organisation ||--o{ fact_application_pipeline : "dim_organisation_id"
  dim_organisation ||--o{ fact_gig_assignment : "dim_organisation_id"
  dim_organisation ||--o{ fact_gig_request_daily : "dim_organisation_id"
  dim_organisation ||--o{ fact_job_posting_daily : "dim_organisation_id"
  dim_organisation ||--o{ fact_licence_seat_daily : "dim_organisation_id"
  dim_organisation ||--o{ fact_outreach_recipient : "dim_organisation_id"
  dim_organisation ||--o{ fact_payment : "dim_organisation_id"
  dim_organisation ||--o{ fact_profile_download : "dim_organisation_id"
  dim_organisation ||--o{ fact_profile_view : "dim_organisation_id"
  dim_organisation ||--o{ fact_sourcing_request_event : "dim_organisation_id"
  dim_organisation ||--o{ fact_subscription_daily_snapshot : "dim_organisation_id"
  dim_organisation ||--o{ fact_subscription_period : "dim_organisation_id"
  dim_organisation ||--o{ fact_wallet_transaction : "dim_organisation_id"
  dim_outreach_type ||--o{ fact_outreach_recipient : "dim_outreach_type_id"
  dim_payment_method ||--o{ agg_revenue_monthly : "dim_payment_method_id"
  dim_payment_method ||--o{ fact_payment : "dim_payment_method_id"
  dim_payment_status ||--o{ fact_payment : "dim_payment_status_id"
  dim_plan |o--o{ fact_payment : "dim_plan_id"
  dim_plan ||--o{ agg_licence_utilisation_daily : "dim_plan_id"
  dim_plan ||--o{ agg_revenue_monthly : "dim_plan_id"
  dim_plan ||--o{ fact_licence_seat_daily : "dim_plan_id"
  dim_plan ||--o{ fact_subscription_daily_snapshot : "dim_plan_id"
  dim_plan ||--o{ fact_subscription_period : "dim_plan_id"
  dim_search_surface ||--o{ agg_search_quality_daily : "dim_search_surface_id"
  dim_search_surface ||--o{ fact_profile_view : "origin_dim_search_surface_id"
  dim_search_surface ||--o{ fact_search_event : "dim_search_surface_id"
  dim_signup_method ||--o{ fact_user_signup : "dim_signup_method_id"
  dim_skill ||--o{ bridge_candidate_skill : "dim_skill_id"
  dim_skill ||--o{ bridge_gig_worker_skill : "dim_skill_id"
  dim_skill ||--o{ bridge_job_requirement_skill : "dim_skill_id"
  dim_source ||--o{ agg_candidate_activation_weekly : "dim_source_id"
  dim_source ||--o{ agg_candidate_retention_cohort : "dim_source_id"
  dim_source ||--o{ agg_funnel_daily : "dim_source_id"
  dim_source ||--o{ agg_source_effectiveness_monthly : "dim_source_id"
  dim_source ||--o{ dim_candidate : "acquisition_dim_source_id"
  dim_source ||--o{ fact_application_event : "dim_source_id"
  dim_source ||--o{ fact_application_pipeline : "dim_source_id"
  dim_source ||--o{ fact_user_signup : "dim_source_id"
  dim_sourcing_status ||--o{ fact_sourcing_request_event : "dim_sourcing_status_id"
  dim_ticket_category ||--o{ agg_support_sla_daily : "dim_ticket_category_id"
  dim_ticket_category ||--o{ fact_support_ticket : "dim_ticket_category_id"
  dim_ticket_priority ||--o{ agg_support_sla_daily : "dim_ticket_priority_id"
  dim_ticket_priority ||--o{ fact_support_ticket : "dim_ticket_priority_id"
  dim_time_of_day ||--o{ fact_application_event : "dim_time_of_day_id"
  dim_time_of_day ||--o{ fact_notification_delivery : "dim_time_of_day_id"
  dim_time_of_day ||--o{ fact_payment : "dim_time_of_day_id"
  dim_time_of_day ||--o{ fact_profile_download : "dim_time_of_day_id"
  dim_time_of_day ||--o{ fact_profile_view : "dim_time_of_day_id"
  dim_time_of_day ||--o{ fact_search_event : "dim_time_of_day_id"
  dim_time_of_day ||--o{ fact_session_activity : "dim_time_of_day_id"
  dim_time_of_day ||--o{ fact_user_signup : "dim_time_of_day_id"
  dim_time_of_day ||--o{ fact_wallet_transaction : "dim_time_of_day_id"
  dim_user |o--o{ fact_application_event : "actor_dim_user_id"
  dim_user |o--o{ fact_application_pipeline : "owner_dim_user_id"
  dim_user |o--o{ fact_notification_delivery : "recipient_dim_user_id"
  dim_user |o--o{ fact_search_event : "dim_user_id"
  dim_user |o--o{ fact_session_activity : "dim_user_id"
  dim_user |o--o{ fact_sourcing_request_event : "consultant_dim_user_id"
  dim_user |o--o{ fact_support_ticket : "dim_user_id"
  dim_user |o--o{ fact_wallet_transaction : "actor_dim_user_id"
  dim_user ||--o{ agg_recruiter_activity_daily : "recruiter_dim_user_id"
  dim_user ||--o{ dim_job_requirement : "posted_by_dim_user_id"
  dim_user ||--o{ fact_outreach_recipient : "sender_dim_user_id"
  dim_user ||--o{ fact_profile_download : "downloader_dim_user_id"
  dim_user ||--o{ fact_profile_view : "viewer_dim_user_id"
  dim_user_role ||--o{ agg_active_user_daily : "dim_user_role_id"
  dim_user_role ||--o{ dim_user : "dim_user_role_id"
  dim_user_role ||--o{ fact_user_signup : "dim_user_role_id"
  dim_wallet_entry_type ||--o{ agg_wallet_burn_daily : "dim_wallet_entry_type_id"
  dim_wallet_entry_type ||--o{ fact_wallet_transaction : "dim_wallet_entry_type_id"
```

## Lookup tables in this module

| Lookup | Values | Description |
|---|---|---|
| `dim_etl_run_status` | 5 | Lifecycle status of an analytic load run. |
| `dim_etl_run_type` | 4 | Kind of analytic load run. |
| `dim_kpi_grain` | 4 | Reporting grain at which a KPI aggregate is refreshed. |
| `dim_kpi_unit` | 6 | Unit of a KPI value. |
| `dim_placeholder_kind` | 2 | Kimball placeholder (unknown member) kind: distinguishes 'unknown' (value exists but could not be resolved) from 'not applicable' (no value can exist), per Rule 5. |

## Lookup references (enumeration FKs)

| Table | Lookup tables referenced |
|---|---|
| `dim_application_status` | `dim_placeholder_kind` |
| `dim_candidate` | `dim_placeholder_kind` |
| `dim_channel` | `dim_placeholder_kind` |
| `dim_delivery_status` | `dim_placeholder_kind` |
| `dim_demand_type` | `dim_placeholder_kind` |
| `dim_device_platform` | `dim_placeholder_kind` |
| `dim_etl_run` | `dim_etl_run_status`, `dim_etl_run_type` |
| `dim_gig_worker` | `dim_placeholder_kind` |
| `dim_industry` | `dim_placeholder_kind` |
| `dim_job_category` | `dim_placeholder_kind` |
| `dim_job_requirement` | `dim_placeholder_kind` |
| `dim_job_type` | `dim_placeholder_kind` |
| `dim_kpi_definition` | `dim_kpi_grain`, `dim_kpi_unit` |
| `dim_location` | `dim_placeholder_kind` |
| `dim_login_outcome` | `dim_placeholder_kind` |
| `dim_notification_type` | `dim_placeholder_kind` |
| `dim_organisation` | `dim_placeholder_kind` |
| `dim_outreach_type` | `dim_placeholder_kind` |
| `dim_payment_method` | `dim_placeholder_kind` |
| `dim_payment_status` | `dim_placeholder_kind` |
| `dim_plan` | `dim_placeholder_kind` |
| `dim_search_surface` | `dim_placeholder_kind` |
| `dim_signup_method` | `dim_placeholder_kind` |
| `dim_skill` | `dim_placeholder_kind` |
| `dim_source` | `dim_placeholder_kind` |
| `dim_sourcing_status` | `dim_placeholder_kind` |
| `dim_ticket_category` | `dim_placeholder_kind` |
| `dim_ticket_priority` | `dim_placeholder_kind` |
| `dim_user` | `dim_placeholder_kind` |
| `dim_user_role` | `dim_placeholder_kind` |
| `dim_wallet_entry_type` | `dim_placeholder_kind` |

## Tables

| Table | Kind | Delete | Volume | Description |
|---|---|---|---|---|
| `agg_active_user_daily` | agg | hard | medium | Grain: day x role x platform: DAU, WAU, MAU and stickiness (K10). |
| `agg_candidate_activation_weekly` | agg | hard | medium | Grain: sign-up week x source x platform: candidate activation (K08). |
| `agg_candidate_retention_cohort` | agg | hard | high | Grain: sign-up month cohort x source x months since sign-up: retention (K09). |
| `agg_cost_per_hire_monthly` | agg | hard | medium | Grain: month x job category: cost per hire (K06). Cost = credit value burned on postings, downloads and outreach attributed to requirements that produced hires. |
| `agg_data_quality_result` | agg | hard | medium | Grain: one row per data-quality check executed in one ETL run (row-count reconciliation, referential completeness, grain uniqueness). Failed checks block aggregate publication. |
| `agg_funnel_daily` | agg | hard | high | Grain: application date x job category x location x source x job type: funnel counts by application cohort (K03, K04). Counts are restated as applications progress (see refresh rules). |
| `agg_gig_fill_rate_weekly` | agg | hard | medium | Grain: request week x job category x location: gig fill rate (K17). |
| `agg_licence_utilisation_daily` | agg | hard | medium | Grain: day x plan: licence seat utilisation and device-limit pressure (K20). |
| `agg_notification_performance_daily` | agg | hard | high | Grain: day x channel x notification type: delivery and engagement (K16). |
| `agg_offer_acceptance_monthly` | agg | hard | medium | Grain: month x job category x job type: offer acceptance (K05). |
| `agg_recruiter_activity_daily` | agg | hard | high | Grain: day x recruiter version x organisation version: workload, response rate and outreach (K11, K12). |
| `agg_revenue_monthly` | agg | hard | medium | Grain: month x plan x payment method x currency: revenue, MRR and churn (K14, K15). |
| `agg_search_quality_daily` | agg | hard | high | Grain: day x search surface x platform: search quality (K18). |
| `agg_source_effectiveness_monthly` | agg | hard | medium | Grain: month x source x job category: source effectiveness (K07). |
| `agg_support_sla_daily` | agg | hard | medium | Grain: open day x ticket category x priority: support SLA (K19). |
| `agg_time_to_fill_monthly` | agg | hard | medium | Grain: fill month x job category x location x job type: time-to-fill and time-to-hire (K01, K02). |
| `agg_wallet_burn_daily` | agg | hard | medium | Grain: day x wallet entry type x currency: credit burn (K13). |
| `bridge_candidate_skill` | bridge | hard | very_high | Grain: one row per candidate version and skill. Resolves candidate-to-skill many-to-many for supply-versus-demand analysis. |
| `bridge_date_country_holiday` | bridge | hard | low | Grain: one row per country and holiday date. Resolves the many-to-many between dates and countries so working-day arithmetic is correct per market (holiday flags per country). |
| `bridge_gig_worker_skill` | bridge | hard | medium | Grain: one row per gig worker version and skill (service offering skills). |
| `bridge_job_requirement_skill` | bridge | hard | medium | Grain: one row per job requirement version and skill. Resolves the many-to-many; weighting_factor lets skill-level metrics allocate a requirement across its skills without double counting. |
| `dim_application_status` | dim | hard | low | Grain: one row per application pipeline status, conformed across jobs and gigs (gig application statuses map onto the same stage groups). |
| `dim_candidate` | dim | hard | very_high | Grain: one row per candidate profile VERSION (SCD Type 2). A new row is written when a tracked attribute changes (see notes for the maintenance algorithm). PII-minimised: no name, email, phone, address, date of birth or free text; only banded and coded attributes. The natural key candidate_profile_id is a copy (not FK-enforced) so analytics survives OLTP hard deletes and erasure. |
| `dim_channel` | dim | hard | low | Grain: one row per communication channel (email, SMS, push, messaging app, in-app, phone). |
| `dim_date` | dim | hard | low | Grain: one row per calendar date (business time zone Asia/Kolkata), 2015-01-01 to 2050-12-31. Conformed date dimension used by every fact in several roles. Key is a deterministic UUIDv5 of the ISO date so loaders resolve it without a lookup. |
| `dim_delivery_status` | dim | hard | low | Grain: one row per notification delivery status event (queued, sent, delivered, failed, bounced, opened, clicked, unsubscribed). |
| `dim_demand_type` | dim | hard | low | Grain: one row per kind of labour demand: permanent/contract job requirement or gig request. Conformed key for the unified demand view. |
| `dim_device_platform` | dim | hard | low | Grain: one row per device platform (web, Android, iOS, desktop app). |
| `dim_etl_run` | dim | hard | medium | Grain: one row per analytic load or aggregate refresh run of one pipeline over one source window. Kimball 'audit dimension': every fact/aggregate/dimension row points to the run that wrote it (lineage), and a restatement run points to the run it supersedes. ETL = extract-transform-load. |
| `dim_etl_watermark` | config | hard | low | Grain: one row per pipeline holding the high-water mark of source changes already loaded. Advanced inside the same transaction that commits a successful run. |
| `dim_gig_worker` | dim | hard | medium | gig worker profile VERSION (SCD Type 2): rate band, availability, rating band and verification are tracked. |
| `dim_industry` | dim | hard | low | industry (Type 1). |
| `dim_job_category` | dim | hard | low | job category (Type 1) with a self-referencing hierarchy; includes the gig category tree, so demand is comparable across jobs and gigs. |
| `dim_job_requirement` | dim | hard | high | job requirement VERSION (SCD Type 2): status, positions, location, salary band and consultant openness changes are tracked, so a fact always reports the attributes in force when it happened. |
| `dim_job_type` | dim | hard | low | Grain: one row per combination of employment type and work type (for example permanent / on-site, contract / remote, apprentice, campus). |
| `dim_kpi_definition` | dim | hard | low | Grain: one row per KPI. Machine-readable catalogue of the KPI definitions, formulas, units and the aggregate table that serves each. |
| `dim_location` | dim | hard | medium | place at city grain (or state/country grain for roll-up members). Type 1. |
| `dim_login_outcome` | dim | hard | low | Grain: one row per login attempt outcome (success, bad credentials, MFA failed, device limit reached, blocked, expired session). |
| `dim_notification_type` | dim | hard | low | Grain: one row per notification type (job alert, application update, transactional, marketing, system). |
| `dim_organisation` | dim | hard | medium | employer / client organisation VERSION (SCD Type 2): size band, verification state, account status and plan tier changes are tracked. |
| `dim_outreach_type` | dim | hard | low | Grain: one row per outreach type (bulk invite, direct message, sourcing shortlist invite, consultant pitch). |
| `dim_payment_method` | dim | hard | low | Grain: one row per payment method family (UPI, card, net banking, wallet, invoice). |
| `dim_payment_status` | dim | hard | low | Grain: one row per payment order status (created, authorised, captured, failed, refunded, chargeback). |
| `dim_plan` | dim | hard | low | subscription plan VERSION (SCD Type 2): price, included credits and entitlement limits are tracked so MRR is reported at the price in force. |
| `dim_search_surface` | dim | hard | low | Grain: one row per search surface (candidate job search, recruiter candidate search, gig search, company search, saved-search alert). |
| `dim_signup_method` | dim | hard | low | Grain: one row per sign-up method (email, phone OTP, Google, LinkedIn, referral invite, campus bulk import). |
| `dim_skill` | dim | hard | medium | skill (Type 1; name and category overwritten on change). Conformed for job requirements, candidates and gig workers via the bridge tables. |
| `dim_source` | dim | hard | low | Grain: one row per acquisition or application source (organic, paid, referral, campaign, partner, legacy import, direct). |
| `dim_sourcing_status` | dim | hard | low | Grain: one row per sourcing request status (requested, accepted, declined, in progress, shortlist delivered, closed). |
| `dim_ticket_category` | dim | hard | low | Grain: one row per support ticket category (billing, account, technical, content, abuse report). |
| `dim_ticket_priority` | dim | hard | low | Grain: one row per support ticket priority. |
| `dim_time_of_day` | dim | hard | low | Grain: one row per minute of the day (1440 rows) in the business time zone. Supports peak-hour analysis of logins, searches and notification opens. |
| `dim_user` | dim | hard | high | employer-side and staff user VERSION (SCD Type 2): recruiters, consultants, placement officers, employers and admins. Candidates are in dim_candidate. No name or contact data: dashboards that need a display name resolve it from app_user under RBAC. |
| `dim_user_role` | dim | hard | low | Grain: one row per account role at sign-up (candidate, gig worker, employer, recruiter, consultant, placement officer). |
| `dim_wallet_entry_type` | dim | hard | low | Grain: one row per wallet ledger entry type (recharge, consume for posting, download or outreach, refund, expiry, adjustment, reversal). |
| `fact_application_event` | fact | append_only | very_high | Grain: one row per application x status transition (application_id, from_status, to_status, event time). Event-grain fact; the lifecycle row is fact_application_pipeline. |
| `fact_application_pipeline` | fact | hard | very_high | Grain: one row per job application (accumulating snapshot). Milestone date keys are NULL until reached and the lag columns are filled in as milestones occur; the row is frozen once closed. Role-playing date dimension: one FK per milestone. |
| `fact_candidate_activity_daily` | fact | append_only | very_high | Grain: one row per candidate x day with at least one activity (periodic, sparse snapshot). Source of DAU/WAU/MAU and activation measures. |
| `fact_gig_assignment` | fact | hard | high | Grain: one row per gig assignment (accumulating snapshot) from request through acceptance, fulfilment handoff, completion and rating. Earnings arrive later from the external fulfilment system. |
| `fact_gig_request_daily` | fact | append_only | high | Grain: one row per gig request x day while the request is open (periodic snapshot). Sibling of fact_job_posting_daily; both feed vw_unified_demand_daily. |
| `fact_job_posting_daily` | fact | append_only | very_high | Grain: one row per job requirement x day while the requirement is published (periodic snapshot). Positions and counters are cumulative-to-date or in-day as named. |
| `fact_licence_seat_daily` | fact | append_only | medium | Grain: one row per licence x day (periodic snapshot of seat and device-limit utilisation). |
| `fact_notification_delivery` | fact | append_only | very_high | Grain: one row per notification delivery status event (queued, sent, delivered, failed, opened, clicked) per notification x channel; the high-volume write path stays INSERT-only. |
| `fact_outreach_recipient` | fact | hard | very_high | Grain: one row per outreach recipient (batch x candidate), accumulating snapshot: sent, delivered, opened, replied, applied. Candidate identity is the dimension key only. |
| `fact_payment` | fact | append_only | high | Grain: one row per payment order outcome event (captured, failed, refunded, charged back). Refunds and chargebacks are new rows with their own status, never updates. |
| `fact_profile_download` | fact | append_only | high | Grain: one row per profile download event with its credit deduction (mirror of profile_download). |
| `fact_profile_view` | fact | append_only | very_high | Grain: one row per profile view event (viewer x candidate x time). Append-only mirror of profile view history; no viewer identity beyond a user dimension key. |
| `fact_search_event` | fact | append_only | very_high | Grain: one row per executed search request (candidate job search, recruiter candidate search, gig search). Query text is not stored, only a one-way hash. |
| `fact_session_activity` | fact | append_only | very_high | Grain: one row per login attempt, with session summary fields filled when a session was established and closed by the load time (lookup of device-limit rejections included). |
| `fact_sourcing_request_event` | fact | append_only | medium | Grain: one row per sourcing request status event (requested, accepted, declined, shortlist delivered, closed). |
| `fact_subscription_daily_snapshot` | fact | append_only | high | Grain: one row per subscription x day (periodic snapshot of active, grace and scheduled-cancel states); source of monthly recurring revenue and churn. |
| `fact_subscription_period` | fact | append_only | medium | Grain: one row per subscription period (a paid term of a subscription). Loaded when the period starts; cancellations are recorded as new rows in the daily snapshot, not by updates here. |
| `fact_support_ticket` | fact | hard | medium | Grain: one row per support ticket (accumulating snapshot): opened, first responded, resolved, closed. SLA attainment is derived from the stored lags. |
| `fact_user_signup` | fact | append_only | high | Grain: one row per account registration (any role), with acquisition attributes at sign-up time. |
| `fact_wallet_transaction` | fact | append_only | very_high | Grain: one row per wallet ledger entry (credit recharge, consumption, refund, expiry, adjustment or reversal). Signed credits; mirror of the append-only wallet ledger. |
| `dim_etl_run_status` | lookup | hard | low | Lifecycle status of an analytic load run. |
| `dim_etl_run_type` | lookup | hard | low | Kind of analytic load run. |
| `dim_kpi_grain` | lookup | hard | low | Reporting grain at which a KPI aggregate is refreshed. |
| `dim_kpi_unit` | lookup | hard | low | Unit of a KPI value. |
| `dim_placeholder_kind` | lookup | hard | low | Kimball placeholder (unknown member) kind: distinguishes 'unknown' (value exists but could not be resolved) from 'not applicable' (no value can exist), per Rule 5. |

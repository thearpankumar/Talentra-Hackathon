# ER diagram: Extension: Ads and sponsored placements

Module key `ext_ads`, owner Team, 79 tables (36 lookups). Generated from `05_data_dictionary.xlsx` (do not edit by hand). Columns shown: primary key, foreign keys, unique/natural keys and a few business columns; see the dictionary for all columns. Edges to lookup (enumeration) tables are listed under *Lookup references* rather than drawn.

## Design notes

<!-- ext_ads.yaml -->
# Ads and sponsored placements (extension module)
Scope: advertiser accounts, campaigns, ad groups, audiences, creatives with an approval workflow, sponsored jobs, dayparting and frequency caps, budget and billing ledger,
raw event streams (impression, click, conversion, auction sample), rollups, invalid-traffic handling, employer-branding packages and candidate-promoted slots.

## Entity rationale
* Hierarchy account -> campaign -> ad group -> creative / sponsored job mirrors LinkedIn Campaign Manager and Indeed Sponsored Jobs. `sponsored_job` promotes a `job_requirement` of the
  advertiser's own organisation (BR-ADS-02); a job appears once per ad group (BR-ADS-03).
* Targeting is rule rows (`ad_audience_rule`, AND within group, OR across groups, exactly one target value per row) plus hashed uploaded lists (`ad_audience_member_list`,
  `ad_audience_list_entry`). Protected attributes can never be targeted (BR-ADS-10); lists hold only keyed HMAC hashes and expire (BR-ADS-12).
* Creatives are immutable once approved; an edit creates a new version. Reviews and approval decisions are append-only (`ad_creative_review`, `ad_approval_decision`).
* Dayparting uses an hour-grid slot table (pattern P2) so windows cannot overlap (BR-ADS-15).

## Money and billing
* Spend lives in an append-only signed ledger (`ad_budget_ledger`, gapless per-campaign sequence, reversal at most once). Click-time charges first go to one of 16 random
  `ad_budget_shard` rows per campaign to avoid a hot row; a pacing job (SELECT ... FOR UPDATE on the campaign) flushes shards into the ledger. Overspend is bounded by flush lag.
* Settlement is an idempotent `ad_billing_event` that debits the wallet (`wallet_ledger_entry`) for prepaid accounts or lands on an invoice line for postpaid ones; ads never bypass
  the wallet (BR-ADS-09).
* Every money column has a companion `currency_id` (FK `currency`) in the same table. The event and rollup tables (`ad_impression`, `ad_click`, `ad_conversion`, `ad_auction_log`,
  `ad_budget_shard`, `ad_performance_hourly`, `ad_performance_daily`, `ad_conversion_goal`) previously used an unchecked ISO text code and now use `currency_id` as well (finding
  from docs/12 seed plan: ten money columns without a currency reference).

## Volume, retention and privacy
* Raw events are very high volume, pseudonymous (`viewer_ref` is a rotating keyed HMAC, IP truncated, no `app_user_id`; BR-ADS-20) and expire by row-level TTL (impressions 30 days,
  clicks 90 days covering the billing dispute window); long-term figures live in `ad_performance_hourly` (120-day TTL) and `ad_performance_daily`.
* TTL versus foreign keys: `ad_budget_ledger.source_ad_performance_hourly_id` is ON DELETE SET NULL (decision F-01) so the hourly rollup can expire without blocking the TTL job.
* Invalid traffic is flagged (`ad_fraud_flag`) and credited through `ad_invalid_traffic_adjustment` and an `invalid_traffic_credit` ledger entry; advertisers are not billed for it (BR-ADS-19).
* Policy strikes accumulate points and restrict or suspend the account at thresholds (BR-ADS-21); an unverified account cannot serve (BR-ADS-22).

## Competitor coverage
* LinkedIn: objectives, bid strategies, audience templates, frequency capping, lead-gen and conversion tracking, company-page promotion (`brand_page_promotion`).
* Indeed/Naukri: sponsored jobs with budget and CPC/CPA targets, employer-branding packages (`employer_branding_package`, `employer_branding_purchase`), promoted candidate slots
  always labelled as promoted (BR-ADS-23).
* Not modelled: real-time bidding exchange, third-party DSP integrations and creative rendering; the auction itself runs in a serving service that only logs samples here.

## Extension points
* New placements, formats and objectives are lookup rows. Ranking beyond `ad_creative_quality_score` inputs is an integration boundary (serving service fed by a changefeed).
* A per-advertiser tax/invoicing profile can attach to `ad_account` without touching the ledger design.

## Diagram

```mermaid
erDiagram
  ad_account {
    UUID id PK
    UUID organisation_id FK
    UUID account_id FK
    UUID wallet_id FK
    UUID currency_id FK
    UUID ad_account_status_id FK
    UUID ad_funding_mode_id FK
    UUID time_zone_id FK
    STRING_150 display_name
    DECIMAL_18_4 daily_spend_cap_amount
    DECIMAL_18_4 credit_limit_amount
    UUID suspended_for_ad_policy_id FK
  }
  ad_account_member {
    UUID id PK
    UUID ad_account_id FK
    UUID app_user_id FK
    UUID ad_member_role_id FK
    TIMESTAMPTZ effective_from
    TIMESTAMPTZ effective_to
  }
  ad_advertiser_policy_strike {
    UUID id PK
    UUID ad_account_id FK
    UUID ad_policy_id FK
    UUID ad_strike_severity_id FK
    UUID ad_creative_id FK
    INT2 points
    TIMESTAMPTZ issued_at
    TIMESTAMPTZ expires_at
    UUID appeal_ad_review_status_id FK
  }
  ad_approval_decision {
    UUID id PK
    UUID ad_approval_request_id FK
    UUID ad_approval_workflow_step_id FK
    INT2 attempt_no UK
    UUID ad_approval_decision_kind_id FK
    UUID decided_by_app_user_id FK
    STRING_1000 comment_text
    TIMESTAMPTZ decided_at
  }
  ad_approval_request {
    UUID id PK
    UUID ad_account_id FK
    UUID ad_approval_workflow_id FK
    UUID ad_approval_subject_kind_id FK
    UUID ad_campaign_id FK
    UUID ad_creative_id FK
    UUID sponsored_job_id FK
    INT2 current_step_no
    BOOL is_open
    UUID final_ad_approval_decision_kind_id FK
    UUID submitted_by_app_user_id FK
    TIMESTAMPTZ submitted_at
  }
  ad_approval_workflow {
    UUID id PK
    STRING_60 workflow_code
    STRING_150 name
    UUID ad_approval_subject_kind_id FK
    JSONB applies_when
  }
  ad_approval_workflow_step {
    UUID id PK
    UUID ad_approval_workflow_id FK
    INT2 step_no UK
    UUID ad_approval_step_kind_id FK
    UUID required_app_role_id FK
    INT2 sla_hours
    BOOL is_blocking
  }
  ad_auction_log {
    UUID id PK
    STRING_64 auction_ref UK
    UUID ad_placement_id FK
    TIMESTAMPTZ occurred_at
    STRING_64 viewer_ref
    INT4 candidate_count
    UUID ad_auction_outcome_id FK
    UUID winning_ad_creative_id FK
    UUID currency_id FK
  }
  ad_audience {
    UUID id PK
    UUID ad_account_id FK
    STRING_150 name
    UUID ad_audience_kind_id FK
    BOOL is_saved
    INT8 estimated_reach
  }
  ad_audience_list_entry {
    UUID id PK
    UUID ad_audience_member_list_id FK
    BYTES identifier_hash UK
    BOOL is_matched
    TIMESTAMPTZ expires_at
  }
  ad_audience_member_list {
    UUID id PK
    UUID ad_audience_id FK
    STRING_150 list_name
    UUID ad_identifier_kind_id FK
    UUID ad_targeting_operator_id FK
    UUID consent_type_id FK
    INT8 row_count
    INT8 matched_count
  }
  ad_audience_rule {
    UUID id PK
    UUID ad_audience_id FK
    INT2 rule_group_no
    UUID ad_targeting_dimension_id FK
    UUID ad_targeting_operator_id FK
    UUID country_id FK
    UUID state_region_id FK
    UUID city_id FK
    UUID skill_id FK
    UUID industry_id FK
    UUID seniority_level_id FK
    UUID job_function_id FK
    UUID language_id FK
    UUID qualification_id FK
    INT4 min_value
    INT4 max_value
  }
  ad_billing_event {
    UUID id PK
    UUID ad_account_id FK
    UUID ad_billing_event_type_id FK
    STRING_120 idempotency_key UK
    TIMESTAMPTZ period_start_at
    TIMESTAMPTZ period_end_at
    DECIMAL_18_4 spend_amount
    UUID currency_id FK
    UUID wallet_ledger_entry_id FK
  }
  ad_billing_event_item {
    UUID id PK
    UUID ad_billing_event_id FK
    UUID ad_budget_ledger_id FK
  }
  ad_budget_ledger {
    UUID id PK
    UUID ad_account_id FK
    UUID ad_campaign_id FK
    UUID sponsored_job_id FK
    INT8 sequence_no UK
    INT8 prev_sequence_no FK
    UUID ad_ledger_entry_type_id FK
    DECIMAL_18_4 amount_delta
    UUID currency_id FK
    DECIMAL_18_4 spent_after
    TIMESTAMPTZ usage_hour_at
    STRING_120 idempotency_key UK
    UUID reverses_ad_budget_ledger_id FK
    UUID source_ad_performance_hourly_id FK
  }
  ad_budget_shard {
    UUID id PK
    UUID ad_campaign_id FK
    INT2 shard_no UK
    DECIMAL_18_6 pending_spend_amount
    UUID currency_id FK
    INT8 event_count
    TIMESTAMPTZ last_flushed_at
  }
  ad_campaign {
    UUID id PK
    UUID ad_account_id FK
    STRING_150 name
    UUID ad_objective_id FK
    UUID ad_entity_status_id FK
    UUID ad_budget_type_id FK
    DECIMAL_18_4 budget_amount
    UUID currency_id FK
    UUID ad_bid_strategy_id FK
    DECIMAL_18_4 bid_amount
    UUID ad_pacing_mode_id FK
  }
  ad_campaign_status_history {
    UUID id PK
    UUID ad_campaign_id FK
    UUID from_ad_entity_status_id FK
    UUID to_ad_entity_status_id FK
    TIMESTAMPTZ occurred_at
    STRING_500 reason_text
  }
  ad_click {
    UUID id PK
    STRING_64 click_ref UK
    STRING_64 serve_ref
    UUID ad_creative_id FK
    UUID ad_campaign_id FK
    UUID sponsored_job_id FK
    UUID ad_placement_id FK
    TIMESTAMPTZ occurred_at
    STRING_64 viewer_ref
    UUID device_platform_id FK
    UUID country_id FK
    UUID currency_id FK
  }
  ad_conversion {
    UUID id PK
    UUID ad_campaign_id FK
    UUID ad_creative_id FK
    UUID ad_conversion_event_type_id FK
    UUID ad_attribution_model_id FK
    STRING_64 click_ref
    STRING_64 serve_ref
    UUID job_application_id FK
    TIMESTAMPTZ occurred_at
    UUID currency_id FK
    STRING_120 dedupe_key UK
  }
  ad_conversion_goal {
    UUID id PK
    UUID ad_campaign_id FK
    UUID ad_conversion_event_type_id FK
    UUID ad_attribution_model_id FK
    INT2 lookback_days
    INT2 view_through_lookback_hours
    DECIMAL_18_4 value_amount
    UUID currency_id FK
  }
  ad_creative {
    UUID id PK
    UUID ad_group_id FK
    UUID ad_creative_format_id FK
    UUID ad_entity_status_id FK
    UUID ad_review_status_id FK
    STRING_200 headline
    STRING_1000 body_text
    UUID ad_call_to_action_id FK
    STRING_2048 destination_url
    UUID organisation_brand_id FK
    UUID sponsored_job_id FK
    UUID language_id FK
    UUID supersedes_ad_creative_id FK
  }
  ad_creative_asset {
    UUID id PK
    UUID ad_creative_id FK
    UUID ad_asset_kind_id FK
    STRING_512 storage_ref
    STRING_100 mime_type
    INT4 width_px
  }
  ad_creative_quality_score {
    UUID id PK
    UUID ad_creative_id FK
    UUID ad_placement_id FK
    DECIMAL_6_5 expected_ctr_ratio
    DECIMAL_5_2 relevance_pct
    DECIMAL_5_2 landing_quality_pct
  }
  ad_creative_review {
    UUID id PK
    UUID ad_creative_id FK
    UUID ad_approval_step_kind_id FK
    UUID ad_review_status_id FK
    UUID ad_policy_id FK
    UUID reviewer_app_user_id FK
    STRING_100 reviewer_model_ref
    STRING_1000 reason_text
    UUID appeal_of_ad_creative_review_id FK
    TIMESTAMPTZ reviewed_at
  }
  ad_fraud_flag {
    UUID id PK
    UUID ad_campaign_id FK
    UUID ad_creative_id FK
    UUID ad_fraud_signal_type_id FK
    UUID ad_fraud_disposition_id FK
    STRING_64 click_ref
    STRING_64 serve_ref
    TIMESTAMPTZ detected_at
    UUID reviewed_by_app_user_id FK
    UUID credit_ad_budget_ledger_id FK
  }
  ad_frequency_cap {
    UUID id PK
    UUID ad_campaign_id FK
    UUID ad_group_id FK
    UUID ad_frequency_window_id FK
    INT4 max_impressions
    INT4 max_clicks
  }
  ad_group {
    UUID id PK
    UUID ad_campaign_id FK
    STRING_150 name
    UUID ad_entity_status_id FK
    UUID ad_audience_id FK
    DECIMAL_18_4 budget_amount
    UUID currency_id FK
    UUID ad_bid_strategy_id FK
    DECIMAL_18_4 bid_amount
  }
  ad_group_placement {
    UUID id PK
    UUID ad_group_id FK
    UUID ad_placement_id FK
    DECIMAL_5_2 bid_multiplier
    BOOL is_enabled
  }
  ad_impression {
    UUID id PK
    STRING_64 serve_ref UK
    UUID ad_creative_id FK
    UUID ad_campaign_id FK
    UUID sponsored_job_id FK
    UUID ad_placement_id FK
    TIMESTAMPTZ occurred_at
    STRING_64 viewer_ref
    UUID device_platform_id FK
    UUID country_id FK
    BOOL is_viewable
    UUID currency_id FK
  }
  ad_invalid_traffic_adjustment {
    UUID id PK
    UUID ad_campaign_id FK
    TIMESTAMPTZ window_start_at UK
    TIMESTAMPTZ window_end_at UK
    INT8 invalid_impressions
    INT8 invalid_clicks
    DECIMAL_18_4 credit_amount
    UUID currency_id FK
    UUID credit_ad_budget_ledger_id FK
  }
  ad_invoice_line_link {
    UUID id PK
    UUID ad_account_id FK
    UUID ad_billing_event_id FK
    UUID invoice_line_id FK
    DECIMAL_18_4 amount
    UUID currency_id FK
  }
  ad_performance_daily {
    UUID id PK
    UUID ad_account_id FK
    UUID ad_campaign_id FK
    UUID ad_group_id FK
    UUID ad_creative_id FK
    UUID sponsored_job_id FK
    UUID ad_placement_id FK
    DATE performance_date UK
    INT8 impressions
    INT8 viewable_impressions
    INT8 clicks
    UUID currency_id FK
  }
  ad_performance_hourly {
    UUID id PK
    UUID ad_campaign_id FK
    UUID ad_group_id FK
    UUID ad_creative_id FK
    UUID ad_placement_id FK
    TIMESTAMPTZ hour_start_at UK
    INT8 impressions
    INT8 viewable_impressions
    INT8 clicks
    UUID currency_id FK
  }
  ad_report_export {
    UUID id PK
    UUID ad_account_id FK
    UUID requested_by_app_user_id FK
    UUID ad_report_grain_id FK
    UUID ad_report_format_id FK
    UUID ad_export_status_id FK
    DATE report_from_date
    DATE report_to_date
    JSONB dimensions
  }
  ad_schedule {
    UUID id PK
    UUID ad_group_id FK
    UUID time_zone_id FK
    BOOL is_active
  }
  ad_schedule_slot {
    UUID id PK
    UUID ad_schedule_id FK
    UUID gig_weekday_id FK
    INT2 hour_of_day UK
    DECIMAL_5_2 bid_multiplier
    BOOL is_enabled
  }
  brand_page_promotion {
    UUID id PK
    UUID ad_account_id FK
    UUID organisation_id FK
    UUID organisation_career_page_id FK
    UUID ad_campaign_id FK
    UUID ad_brand_promotion_kind_id FK
    UUID ad_entity_status_id FK
    STRING_200 headline
    TIMESTAMPTZ starts_at
    TIMESTAMPTZ ends_at
  }
  employer_branding_package {
    UUID id PK
    STRING_60 package_code
    STRING_150 name
    JSONB name_i18n
    UUID currency_id FK
  }
  employer_branding_package_item {
    UUID id PK
    UUID employer_branding_package_id FK
    UUID ad_package_item_kind_id FK
    INT4 quantity
  }
  employer_branding_purchase {
    UUID id PK
    UUID ad_account_id FK
    UUID employer_branding_package_id FK
    UUID employer_branding_purchase_status_id FK
    DECIMAL_18_4 price_paid_amount
    UUID currency_id FK
    TIMESTAMPTZ starts_at
    TIMESTAMPTZ ends_at
    UUID wallet_ledger_entry_id FK
    UUID invoice_line_id FK
    STRING_120 idempotency_key UK
  }
  sponsored_candidate_slot {
    UUID id PK
    UUID candidate_profile_id FK
    UUID user_consent_id FK
    UUID payment_order_id FK
    UUID ad_placement_id FK
    UUID ad_entity_status_id FK
    INT2 boost_level
    UUID target_job_role_id FK
    UUID target_city_id FK
    TIMESTAMPTZ starts_at
    TIMESTAMPTZ ends_at
  }
  sponsored_job {
    UUID id PK
    UUID ad_account_id FK
    UUID organisation_id FK
    UUID ad_campaign_id FK
    UUID ad_group_id FK
    UUID job_requirement_id FK
    UUID job_boost_id FK
    UUID ad_entity_status_id FK
    UUID ad_review_status_id FK
    UUID ad_budget_type_id FK
    DECIMAL_18_4 budget_amount
    UUID currency_id FK
    UUID ad_bid_strategy_id FK
    DECIMAL_18_4 bid_amount
    DECIMAL_18_4 cpa_target_amount
  }
  account {
    UUID id PK
  }
  app_role {
    UUID id PK
  }
  app_user {
    UUID id PK
  }
  candidate_profile {
    UUID id PK
  }
  city {
    UUID id PK
  }
  country {
    UUID id PK
  }
  currency {
    UUID id PK
  }
  industry {
    UUID id PK
  }
  invoice_line {
    UUID id PK
  }
  job_application {
    UUID id PK
  }
  job_boost {
    UUID id PK
  }
  job_requirement {
    UUID id PK
  }
  job_role {
    UUID id PK
  }
  language {
    UUID id PK
  }
  organisation {
    UUID id PK
  }
  organisation_brand {
    UUID id PK
  }
  organisation_career_page {
    UUID id PK
  }
  payment_order {
    UUID id PK
  }
  qualification {
    UUID id PK
  }
  skill {
    UUID id PK
  }
  state_region {
    UUID id PK
  }
  time_zone {
    UUID id PK
  }
  user_consent {
    UUID id PK
  }
  wallet {
    UUID id PK
  }
  wallet_ledger_entry {
    UUID id PK
  }
  account |o--o{ ad_account : "account_id"
  ad_account ||--o{ ad_account_member : "ad_account_id"
  ad_account ||--o{ ad_advertiser_policy_strike : "ad_account_id"
  ad_account ||--o{ ad_approval_request : "ad_account_id"
  ad_account ||--o{ ad_audience : "ad_account_id"
  ad_account ||--o{ ad_billing_event : "ad_account_id"
  ad_account ||--o{ ad_budget_ledger : "ad_account_id"
  ad_account ||--o{ ad_campaign : "ad_account_id"
  ad_account ||--o{ ad_invoice_line_link : "ad_account_id"
  ad_account ||--o{ ad_performance_daily : "ad_account_id"
  ad_account ||--o{ ad_report_export : "ad_account_id"
  ad_account ||--o{ brand_page_promotion : "ad_account_id"
  ad_account ||--o{ employer_branding_purchase : "ad_account_id"
  ad_account ||--o{ sponsored_job : "ad_account_id"
  ad_approval_request ||--o{ ad_approval_decision : "ad_approval_request_id"
  ad_approval_workflow ||--o{ ad_approval_request : "ad_approval_workflow_id"
  ad_approval_workflow ||--o{ ad_approval_workflow_step : "ad_approval_workflow_id"
  ad_approval_workflow_step ||--o{ ad_approval_decision : "ad_approval_workflow_step_id"
  ad_audience |o--o{ ad_group : "ad_audience_id"
  ad_audience ||--o{ ad_audience_member_list : "ad_audience_id"
  ad_audience ||--o{ ad_audience_rule : "ad_audience_id"
  ad_audience_member_list ||--o{ ad_audience_list_entry : "ad_audience_member_list_id"
  ad_billing_event ||--o{ ad_billing_event_item : "ad_billing_event_id"
  ad_billing_event ||--o{ ad_invoice_line_link : "ad_billing_event_id"
  ad_budget_ledger |o--o{ ad_budget_ledger : "reverses_ad_budget_ledger_id"
  ad_budget_ledger |o--o{ ad_fraud_flag : "credit_ad_budget_ledger_id"
  ad_budget_ledger ||--o{ ad_budget_ledger : "ad_campaign_id"
  ad_budget_ledger ||--o| ad_billing_event_item : "ad_budget_ledger_id"
  ad_budget_ledger ||--o| ad_invalid_traffic_adjustment : "credit_ad_budget_ledger_id"
  ad_campaign |o--o{ ad_approval_request : "ad_campaign_id"
  ad_campaign |o--o{ ad_frequency_cap : "ad_campaign_id"
  ad_campaign ||--o{ ad_budget_ledger : "ad_campaign_id"
  ad_campaign ||--o{ ad_budget_shard : "ad_campaign_id"
  ad_campaign ||--o{ ad_campaign_status_history : "ad_campaign_id"
  ad_campaign ||--o{ ad_click : "ad_campaign_id"
  ad_campaign ||--o{ ad_conversion : "ad_campaign_id"
  ad_campaign ||--o{ ad_conversion_goal : "ad_campaign_id"
  ad_campaign ||--o{ ad_fraud_flag : "ad_campaign_id"
  ad_campaign ||--o{ ad_group : "ad_campaign_id"
  ad_campaign ||--o{ ad_impression : "ad_campaign_id"
  ad_campaign ||--o{ ad_invalid_traffic_adjustment : "ad_campaign_id"
  ad_campaign ||--o{ ad_performance_daily : "ad_campaign_id"
  ad_campaign ||--o{ ad_performance_hourly : "ad_campaign_id"
  ad_campaign ||--o{ brand_page_promotion : "ad_campaign_id"
  ad_campaign ||--o{ sponsored_job : "ad_campaign_id"
  ad_creative |o--o{ ad_advertiser_policy_strike : "ad_creative_id"
  ad_creative |o--o{ ad_approval_request : "ad_creative_id"
  ad_creative |o--o{ ad_auction_log : "winning_ad_creative_id"
  ad_creative |o--o{ ad_conversion : "ad_creative_id"
  ad_creative |o--o{ ad_creative : "supersedes_ad_creative_id"
  ad_creative |o--o{ ad_fraud_flag : "ad_creative_id"
  ad_creative ||--o{ ad_click : "ad_creative_id"
  ad_creative ||--o{ ad_creative_asset : "ad_creative_id"
  ad_creative ||--o{ ad_creative_quality_score : "ad_creative_id"
  ad_creative ||--o{ ad_creative_review : "ad_creative_id"
  ad_creative ||--o{ ad_impression : "ad_creative_id"
  ad_creative ||--o{ ad_performance_daily : "ad_creative_id"
  ad_creative ||--o{ ad_performance_hourly : "ad_creative_id"
  ad_creative_review |o--o{ ad_creative_review : "appeal_of_ad_creative_review_id"
  ad_group |o--o{ ad_frequency_cap : "ad_group_id"
  ad_group ||--o{ ad_creative : "ad_group_id"
  ad_group ||--o{ ad_group_placement : "ad_group_id"
  ad_group ||--o{ ad_performance_daily : "ad_group_id"
  ad_group ||--o{ ad_performance_hourly : "ad_group_id"
  ad_group ||--o{ ad_schedule : "ad_group_id"
  ad_group ||--o{ sponsored_job : "ad_group_id"
  ad_performance_hourly |o--o{ ad_budget_ledger : "source_ad_performance_hourly_id"
  ad_schedule ||--o{ ad_schedule_slot : "ad_schedule_id"
  app_role |o--o{ ad_approval_workflow_step : "required_app_role_id"
  app_user |o--o{ ad_approval_decision : "decided_by_app_user_id"
  app_user |o--o{ ad_creative_review : "reviewer_app_user_id"
  app_user |o--o{ ad_fraud_flag : "reviewed_by_app_user_id"
  app_user ||--o{ ad_account_member : "app_user_id"
  app_user ||--o{ ad_approval_request : "submitted_by_app_user_id"
  app_user ||--o{ ad_report_export : "requested_by_app_user_id"
  candidate_profile ||--o{ sponsored_candidate_slot : "candidate_profile_id"
  city |o--o{ ad_audience_rule : "city_id"
  city |o--o{ sponsored_candidate_slot : "target_city_id"
  country |o--o{ ad_audience_rule : "country_id"
  country |o--o{ ad_click : "country_id"
  country |o--o{ ad_impression : "country_id"
  currency |o--o{ ad_auction_log : "currency_id"
  currency |o--o{ ad_conversion : "currency_id"
  currency |o--o{ ad_conversion_goal : "currency_id"
  currency |o--o{ ad_group : "currency_id"
  currency |o--o{ sponsored_job : "currency_id"
  currency ||--o{ ad_account : "currency_id"
  currency ||--o{ ad_billing_event : "currency_id"
  currency ||--o{ ad_budget_ledger : "currency_id"
  currency ||--o{ ad_budget_shard : "currency_id"
  currency ||--o{ ad_campaign : "currency_id"
  currency ||--o{ ad_click : "currency_id"
  currency ||--o{ ad_impression : "currency_id"
  currency ||--o{ ad_invalid_traffic_adjustment : "currency_id"
  currency ||--o{ ad_invoice_line_link : "currency_id"
  currency ||--o{ ad_performance_daily : "currency_id"
  currency ||--o{ ad_performance_hourly : "currency_id"
  currency ||--o{ employer_branding_package : "currency_id"
  currency ||--o{ employer_branding_purchase : "currency_id"
  employer_branding_package ||--o{ employer_branding_package_item : "employer_branding_package_id"
  employer_branding_package ||--o{ employer_branding_purchase : "employer_branding_package_id"
  industry |o--o{ ad_audience_rule : "industry_id"
  invoice_line |o--o{ employer_branding_purchase : "invoice_line_id"
  invoice_line ||--o{ ad_invoice_line_link : "invoice_line_id"
  job_application |o--o{ ad_conversion : "job_application_id"
  job_boost |o--o{ sponsored_job : "job_boost_id"
  job_requirement ||--o{ sponsored_job : "job_requirement_id"
  job_requirement ||--o{ sponsored_job : "organisation_id"
  job_role |o--o{ sponsored_candidate_slot : "target_job_role_id"
  language |o--o{ ad_audience_rule : "language_id"
  language |o--o{ ad_creative : "language_id"
  organisation ||--o{ ad_account : "organisation_id"
  organisation ||--o{ brand_page_promotion : "organisation_id"
  organisation ||--o{ sponsored_job : "organisation_id"
  organisation_brand |o--o{ ad_creative : "organisation_brand_id"
  organisation_career_page |o--o{ brand_page_promotion : "organisation_career_page_id"
  payment_order |o--o{ sponsored_candidate_slot : "payment_order_id"
  qualification |o--o{ ad_audience_rule : "qualification_id"
  skill |o--o{ ad_audience_rule : "skill_id"
  sponsored_job |o--o{ ad_approval_request : "sponsored_job_id"
  sponsored_job |o--o{ ad_budget_ledger : "sponsored_job_id"
  sponsored_job |o--o{ ad_click : "sponsored_job_id"
  sponsored_job |o--o{ ad_creative : "sponsored_job_id"
  sponsored_job |o--o{ ad_impression : "sponsored_job_id"
  sponsored_job |o--o{ ad_performance_daily : "sponsored_job_id"
  state_region |o--o{ ad_audience_rule : "state_region_id"
  time_zone ||--o{ ad_account : "time_zone_id"
  time_zone ||--o{ ad_schedule : "time_zone_id"
  user_consent ||--o{ sponsored_candidate_slot : "user_consent_id"
  wallet |o--o{ ad_account : "wallet_id"
  wallet_ledger_entry |o--o{ ad_billing_event : "wallet_ledger_entry_id"
  wallet_ledger_entry |o--o{ employer_branding_purchase : "wallet_ledger_entry_id"
```

## Lookup tables in this module

| Lookup | Values | Description |
|---|---|---|
| `ad_account_status` | 5 | Advertiser account standing. |
| `ad_approval_decision_kind` | 4 | Decision at an approval step. |
| `ad_approval_step_kind` | 4 | Review step type. |
| `ad_approval_subject_kind` | 3 | What is submitted for approval. |
| `ad_asset_kind` | 5 | Kind of creative asset. |
| `ad_attribution_model` | 3 | Attribution model for conversions. |
| `ad_auction_outcome` | 6 | Auction result. |
| `ad_audience_kind` | 4 | Audience type. |
| `ad_bid_strategy` | 6 | How the advertiser pays: cost per click/application/thousand impressions, flat fee or automated. |
| `ad_billing_event_type` | 3 | Billing event type. |
| `ad_brand_promotion_kind` | 4 | Employer brand promotion kind. |
| `ad_budget_type` | 2 | Budget semantics: spend per day or total for the flight. |
| `ad_call_to_action` | 5 | Call-to-action button label. |
| `ad_conversion_event_type` | 7 | Conversion event the advertiser values. |
| `ad_creative_format` | 7 | Creative format. |
| `ad_entity_status` | 8 | Serving lifecycle shared by campaign, ad group, creative, sponsored job and promotions. |
| `ad_export_status` | 5 | Export job state. |
| `ad_fraud_disposition` | 3 | Review result of a fraud flag. |
| `ad_fraud_signal_type` | 7 | Invalid-traffic signal. |
| `ad_frequency_window` | 4 | Window for impression/click caps. |
| `ad_funding_mode` | 2 | How ad spend is funded. |
| `ad_identifier_kind` | 3 | Kind of hashed identifier in an uploaded list. |
| `ad_ledger_entry_type` | 6 | Spend ledger entry type. |
| `ad_member_role` | 4 | Role of a user inside an ad account. |
| `ad_objective` | 7 | Campaign objective (what the advertiser optimises for); mirrors LinkedIn Campaign Manager objectives / Indeed goals. |
| `ad_pacing_mode` | 3 | Budget delivery pacing. |
| `ad_package_item_kind` | 4 | Component of an employer branding package. |
| `ad_placement` | 8 | Where an ad may appear (surface/slot). |
| `ad_policy` | 10 | Advertising policy catalogue used for reviews and strikes (hiring-ad policies). |
| `ad_report_format` | 3 | Export format. |
| `ad_report_grain` | 4 | Report time grain. |
| `ad_review_status` | 5 | Outcome of a policy review (also used for appeals). |
| `ad_strike_severity` | 4 | Escalation level of an advertiser policy strike. |
| `ad_targeting_dimension` | 11 | Targetable dimension. Protected attributes (gender, age, religion, marital status, disability, caste) are deliberately NOT offered. |
| `ad_targeting_operator` | 3 | Rule polarity. |
| `employer_branding_purchase_status` | 4 | State of a package purchase. |

## Lookup references (enumeration FKs)

| Table | Lookup tables referenced |
|---|---|
| `ad_account` | `ad_account_status`, `ad_funding_mode`, `ad_policy` |
| `ad_account_member` | `ad_member_role` |
| `ad_advertiser_policy_strike` | `ad_policy`, `ad_review_status`, `ad_strike_severity` |
| `ad_approval_decision` | `ad_approval_decision_kind` |
| `ad_approval_request` | `ad_approval_decision_kind`, `ad_approval_subject_kind` |
| `ad_approval_workflow` | `ad_approval_subject_kind` |
| `ad_approval_workflow_step` | `ad_approval_step_kind` |
| `ad_auction_log` | `ad_auction_outcome`, `ad_placement` |
| `ad_audience` | `ad_audience_kind` |
| `ad_audience_member_list` | `ad_identifier_kind`, `ad_targeting_operator`, `consent_type` |
| `ad_audience_rule` | `ad_targeting_dimension`, `ad_targeting_operator`, `job_function`, `seniority_level` |
| `ad_billing_event` | `ad_billing_event_type` |
| `ad_budget_ledger` | `ad_ledger_entry_type` |
| `ad_campaign` | `ad_bid_strategy`, `ad_budget_type`, `ad_entity_status`, `ad_objective`, `ad_pacing_mode` |
| `ad_campaign_status_history` | `ad_entity_status` |
| `ad_click` | `ad_placement`, `device_platform` |
| `ad_conversion` | `ad_attribution_model`, `ad_conversion_event_type` |
| `ad_conversion_goal` | `ad_attribution_model`, `ad_conversion_event_type` |
| `ad_creative` | `ad_call_to_action`, `ad_creative_format`, `ad_entity_status`, `ad_review_status` |
| `ad_creative_asset` | `ad_asset_kind` |
| `ad_creative_quality_score` | `ad_placement` |
| `ad_creative_review` | `ad_approval_step_kind`, `ad_policy`, `ad_review_status` |
| `ad_fraud_flag` | `ad_fraud_disposition`, `ad_fraud_signal_type` |
| `ad_frequency_cap` | `ad_frequency_window` |
| `ad_group` | `ad_bid_strategy`, `ad_entity_status` |
| `ad_group_placement` | `ad_placement` |
| `ad_impression` | `ad_placement`, `device_platform` |
| `ad_performance_daily` | `ad_placement` |
| `ad_performance_hourly` | `ad_placement` |
| `ad_report_export` | `ad_export_status`, `ad_report_format`, `ad_report_grain` |
| `ad_schedule_slot` | `gig_weekday` |
| `brand_page_promotion` | `ad_brand_promotion_kind`, `ad_entity_status` |
| `employer_branding_package_item` | `ad_package_item_kind` |
| `employer_branding_purchase` | `employer_branding_purchase_status` |
| `sponsored_candidate_slot` | `ad_entity_status`, `ad_placement` |
| `sponsored_job` | `ad_bid_strategy`, `ad_budget_type`, `ad_entity_status`, `ad_review_status` |

## Tables

| Table | Kind | Delete | Volume | Description |
|---|---|---|---|---|
| `ad_account` | entity | soft | low | Advertiser account of an organisation: owns campaigns, funds spend from a wallet (prepaid) or an invoice credit limit (postpaid). Billing is not duplicated: money moves through subscriptions_payments wallet/invoice. |
| `ad_account_member` | link | hard | low | Users (recruiters/agency staff) with a role in an ad account; time-bounded. |
| `ad_advertiser_policy_strike` | entity | soft | low | Policy strike/warning against an advertiser account (escalating enforcement). |
| `ad_approval_decision` | audit | append_only | medium | Immutable decision at an approval step. |
| `ad_approval_request` | entity | hard | medium | Submission of a campaign/creative/sponsored job into an approval workflow; exactly one subject, one open request per subject. |
| `ad_approval_workflow` | config | soft | low | Configurable approval workflow definition (which steps apply to which submissions). |
| `ad_approval_workflow_step` | config | hard | low | Ordered step of an approval workflow. |
| `ad_auction_log` | event | append_only | very_high | Sampled auction decisions (1-5 percent) for debugging relevance/pricing; short TTL. |
| `ad_audience` | entity | soft | medium | Targeting audience (saved or inline): rule-based, hashed list, lookalike, retargeting. Never contains raw personal identifiers. |
| `ad_audience_list_entry` | entity | hard | high | One HMAC-hashed identifier of an uploaded list. Raw email/phone is never stored; matching hashes the platform side with the same pepper. |
| `ad_audience_member_list` | entity | soft | low | Uploaded include/exclude list of HASHED identifiers (e.g. suppress existing employees, retarget past applicants) with consent basis and expiry. |
| `ad_audience_rule` | entity | hard | medium | One targeting rule row: AND within rule_group_no, OR across groups; exactly one target value per row. |
| `ad_billing_event` | event | append_only | medium | Idempotent settlement/posting event: converts accrued ad spend into a wallet debit (prepaid) or an invoice posting (postpaid). Inserted in the same txn as the wallet ledger entry. |
| `ad_billing_event_item` | link | append_only | high | Ledger entries covered by a billing event; unique ledger id means each accrual is settled exactly once. |
| `ad_budget_ledger` | ledger | append_only | high | Append-only ad spend ledger per campaign (single entry, signed). Gapless per-campaign sequence; corrections by reversal; settlement into the wallet via ad_billing_event. |
| `ad_budget_shard` | entity | hard | medium | Sharded spend accumulator: click-time charges UPDATE one of 16 random shards per campaign instead of the single hot campaign row. |
| `ad_campaign` | entity | soft | medium | Advertiser campaign: objective, budget, bid strategy, flight dates. Spend is cached here from the ledger. |
| `ad_campaign_status_history` | audit | append_only | medium | Immutable status transitions of campaigns (who/when/why). |
| `ad_click` | event | append_only | very_high | Raw ad click event (billable for CPC); deduped by click_ref; 90-day TTL covers the billing dispute window. |
| `ad_conversion` | event | append_only | high | Attributed conversion (application, follow, ...) with attribution evidence and optional link to job_application. |
| `ad_conversion_goal` | config | hard | low | Conversion events a campaign optimises/reports on, with attribution settings and per-conversion value. |
| `ad_creative` | entity | soft | medium | Ad creative (copy + CTA + destination) attached to an ad group; approved versions are immutable (edits create a superseding row). |
| `ad_creative_asset` | entity | hard | medium | Binary asset (image/video/logo/HTML5) of a creative; object-store key only. |
| `ad_creative_quality_score` | snapshot | hard | medium | Per creative+placement quality inputs to the auction (expected CTR, relevance, landing quality). |
| `ad_creative_review` | audit | append_only | medium | Immutable review verdicts of a creative (automated scan, human review, appeal), per policy. |
| `ad_fraud_flag` | entity | soft | high | Invalid-traffic flag on a click/impression with detector, confidence and review disposition (soft: evidence is never hard deleted). |
| `ad_frequency_cap` | config | soft | low | Impression/click cap per viewer for a campaign or ad group. |
| `ad_group` | entity | soft | medium | Ad group / ad set: audience, bid and budget overrides beneath a campaign. |
| `ad_group_placement` | link | hard | medium | Placements an ad group may serve in, with bid multiplier. |
| `ad_impression` | event | append_only | very_high | Raw ad impression event (very high volume, pseudonymous). Hash-sharded time index; 30-day TTL after hourly rollup. |
| `ad_invalid_traffic_adjustment` | ledger | append_only | medium | Batch invalid-traffic correction for a campaign window; drives an invalid_traffic_credit ledger entry (advertiser is not charged for invalid traffic). |
| `ad_invoice_line_link` | link | append_only | medium | Reference from a billing event to the invoice line it appears on (postpaid statements; prepaid receipts). Billing documents stay in subscriptions_payments. |
| `ad_performance_daily` | agg | hard | high | Daily rollup per creative and placement (advertiser reporting, invoicing evidence). |
| `ad_performance_hourly` | agg | hard | very_high | Hourly rollup of impressions/clicks/conversions/spend per creative and placement; owned by ads (billing-grade), distinct from analytics agg_*. Rewritten until is_final. |
| `ad_report_export` | entity | hard | low | Advertiser report export job (async); file lives in object storage. |
| `ad_schedule` | entity | soft | low | Dayparting schedule header of an ad group (hour-grid in the schedule's time zone). |
| `ad_schedule_slot` | entity | hard | medium | One enabled hour of a weekday (P2 grid: unique per hour, so windows cannot overlap). |
| `brand_page_promotion` | entity | soft | low | Promotion of an organisation's company/career page (follower growth, spotlight, event, brand video). |
| `employer_branding_package` | config | soft | low | Sellable employer-branding bundle (e.g. Naukri Branding / LinkedIn Life page packages). |
| `employer_branding_package_item` | link | hard | low | Components and quantities of a package. |
| `employer_branding_purchase` | entity | soft | low | Purchase of a branding package; payment through wallet or invoice line (no duplicate billing). |
| `sponsored_candidate_slot` | entity | soft | low | Candidate-purchased promoted profile in recruiter search (opt-in, always labelled 'Promoted'). Never shown without explicit consent. |
| `sponsored_job` | entity | soft | medium | Promotion of a job_requirement inside an ad group (Indeed Sponsored Jobs / LinkedIn Job Slots style). Complements jobs_applications.job_boost (flat boost product) for auction-based promotion. |
| `ad_account_status` | lookup | hard | low | Advertiser account standing. |
| `ad_approval_decision_kind` | lookup | hard | low | Decision at an approval step. |
| `ad_approval_step_kind` | lookup | hard | low | Review step type. |
| `ad_approval_subject_kind` | lookup | hard | low | What is submitted for approval. |
| `ad_asset_kind` | lookup | hard | low | Kind of creative asset. |
| `ad_attribution_model` | lookup | hard | low | Attribution model for conversions. |
| `ad_auction_outcome` | lookup | hard | low | Auction result. |
| `ad_audience_kind` | lookup | hard | low | Audience type. |
| `ad_bid_strategy` | lookup | hard | low | How the advertiser pays: cost per click/application/thousand impressions, flat fee or automated. |
| `ad_billing_event_type` | lookup | hard | low | Billing event type. |
| `ad_brand_promotion_kind` | lookup | hard | low | Employer brand promotion kind. |
| `ad_budget_type` | lookup | hard | low | Budget semantics: spend per day or total for the flight. |
| `ad_call_to_action` | lookup | hard | low | Call-to-action button label. |
| `ad_conversion_event_type` | lookup | hard | low | Conversion event the advertiser values. |
| `ad_creative_format` | lookup | hard | low | Creative format. |
| `ad_entity_status` | lookup | hard | low | Serving lifecycle shared by campaign, ad group, creative, sponsored job and promotions. |
| `ad_export_status` | lookup | hard | low | Export job state. |
| `ad_fraud_disposition` | lookup | hard | low | Review result of a fraud flag. |
| `ad_fraud_signal_type` | lookup | hard | low | Invalid-traffic signal. |
| `ad_frequency_window` | lookup | hard | low | Window for impression/click caps. |
| `ad_funding_mode` | lookup | hard | low | How ad spend is funded. |
| `ad_identifier_kind` | lookup | hard | low | Kind of hashed identifier in an uploaded list. |
| `ad_ledger_entry_type` | lookup | hard | low | Spend ledger entry type. |
| `ad_member_role` | lookup | hard | low | Role of a user inside an ad account. |
| `ad_objective` | lookup | hard | low | Campaign objective (what the advertiser optimises for); mirrors LinkedIn Campaign Manager objectives / Indeed goals. |
| `ad_pacing_mode` | lookup | hard | low | Budget delivery pacing. |
| `ad_package_item_kind` | lookup | hard | low | Component of an employer branding package. |
| `ad_placement` | lookup | hard | low | Where an ad may appear (surface/slot). |
| `ad_policy` | lookup | hard | low | Advertising policy catalogue used for reviews and strikes (hiring-ad policies). |
| `ad_report_format` | lookup | hard | low | Export format. |
| `ad_report_grain` | lookup | hard | low | Report time grain. |
| `ad_review_status` | lookup | hard | low | Outcome of a policy review (also used for appeals). |
| `ad_strike_severity` | lookup | hard | low | Escalation level of an advertiser policy strike. |
| `ad_targeting_dimension` | lookup | hard | low | Targetable dimension. Protected attributes (gender, age, religion, marital status, disability, caste) are deliberately NOT offered. |
| `ad_targeting_operator` | lookup | hard | low | Rule polarity. |
| `employer_branding_purchase_status` | lookup | hard | low | State of a package purchase. |

# ER diagram: Extension: Recommendations and matching signals

Module key `ext_matching`, owner Team, 78 tables (32 lookups). Generated from `05_data_dictionary.xlsx` (do not edit by hand). Columns shown: primary key, foreign keys, unique/natural keys and a few business columns; see the dictionary for all columns. Edges to lookup (enumeration) tables are listed under *Lookup references* rather than drawn.

## Design notes

<!-- ext_matching.yaml -->
**Scope**: EXTENSION module (tier X) beyond the docket's 11 modules: recommendations, matching signals, search configuration/sync, experimentation and quality/risk scoring - the 'ML/search platform' layer of a LinkedIn/Naukri-class product. No UI, no live integration, no ETL: tables hold the *contract* between the OLTP DB and external ML/search services.

**What is persisted vs externalised**
* Models, embeddings and ranking happen outside CockroachDB. The DB stores the registry (recommendation_model / _version / _deployment), pointers (embedding_ref: model, dims, vector_store_ref, object_key, content_hash), feature snapshots (JSONB, versioned by feature_schema_version) and results (match_score, job_recommendation, candidate_suggestion, similar_*).
* **Vector type**: CockroachDB has a VECTOR type and `CREATE VECTOR INDEX` (needs cluster setting feature.vector_index.enabled; GA label unverified per docs/_staging/crdb_capabilities.md row 3g; blocks writes during backfill; breaks IMPORT INTO). We therefore do NOT put a VECTOR column in the core path (domain `vector` is 'avoid'); embedding_ref is the portable choice. Optional hardening: a side table with `embedding VECTOR(n)` + vector index where the cluster supports it.
* **Search**: per D-16 the search engine is external. search_index_outbox is the transactional outbox (written in the same txn as the source row, relay with lease claim, dedupe_key idempotency, monotonic payload_version for external versioning); search_index_state/watermark track blue-green index versions and lag. In-DB trigram GIN indexes (inverted: true) are used only for typeahead/normalisation vocabularies.

**Volume / write paths (billions of rows)**
* recommendation_impression (largest table) and recommendation_click_event: append-only, random UUID PK, hash-sharded time indexes (32 buckets) so inserts/exports do not hot-spot, row-level TTL (90/180 days on created_at), idempotency via unique (request_ref, rank_position) / event_ref, batched multi-row INSERT ... ON CONFLICT DO NOTHING from the serving layer. Click rows join impressions by request_ref+rank_position (no FK, impressions expire earlier). Primary analytical reader is a changefeed sink; aggregates land in analytics (agg_search_quality_daily). Production deployments may drop the FKs on these two tables for ingest throughput (documented trade-off; erasure then relies on TTL + user-keyed purge job).
* job_recommendation, candidate_suggestion, match_score, similar_*: batch-swap pattern (new model-version rows inserted, old ones expire by TTL on expires_at; readers filter expires_at > now() because TTL deletes lag). Tables written `ttl: {expire_after: "0 seconds", column: expires_at}` mean physical `ttl_expiration_expression = 'expires_at'` (NULL = never expires).
* ab_assignment: sticky bucket per unit; read-through cache; INSERT ... ON CONFLICT DO NOTHING.

**Deconfliction with other modules (grepped before writing)**
* profile.candidate_recommendation = person-to-person written recommendations; ours is `candidate_suggestion` (recruiter ML suggestions).
* jobs_applications.candidate_job_search_history, sourcing_consultant.candidate_search_history, job_view_event, profile.candidate_profile_view, analytics fact_search_event/agg_search_quality_daily and notifications job_alert_match are the raw logs; we add only the shared vocabulary (search_query_normalised), zero-result gaps, typeahead, config and sync. candidate_search_alert match logging is already covered by job_alert_match, not duplicated.
* tier3 skill_demand_snapshot/salary_benchmark = raw market stats; skill_supply_demand_signal and salary_prediction are model outputs with supply side/confidence.
* profile completeness/profile_strength = field coverage; candidate_quality_score = trust/discoverability.
* administration duplicate_match_status reused for duplicate_job_cluster review; tier3 feature_flag optionally gates an ab_experiment.

**Privacy**: behavioural profiling (user_interest_affinity, inferred skills, quality scores, impressions) is Personal; gated by profiling consent (BR-MTH-20), erased by CASCADE on app_user/candidate_profile, TTL on logs, anonymous visitors only as salted rotating hashes (visitor_ref). Candidates see only reason codes; recruiter-side scores are never exposed to candidates.

**Deviations / caveats**: (1) same-tenant / same-experiment composite FKs (candidate_suggestion organisation vs job, ab_assignment variant vs experiment) are enforced by application + nightly check (BR-MTH-15); (2) outbox claim uses lease UPDATE, SKIP LOCKED is version-gated and unverified here; (3) LinkedIn/Naukri/Indeed model-shape claims come from public product behaviour and engineering write-ups known to the author, not re-fetched in this session.

**Extension points**: propensity/off-policy logging columns, multi-armed bandit state per surface, per-recruiter learned preferences, online/offline feature-store split.

## Diagram

```mermaid
erDiagram
  ab_assignment {
    UUID id PK
    UUID ab_experiment_id FK
    UUID ab_variant_id FK
    UUID app_user_id FK
    STRING_64 visitor_ref
    INT2 bucket
    TIMESTAMPTZ assigned_at
  }
  ab_experiment {
    UUID id PK
    STRING_80 experiment_key
    STRING_150 experiment_name
    STRING_1000 hypothesis
    UUID ab_experiment_status_id FK
    UUID ab_unit_kind_id FK
    UUID search_scope_id FK
    UUID recommendation_surface_id FK
    UUID feature_flag_id FK
    UUID primary_ab_metric_id FK
    UUID owner_app_user_id FK
  }
  ab_experiment_metric_result {
    UUID id PK
    UUID ab_experiment_id FK
    UUID ab_variant_id FK
    UUID ab_metric_id FK
    DATE window_start_date
    DATE window_end_date UK
    INT8 sample_size
    DECIMAL_18_6 metric_value
  }
  ab_metric {
    UUID id PK
    STRING_80 metric_key
    STRING_150 metric_name
    UUID ab_metric_kind_id FK
    BOOL higher_is_better
  }
  ab_variant {
    UUID id PK
    UUID ab_experiment_id FK
    STRING_40 variant_key UK
    BOOL is_control
    INT2 traffic_weight
    UUID recommendation_model_version_id FK
    UUID ranking_profile_id FK
    JSONB config
  }
  candidate_feature_snapshot {
    UUID id PK
    UUID candidate_profile_id FK
    INT4 feature_schema_version
    JSONB features
    STRING_64 feature_hash
  }
  candidate_quality_score {
    UUID id PK
    UUID candidate_profile_id FK
    UUID recommendation_model_version_id FK
    DECIMAL_5_2 authenticity_score
    DECIMAL_5_2 responsiveness_score
    DECIMAL_5_2 discoverability_score
  }
  candidate_suggestion {
    UUID id PK
    UUID recruiter_app_user_id FK
    UUID organisation_id FK
    UUID job_requirement_id FK
    UUID candidate_profile_id FK
    UUID recommendation_model_version_id FK
    UUID recommendation_surface_id FK
    UUID recommendation_serve_status_id FK
    INT2 rank_position
    DECIMAL_7_6 model_score
    TIMESTAMPTZ generated_at
  }
  candidate_suggestion_reason {
    UUID id PK
    UUID candidate_suggestion_id FK
    UUID recommendation_reason_code_id FK
    DECIMAL_5_4 contribution
    JSONB reason_detail
  }
  content_risk_score {
    UUID id PK
    UUID risk_kind_id FK
    UUID job_requirement_id FK
    UUID candidate_profile_id FK
    UUID organisation_id FK
    UUID recommendation_model_version_id FK
    DECIMAL_7_6 risk_score
    UUID risk_decision_id FK
    JSONB signals
    UUID reviewed_by_app_user_id FK
    TIMESTAMPTZ reviewed_at
  }
  duplicate_job_cluster {
    UUID id PK
    UUID canonical_job_requirement_id FK
    UUID duplicate_detection_method_id FK
    UUID duplicate_match_status_id FK
    DECIMAL_7_6 cluster_confidence
    INT4 member_count
    TIMESTAMPTZ detected_at
    UUID reviewed_by_app_user_id FK
  }
  duplicate_job_cluster_member {
    UUID id PK
    UUID duplicate_job_cluster_id FK
    UUID job_requirement_id FK
    DECIMAL_7_6 similarity_score
    BOOL is_canonical
  }
  embedding_ref {
    UUID id PK
    UUID embedding_subject_kind_id FK
    UUID candidate_profile_id FK
    UUID job_requirement_id FK
    UUID skill_id FK
    UUID recommendation_model_version_id FK
    INT4 dimensions
    STRING_120 vector_store_ref
    STRING_500 object_key
  }
  interest_topic {
    UUID id PK
    STRING_80 topic_key UK
    STRING_150 topic_name
    UUID parent_interest_topic_id FK
    UUID skill_id FK
    UUID job_category_id FK
    UUID industry_id FK
    BOOL is_active
  }
  job_feature_snapshot {
    UUID id PK
    UUID job_requirement_id FK
    INT4 feature_schema_version
    JSONB features
    STRING_64 feature_hash
  }
  job_quality_score {
    UUID id PK
    UUID job_requirement_id FK
    UUID recommendation_model_version_id FK
    DECIMAL_5_2 overall_score
    DECIMAL_5_2 completeness_score
    DECIMAL_5_2 salary_transparency_score
  }
  job_recommendation {
    UUID id PK
    UUID candidate_profile_id FK
    UUID job_requirement_id FK
    UUID recommendation_model_version_id FK
    UUID recommendation_surface_id FK
    UUID recommendation_serve_status_id FK
    INT2 rank_position
    DECIMAL_7_6 model_score
    STRING_64 request_ref
  }
  job_recommendation_reason {
    UUID id PK
    UUID job_recommendation_id FK
    UUID recommendation_reason_code_id FK
    DECIMAL_5_4 contribution
    JSONB reason_detail
  }
  match_score {
    UUID id PK
    UUID candidate_profile_id FK
    UUID job_requirement_id FK
    UUID recommendation_model_version_id FK
    DECIMAL_7_6 overall_score
    DECIMAL_5_2 match_percent
    TIMESTAMPTZ computed_at
  }
  match_score_component {
    UUID id PK
    UUID match_score_id FK
    UUID match_component_type_id FK
    DECIMAL_7_6 component_score
    DECIMAL_5_4 weight
    JSONB matched_detail
  }
  ranking_profile {
    UUID id PK
    UUID search_scope_id FK
    STRING_150 profile_name UK
    INT4 version_no UK
    TIMESTAMPTZ effective_from
    TIMESTAMPTZ effective_to
    BOOL is_default
  }
  ranking_rule {
    UUID id PK
    UUID ranking_profile_id FK
    UUID ranking_signal_kind_id FK
    DECIMAL_8_4 weight
    DECIMAL_8_4 max_contribution
    BOOL is_enabled
  }
  raw_title_normalisation {
    UUID id PK
    STRING_200 raw_title
    STRING_200 raw_title_lower UK
    UUID language_id FK
    UUID job_role_id FK
    DECIMAL_5_4 confidence
    UUID normalisation_status_id FK
    INT8 seen_count
    UUID recommendation_model_version_id FK
    UUID reviewed_by_app_user_id FK
  }
  recommendation_click_event {
    UUID id PK
    UUID app_user_id FK
    STRING_64 visitor_ref
    UUID recommendation_surface_id FK
    UUID recommendation_model_version_id FK
    UUID ab_variant_id FK
    UUID job_requirement_id FK
    UUID candidate_profile_id FK
    INT2 rank_position
    STRING_64 request_ref
    UUID recommendation_click_kind_id FK
    STRING_64 event_ref UK
  }
  recommendation_feedback {
    UUID id PK
    UUID app_user_id FK
    UUID job_requirement_id FK
    UUID candidate_profile_id FK
    UUID organisation_id FK
    UUID recommendation_feedback_kind_id FK
    UUID not_interested_reason_id FK
    UUID recommendation_surface_id FK
    UUID recommendation_model_version_id FK
    STRING_64 request_ref
    STRING_500 comment_text
    TIMESTAMPTZ feedback_at
  }
  recommendation_impression {
    UUID id PK
    UUID app_user_id FK
    STRING_64 visitor_ref
    UUID recommendation_surface_id FK
    UUID recommendation_model_version_id FK
    UUID ab_variant_id FK
    UUID job_requirement_id FK
    UUID candidate_profile_id FK
    INT2 rank_position UK
    STRING_64 request_ref UK
    TIMESTAMPTZ impressed_at
    BOOL is_viewable
  }
  recommendation_model {
    UUID id PK
    STRING_80 model_key
    STRING_150 model_name
    STRING_1000 description
    UUID recommendation_surface_id FK
    UUID recommendation_objective_id FK
  }
  recommendation_model_deployment {
    UUID id PK
    UUID recommendation_model_version_id FK
    UUID recommendation_surface_id FK
    UUID deployment_mode_id FK
    DECIMAL_5_2 traffic_percent
    BOOL is_primary
    TIMESTAMPTZ effective_from
    UUID deployed_by_app_user_id FK
  }
  recommendation_model_version {
    UUID id PK
    UUID recommendation_model_id FK
    STRING_40 version_label UK
    UUID model_status_id FK
    STRING_500 artifact_object_ref
    INT4 feature_schema_version
    TIMESTAMPTZ training_data_cutoff_at
    UUID approved_by_app_user_id FK
  }
  resume_parse_feedback {
    UUID id PK
    UUID resume_id FK
    UUID resume_parse_result_id FK
    UUID app_user_id FK
    UUID parse_feedback_source_id FK
    STRING_150 field_path
    STRING_1000 parsed_value
    STRING_1000 corrected_value
  }
  salary_prediction {
    UUID id PK
    UUID job_requirement_id FK
    UUID candidate_profile_id FK
    UUID job_role_id FK
    UUID country_id FK
    UUID city_id FK
    DECIMAL_18_4 predicted_min
    DECIMAL_18_4 predicted_median
    DECIMAL_18_4 predicted_max
    UUID currency_id FK
    UUID salary_period_id FK
    UUID recommendation_model_version_id FK
  }
  search_boost_rule {
    UUID id PK
    UUID search_scope_id FK
    UUID search_boost_target_id FK
    STRING_150 rule_name
    DECIMAL_6_3 boost_factor
    JSONB conditions
  }
  search_index_outbox {
    UUID id PK
    UUID search_index_entity_kind_id FK
    STRING_64 source_row_ref
    UUID outbox_operation_id FK
    INT8 payload_version
    STRING_120 dedupe_key UK
    UUID outbox_status_id FK
    BOOL is_done
  }
  search_index_state {
    UUID id PK
    UUID search_index_entity_kind_id FK
    UUID search_index_status_id FK
    STRING_120 index_name UK
    STRING_120 alias_name
    INT4 schema_version
    INT8 document_count
  }
  search_index_watermark {
    UUID id PK
    UUID search_index_state_id FK
    STRING_80 consumer_ref UK
    TIMESTAMPTZ last_outbox_created_at
    TIMESTAMPTZ last_resolved_at
    INT4 lag_seconds
  }
  search_query_normalised {
    UUID id PK
    UUID search_scope_id FK
    UUID language_id FK
    STRING_300 normalised_text UK
    UUID search_intent_kind_id FK
    UUID parsed_job_role_id FK
    UUID parsed_skill_id FK
    UUID parsed_city_id FK
    JSONB parsed_filters
    INT8 occurrence_count
    INT8 zero_result_count
  }
  search_stopword {
    UUID id PK
    UUID language_id FK
    UUID search_scope_id FK
    STRING_60 word_lower UK
  }
  search_synonym_group {
    UUID id PK
    UUID search_scope_id FK
    UUID language_id FK
    STRING_150 group_name
    BOOL is_bidirectional
    BOOL is_active
  }
  search_synonym_term {
    UUID id PK
    UUID search_synonym_group_id FK
    STRING_150 term_text
    STRING_150 term_lower UK
    BOOL is_canonical
  }
  search_typeahead_term {
    UUID id PK
    UUID search_scope_id FK
    UUID language_id FK
    UUID search_typeahead_kind_id FK
    STRING_150 term_text
    STRING_150 term_lower
    UUID job_role_id FK
    UUID skill_id FK
    UUID organisation_id FK
    UUID city_id FK
    INT8 popularity_score
  }
  search_zero_result_log {
    UUID id PK
    UUID search_scope_id FK
    UUID search_query_normalised_id FK
    UUID app_user_id FK
    STRING_300 raw_query_text
    JSONB filters
    STRING_300 relaxed_query_text
    UUID suggested_search_typeahead_term_id FK
    STRING_64 request_ref UK
  }
  similar_candidate {
    UUID id PK
    UUID candidate_profile_id FK
    UUID similar_candidate_profile_id FK
    UUID similarity_method_id FK
    DECIMAL_7_6 similarity_score
    INT2 rank_position
    UUID recommendation_model_version_id FK
    TIMESTAMPTZ computed_at
  }
  similar_job {
    UUID id PK
    UUID job_requirement_id FK
    UUID similar_job_requirement_id FK
    UUID similarity_method_id FK
    DECIMAL_7_6 similarity_score
    INT2 rank_position
    UUID recommendation_model_version_id FK
    TIMESTAMPTZ computed_at
  }
  skill_inference {
    UUID id PK
    UUID candidate_profile_id FK
    UUID skill_id FK
    DECIMAL_5_4 confidence
    UUID inference_source_id FK
    UUID inference_status_id FK
    UUID recommendation_model_version_id FK
    JSONB evidence
    TIMESTAMPTZ decided_at
  }
  skill_supply_demand_signal {
    UUID id PK
    UUID skill_id FK
    UUID country_id FK
    UUID city_id FK
    DATE period_start_date
    DATE period_end_date
    INT4 open_job_count
    UUID recommendation_model_version_id FK
  }
  user_interest_affinity {
    UUID id PK
    UUID app_user_id FK
    UUID interest_topic_id FK
    DECIMAL_7_6 affinity_score
    UUID interest_source_id FK
    BOOL is_explicit
    INT8 signal_count
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
  feature_flag {
    UUID id PK
  }
  industry {
    UUID id PK
  }
  job_category {
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
  resume {
    UUID id PK
  }
  resume_parse_result {
    UUID id PK
  }
  skill {
    UUID id PK
  }
  ab_experiment ||--o{ ab_assignment : "ab_experiment_id"
  ab_experiment ||--o{ ab_experiment_metric_result : "ab_experiment_id"
  ab_experiment ||--o{ ab_variant : "ab_experiment_id"
  ab_metric ||--o{ ab_experiment : "primary_ab_metric_id"
  ab_metric ||--o{ ab_experiment_metric_result : "ab_metric_id"
  ab_variant |o--o{ recommendation_click_event : "ab_variant_id"
  ab_variant |o--o{ recommendation_impression : "ab_variant_id"
  ab_variant ||--o{ ab_assignment : "ab_variant_id"
  ab_variant ||--o{ ab_experiment_metric_result : "ab_variant_id"
  app_user |o--o{ ab_assignment : "app_user_id"
  app_user |o--o{ ab_experiment : "owner_app_user_id"
  app_user |o--o{ content_risk_score : "reviewed_by_app_user_id"
  app_user |o--o{ duplicate_job_cluster : "reviewed_by_app_user_id"
  app_user |o--o{ raw_title_normalisation : "reviewed_by_app_user_id"
  app_user |o--o{ recommendation_click_event : "app_user_id"
  app_user |o--o{ recommendation_impression : "app_user_id"
  app_user |o--o{ recommendation_model_deployment : "deployed_by_app_user_id"
  app_user |o--o{ recommendation_model_version : "approved_by_app_user_id"
  app_user |o--o{ resume_parse_feedback : "app_user_id"
  app_user |o--o{ search_zero_result_log : "app_user_id"
  app_user ||--o{ candidate_suggestion : "recruiter_app_user_id"
  app_user ||--o{ recommendation_feedback : "app_user_id"
  app_user ||--o{ user_interest_affinity : "app_user_id"
  candidate_profile |o--o{ content_risk_score : "candidate_profile_id"
  candidate_profile |o--o{ embedding_ref : "candidate_profile_id"
  candidate_profile |o--o{ recommendation_click_event : "candidate_profile_id"
  candidate_profile |o--o{ recommendation_feedback : "candidate_profile_id"
  candidate_profile |o--o{ recommendation_impression : "candidate_profile_id"
  candidate_profile |o--o{ salary_prediction : "candidate_profile_id"
  candidate_profile ||--o{ candidate_feature_snapshot : "candidate_profile_id"
  candidate_profile ||--o{ candidate_quality_score : "candidate_profile_id"
  candidate_profile ||--o{ candidate_suggestion : "candidate_profile_id"
  candidate_profile ||--o{ job_recommendation : "candidate_profile_id"
  candidate_profile ||--o{ match_score : "candidate_profile_id"
  candidate_profile ||--o{ similar_candidate : "candidate_profile_id"
  candidate_profile ||--o{ similar_candidate : "similar_candidate_profile_id"
  candidate_profile ||--o{ skill_inference : "candidate_profile_id"
  candidate_suggestion ||--o{ candidate_suggestion_reason : "candidate_suggestion_id"
  city |o--o{ salary_prediction : "city_id"
  city |o--o{ search_query_normalised : "parsed_city_id"
  city |o--o{ search_typeahead_term : "city_id"
  city |o--o{ skill_supply_demand_signal : "city_id"
  country ||--o{ salary_prediction : "country_id"
  country ||--o{ skill_supply_demand_signal : "country_id"
  currency ||--o{ salary_prediction : "currency_id"
  duplicate_job_cluster ||--o{ duplicate_job_cluster_member : "duplicate_job_cluster_id"
  feature_flag |o--o{ ab_experiment : "feature_flag_id"
  industry |o--o{ interest_topic : "industry_id"
  interest_topic |o--o{ interest_topic : "parent_interest_topic_id"
  interest_topic ||--o{ user_interest_affinity : "interest_topic_id"
  job_category |o--o{ interest_topic : "job_category_id"
  job_recommendation ||--o{ job_recommendation_reason : "job_recommendation_id"
  job_requirement |o--o{ content_risk_score : "job_requirement_id"
  job_requirement |o--o{ duplicate_job_cluster : "canonical_job_requirement_id"
  job_requirement |o--o{ embedding_ref : "job_requirement_id"
  job_requirement |o--o{ recommendation_click_event : "job_requirement_id"
  job_requirement |o--o{ recommendation_feedback : "job_requirement_id"
  job_requirement |o--o{ recommendation_impression : "job_requirement_id"
  job_requirement |o--o{ salary_prediction : "job_requirement_id"
  job_requirement ||--o{ candidate_suggestion : "job_requirement_id"
  job_requirement ||--o{ job_feature_snapshot : "job_requirement_id"
  job_requirement ||--o{ job_quality_score : "job_requirement_id"
  job_requirement ||--o{ job_recommendation : "job_requirement_id"
  job_requirement ||--o{ match_score : "job_requirement_id"
  job_requirement ||--o{ similar_job : "job_requirement_id"
  job_requirement ||--o{ similar_job : "similar_job_requirement_id"
  job_requirement ||--o| duplicate_job_cluster_member : "job_requirement_id"
  job_role |o--o{ raw_title_normalisation : "job_role_id"
  job_role |o--o{ salary_prediction : "job_role_id"
  job_role |o--o{ search_query_normalised : "parsed_job_role_id"
  job_role |o--o{ search_typeahead_term : "job_role_id"
  language ||--o{ raw_title_normalisation : "language_id"
  language ||--o{ search_query_normalised : "language_id"
  language ||--o{ search_stopword : "language_id"
  language ||--o{ search_synonym_group : "language_id"
  language ||--o{ search_typeahead_term : "language_id"
  match_score ||--o{ match_score_component : "match_score_id"
  organisation |o--o{ content_risk_score : "organisation_id"
  organisation |o--o{ recommendation_feedback : "organisation_id"
  organisation |o--o{ search_typeahead_term : "organisation_id"
  organisation ||--o{ candidate_suggestion : "organisation_id"
  ranking_profile |o--o{ ab_variant : "ranking_profile_id"
  ranking_profile ||--o{ ranking_rule : "ranking_profile_id"
  recommendation_model ||--o{ recommendation_model_version : "recommendation_model_id"
  recommendation_model_version |o--o{ ab_variant : "recommendation_model_version_id"
  recommendation_model_version |o--o{ candidate_quality_score : "recommendation_model_version_id"
  recommendation_model_version |o--o{ content_risk_score : "recommendation_model_version_id"
  recommendation_model_version |o--o{ job_quality_score : "recommendation_model_version_id"
  recommendation_model_version |o--o{ raw_title_normalisation : "recommendation_model_version_id"
  recommendation_model_version |o--o{ recommendation_feedback : "recommendation_model_version_id"
  recommendation_model_version |o--o{ similar_candidate : "recommendation_model_version_id"
  recommendation_model_version |o--o{ similar_job : "recommendation_model_version_id"
  recommendation_model_version |o--o{ skill_supply_demand_signal : "recommendation_model_version_id"
  recommendation_model_version ||--o{ candidate_suggestion : "recommendation_model_version_id"
  recommendation_model_version ||--o{ embedding_ref : "recommendation_model_version_id"
  recommendation_model_version ||--o{ job_recommendation : "recommendation_model_version_id"
  recommendation_model_version ||--o{ match_score : "recommendation_model_version_id"
  recommendation_model_version ||--o{ recommendation_click_event : "recommendation_model_version_id"
  recommendation_model_version ||--o{ recommendation_impression : "recommendation_model_version_id"
  recommendation_model_version ||--o{ recommendation_model_deployment : "recommendation_model_version_id"
  recommendation_model_version ||--o{ salary_prediction : "recommendation_model_version_id"
  recommendation_model_version ||--o{ skill_inference : "recommendation_model_version_id"
  resume ||--o{ resume_parse_feedback : "resume_id"
  resume_parse_result |o--o{ resume_parse_feedback : "resume_parse_result_id"
  search_index_state ||--o{ search_index_watermark : "search_index_state_id"
  search_query_normalised |o--o{ search_zero_result_log : "search_query_normalised_id"
  search_synonym_group ||--o{ search_synonym_term : "search_synonym_group_id"
  search_typeahead_term |o--o{ search_zero_result_log : "suggested_search_typeahead_term_id"
  skill |o--o{ embedding_ref : "skill_id"
  skill |o--o{ interest_topic : "skill_id"
  skill |o--o{ search_query_normalised : "parsed_skill_id"
  skill |o--o{ search_typeahead_term : "skill_id"
  skill ||--o{ skill_inference : "skill_id"
  skill ||--o{ skill_supply_demand_signal : "skill_id"
```

## Lookup tables in this module

| Lookup | Values | Description |
|---|---|---|
| `ab_experiment_status` | 6 | Lifecycle of an A/B experiment. |
| `ab_metric_kind` | 5 | Statistical shape of an experiment metric. |
| `ab_unit_kind` | 3 | Randomisation unit of an experiment. |
| `deployment_mode` | 4 | How a model version is exposed to traffic. |
| `duplicate_detection_method` | 4 | How duplicate job postings were detected. |
| `embedding_subject_kind` | 3 | What an embedding vector represents. |
| `inference_source` | 5 | Evidence source of an inferred skill. |
| `inference_status` | 5 | Decision state for an inferred skill. |
| `interest_source` | 6 | How a user interest/affinity was learned. |
| `match_component_type` | 10 | Component of an explainable candidate-job match score. |
| `model_status` | 6 | Lifecycle of a trained model version. |
| `normalisation_status` | 5 | Review state of a raw-title-to-role mapping. |
| `not_interested_reason` | 8 | Why a user marked a recommendation not interested. |
| `outbox_operation` | 3 | Operation carried by a search outbox event. |
| `outbox_status` | 5 | Processing state of a search outbox row. |
| `parse_feedback_source` | 4 | Origin of a resume-parse correction. |
| `ranking_signal_kind` | 8 | Signal that can be weighted in a ranking profile. |
| `recommendation_click_kind` | 8 | Interaction recorded against a served recommendation. |
| `recommendation_feedback_kind` | 6 | Explicit feedback on a recommendation. |
| `recommendation_objective` | 6 | Optimisation objective of a recommendation model. |
| `recommendation_reason_code` | 12 | Explainability reason shown as 'why you see this'. |
| `recommendation_serve_status` | 6 | Delivery state of a generated recommendation. |
| `recommendation_surface` | 9 | Product placement where recommendations or ranked lists are shown. |
| `risk_decision` | 4 | Outcome of risk scoring. |
| `risk_kind` | 6 | Kind of spam/fraud risk scored. |
| `search_boost_target` | 7 | What a search boost rule favours. |
| `search_index_entity_kind` | 6 | Searchable entity synced to the external search engine. |
| `search_index_status` | 4 | Lifecycle of an external search index version. |
| `search_intent_kind` | 7 | Parsed intent of a normalised query. |
| `search_scope` | 5 | Search product the config/log applies to. |
| `search_typeahead_kind` | 5 | Kind of typeahead suggestion. |
| `similarity_method` | 5 | Technique producing a similarity edge. |

## Lookup references (enumeration FKs)

| Table | Lookup tables referenced |
|---|---|
| `ab_experiment` | `ab_experiment_status`, `ab_unit_kind`, `recommendation_surface`, `search_scope` |
| `ab_metric` | `ab_metric_kind` |
| `candidate_suggestion` | `recommendation_serve_status`, `recommendation_surface` |
| `candidate_suggestion_reason` | `recommendation_reason_code` |
| `content_risk_score` | `risk_decision`, `risk_kind` |
| `duplicate_job_cluster` | `duplicate_detection_method`, `duplicate_match_status` |
| `embedding_ref` | `embedding_subject_kind` |
| `job_recommendation` | `recommendation_serve_status`, `recommendation_surface` |
| `job_recommendation_reason` | `recommendation_reason_code` |
| `match_score_component` | `match_component_type` |
| `ranking_profile` | `search_scope` |
| `ranking_rule` | `ranking_signal_kind` |
| `raw_title_normalisation` | `normalisation_status` |
| `recommendation_click_event` | `recommendation_click_kind`, `recommendation_surface` |
| `recommendation_feedback` | `not_interested_reason`, `recommendation_feedback_kind`, `recommendation_surface` |
| `recommendation_impression` | `recommendation_surface` |
| `recommendation_model` | `recommendation_objective`, `recommendation_surface` |
| `recommendation_model_deployment` | `deployment_mode`, `recommendation_surface` |
| `recommendation_model_version` | `model_status` |
| `resume_parse_feedback` | `parse_feedback_source` |
| `salary_prediction` | `salary_period` |
| `search_boost_rule` | `search_boost_target`, `search_scope` |
| `search_index_outbox` | `outbox_operation`, `outbox_status`, `search_index_entity_kind` |
| `search_index_state` | `search_index_entity_kind`, `search_index_status` |
| `search_query_normalised` | `search_intent_kind`, `search_scope` |
| `search_stopword` | `search_scope` |
| `search_synonym_group` | `search_scope` |
| `search_typeahead_term` | `search_scope`, `search_typeahead_kind` |
| `search_zero_result_log` | `search_scope` |
| `similar_candidate` | `similarity_method` |
| `similar_job` | `similarity_method` |
| `skill_inference` | `inference_source`, `inference_status` |
| `user_interest_affinity` | `interest_source` |

## Tables

| Table | Kind | Delete | Volume | Description |
|---|---|---|---|---|
| `ab_assignment` | entity | hard | very_high | Sticky unit-to-variant assignment (hash bucket) written on first exposure; read-through cached by serving layer. |
| `ab_experiment` | entity | soft | low | A/B experiment definition on recommendations/search/ranking (hypothesis, unit, traffic, status). |
| `ab_experiment_metric_result` | snapshot | hard | medium | Computed metric result per experiment arm and window (value, lift, confidence interval, p-value). |
| `ab_metric` | config | soft | low | Experiment metric definition (CTR, apply rate, response rate...). |
| `ab_variant` | link | hard | low | Variant (arm) of an experiment, optionally binding a model version or ranking profile. |
| `candidate_feature_snapshot` | snapshot | hard | high | Versioned feature vector (JSONB) for a candidate, the single input contract of ranking/matching models (feature-store row). |
| `candidate_quality_score` | snapshot | hard | high | Model-derived candidate trust/discoverability scores (authenticity, responsiveness, discoverability); complements profile completeness/profile_strength (field coverage only). |
| `candidate_suggestion` | entity | hard | very_high | Recruiter-facing candidate recommendation for a job requirement (LinkedIn Recruiter 'recommended matches', Naukri suggested candidates). |
| `candidate_suggestion_reason` | link | hard | very_high | Reason codes for a recruiter-side candidate suggestion. |
| `content_risk_score` | entity | hard | high | Spam/scam/fake-content risk score reference for a job, candidate or organisation (score + decision; moderation queue lives elsewhere). |
| `duplicate_job_cluster` | entity | hard | medium | Cluster of duplicate/near-duplicate job postings (reposts, multi-poster copies) with canonical member and detection method. |
| `duplicate_job_cluster_member` | link | hard | medium | Membership of a job posting in a duplicate cluster. |
| `embedding_ref` | entity | hard | high | Pointer to an embedding vector held in an external vector store (model, dims, object key); NO vector payload in the OLTP DB. |
| `interest_topic` | config | hard | low | Topic taxonomy for interest/affinity modelling (maps to skills, job categories, industries). |
| `job_feature_snapshot` | snapshot | hard | high | Versioned feature vector (JSONB) for a job, the single input contract of ranking/matching models (feature-store row). |
| `job_quality_score` | snapshot | hard | high | Job posting quality score (description completeness, salary transparency, clarity) used for ranking and employer coaching. |
| `job_recommendation` | entity | hard | very_high | Recommendation of a job to a candidate on a surface (LinkedIn 'jobs you may be interested in' / Naukri recommended jobs): model, score, rank, serve/dismiss state. |
| `job_recommendation_reason` | link | hard | very_high | Reason codes behind a job recommendation ('because you viewed...', 'matches your skills'). |
| `match_score` | snapshot | hard | very_high | Pre-computed candidate x job match (explainable overall score; component rows in match_score_component). Only top-K pairs materialised. |
| `match_score_component` | snapshot | hard | very_high | Per-dimension component of a match score (skills, experience, location, salary...) for 'why this match' explanations. |
| `ranking_profile` | config | soft | low | Named set of ranking signal weights for a search scope (versioned; experiments reference it). |
| `ranking_rule` | config | hard | low | Signal weight within a ranking profile. |
| `raw_title_normalisation` | entity | hard | high | Taxonomy normalisation queue: raw job title (as typed by candidates/employers) mapped to master job_role with confidence and human review. |
| `recommendation_click_event` | event | append_only | very_high | Interaction (open, save, apply start...) on a served recommendation; joins to impression by request_ref+rank_position (no FK: impression rows expire earlier). |
| `recommendation_feedback` | event | append_only | high | Explicit user feedback on a recommendation (thumbs, not-interested + reason, hide company). |
| `recommendation_impression` | event | append_only | very_high | One row per recommended item rendered to a user (billions of rows). Training label source and CTR denominator. Written by the serving layer in batches (multi-row INSERT, ON CONFLICT DO NOTHING on uq request/position). |
| `recommendation_model` | config | soft | low | Registry of recommendation / matching / ranking models (one row per logical model; versions in recommendation_model_version). |
| `recommendation_model_deployment` | config | hard | low | Where/when a model version serves traffic (live, shadow, canary), with traffic share; supports rollback history. |
| `recommendation_model_version` | config | hard | low | Immutable trained version of a model with artifact pointer, offline metrics and approval. |
| `resume_parse_feedback` | event | append_only | medium | Corrections/reports on resume parser output (field path, parsed vs corrected) used to retrain parsers. |
| `salary_prediction` | snapshot | hard | high | Predicted salary range for a job posting or candidate profile (Glassdoor/Naukri salary-insights style) with confidence and sample size; complements tier3 salary_benchmark. |
| `search_boost_rule` | config | soft | low | Business boost rule applied on top of relevance (verified employers, fresh jobs, premium placement) with validity window. |
| `search_index_outbox` | event | hard | very_high | Transactional outbox: rows written in the SAME transaction as the source change; a relay claims pending rows and syncs the external search engine. Idempotent via dedupe_key and monotonic payload_version. |
| `search_index_state` | entity | hard | low | State of each external search index version (build, live alias, schema version, doc counts) for blue/green reindex. |
| `search_index_watermark` | entity | hard | low | Per consumer sync watermark (last outbox/changefeed position) and lag for a search index. |
| `search_query_normalised` | entity | hard | high | Canonical normalised query (query understanding): parsed intent, role/skill/city entities and aggregate counters; gives per-user logs (candidate_job_search_history, candidate_search_history) a shared vocabulary. |
| `search_stopword` | config | hard | low | Stop words per language and search scope (not indexed/ignored in queries). |
| `search_synonym_group` | config | soft | low | Synonym set for query expansion (e.g. 'sde' = 'software developer' = 'software engineer'); synced to the search engine. |
| `search_synonym_term` | link | hard | medium | Term inside a synonym group. |
| `search_typeahead_term` | config | soft | high | Typeahead / autocomplete vocabulary (job titles, skills, companies, locations, popular queries) with popularity and blocklist. |
| `search_zero_result_log` | event | append_only | high | Searches that returned zero results (candidate job search, recruiter search) with filters and attempted relaxations; drives synonym/taxonomy gaps and 'no results' recovery. |
| `similar_candidate` | link | hard | high | Pre-computed nearest-neighbour edge between two candidates (similar profiles for recruiters, never shown to candidates); read by detail-page widgets. |
| `similar_job` | link | hard | high | Pre-computed nearest-neighbour edge between two jobs (LinkedIn similar jobs); read by detail-page widgets. |
| `skill_inference` | entity | hard | very_high | Skills inferred for a candidate (resume text, job history, co-occurrence) with confidence, source and accept/reject decision (LinkedIn skill suggestions). |
| `skill_supply_demand_signal` | snapshot | hard | medium | Model-derived supply (candidates) vs demand (open jobs) per skill/geo/period with growth and scarcity; complements tier3 skill_demand_snapshot (raw posting counts). |
| `user_interest_affinity` | entity | hard | high | Decaying affinity of a user for a topic (explicit interests + implicit from behaviour); feeds personalisation and feed cold-start. |
| `ab_experiment_status` | lookup | hard | low | Lifecycle of an A/B experiment. |
| `ab_metric_kind` | lookup | hard | low | Statistical shape of an experiment metric. |
| `ab_unit_kind` | lookup | hard | low | Randomisation unit of an experiment. |
| `deployment_mode` | lookup | hard | low | How a model version is exposed to traffic. |
| `duplicate_detection_method` | lookup | hard | low | How duplicate job postings were detected. |
| `embedding_subject_kind` | lookup | hard | low | What an embedding vector represents. |
| `inference_source` | lookup | hard | low | Evidence source of an inferred skill. |
| `inference_status` | lookup | hard | low | Decision state for an inferred skill. |
| `interest_source` | lookup | hard | low | How a user interest/affinity was learned. |
| `match_component_type` | lookup | hard | low | Component of an explainable candidate-job match score. |
| `model_status` | lookup | hard | low | Lifecycle of a trained model version. |
| `normalisation_status` | lookup | hard | low | Review state of a raw-title-to-role mapping. |
| `not_interested_reason` | lookup | hard | low | Why a user marked a recommendation not interested. |
| `outbox_operation` | lookup | hard | low | Operation carried by a search outbox event. |
| `outbox_status` | lookup | hard | low | Processing state of a search outbox row. |
| `parse_feedback_source` | lookup | hard | low | Origin of a resume-parse correction. |
| `ranking_signal_kind` | lookup | hard | low | Signal that can be weighted in a ranking profile. |
| `recommendation_click_kind` | lookup | hard | low | Interaction recorded against a served recommendation. |
| `recommendation_feedback_kind` | lookup | hard | low | Explicit feedback on a recommendation. |
| `recommendation_objective` | lookup | hard | low | Optimisation objective of a recommendation model. |
| `recommendation_reason_code` | lookup | hard | low | Explainability reason shown as 'why you see this'. |
| `recommendation_serve_status` | lookup | hard | low | Delivery state of a generated recommendation. |
| `recommendation_surface` | lookup | hard | low | Product placement where recommendations or ranked lists are shown. |
| `risk_decision` | lookup | hard | low | Outcome of risk scoring. |
| `risk_kind` | lookup | hard | low | Kind of spam/fraud risk scored. |
| `search_boost_target` | lookup | hard | low | What a search boost rule favours. |
| `search_index_entity_kind` | lookup | hard | low | Searchable entity synced to the external search engine. |
| `search_index_status` | lookup | hard | low | Lifecycle of an external search index version. |
| `search_intent_kind` | lookup | hard | low | Parsed intent of a normalised query. |
| `search_scope` | lookup | hard | low | Search product the config/log applies to. |
| `search_typeahead_kind` | lookup | hard | low | Kind of typeahead suggestion. |
| `similarity_method` | lookup | hard | low | Technique producing a similarity edge. |

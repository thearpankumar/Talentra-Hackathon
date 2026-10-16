# ER diagram: Jobs and Applications

Module key `jobs_applications`, owner Team, 99 tables (42 lookups). Generated from `05_data_dictionary.xlsx` (do not edit by hand). Columns shown: primary key, foreign keys, unique/natural keys and a few business columns; see the dictionary for all columns. Edges to lookup (enumeration) tables are listed under *Lookup references* rather than drawn.

## Design notes

<!-- jobs_applications.yaml -->

## Scope and entity rationale
* Module = Jobs and Applications (milestones B6-B23 entity/relationship/column/design/constraint backlogs). Gigs are a separate subsystem (D-15): `job_category` here covers permanent,
  staffing, apprentice, campus, consultant marketplace and managed remote only. Registry tables defined: job_requirement, job_position, job_application, application_status (lookup),
  job_shortlist, interview, offer, questionnaire.
* Splits: requirement header (`job_requirement`) / per-slot `job_position` (Greenhouse "openings") / children for skill, location, qualification, benefit, screening rule (all normalised, none as arrays).
  Counters live in `job_requirement_stat`, not on the header (hot-row contention under SERIALIZABLE).
* Pipeline = `job_application.application_status_id` (current) + append-only `job_application_status_history` (transitions) + `application_status_transition` (allowed matrix).
* Feedback layer: application_note, application_scorecard(+score), interview(+interviewer/slot/feedback/reschedule/panel), offer(+revision/component/letter/approval), joining(+onboarding_task), rejection.
* Candidate-side Tier-2: candidate_job_search_history, job_view_event, job_bookmark (saved jobs), job_application (application history). Job alert preferences stay in notifications; no job_alert_match table here.
* Reach: job_template, job_posting_channel/job_syndication/job_share_event (external boards, social share events - outbound only), job_report, job_boost (ads hook to wallet_ledger_entry), talent_pool(+member), job_walk_in_session.

## Key decisions
1. **Transitions in the database, not triggers (D-01)**: history row carries a composite FK to `application_status_transition`, and a self composite FK (application, prior_seq, from_status) -> (application, seq, to_status) makes the chain gap-free. The remaining gap (application.current status == last history) is closed with a FOR UPDATE transaction + optimistic `status_sequence_no`; a nightly reconciliation job is the safety net (BR-JOB-12). Reason: CRDB has no deferrable FKs, so application<->history cannot reference each other declaratively.
2. **One live application per candidate+requirement**: partial unique `WHERE withdrawn_at IS NULL AND deleted_at IS NULL` (re-apply after withdrawal allowed; the partial predicate cannot reference the status lookup, so withdrawal is a column, and status 'withdrawn' mirrors it).
3. **Questionnaire integrity** via composite FK chain through `job_requirement(id, questionnaire_id)`; if an employer changes the attached questionnaire after applications exist, it is rejected by RESTRICT - the questionnaire must be cloned/new version (is_locked).
4. **Tenant isolation**: `organisation_id` denormalised on high-volume children with composite FK to `job_requirement(organisation_id,id)` (D-11). Recruiter access = `job_requirement_assignment` + `recruiter_organisation_mapping` through `vw_recruiter_job_requirement`; the mapping/organisation equality is view+app enforced because the organisation module owns the mapping table (open question: expose UNIQUE(id, organisation_id) there).
5. **Consultants**: flag `is_open_to_consultants` (fast filter, partial index) backed by `job_consultant_openness` windows (commission, scope, guarantee). Overlap of bounded windows uses a TXN_LOCK on the requirement row (no exclusion constraints in CRDB).
6. **Salary semantics (D-09)**: min/max nullable + `salary_disclosure_id` lookup (disclosed/undisclosed/negotiable/as per industry/not applicable); CHECK min<=max; currency+period required with amounts.
7. **Search (D-16)**: STORED computed `title_search = lower(title)` with trigram GIN (`USING GIN (title_search gin_trgm_ops)`, verified in CockroachDB trigram docs: LIKE/ILIKE/% supported), JSONB inverted index on `attributes`, partial indexes `WHERE is_live` on city/category (is_live is a service-maintained boolean because partial predicates cannot call now()). Ranking/full text is an integration boundary: a search service fed by changefeed from job_requirement, skills, location.
8. **Language (Rule 7)**: `description_language_id`, text stored once as authored; no translation table (UI strings only).
9. **Delete policies**: requirement/application/offer/interview are soft (history references them); histories/events append_only; link/child rows hard (cascade from soft parent only through retention/erasure jobs, which run in bounded batches).
10. Long index names (>63 chars) exist; CockroachDB does not enforce the PostgreSQL 63-byte limit. Rename if the physical DDL must also run on stock PostgreSQL.

## Volume, sharding, partitioning (laptop 5M apps -> full 100M apps)
* PKs are random UUIDv4 (D-02): inserts spread over ranges; no hot PK range at any volume.
* `job_application` (100M, ~1 KB/row => ~100 GB + indexes): secondary indexes lead with ids (requirement, candidate, organisation) so reads are range-local; the two purely time-ordered indexes (`applied_at`, `status_changed_at`) are **hash-sharded** (bucket_count 32 ~= up to 4x node count of an 8-node cluster; CRDB docs advise not exceeding the node count by much: tune at deployment) to avoid a single-range write hotspot.
* `job_application_status_history` (est. 400M rows, 4 transitions/application): hash-sharded `created_at`; funnel index (requirement, to_status, created_at). Old months can be exported to cold storage via changefeed/EXPORT then pruned by the retention job.
* High-volume events (`job_view_event`, `candidate_job_search_history`, `job_share_event`, `application_view_event`) use row-level TTL (395-400 days) plus hash-sharded time indexes (TTL job scans by expiration).
* Multi-region option: `job_application` and `job_requirement` can be `REGIONAL BY ROW` on country-derived `crdb_region`; named `job_application_zone`/`job_application_p_<region>` in physical design. Caveat from docs: hash-sharded indexes cannot include the partitioning/region column and an index with explicit PARTITION BY cannot be hash-sharded, so time indexes would become plain indexes per region (volume is split by region anyway).
* Expected first query to degrade at full scale: employer pipeline board (`job_requirement_id, application_status_id, applied_at DESC`) for mega-requirements (100k+ applicants): mitigated by STORING covering columns and keyset pagination.

## Competitor coverage observations
* **LinkedIn** (posting & Talent): title, company, workplace type, employment type, seniority, location, skills, screening questions with must-have/ideal answers, Easy Apply, hiring team, applicant pipeline stages, rating (good fit). Covered: all of these (screening via questionnaire + job_requirement_screening, seniority_level, work_type, pipeline, scorecard, hiring-team via assignment). Not covered: InMail credits (subscriptions module), "hiring-team roles" (sub-roles are out of scope per docket; extension = role column on job_requirement_assignment / app_role).
* **Naukri** (RMS): walk-in details (job_walk_in_session), CTC range with "Not disclosed" (salary_disclosure), key skills, freshers flag, questionnaires, hot-vacancy/boost (job_boost, hiring_urgency), response/folder management (talent_pool), consultant postings (job_consultant_openness), notice period. Exceeded by tenant-safe composite FKs, status chain, offer versions.
* **Monster / Indeed**: job type/shift, benefits chips (job_benefit_type), screener questions with deal-breakers (is_knockout), sponsored jobs (job_boost), interview scheduling and ATS statuses, reporting of jobs (job_report), syndication/apply URLs (job_syndication, apply_mode).
* **Greenhouse** (Harvest API, verified from public docs): applications link candidate->job with status active/rejected/hired, current stage, source, rejection reason/details, answers, scorecards, scheduled interviews, offers, approval flows (job and offer approvals), openings, close reasons. Covered via job_application(+history, rejection, questionnaire_answer), application_scorecard, interview, offer(+offer_approval), job_requirement_approval, job_position, job_close_reason. Not covered: demographic/EEOC data (deliberately excluded, sensitive), custom fields (use `attributes` JSONB on the requirement).

## Extension points
* Sub-roles in an employer organisation (posting/interviewing/pipeline): add `hiring_team_role_id` to job_requirement_assignment and new permissions via administration app_role_permission; no schema change in core tables.
* Per-stage pipelines per category (campus vs staffing): add `job_category_id` to application_status_transition (nullable = default).
* Assessment results: link from profile assessments to job_application via a new link table.
* Declarative requirement->consultant window exclusion when CRDB exclusion constraints become available.

## Recruiter access path (decision F-02)
`vw_recruiter_job_requirement` is defined once, in `organisation_account` (which owns `recruiter_organisation_mapping` and the hierarchy closure). The earlier copy in this module
(a per-job `job_requirement_assignment` narrowing, session variable `talentra.app_user_id`) was removed. Per-job narrowing stays available as an extra predicate on
`job_requirement_assignment` in the data-access layer. The viewer is always an explicit argument (`viewer_app_user_id`), never a custom session variable.

Language of user-entered text (finding F-06): free-text tables of this module carry a nullable `language_id` (FK `language`), NULL = unknown.

## Diagram

```mermaid
erDiagram
  application_note {
    UUID id PK
    UUID job_application_id FK
    UUID job_requirement_id FK
    STRING_4000 note_text
    UUID note_visibility_id FK
    BOOL is_pinned
  }
  application_scorecard {
    UUID id PK
    UUID job_application_id FK
    UUID job_requirement_id FK
    UUID interview_id FK
    UUID evaluator_app_user_id FK
    DECIMAL_3_1 overall_score
    UUID interview_recommendation_id FK
    STRING_2000 summary_text
    TIMESTAMPTZ submitted_at
  }
  application_scorecard_score {
    UUID id PK
    UUID application_scorecard_id FK
    UUID scorecard_criterion_id FK
    DECIMAL_3_1 score
    STRING_500 comment_text
  }
  application_status_transition {
    UUID id PK
    UUID from_application_status_id FK
    UUID to_application_status_id FK
    BOOL requires_reason
    BOOL requires_comment
    BOOL allowed_for_candidate
  }
  application_view_event {
    UUID id PK
    UUID job_application_id FK
    UUID job_requirement_id FK
    UUID application_view_kind_id FK
    UUID viewer_organisation_id FK
  }
  candidate_job_search_history {
    UUID id PK
    UUID candidate_profile_id FK
    STRING_200 search_keyword
    STRING_100 location_text
    UUID city_id FK
    JSONB filters
    UUID search_origin_id FK
  }
  interview {
    UUID id PK
    UUID job_application_id FK
    UUID job_requirement_id FK
    UUID organisation_id FK
    UUID interview_panel_id FK
    INT2 round_no
    UUID interview_round_type_id FK
    UUID interview_mode_id FK
    UUID interview_status_id FK
    TIMESTAMPTZ scheduled_start_at
    TIMESTAMPTZ scheduled_end_at
    UUID organisation_location_id FK
    UUID cancelled_by_actor_kind_id FK
    UUID overall_interview_recommendation_id FK
  }
  interview_feedback {
    UUID id PK
    UUID interview_id FK
    UUID interviewer_app_user_id FK
    UUID interview_recommendation_id FK
    DECIMAL_3_1 overall_rating
    STRING_2000 strengths_text
    STRING_2000 concerns_text
  }
  interview_interviewer {
    UUID id PK
    UUID interview_id FK
    UUID interviewer_app_user_id FK
    UUID interviewer_role_id FK
    UUID invitation_response_id FK
    TIMESTAMPTZ responded_at
  }
  interview_panel {
    UUID id PK
    UUID organisation_id FK
    UUID job_requirement_id FK
    STRING_150 name
    STRING_500 description_text
  }
  interview_panel_member {
    UUID id PK
    UUID interview_panel_id FK
    UUID member_app_user_id FK
    UUID interviewer_role_id FK
  }
  interview_reschedule {
    UUID id PK
    UUID interview_id FK
    TIMESTAMPTZ previous_start_at
    TIMESTAMPTZ previous_end_at
    TIMESTAMPTZ new_start_at
    UUID requested_by_actor_kind_id FK
  }
  interview_slot {
    UUID id PK
    UUID job_application_id FK
    UUID job_requirement_id FK
    UUID interview_id FK
    TIMESTAMPTZ slot_start_at
    TIMESTAMPTZ slot_end_at
    STRING_64 time_zone
  }
  job_application {
    UUID id PK
    UUID candidate_profile_id FK
    UUID organisation_id FK
    UUID job_requirement_id FK
    UUID job_position_id FK
    UUID job_source_id FK
    UUID application_status_id FK
    INT4 status_sequence_no
    TIMESTAMPTZ status_changed_at
    TIMESTAMPTZ applied_at
    UUID resume_id FK
    UUID currency_id FK
    UUID salary_period_id FK
    UUID submitted_by_app_user_id FK
    UUID withdrawal_reason_id FK
  }
  job_application_document {
    UUID id PK
    UUID job_application_id FK
    UUID job_requirement_id FK
    UUID document_type_id FK
    STRING_255 file_name
    STRING_500 storage_url
    STRING_100 mime_type
  }
  job_application_screening_result {
    UUID id PK
    UUID job_application_id FK
    UUID job_requirement_id FK
    UUID job_requirement_screening_id FK
    STRING_200 actual_value
    BOOL is_passed
  }
  job_application_status_history {
    UUID id PK
    UUID job_application_id FK
    UUID job_requirement_id FK
    INT4 sequence_no UK
    INT4 prior_sequence_no FK
    UUID from_application_status_id FK
    UUID to_application_status_id FK
    UUID actor_kind_id FK
    UUID rejection_reason_id FK
    STRING_1000 comment_text
    STRING_100 idempotency_key
  }
  job_bookmark {
    UUID id PK
    UUID candidate_profile_id FK
    UUID job_requirement_id FK
    STRING_300 note_text
    BOOL remind_before_closing
  }
  job_boost {
    UUID id PK
    UUID job_requirement_id FK
    UUID job_boost_type_id FK
    UUID boost_status_id FK
    TIMESTAMPTZ starts_at
    TIMESTAMPTZ ends_at
    DECIMAL_18_4 price_amount
    UUID currency_id FK
    UUID wallet_ledger_entry_id FK
  }
  job_consultant_openness {
    UUID id PK
    UUID job_requirement_id FK
    UUID consultant_access_scope_id FK
    TIMESTAMPTZ valid_from
    TIMESTAMPTZ valid_to
    DECIMAL_5_2 commission_percent
    UUID currency_id FK
  }
  job_position {
    UUID id PK
    UUID job_requirement_id FK
    INT4 position_number UK
    UUID job_position_status_id FK
    UUID city_id FK
    STRING_100 requisition_ref
    UUID hiring_manager_app_user_id FK
    DATE target_joining_date
    TIMESTAMPTZ opened_at
    UUID filled_by_job_application_id FK
  }
  job_posting_channel {
    UUID id PK
    STRING_40 channel_code
    STRING_150 name
    UUID job_posting_channel_type_id FK
    UUID organisation_id FK
    BOOL is_paid
  }
  job_publish_history {
    UUID id PK
    UUID job_requirement_id FK
    INT4 sequence_no UK
    UUID from_job_publish_state_id FK
    UUID to_job_publish_state_id FK
    STRING_300 reason
    UUID actor_kind_id FK
  }
  job_report {
    UUID id PK
    UUID job_requirement_id FK
    UUID reporter_app_user_id FK
    UUID job_report_reason_id FK
    UUID report_status_id FK
    STRING_2000 details
    TIMESTAMPTZ reviewed_at
    STRING_1000 resolution_note
  }
  job_requirement {
    UUID id PK
    UUID organisation_id FK
    UUID posted_by_app_user_id FK
    UUID job_template_id FK
    UUID parent_job_requirement_id FK
    STRING_40 reference_code
    STRING_100 external_job_ref
    STRING_200 title
    UUID description_language_id FK
    UUID job_role_id FK
    UUID job_category_id FK
    UUID employment_type_id FK
    UUID work_type_id FK
    UUID seniority_level_id FK
    UUID industry_id FK
    UUID shift_type_id FK
    UUID hiring_urgency_id FK
    UUID apply_mode_id FK
    UUID primary_country_id FK
    UUID primary_city_id FK
    UUID currency_id FK
    UUID salary_period_id FK
    UUID salary_disclosure_id FK
    UUID visibility_level_id FK
    UUID job_publish_state_id FK
    UUID job_requirement_status_id FK
    UUID job_close_reason_id FK
    UUID questionnaire_id FK
    UUID legacy_source_system_id FK
  }
  job_requirement_approval {
    UUID id PK
    UUID job_requirement_id FK
    UUID approver_app_user_id FK
    UUID approval_status_id FK
    TIMESTAMPTZ decided_at
    STRING_500 decision_note
  }
  job_requirement_assignment {
    UUID id PK
    UUID job_requirement_id FK
    UUID organisation_id FK
    UUID recruiter_app_user_id FK
    UUID recruiter_organisation_mapping_id FK
    UUID requirement_assignment_role_id FK
    BOOL is_primary
    TIMESTAMPTZ effective_from
    TIMESTAMPTZ effective_to
  }
  job_requirement_benefit {
    UUID id PK
    UUID job_requirement_id FK
    UUID job_benefit_type_id FK
    STRING_300 details
  }
  job_requirement_location {
    UUID id PK
    UUID job_requirement_id FK
    UUID country_id FK
    UUID state_region_id FK
    UUID city_id FK
    UUID organisation_location_id FK
    STRING_300 address_line
    BOOL is_primary
    DECIMAL_9_6 latitude
    UUID geo_source_id FK
  }
  job_requirement_qualification {
    UUID id PK
    UUID job_requirement_id FK
    UUID qualification_id FK
    UUID requirement_importance_id FK
    STRING_150 specialisation
    DECIMAL_5_2 min_score_percent
  }
  job_requirement_revision {
    UUID id PK
    UUID job_requirement_id FK
    INT4 revision_no UK
    JSONB changed_fields
    JSONB snapshot
    STRING_300 change_reason
  }
  job_requirement_screening {
    UUID id PK
    UUID job_requirement_id FK
    UUID job_screening_criterion_type_id FK
    UUID screening_operator_id FK
    STRING_200 expected_value
    BOOL is_knockout
    DECIMAL_5_2 weight_percent
  }
  job_requirement_skill {
    UUID id PK
    UUID job_requirement_id FK
    UUID skill_id FK
    UUID requirement_importance_id FK
    UUID proficiency_level_id FK
    INT4 min_experience_months
    INT2 display_order
  }
  job_requirement_stat {
    UUID id PK
    UUID job_requirement_id FK
    INT8 view_count
    INT8 unique_viewer_count
    INT8 application_count
  }
  job_requirement_status_history {
    UUID id PK
    UUID job_requirement_id FK
    INT4 sequence_no UK
    UUID from_job_requirement_status_id FK
    UUID to_job_requirement_status_id FK
    UUID job_close_reason_id FK
    STRING_300 reason
    UUID actor_kind_id FK
  }
  job_share_event {
    UUID id PK
    UUID job_requirement_id FK
    UUID shared_by_candidate_profile_id FK
    UUID share_channel_id FK
    STRING_64 share_token
  }
  job_shortlist {
    UUID id PK
    UUID organisation_id FK
    UUID job_requirement_id FK
    UUID owner_app_user_id FK
    STRING_150 name
    STRING_500 description_text
    UUID shortlist_type_id FK
    BOOL is_shared_with_team
  }
  job_shortlist_item {
    UUID id PK
    UUID job_shortlist_id FK
    UUID candidate_profile_id FK
    UUID job_application_id FK
    UUID shortlist_decision_id FK
    INT4 rank_no
    STRING_500 note_text
    TIMESTAMPTZ submitted_at
  }
  job_syndication {
    UUID id PK
    UUID job_requirement_id FK
    UUID job_posting_channel_id FK
    UUID syndication_status_id FK
    STRING_200 external_posting_ref
    STRING_500 external_url
    TIMESTAMPTZ posted_at
    UUID currency_id FK
  }
  job_template {
    UUID id PK
    UUID organisation_id FK
    STRING_150 name
    UUID job_role_id FK
    UUID job_category_id FK
    UUID employment_type_id FK
    UUID work_type_id FK
    STRING_200 title_template
    STRING description_template
    UUID questionnaire_id FK
    UUID language_id FK
  }
  job_view_event {
    UUID id PK
    UUID job_requirement_id FK
    UUID candidate_profile_id FK
    UUID job_view_kind_id FK
    UUID job_source_id FK
    INT2 search_rank_position
    INT4 dwell_seconds
    STRING_64 visitor_ref
  }
  job_walk_in_session {
    UUID id PK
    UUID job_requirement_id FK
    STRING_200 venue_name
    STRING_300 address_line
    UUID city_id FK
    TIMESTAMPTZ walk_in_start_at
    UUID contact_phone_country_id FK
  }
  joining {
    UUID id PK
    UUID job_application_id FK
    UUID job_requirement_id FK
    UUID offer_id FK
    UUID joining_status_id FK
    DATE expected_joining_date
    DATE actual_joining_date
    DATE deferred_to_date
  }
  offer {
    UUID id PK
    UUID job_application_id FK
    UUID job_requirement_id FK
    UUID organisation_id FK
    UUID offer_status_id FK
    BOOL is_open
    INT4 current_revision_no FK
    TIMESTAMPTZ sent_at
    TIMESTAMPTZ expires_at
    UUID offer_decline_reason_id FK
  }
  offer_approval {
    UUID id PK
    UUID offer_id FK
    UUID approver_app_user_id FK
    UUID approval_status_id FK
    TIMESTAMPTZ decided_at
    STRING_500 decision_note
  }
  offer_component {
    UUID id PK
    UUID offer_revision_id FK
    UUID offer_id FK
    UUID offer_component_type_id FK
    DECIMAL_18_4 amount
    UUID currency_id FK
    UUID salary_period_id FK
  }
  offer_letter {
    UUID id PK
    UUID offer_revision_id FK
    UUID offer_id FK
    UUID document_type_id FK
    STRING_255 file_name
    STRING_500 storage_url
    STRING_64 content_sha256
  }
  offer_revision {
    UUID id PK
    UUID offer_id FK
    INT4 revision_no UK
    UUID proposed_by_actor_kind_id FK
    STRING_200 designation
    DECIMAL_18_4 total_compensation_amount
    UUID currency_id FK
    UUID work_city_id FK
    DATE proposed_joining_date
  }
  onboarding_task {
    UUID id PK
    UUID joining_id FK
    UUID onboarding_task_type_id FK
    STRING_200 title
    DATE due_date
    TIMESTAMPTZ completed_at
    UUID assigned_to_app_user_id FK
  }
  questionnaire {
    UUID id PK
    UUID organisation_id FK
    STRING_200 title
    STRING_1000 description_text
    UUID language_id FK
    INT4 version_no
  }
  questionnaire_answer {
    UUID id PK
    UUID job_application_id FK
    UUID job_requirement_id FK
    UUID questionnaire_id FK
    UUID questionnaire_question_id FK
    STRING_4000 answer_text
    DECIMAL_18_4 answer_number
    DATE answer_date
  }
  questionnaire_answer_option {
    UUID id PK
    UUID questionnaire_answer_id FK
    UUID questionnaire_question_id FK
    UUID questionnaire_question_option_id FK
  }
  questionnaire_question {
    UUID id PK
    UUID questionnaire_id FK
    INT4 sequence_no UK
    STRING_1000 question_text
    UUID question_type_id FK
    STRING_500 help_text
    BOOL is_required
  }
  questionnaire_question_option {
    UUID id PK
    UUID questionnaire_question_id FK
    INT4 sequence_no UK
    STRING_500 option_text
    BOOL is_acceptable
    INT2 score_points
  }
  rejection {
    UUID id PK
    UUID job_application_id FK
    UUID job_requirement_id FK
    UUID rejection_reason_id FK
    UUID rejected_from_application_status_id FK
    TIMESTAMPTZ rejected_at
    STRING_1000 internal_comment
    STRING_1000 candidate_message
  }
  talent_pool {
    UUID id PK
    UUID organisation_id FK
    UUID owner_app_user_id FK
    STRING_150 name
    STRING_500 description_text
    BOOL is_shared_with_team
  }
  talent_pool_member {
    UUID id PK
    UUID talent_pool_id FK
    UUID candidate_profile_id FK
    UUID source_job_application_id FK
    STRING_200 tags
    DATE retain_until_date
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
  job_category {
    UUID id PK
  }
  job_role {
    UUID id PK
  }
  language {
    UUID id PK
  }
  legacy_source_system {
    UUID id PK
  }
  organisation {
    UUID id PK
  }
  organisation_location {
    UUID id PK
  }
  qualification {
    UUID id PK
  }
  recruiter_organisation_mapping {
    UUID id PK
  }
  resume {
    UUID id PK
  }
  skill {
    UUID id PK
  }
  state_region {
    UUID id PK
  }
  wallet_ledger_entry {
    UUID id PK
  }
  app_user |o--o{ job_application : "submitted_by_app_user_id"
  app_user |o--o{ job_position : "hiring_manager_app_user_id"
  app_user |o--o{ onboarding_task : "assigned_to_app_user_id"
  app_user ||--o{ application_scorecard : "evaluator_app_user_id"
  app_user ||--o{ interview_interviewer : "interviewer_app_user_id"
  app_user ||--o{ interview_panel_member : "member_app_user_id"
  app_user ||--o{ job_report : "reporter_app_user_id"
  app_user ||--o{ job_requirement : "posted_by_app_user_id"
  app_user ||--o{ job_requirement_approval : "approver_app_user_id"
  app_user ||--o{ job_requirement_assignment : "recruiter_app_user_id"
  app_user ||--o{ job_shortlist : "owner_app_user_id"
  app_user ||--o{ offer_approval : "approver_app_user_id"
  app_user ||--o{ talent_pool : "owner_app_user_id"
  application_scorecard ||--o{ application_scorecard_score : "application_scorecard_id"
  application_status_transition |o--o{ job_application_status_history : "from_application_status_id"
  candidate_profile |o--o{ job_share_event : "shared_by_candidate_profile_id"
  candidate_profile |o--o{ job_view_event : "candidate_profile_id"
  candidate_profile ||--o{ candidate_job_search_history : "candidate_profile_id"
  candidate_profile ||--o{ job_application : "candidate_profile_id"
  candidate_profile ||--o{ job_bookmark : "candidate_profile_id"
  candidate_profile ||--o{ job_shortlist_item : "candidate_profile_id"
  candidate_profile ||--o{ talent_pool_member : "candidate_profile_id"
  city |o--o{ candidate_job_search_history : "city_id"
  city |o--o{ job_position : "city_id"
  city |o--o{ job_requirement : "primary_city_id"
  city |o--o{ job_requirement_location : "city_id"
  city |o--o{ offer_revision : "work_city_id"
  city ||--o{ job_walk_in_session : "city_id"
  country |o--o{ job_walk_in_session : "contact_phone_country_id"
  country ||--o{ job_requirement : "primary_country_id"
  country ||--o{ job_requirement_location : "country_id"
  currency |o--o{ job_application : "currency_id"
  currency |o--o{ job_boost : "currency_id"
  currency |o--o{ job_consultant_openness : "currency_id"
  currency |o--o{ job_requirement : "currency_id"
  currency |o--o{ job_syndication : "currency_id"
  currency ||--o{ offer_component : "currency_id"
  currency ||--o{ offer_revision : "currency_id"
  industry |o--o{ job_requirement : "industry_id"
  interview |o--o{ application_scorecard : "interview_id"
  interview |o--o{ interview_slot : "interview_id"
  interview ||--o{ interview_interviewer : "interview_id"
  interview ||--o{ interview_reschedule : "interview_id"
  interview_interviewer ||--o{ interview_feedback : "interview_id"
  interview_panel |o--o{ interview : "interview_panel_id"
  interview_panel ||--o{ interview_panel_member : "interview_panel_id"
  job_application |o--o{ job_position : "filled_by_job_application_id"
  job_application |o--o{ job_shortlist_item : "job_application_id"
  job_application |o--o{ talent_pool_member : "source_job_application_id"
  job_application ||--o{ application_note : "job_application_id"
  job_application ||--o{ application_scorecard : "job_application_id"
  job_application ||--o{ application_view_event : "job_application_id"
  job_application ||--o{ interview : "job_application_id"
  job_application ||--o{ interview_slot : "job_application_id"
  job_application ||--o{ job_application_document : "job_application_id"
  job_application ||--o{ job_application_screening_result : "job_application_id"
  job_application ||--o{ job_application_status_history : "job_application_id"
  job_application ||--o{ joining : "job_application_id"
  job_application ||--o{ offer : "job_application_id"
  job_application ||--o{ questionnaire_answer : "job_application_id"
  job_application ||--o{ rejection : "job_application_id"
  job_application_status_history ||--o{ job_application_status_history : "job_application_id"
  job_category ||--o{ job_requirement : "job_category_id"
  job_category ||--o{ job_template : "job_category_id"
  job_position ||--o{ job_application : "job_requirement_id"
  job_posting_channel ||--o{ job_syndication : "job_posting_channel_id"
  job_requirement |o--o{ job_requirement : "parent_job_requirement_id"
  job_requirement ||--o{ interview_panel : "organisation_id"
  job_requirement ||--o{ job_application : "organisation_id"
  job_requirement ||--o{ job_bookmark : "job_requirement_id"
  job_requirement ||--o{ job_boost : "job_requirement_id"
  job_requirement ||--o{ job_consultant_openness : "job_requirement_id"
  job_requirement ||--o{ job_position : "job_requirement_id"
  job_requirement ||--o{ job_publish_history : "job_requirement_id"
  job_requirement ||--o{ job_report : "job_requirement_id"
  job_requirement ||--o{ job_requirement_approval : "job_requirement_id"
  job_requirement ||--o{ job_requirement_assignment : "organisation_id"
  job_requirement ||--o{ job_requirement_benefit : "job_requirement_id"
  job_requirement ||--o{ job_requirement_location : "job_requirement_id"
  job_requirement ||--o{ job_requirement_qualification : "job_requirement_id"
  job_requirement ||--o{ job_requirement_revision : "job_requirement_id"
  job_requirement ||--o{ job_requirement_screening : "job_requirement_id"
  job_requirement ||--o{ job_requirement_skill : "job_requirement_id"
  job_requirement ||--o{ job_requirement_status_history : "job_requirement_id"
  job_requirement ||--o{ job_share_event : "job_requirement_id"
  job_requirement ||--o{ job_shortlist : "organisation_id"
  job_requirement ||--o{ job_syndication : "job_requirement_id"
  job_requirement ||--o{ job_view_event : "job_requirement_id"
  job_requirement ||--o{ job_walk_in_session : "job_requirement_id"
  job_requirement ||--o{ questionnaire_answer : "job_requirement_id"
  job_requirement ||--o| job_requirement_stat : "job_requirement_id"
  job_requirement_screening ||--o{ job_application_screening_result : "job_requirement_screening_id"
  job_role |o--o{ job_requirement : "job_role_id"
  job_role |o--o{ job_template : "job_role_id"
  job_shortlist ||--o{ job_shortlist_item : "job_shortlist_id"
  job_template |o--o{ job_requirement : "job_template_id"
  joining ||--o{ onboarding_task : "joining_id"
  language |o--o{ job_template : "language_id"
  language ||--o{ job_requirement : "description_language_id"
  language ||--o{ questionnaire : "language_id"
  legacy_source_system |o--o{ job_requirement : "legacy_source_system_id"
  offer |o--o{ joining : "offer_id"
  offer ||--o{ offer_approval : "offer_id"
  offer ||--o{ offer_revision : "offer_id"
  offer_revision ||--o{ offer : "id"
  offer_revision ||--o{ offer_component : "offer_revision_id"
  offer_revision ||--o{ offer_letter : "offer_revision_id"
  organisation |o--o{ application_view_event : "viewer_organisation_id"
  organisation |o--o{ job_posting_channel : "organisation_id"
  organisation ||--o{ interview : "organisation_id"
  organisation ||--o{ interview_panel : "organisation_id"
  organisation ||--o{ job_requirement : "organisation_id"
  organisation ||--o{ job_shortlist : "organisation_id"
  organisation ||--o{ job_template : "organisation_id"
  organisation ||--o{ offer : "organisation_id"
  organisation ||--o{ questionnaire : "organisation_id"
  organisation ||--o{ talent_pool : "organisation_id"
  organisation_location |o--o{ interview : "organisation_location_id"
  organisation_location |o--o{ job_requirement_location : "organisation_location_id"
  qualification ||--o{ job_requirement_qualification : "qualification_id"
  questionnaire ||--o{ job_requirement : "organisation_id"
  questionnaire ||--o{ job_template : "organisation_id"
  questionnaire ||--o{ questionnaire_question : "questionnaire_id"
  questionnaire_answer ||--o{ questionnaire_answer_option : "questionnaire_answer_id"
  questionnaire_question ||--o{ questionnaire_answer : "questionnaire_question_id"
  questionnaire_question ||--o{ questionnaire_question_option : "questionnaire_question_id"
  questionnaire_question_option ||--o{ questionnaire_answer_option : "questionnaire_question_id"
  recruiter_organisation_mapping ||--o{ job_requirement_assignment : "recruiter_organisation_mapping_id"
  resume |o--o{ job_application : "resume_id"
  skill ||--o{ job_requirement_skill : "skill_id"
  state_region |o--o{ job_requirement_location : "state_region_id"
  talent_pool ||--o{ talent_pool_member : "talent_pool_id"
  wallet_ledger_entry |o--o{ job_boost : "wallet_ledger_entry_id"
```

## Lookup tables in this module

| Lookup | Values | Description |
|---|---|---|
| `actor_kind` | 6 | Kind of party that caused a transition or event. |
| `application_status` | 12 | Pipeline stage of a job application (the status lookup requested by the registry). Terminal flag drives 'active application' logic. |
| `application_view_kind` | 4 | What an employer looked at on an application. |
| `apply_mode` | 4 | How a candidate applies to a requirement. |
| `boost_status` | 5 | State of a boost. |
| `consultant_access_scope` | 3 | Which consultants may work on a consultant-open requirement. |
| `hiring_urgency` | 5 | How soon the employer wants the role filled. |
| `interview_mode` | 4 | Medium of the interview. |
| `interview_recommendation` | 5 | Interviewer recommendation (Greenhouse scorecard overall recommendation). |
| `interview_round_type` | 7 | Type of interview round. |
| `interview_status` | 7 | Lifecycle of a scheduled interview. |
| `interviewer_role` | 4 | Role of a person on an interview panel. |
| `invitation_response` | 4 | Interviewer response to an invitation. |
| `job_benefit_type` | 13 | Perks and benefits advertised on a requirement. |
| `job_boost_type` | 4 | Sponsored visibility product. |
| `job_close_reason` | 7 | Why a requirement was closed or cancelled (Greenhouse close reasons). |
| `job_position_status` | 4 | Status of an individual position (headcount slot) within a requirement. |
| `job_posting_channel_type` | 6 | Kind of publishing channel. |
| `job_publish_state` | 7 | Publish state of a job requirement. |
| `job_report_reason` | 8 | Reasons a job can be reported. |
| `job_requirement_status` | 6 | Hiring status of a job requirement (independent of publish state). |
| `job_screening_criterion_type` | 8 | Dimension tested by a knockout/screening rule. |
| `job_source` | 13 | Where an application, view or share originated (attribution). |
| `job_view_kind` | 3 | Kind of job exposure event. |
| `joining_status` | 5 | Outcome tracking after an accepted offer. |
| `note_visibility` | 3 | Who can read an application note. |
| `offer_component_type` | 7 | Compensation component of an offer. |
| `offer_decline_reason` | 7 | Why a candidate declined an offer. |
| `offer_status` | 9 | Lifecycle of an offer. |
| `onboarding_task_type` | 6 | Kind of onboarding task. |
| `question_type` | 9 | Answer type of a questionnaire question. |
| `report_status` | 4 | Moderation state of a report. |
| `requirement_assignment_role` | 4 | Role of a recruiter on a requirement. |
| `requirement_importance` | 3 | Importance of a skill or qualification for a requirement. |
| `scorecard_criterion` | 8 | Competency scored on a scorecard. |
| `screening_operator` | 7 | Comparison operator of a screening rule. |
| `search_origin` | 4 | Client surface a candidate search came from. |
| `share_channel` | 8 | Outbound sharing channel for share events. |
| `shift_type` | 5 | Working shift pattern, important for blue and grey collar staffing. |
| `shortlist_decision` | 3 | Employer decision on a shortlisted/submitted candidate. |
| `shortlist_type` | 3 | Purpose of a shortlist. |
| `syndication_status` | 5 | State of a requirement posted to an external channel. |

## Lookup references (enumeration FKs)

| Table | Lookup tables referenced |
|---|---|
| `application_note` | `note_visibility` |
| `application_scorecard` | `interview_recommendation` |
| `application_scorecard_score` | `scorecard_criterion` |
| `application_status_transition` | `application_status` |
| `application_view_event` | `application_view_kind` |
| `candidate_job_search_history` | `search_origin` |
| `interview` | `actor_kind`, `interview_mode`, `interview_recommendation`, `interview_round_type`, `interview_status` |
| `interview_feedback` | `interview_recommendation` |
| `interview_interviewer` | `interviewer_role`, `invitation_response` |
| `interview_panel_member` | `interviewer_role` |
| `interview_reschedule` | `actor_kind` |
| `job_application` | `application_status`, `job_source`, `salary_period`, `withdrawal_reason` |
| `job_application_document` | `document_type` |
| `job_application_status_history` | `actor_kind`, `application_status`, `rejection_reason` |
| `job_boost` | `boost_status`, `job_boost_type` |
| `job_consultant_openness` | `consultant_access_scope` |
| `job_position` | `job_position_status` |
| `job_posting_channel` | `job_posting_channel_type` |
| `job_publish_history` | `actor_kind`, `job_publish_state` |
| `job_report` | `job_report_reason`, `report_status` |
| `job_requirement` | `apply_mode`, `employment_type`, `hiring_urgency`, `job_close_reason`, `job_publish_state`, `job_requirement_status`, `salary_disclosure`, `salary_period`, `seniority_level`, `shift_type`, `visibility_level`, `work_type` |
| `job_requirement_approval` | `approval_status` |
| `job_requirement_assignment` | `requirement_assignment_role` |
| `job_requirement_benefit` | `job_benefit_type` |
| `job_requirement_location` | `geo_source` |
| `job_requirement_qualification` | `requirement_importance` |
| `job_requirement_screening` | `job_screening_criterion_type`, `screening_operator` |
| `job_requirement_skill` | `proficiency_level`, `requirement_importance` |
| `job_requirement_status_history` | `actor_kind`, `job_close_reason`, `job_requirement_status` |
| `job_share_event` | `share_channel` |
| `job_shortlist` | `shortlist_type` |
| `job_shortlist_item` | `shortlist_decision` |
| `job_syndication` | `syndication_status` |
| `job_template` | `employment_type`, `work_type` |
| `job_view_event` | `job_source`, `job_view_kind` |
| `joining` | `joining_status` |
| `offer` | `offer_decline_reason`, `offer_status` |
| `offer_approval` | `approval_status` |
| `offer_component` | `offer_component_type`, `salary_period` |
| `offer_letter` | `document_type` |
| `offer_revision` | `actor_kind` |
| `onboarding_task` | `onboarding_task_type` |
| `questionnaire_question` | `question_type` |
| `rejection` | `application_status`, `rejection_reason` |

## Tables

| Table | Kind | Delete | Volume | Description |
|---|---|---|---|---|
| `application_note` | entity | soft | high | Internal recruiter/hiring-team note on an application (Greenhouse activity feed notes). |
| `application_scorecard` | entity | soft | high | Structured evaluation of an application by one evaluator, optionally tied to an interview (Greenhouse scorecards; Lever feedback). |
| `application_scorecard_score` | entity | hard | high | Per-criterion score of a scorecard. |
| `application_status_transition` | config | hard | low | Allowed pipeline transitions (state machine). job_application_status_history has a composite FK to this table, so an illegal from->to pair cannot be inserted (decision: DB-enforced transitions, no triggers). |
| `application_view_event` | event | append_only | high | Records that an employer user looked at an application or resume (read receipt; candidate 'viewed by employer' feature; audit of who saw candidate data). |
| `candidate_job_search_history` | event | append_only | very_high | Candidate's job search history (keywords, filters, results) for 'recent searches', alert suggestions and ranking (Tier 2 coverage). |
| `interview` | entity | soft | high | A scheduled interview round for an application (Greenhouse scheduled interviews, LinkedIn Talent interview scheduling). |
| `interview_feedback` | entity | soft | high | Written feedback of an assigned interviewer. Composite FK ensures only an assigned interviewer of that interview can submit. |
| `interview_interviewer` | link | hard | high | Interviewers assigned to one interview with their invitation response. |
| `interview_panel` | entity | soft | low | Reusable interview panel of an organisation (optionally for one requirement). |
| `interview_panel_member` | link | hard | low | Member of a reusable panel. |
| `interview_reschedule` | event | append_only | medium | Each reschedule of an interview with old and new time and who asked. |
| `interview_slot` | entity | hard | high | Candidate-selectable time slots offered by the employer for an interview (self-scheduling). |
| `job_application` | entity | soft | very_high | A candidate's application to a job requirement (100M rows at full scale). Holds the current pipeline status; every change is mirrored by an append-only job_application_status_history row. |
| `job_application_document` | entity | soft | high | Extra documents attached to an application (cover letter, certificates, portfolio). Resume is referenced separately. |
| `job_application_screening_result` | entity | hard | very_high | Outcome of each configured screening rule for an application (explains is_screened_out). |
| `job_application_status_history` | event | append_only | very_high | Append-only stage transition log: who moved which application from which status to which, with reason and comment. Enforces legal transitions (FK to application_status_transition) and a gap-free chain (self composite FK). |
| `job_bookmark` | entity | soft | high | Job saved by a candidate (saved jobs / bookmarks), with optional reminder. |
| `job_boost` | entity | soft | medium | Paid visibility (sponsored/featured) for a requirement; hook to wallet credit consumption. |
| `job_consultant_openness` | entity | soft | low | Time window during which a requirement is open to consultants (docket: consultant may only work requirements open to consultants), with commission terms and access scope. job_requirement.is_open_to_consultants is true while a window is open. |
| `job_position` | entity | soft | high | One position/opening (headcount slot) of a requirement with its own status; Greenhouse 'openings'. Fill points to the winning application. |
| `job_posting_channel` | config | soft | low | Channel a requirement can be published to (own career site, partner board, social, campus portal). |
| `job_publish_history` | event | append_only | medium | Append-only history of publish state changes of a requirement. |
| `job_report` | entity | soft | low | Abuse/fraud report on a job by a user; feeds moderation. |
| `job_requirement` | entity | soft | high | A job requirement (posting) of an organisation: the central demand entity. Covers permanent, staffing, apprentice, campus, consultant-marketplace and managed-remote categories (gigs are a separate subsystem, D-15). |
| `job_requirement_approval` | entity | soft | medium | Internal approval before a requirement may publish (Greenhouse job approvals); one row per approver. |
| `job_requirement_assignment` | entity | soft | medium | Assignment of a requirement to a recruiter user. A recruiter acts only on requirements with an open assignment whose recruiter_organisation_mapping is active and belongs to the requirement's organisation (docket Section 3). |
| `job_requirement_benefit` | link | hard | medium | Perks and benefits advertised on a requirement. |
| `job_requirement_location` | link | hard | high | Work locations of a requirement (multi-city postings, Naukri multiple locations) with optional branch link and coordinates. |
| `job_requirement_qualification` | link | hard | medium | Qualifications sought (degree/diploma/certification) with mandatory flag and minimum score. |
| `job_requirement_revision` | event | append_only | medium | Versioned snapshot of a requirement's editable fields for audit and rollback (who changed salary, title, description). |
| `job_requirement_screening` | entity | soft | medium | Automatic screening rule of a requirement (min experience, max notice, qualification, city...), optionally knockout; applied at apply-time. |
| `job_requirement_skill` | link | hard | high | Skills sought by a requirement with importance and minimum proficiency (LinkedIn skills, Naukri key skills). |
| `job_requirement_stat` | snapshot | hard | high | Hot counters per requirement refreshed in batches from events (keeps per-view writes off the job_requirement row: avoids hot-row contention under SERIALIZABLE). |
| `job_requirement_status_history` | event | append_only | medium | Append-only history of hiring status changes of a requirement. |
| `job_share_event` | event | append_only | high | Outbound share of a job (social sharing boundary: stores the event and tracking token only, not external post content). |
| `job_shortlist` | entity | soft | medium | Employer/recruiter shortlist header (per requirement or organisation-wide). Referenced by the sourcing module for candidate sourcing shortlists and consultant submissions. |
| `job_shortlist_item` | link | hard | high | Candidate (optionally with her application) on a shortlist, with decision for consultant submissions. |
| `job_syndication` | entity | soft | high | Posting of a requirement to an external channel (integration boundary: stores reference and status, not external content). |
| `job_template` | entity | soft | low | Reusable posting template owned by an organisation (LinkedIn/Naukri job templates, Greenhouse job templates): pre-fills a new requirement. |
| `job_view_event` | event | append_only | very_high | Exposure event: impression, detail view, apply click, with attribution and search rank (Indeed/LinkedIn job view analytics; basis for 'viewed jobs' and ranking). |
| `job_walk_in_session` | entity | soft | medium | Walk-in interview drive of a requirement (Naukri walk-ins): venue, window, contact. |
| `joining` | entity | soft | medium | Outcome after offer acceptance: joined, no-show, deferred. One per application; feeds placement analytics and consultant fees. |
| `offer` | entity | soft | high | Job offer to a candidate for an application. Terms are versioned in offer_revision; at most one open offer per application. |
| `offer_approval` | entity | soft | medium | Internal approval request for an offer (Greenhouse approval flows), one row per approver. |
| `offer_component` | event | append_only | high | Compensation components (base, variable, bonus, stock) of one offer revision. |
| `offer_letter` | entity | soft | medium | Reference to the generated/signed offer letter document (object storage) for a revision; e-sign provider is an integration boundary. |
| `offer_revision` | event | append_only | high | Immutable version of offer terms (initial, counter, revised). Corrections are new revisions. |
| `onboarding_task` | entity | hard | medium | Checklist task for a joiner (documents, background check, IT). |
| `questionnaire` | entity | soft | medium | Employer-authored screening questionnaire (LinkedIn screening questions, Naukri questionnaires, Indeed screener questions). Reusable by many requirements of the same organisation. |
| `questionnaire_answer` | entity | hard | very_high | Candidate's answer to one questionnaire question within an application. Composite FKs prove that application, requirement, questionnaire and question all line up (BR-JOB-09). |
| `questionnaire_answer_option` | link | hard | very_high | Options selected by a candidate for choice questions (supports multi-choice). |
| `questionnaire_question` | entity | hard | high | One question of a questionnaire with answer type, required and knockout flags. |
| `questionnaire_question_option` | entity | hard | high | Selectable option of a choice question, with knockout acceptability and scoring points. |
| `rejection` | entity | soft | high | Rejection of an application with reason, optional message to the candidate. Reinstatement soft-deletes the row. |
| `talent_pool` | entity | soft | low | Employer talent pool/folder (Naukri folders, LinkedIn Talent pipeline projects) for candidates not tied to one requirement. |
| `talent_pool_member` | link | hard | high | Candidate stored in a talent pool with source and retention date. |
| `actor_kind` | lookup | hard | low | Kind of party that caused a transition or event. |
| `application_status` | lookup | hard | low | Pipeline stage of a job application (the status lookup requested by the registry). Terminal flag drives 'active application' logic. |
| `application_view_kind` | lookup | hard | low | What an employer looked at on an application. |
| `apply_mode` | lookup | hard | low | How a candidate applies to a requirement. |
| `boost_status` | lookup | hard | low | State of a boost. |
| `consultant_access_scope` | lookup | hard | low | Which consultants may work on a consultant-open requirement. |
| `hiring_urgency` | lookup | hard | low | How soon the employer wants the role filled. |
| `interview_mode` | lookup | hard | low | Medium of the interview. |
| `interview_recommendation` | lookup | hard | low | Interviewer recommendation (Greenhouse scorecard overall recommendation). |
| `interview_round_type` | lookup | hard | low | Type of interview round. |
| `interview_status` | lookup | hard | low | Lifecycle of a scheduled interview. |
| `interviewer_role` | lookup | hard | low | Role of a person on an interview panel. |
| `invitation_response` | lookup | hard | low | Interviewer response to an invitation. |
| `job_benefit_type` | lookup | hard | low | Perks and benefits advertised on a requirement. |
| `job_boost_type` | lookup | hard | low | Sponsored visibility product. |
| `job_close_reason` | lookup | hard | low | Why a requirement was closed or cancelled (Greenhouse close reasons). |
| `job_position_status` | lookup | hard | low | Status of an individual position (headcount slot) within a requirement. |
| `job_posting_channel_type` | lookup | hard | low | Kind of publishing channel. |
| `job_publish_state` | lookup | hard | low | Publish state of a job requirement. |
| `job_report_reason` | lookup | hard | low | Reasons a job can be reported. |
| `job_requirement_status` | lookup | hard | low | Hiring status of a job requirement (independent of publish state). |
| `job_screening_criterion_type` | lookup | hard | low | Dimension tested by a knockout/screening rule. |
| `job_source` | lookup | hard | low | Where an application, view or share originated (attribution). |
| `job_view_kind` | lookup | hard | low | Kind of job exposure event. |
| `joining_status` | lookup | hard | low | Outcome tracking after an accepted offer. |
| `note_visibility` | lookup | hard | low | Who can read an application note. |
| `offer_component_type` | lookup | hard | low | Compensation component of an offer. |
| `offer_decline_reason` | lookup | hard | low | Why a candidate declined an offer. |
| `offer_status` | lookup | hard | low | Lifecycle of an offer. |
| `onboarding_task_type` | lookup | hard | low | Kind of onboarding task. |
| `question_type` | lookup | hard | low | Answer type of a questionnaire question. |
| `report_status` | lookup | hard | low | Moderation state of a report. |
| `requirement_assignment_role` | lookup | hard | low | Role of a recruiter on a requirement. |
| `requirement_importance` | lookup | hard | low | Importance of a skill or qualification for a requirement. |
| `scorecard_criterion` | lookup | hard | low | Competency scored on a scorecard. |
| `screening_operator` | lookup | hard | low | Comparison operator of a screening rule. |
| `search_origin` | lookup | hard | low | Client surface a candidate search came from. |
| `share_channel` | lookup | hard | low | Outbound sharing channel for share events. |
| `shift_type` | lookup | hard | low | Working shift pattern, important for blue and grey collar staffing. |
| `shortlist_decision` | lookup | hard | low | Employer decision on a shortlisted/submitted candidate. |
| `shortlist_type` | lookup | hard | low | Purpose of a shortlist. |
| `syndication_status` | lookup | hard | low | State of a requirement posted to an external channel. |

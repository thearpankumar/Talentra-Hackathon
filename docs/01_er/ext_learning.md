# ER diagram: Extension: Learning and assessments marketplace

Module key `ext_learning`, owner Team, 112 tables (43 lookups). Generated from `05_data_dictionary.xlsx` (do not edit by hand). Columns shown: primary key, foreign keys, unique/natural keys and a few business columns; see the dictionary for all columns. Edges to lookup (enumeration) tables are listed under *Lookup references* rather than drawn.

## Design notes

<!-- ext_learning.yaml -->
**Module ext_learning (tier X) - Learning, assessments, credentials, mentoring and career resources.** Extension beyond the docket's 11 modules;
it stays inside Section 5 rules (lookups for enums, no UI, no live integrations, no ETL).

**Relationship to profile (Tier 2).** `profile` already owns `course_provider`, `course`, `course_enrolment`, `assessment`, `assessment_attempt`,
`candidate_certification`, `candidate_test_score`. This module does NOT redefine them; it adds the marketplace depth around them:
* Catalogue depth: instructor, module, lesson, lesson_progress, review, skill mapping, price/offer, prerequisite, language availability.
* Learning paths (path, item, enrolment, per-item progress) - LinkedIn Learning "learning path" / Coursera "specialisation".
* Question bank (bank, question with IRT calibration columns, options, per-language translations, skill mapping) and assessment design:
  blueprint (versioned timer/shuffle/adaptive/proctoring rules), section, session (timed sitting), answer / answer option (very high volume),
  result detail (section x skill breakdown), proctoring_event (append-only), proctoring_review - modelled on HackerRank / Pluralsight Skill IQ /
  Naukri Campus assessments (per-skill scoring, percentile, adaptive ability estimate, integrity score).
* Credentials: certificate_template, certificate (public `verification_code` UNIQUE + `serial_no` UNIQUE + SHA-256 content_hash), append-only
  certificate_verification and certificate_share logs, skill_badge + candidate_skill_badge (Skill IQ / LinkedIn skill-badge pattern).
* Mentorship (mentor_profile, skills/focus/availability, request, session, feedback), coaching_session (career counselling).
* Interview prep: practice_question_set(+item), mock_interview, mock_interview_answer with recording references (candidate_media_file; no media is stored in DB).
* Career resources: article (+author, category, tag, translation, view, salary-benchmark link) and resume_tip (+translation).
* Skill-gap analysis (report, item) and learning_recommendation with impression/outcome tracking.
* Employer-sponsored learning: learning_assignment (tenant `organisation_id`, mandatory flag, due date, sponsored amount).
* Provider integration boundary: learning_provider_integration + course_sync_event (idempotent on integration + external_event_ref, 90-day TTL).
* Engagement: learning_goal, learning_activity_event (append-only, TTL), learning_activity_day (roll-up), learning_streak, leaderboard_snapshot/entry.

**Decisions / deviations.**
1. Webinars/workshops: NOT modelled here; events belong to ext_network. Group workshops are only a `coaching_session_type` value. Add a nullable
   `event_id` FK once the ext_network event table is final (extension point).
2. Question text is never stored on `quiz_question`; stems/explanations/option labels live in `*_translation` (one row per language, `translation_status`
   + `translation_origin`), so a language fallback chain (requested -> language.fallback_language_id -> en) works exactly like ui_label_translation.
3. Polymorphic targets (certificate source, learning_assignment target, recommendation target, path item) use one nullable FK per target type plus
   a `num_nonnulls(...)` CHECK, never an untyped (type,id) pair, so every `*_id` stays an enforced FK (Rule 2).
4. High volume: `assessment_answer`, `assessment_answer_option`, `proctoring_event`, `lesson_progress`, `learning_recommendation`,
   `learning_activity_event`, `article_view` carry hash-sharded time indexes; keys are random UUIDs; progress rows are upserts on a natural unique key.
5. Append-only: proctoring_event, certificate_verification, certificate_share, article_view, learning_activity_event (INSERT/SELECT grants only).
   TTL (CockroachDB row-level) is used for article_view, learning_activity_event and course_sync_event. proctoring evidence retention (90 days) is a JOB.
6. Answer correctness: nullable booleans are avoided (Rule 3); `graded_at IS NULL` means ungraded, `is_correct`/`is_passed` default false until then.
7. Mentor session overlap: a unique `(mentor_profile_id, scheduled_at)` blocks identical starts; full interval non-overlap would use the P2 slot-table
   pattern (15-minute slots) and is flagged as an extension (not required by the docket).
8. Money columns (course_list_price, offer, mentoring/coaching fees, sponsored amount) are DECIMAL(12,2) with a companion currency_id and pair CHECKs.
9. Payments for paid courses/sessions use existing `wallet_ledger_entry` / payment tables via nullable FKs; this module owns no ledger.
10. Search: question text / article body search uses the search-service boundary (changefeed) per D-16; only slug/category/skill indexes are in-DB.
11. Web-research basis: LinkedIn Learning (paths, courses, skill badges, certificates of completion), Coursera (specialisations, peer review, verified
    certificates with verification URL), HackerRank (question banks, test sections, proctoring signals, plagiarism flags, integrity), Pluralsight Skill IQ
    (adaptive scoring, percentile bands), Naukri Campus / Skill Assessments (skill tests, badges), Open Badges concepts for badge/credential fields.
    Table shapes are our own design informed by these public product behaviours; no vendor schema is reproduced.
12. Not modelled (out of docket scope): SCORM runtime tracking, live video delivery, payment gateways, real proctoring AI - boundaries only (`*_ref`, `media_file`).

Language of user-entered text (finding F-06): free-text tables of this module carry a nullable `language_id` (FK `language`), NULL = unknown.

## Diagram

```mermaid
erDiagram
  article {
    UUID id PK
    UUID article_author_id FK
    UUID article_category_id FK
    UUID article_kind_id FK
    UUID article_status_id FK
    STRING_150 slug
    UUID source_language_id FK
    UUID country_id FK
    UUID job_role_id FK
    STRING_500 cover_image_url
    INT2 reading_time_minutes
  }
  article_author {
    UUID id PK
    STRING_150 display_name
    STRING_2000 bio
    STRING_500 photo_url
    UUID candidate_profile_id FK
    UUID organisation_id FK
  }
  article_category {
    UUID id PK
    STRING_60 code
    JSONB name_i18n
    UUID parent_article_category_id FK
    INT2 sort_order
  }
  article_salary_benchmark {
    UUID id PK
    UUID article_id FK
    UUID salary_benchmark_id FK
  }
  article_tag {
    UUID id PK
    STRING_60 code
    JSONB name_i18n
  }
  article_tag_assignment {
    UUID id PK
    UUID article_id FK
    UUID article_tag_id FK
  }
  article_translation {
    UUID id PK
    UUID article_id FK
    UUID language_id FK
    STRING_250 title
    STRING_600 summary
    STRING body
    UUID translation_status_id FK
    UUID translation_origin_id FK
  }
  article_view {
    UUID id PK
    UUID article_id FK
    UUID candidate_profile_id FK
    STRING_64 visitor_token
    UUID language_id FK
    TIMESTAMPTZ viewed_at
    INT4 read_seconds
  }
  assessment_answer {
    UUID id PK
    UUID assessment_session_id FK
    UUID quiz_question_id FK
    UUID assessment_section_id FK
    INT2 served_sequence
    TIMESTAMPTZ served_at
    TIMESTAMPTZ answered_at
    UUID language_id FK
  }
  assessment_answer_option {
    UUID id PK
    UUID assessment_answer_id FK
    UUID quiz_question_option_id FK
  }
  assessment_blueprint {
    UUID id PK
    UUID assessment_id FK
    INT2 version_no
    INT4 time_limit_seconds
    INT2 question_count
    UUID proctoring_mode_id FK
  }
  assessment_result_detail {
    UUID id PK
    UUID assessment_session_id FK
    UUID assessment_section_id FK
    UUID skill_id FK
    INT2 question_count
    INT2 correct_count
    DECIMAL_5_2 score_percent
    UUID awarded_proficiency_level_id FK
  }
  assessment_section {
    UUID id PK
    UUID assessment_blueprint_id FK
    INT2 sequence_no UK
    STRING_150 name
    UUID question_bank_id FK
    UUID skill_id FK
    INT2 question_count
    JSONB difficulty_mix
  }
  assessment_session {
    UUID id PK
    UUID candidate_profile_id FK
    UUID assessment_blueprint_id FK
    UUID assessment_attempt_id FK
    UUID assessment_session_status_id FK
    UUID proctoring_mode_id FK
    UUID language_id FK
    TIMESTAMPTZ scheduled_at
    TIMESTAMPTZ started_at
    TIMESTAMPTZ deadline_at
  }
  candidate_skill_badge {
    UUID id PK
    UUID candidate_profile_id FK
    UUID skill_badge_id FK
    TIMESTAMPTZ awarded_at
    TIMESTAMPTZ expires_at
    UUID assessment_session_id FK
    UUID certificate_id FK
    BOOL show_on_profile
  }
  certificate {
    UUID id PK
    UUID candidate_profile_id FK
    UUID certificate_template_id FK
    UUID certificate_status_id FK
    STRING_40 serial_no
    STRING_32 verification_code
    STRING_200 title
    UUID course_id FK
    UUID learning_path_id FK
    UUID assessment_attempt_id FK
    UUID skill_id FK
    UUID candidate_certification_id FK
    UUID candidate_media_file_id FK
    UUID certificate_revocation_reason_id FK
  }
  certificate_share {
    UUID id PK
    UUID certificate_id FK
    UUID credential_share_target_id FK
    TIMESTAMPTZ shared_at
  }
  certificate_template {
    UUID id PK
    STRING_60 code
    STRING_150 name
    INT2 version_no
    UUID course_provider_id FK
    UUID language_id FK
  }
  certificate_verification {
    UUID id PK
    UUID certificate_id FK
    STRING_32 presented_code
    UUID verifier_organisation_id FK
    INET verifier_ip
    BOOL is_valid
  }
  coaching_session {
    UUID id PK
    UUID candidate_profile_id FK
    UUID coach_app_user_id FK
    UUID coaching_session_type_id FK
    UUID learning_session_status_id FK
    UUID session_mode_id FK
    UUID sponsor_organisation_id FK
    TIMESTAMPTZ scheduled_at
    INT2 duration_minutes
    STRING_300 topic
    UUID wallet_ledger_entry_id FK
    UUID language_id FK
  }
  course_instructor {
    UUID id PK
    UUID course_id FK
    UUID course_instructor_profile_id FK
    UUID course_instructor_role_id FK
    INT2 display_order
  }
  course_instructor_profile {
    UUID id PK
    STRING_150 display_name
    STRING_200 headline
    STRING bio
    UUID candidate_profile_id FK
    UUID course_provider_id FK
    UUID language_id FK
  }
  course_language_availability {
    UUID id PK
    UUID course_id FK
    UUID language_id FK
    BOOL has_audio
    BOOL has_subtitles
    BOOL has_transcript
  }
  course_lesson {
    UUID id PK
    UUID course_module_id FK
    INT2 sequence_no
    STRING_200 title
    UUID learning_content_type_id FK
    INT4 duration_seconds
    UUID quiz_question_bank_id FK
  }
  course_list_price {
    UUID id PK
    UUID course_id FK
    UUID country_id FK
    UUID course_access_model_id FK
    UUID currency_id FK
    DECIMAL_12_2 list_amount
    INT4 credit_cost
    TIMESTAMPTZ valid_from
  }
  course_module {
    UUID id PK
    UUID course_id FK
    INT2 sequence_no
    STRING_200 title
    STRING_1000 summary
  }
  course_offer {
    UUID id PK
    UUID course_id FK
    UUID learning_path_id FK
    UUID course_provider_id FK
    UUID course_offer_kind_id FK
    STRING_40 offer_code
    DECIMAL_5_2 discount_percent
    DECIMAL_12_2 discount_amount
    UUID currency_id FK
  }
  course_prerequisite {
    UUID id PK
    UUID course_id FK
    UUID prerequisite_course_id FK
    BOOL is_mandatory
  }
  course_review {
    UUID id PK
    UUID course_id FK
    UUID candidate_profile_id FK
    INT2 rating
    STRING_200 title
    STRING body
    UUID content_review_status_id FK
    UUID language_id FK
  }
  course_skill {
    UUID id PK
    UUID course_id FK
    UUID skill_id FK
    UUID proficiency_level_id FK
    BOOL is_primary
    DECIMAL_4_2 weight
  }
  course_sync_event {
    UUID id PK
    UUID learning_provider_integration_id FK
    STRING_150 external_event_ref UK
    UUID provider_sync_event_type_id FK
    UUID provider_sync_status_id FK
    JSONB payload
    TIMESTAMPTZ received_at
    TIMESTAMPTZ processed_at
  }
  leaderboard_entry {
    UUID id PK
    UUID leaderboard_snapshot_id FK
    UUID candidate_profile_id FK
    INT4 rank_no
    INT4 score_points
    BOOL is_anonymised
  }
  leaderboard_snapshot {
    UUID id PK
    UUID leaderboard_scope_id FK
    UUID leaderboard_period_id FK
    STRING_100 scope_key UK
    UUID skill_id FK
    UUID country_id FK
    UUID organisation_id FK
    DATE period_start_date UK
    DATE period_end_date
    TIMESTAMPTZ computed_at
    INT4 entry_count
  }
  learning_activity_day {
    UUID id PK
    UUID candidate_profile_id FK
    DATE activity_date UK
    INT4 minutes_spent
    INT4 xp_points
    INT4 activity_count
  }
  learning_activity_event {
    UUID id PK
    UUID candidate_profile_id FK
    UUID learning_activity_kind_id FK
    DATE activity_date
    TIMESTAMPTZ occurred_at
    INT4 xp_points
    STRING_100 idempotency_key UK
  }
  learning_assignment {
    UUID id PK
    UUID organisation_id FK
    UUID assigned_by_app_user_id FK
    UUID candidate_profile_id FK
    UUID learning_target_type_id FK
    UUID course_id FK
    UUID learning_path_id FK
    UUID assessment_id FK
    UUID learning_assignment_status_id FK
    BOOL is_mandatory
    DATE due_date
    TIMESTAMPTZ assigned_at
    UUID currency_id FK
  }
  learning_goal {
    UUID id PK
    UUID candidate_profile_id FK
    UUID learning_goal_metric_id FK
    UUID skill_id FK
    INT4 target_value
    INT4 current_value
    UUID learning_goal_status_id FK
    DATE goal_start_date
  }
  learning_path {
    UUID id PK
    STRING_60 code
    STRING_200 title
    STRING description
    UUID target_job_role_id FK
    UUID primary_skill_id FK
    UUID proficiency_level_id FK
    UUID owner_organisation_id FK
    UUID language_id FK
    UUID visibility_level_id FK
    UUID course_access_model_id FK
  }
  learning_path_enrolment {
    UUID id PK
    UUID learning_path_id FK
    UUID candidate_profile_id FK
    UUID learning_progress_status_id FK
    TIMESTAMPTZ enrolled_at
    TIMESTAMPTZ completed_at
    DECIMAL_5_2 progress_percent
    UUID learning_assignment_id FK
    UUID certificate_id FK
  }
  learning_path_item {
    UUID id PK
    UUID learning_path_id FK
    INT2 sequence_no UK
    UUID course_id FK
    UUID assessment_id FK
    BOOL is_optional
  }
  learning_path_item_progress {
    UUID id PK
    UUID learning_path_enrolment_id FK
    UUID learning_path_item_id FK
    UUID learning_progress_status_id FK
    TIMESTAMPTZ completed_at
    DECIMAL_5_2 score_percent
  }
  learning_provider_integration {
    UUID id PK
    UUID course_provider_id FK
    UUID provider_sync_mode_id FK
    STRING_500 endpoint_url
    STRING_200 credential_vault_ref
    STRING_300 sync_cursor
  }
  learning_recommendation {
    UUID id PK
    UUID candidate_profile_id FK
    UUID skill_gap_item_id FK
    UUID course_id FK
    UUID learning_path_id FK
    UUID assessment_id FK
    INT2 rank_no
    DECIMAL_6_4 relevance_score
    STRING_40 reason_code
    UUID recommendation_outcome_id FK
  }
  learning_streak {
    UUID id PK
    UUID candidate_profile_id FK
    INT4 current_streak_days
    INT4 longest_streak_days
    DATE last_activity_date
  }
  lesson_progress {
    UUID id PK
    UUID candidate_profile_id FK
    UUID course_lesson_id FK
    UUID course_enrolment_id FK
    UUID learning_progress_status_id FK
    INT4 last_position_seconds
    INT4 watched_seconds
    TIMESTAMPTZ completed_at
  }
  mentor_availability {
    UUID id PK
    UUID mentor_profile_id FK
    INT2 weekday_no UK
    TIME window_start_time UK
    TIME window_end_time
  }
  mentor_focus {
    UUID id PK
    UUID mentor_profile_id FK
    UUID mentorship_focus_area_id FK
  }
  mentor_profile {
    UUID id PK
    UUID candidate_profile_id FK
    UUID mentor_status_id FK
    STRING_200 headline
    STRING bio
    UUID industry_id FK
    INT2 years_experience
    UUID currency_id FK
    UUID language_id FK
  }
  mentor_skill {
    UUID id PK
    UUID mentor_profile_id FK
    UUID skill_id FK
    UUID proficiency_level_id FK
  }
  mentorship_feedback {
    UUID id PK
    UUID mentorship_session_id FK
    UUID author_candidate_profile_id FK
    BOOL is_from_mentor
    INT2 rating
    STRING comment_text
    UUID content_review_status_id FK
    UUID language_id FK
  }
  mentorship_request {
    UUID id PK
    UUID mentee_candidate_profile_id FK
    UUID mentor_profile_id FK
    UUID mentorship_focus_area_id FK
    UUID mentorship_request_status_id FK
    STRING message
    TIMESTAMPTZ responded_at
    TIMESTAMPTZ expires_at
    UUID language_id FK
  }
  mentorship_session {
    UUID id PK
    UUID mentorship_request_id FK
    UUID mentor_profile_id FK
    UUID mentee_candidate_profile_id FK
    UUID learning_session_status_id FK
    UUID session_mode_id FK
    TIMESTAMPTZ scheduled_at
    INT2 duration_minutes
    STRING_64 time_zone
    UUID currency_id FK
    UUID wallet_ledger_entry_id FK
    UUID language_id FK
  }
  mock_interview {
    UUID id PK
    UUID candidate_profile_id FK
    UUID practice_question_set_id FK
    UUID target_job_role_id FK
    UUID mentor_profile_id FK
    UUID mock_interview_status_id FK
    UUID session_mode_id FK
    TIMESTAMPTZ scheduled_at
    TIMESTAMPTZ started_at
    TIMESTAMPTZ completed_at
    UUID interview_id FK
    UUID language_id FK
  }
  mock_interview_answer {
    UUID id PK
    UUID mock_interview_id FK
    UUID quiz_question_id FK
    INT2 sequence_no UK
    UUID recording_candidate_media_file_id FK
    STRING transcript
    INT4 answer_seconds
    DECIMAL_5_2 score_percent
    UUID evaluator_kind_id FK
    UUID language_id FK
  }
  practice_question_set {
    UUID id PK
    STRING_60 code
    STRING_200 title
    UUID job_role_id FK
    UUID skill_id FK
    UUID question_difficulty_id FK
    UUID language_id FK
    UUID owner_organisation_id FK
    BOOL is_published
  }
  practice_question_set_item {
    UUID id PK
    UUID practice_question_set_id FK
    UUID quiz_question_id FK
    INT2 sequence_no UK
  }
  proctoring_event {
    UUID id PK
    UUID assessment_session_id FK
    UUID proctoring_event_type_id FK
    UUID proctoring_severity_id FK
    TIMESTAMPTZ occurred_at
    INT4 offset_seconds
    DECIMAL_5_4 confidence
    UUID evidence_candidate_media_file_id FK
  }
  proctoring_review {
    UUID id PK
    UUID assessment_session_id FK
    UUID reviewer_app_user_id FK
    UUID proctoring_review_outcome_id FK
    TIMESTAMPTZ reviewed_at
    STRING_2000 notes
  }
  question_bank {
    UUID id PK
    STRING_60 code
    STRING_200 name
    STRING_1000 description
    UUID primary_skill_id FK
    UUID owner_organisation_id FK
    UUID source_language_id FK
    UUID visibility_level_id FK
  }
  quiz_question {
    UUID id PK
    UUID question_bank_id FK
    STRING_60 item_code
    UUID quiz_question_format_id FK
    UUID question_difficulty_id FK
    UUID question_status_id FK
    UUID primary_skill_id FK
    DECIMAL_6_2 points
    INT4 time_limit_seconds
    UUID supersedes_quiz_question_id FK
    UUID language_id FK
  }
  quiz_question_option {
    UUID id PK
    UUID quiz_question_id FK
    STRING_20 option_key UK
    INT2 sequence_no
    BOOL is_correct
    DECIMAL_5_2 partial_credit_percent
  }
  quiz_question_option_translation {
    UUID id PK
    UUID quiz_question_option_id FK
    UUID language_id FK
    STRING_1000 label
  }
  quiz_question_skill {
    UUID id PK
    UUID quiz_question_id FK
    UUID skill_id FK
    DECIMAL_4_2 weight
  }
  quiz_question_translation {
    UUID id PK
    UUID quiz_question_id FK
    UUID language_id FK
    STRING stem
    STRING explanation
    UUID translation_status_id FK
    UUID translation_origin_id FK
  }
  resume_tip {
    UUID id PK
    STRING_60 code
    UUID resume_tip_section_id FK
    UUID job_role_id FK
    UUID skill_id FK
    INT2 priority
    BOOL is_published
  }
  resume_tip_translation {
    UUID id PK
    UUID resume_tip_id FK
    UUID language_id FK
    STRING_200 headline
    STRING_2000 body
    STRING_1000 example_before
  }
  skill_badge {
    UUID id PK
    STRING_60 code
    STRING_150 name
    UUID skill_id FK
    UUID badge_tier_id FK
    UUID assessment_id FK
    DECIMAL_5_2 min_score_percent
  }
  skill_gap_item {
    UUID id PK
    UUID skill_gap_report_id FK
    UUID skill_id FK
    UUID required_proficiency_level_id FK
    UUID current_proficiency_level_id FK
    UUID gap_priority_id FK
    INT2 gap_levels
    BOOL is_verified_skill
  }
  skill_gap_report {
    UUID id PK
    UUID candidate_profile_id FK
    UUID target_job_role_id FK
    UUID target_job_requirement_id FK
    UUID gap_report_status_id FK
    TIMESTAMPTZ generated_at
    DECIMAL_5_2 match_percent
    STRING_40 model_version
  }
  app_user {
    UUID id PK
  }
  assessment {
    UUID id PK
  }
  assessment_attempt {
    UUID id PK
  }
  candidate_certification {
    UUID id PK
  }
  candidate_media_file {
    UUID id PK
  }
  candidate_profile {
    UUID id PK
  }
  country {
    UUID id PK
  }
  course {
    UUID id PK
  }
  course_enrolment {
    UUID id PK
  }
  course_provider {
    UUID id PK
  }
  currency {
    UUID id PK
  }
  industry {
    UUID id PK
  }
  interview {
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
  salary_benchmark {
    UUID id PK
  }
  skill {
    UUID id PK
  }
  wallet_ledger_entry {
    UUID id PK
  }
  app_user |o--o{ proctoring_review : "reviewer_app_user_id"
  app_user ||--o{ coaching_session : "coach_app_user_id"
  app_user ||--o{ learning_assignment : "assigned_by_app_user_id"
  article ||--o{ article_salary_benchmark : "article_id"
  article ||--o{ article_tag_assignment : "article_id"
  article ||--o{ article_translation : "article_id"
  article ||--o{ article_view : "article_id"
  article_author ||--o{ article : "article_author_id"
  article_category |o--o{ article_category : "parent_article_category_id"
  article_category ||--o{ article : "article_category_id"
  article_tag ||--o{ article_tag_assignment : "article_tag_id"
  assessment |o--o{ learning_assignment : "assessment_id"
  assessment |o--o{ learning_path_item : "assessment_id"
  assessment |o--o{ learning_recommendation : "assessment_id"
  assessment |o--o{ skill_badge : "assessment_id"
  assessment ||--o{ assessment_blueprint : "assessment_id"
  assessment_answer ||--o{ assessment_answer_option : "assessment_answer_id"
  assessment_attempt |o--o{ assessment_session : "assessment_attempt_id"
  assessment_attempt |o--o{ certificate : "assessment_attempt_id"
  assessment_blueprint ||--o{ assessment_section : "assessment_blueprint_id"
  assessment_blueprint ||--o{ assessment_session : "assessment_blueprint_id"
  assessment_section ||--o{ assessment_answer : "assessment_section_id"
  assessment_section ||--o{ assessment_result_detail : "assessment_section_id"
  assessment_session |o--o{ candidate_skill_badge : "assessment_session_id"
  assessment_session ||--o{ assessment_answer : "assessment_session_id"
  assessment_session ||--o{ assessment_result_detail : "assessment_session_id"
  assessment_session ||--o{ proctoring_event : "assessment_session_id"
  assessment_session ||--o{ proctoring_review : "assessment_session_id"
  candidate_certification |o--o{ certificate : "candidate_certification_id"
  candidate_media_file |o--o{ certificate : "candidate_media_file_id"
  candidate_media_file |o--o{ mock_interview_answer : "recording_candidate_media_file_id"
  candidate_media_file |o--o{ proctoring_event : "evidence_candidate_media_file_id"
  candidate_profile |o--o{ article_author : "candidate_profile_id"
  candidate_profile |o--o{ article_view : "candidate_profile_id"
  candidate_profile |o--o{ course_instructor_profile : "candidate_profile_id"
  candidate_profile ||--o{ assessment_session : "candidate_profile_id"
  candidate_profile ||--o{ candidate_skill_badge : "candidate_profile_id"
  candidate_profile ||--o{ certificate : "candidate_profile_id"
  candidate_profile ||--o{ coaching_session : "candidate_profile_id"
  candidate_profile ||--o{ course_review : "candidate_profile_id"
  candidate_profile ||--o{ leaderboard_entry : "candidate_profile_id"
  candidate_profile ||--o{ learning_activity_day : "candidate_profile_id"
  candidate_profile ||--o{ learning_activity_event : "candidate_profile_id"
  candidate_profile ||--o{ learning_assignment : "candidate_profile_id"
  candidate_profile ||--o{ learning_goal : "candidate_profile_id"
  candidate_profile ||--o{ learning_path_enrolment : "candidate_profile_id"
  candidate_profile ||--o{ learning_recommendation : "candidate_profile_id"
  candidate_profile ||--o{ lesson_progress : "candidate_profile_id"
  candidate_profile ||--o{ mentor_profile : "candidate_profile_id"
  candidate_profile ||--o{ mentorship_feedback : "author_candidate_profile_id"
  candidate_profile ||--o{ mentorship_request : "mentee_candidate_profile_id"
  candidate_profile ||--o{ mentorship_session : "mentee_candidate_profile_id"
  candidate_profile ||--o{ mock_interview : "candidate_profile_id"
  candidate_profile ||--o{ skill_gap_report : "candidate_profile_id"
  candidate_profile ||--o| learning_streak : "candidate_profile_id"
  certificate |o--o{ candidate_skill_badge : "certificate_id"
  certificate |o--o{ certificate_verification : "certificate_id"
  certificate |o--o{ learning_path_enrolment : "certificate_id"
  certificate ||--o{ certificate_share : "certificate_id"
  certificate_template ||--o{ certificate : "certificate_template_id"
  country |o--o{ article : "country_id"
  country |o--o{ course_list_price : "country_id"
  country |o--o{ leaderboard_snapshot : "country_id"
  course |o--o{ certificate : "course_id"
  course |o--o{ course_offer : "course_id"
  course |o--o{ learning_assignment : "course_id"
  course |o--o{ learning_path_item : "course_id"
  course |o--o{ learning_recommendation : "course_id"
  course ||--o{ course_instructor : "course_id"
  course ||--o{ course_language_availability : "course_id"
  course ||--o{ course_list_price : "course_id"
  course ||--o{ course_module : "course_id"
  course ||--o{ course_prerequisite : "course_id"
  course ||--o{ course_prerequisite : "prerequisite_course_id"
  course ||--o{ course_review : "course_id"
  course ||--o{ course_skill : "course_id"
  course_enrolment |o--o{ lesson_progress : "course_enrolment_id"
  course_instructor_profile ||--o{ course_instructor : "course_instructor_profile_id"
  course_lesson ||--o{ lesson_progress : "course_lesson_id"
  course_module ||--o{ course_lesson : "course_module_id"
  course_provider |o--o{ certificate_template : "course_provider_id"
  course_provider |o--o{ course_instructor_profile : "course_provider_id"
  course_provider |o--o{ course_offer : "course_provider_id"
  course_provider ||--o{ learning_provider_integration : "course_provider_id"
  currency |o--o{ course_offer : "currency_id"
  currency |o--o{ learning_assignment : "currency_id"
  currency |o--o{ mentor_profile : "currency_id"
  currency |o--o{ mentorship_session : "currency_id"
  currency ||--o{ course_list_price : "currency_id"
  industry |o--o{ mentor_profile : "industry_id"
  interview |o--o{ mock_interview : "interview_id"
  job_requirement |o--o{ skill_gap_report : "target_job_requirement_id"
  job_role |o--o{ article : "job_role_id"
  job_role |o--o{ learning_path : "target_job_role_id"
  job_role |o--o{ mock_interview : "target_job_role_id"
  job_role |o--o{ practice_question_set : "job_role_id"
  job_role |o--o{ resume_tip : "job_role_id"
  job_role |o--o{ skill_gap_report : "target_job_role_id"
  language |o--o{ assessment_answer : "language_id"
  language |o--o{ coaching_session : "language_id"
  language |o--o{ course_instructor_profile : "language_id"
  language |o--o{ course_review : "language_id"
  language |o--o{ mentor_profile : "language_id"
  language |o--o{ mentorship_feedback : "language_id"
  language |o--o{ mentorship_request : "language_id"
  language |o--o{ mentorship_session : "language_id"
  language |o--o{ mock_interview : "language_id"
  language |o--o{ mock_interview_answer : "language_id"
  language |o--o{ quiz_question : "language_id"
  language ||--o{ article : "source_language_id"
  language ||--o{ article_translation : "language_id"
  language ||--o{ article_view : "language_id"
  language ||--o{ assessment_session : "language_id"
  language ||--o{ certificate_template : "language_id"
  language ||--o{ course_language_availability : "language_id"
  language ||--o{ learning_path : "language_id"
  language ||--o{ practice_question_set : "language_id"
  language ||--o{ question_bank : "source_language_id"
  language ||--o{ quiz_question_option_translation : "language_id"
  language ||--o{ quiz_question_translation : "language_id"
  language ||--o{ resume_tip_translation : "language_id"
  leaderboard_snapshot ||--o{ leaderboard_entry : "leaderboard_snapshot_id"
  learning_assignment |o--o{ learning_path_enrolment : "learning_assignment_id"
  learning_path |o--o{ certificate : "learning_path_id"
  learning_path |o--o{ course_offer : "learning_path_id"
  learning_path |o--o{ learning_assignment : "learning_path_id"
  learning_path |o--o{ learning_recommendation : "learning_path_id"
  learning_path ||--o{ learning_path_enrolment : "learning_path_id"
  learning_path ||--o{ learning_path_item : "learning_path_id"
  learning_path_enrolment ||--o{ learning_path_item_progress : "learning_path_enrolment_id"
  learning_path_item ||--o{ learning_path_item_progress : "learning_path_item_id"
  learning_provider_integration ||--o{ course_sync_event : "learning_provider_integration_id"
  mentor_profile |o--o{ mock_interview : "mentor_profile_id"
  mentor_profile ||--o{ mentor_availability : "mentor_profile_id"
  mentor_profile ||--o{ mentor_focus : "mentor_profile_id"
  mentor_profile ||--o{ mentor_skill : "mentor_profile_id"
  mentor_profile ||--o{ mentorship_request : "mentor_profile_id"
  mentor_profile ||--o{ mentorship_session : "mentor_profile_id"
  mentorship_request |o--o{ mentorship_session : "mentorship_request_id"
  mentorship_session ||--o{ mentorship_feedback : "mentorship_session_id"
  mock_interview ||--o{ mock_interview_answer : "mock_interview_id"
  organisation |o--o{ article_author : "organisation_id"
  organisation |o--o{ certificate_verification : "verifier_organisation_id"
  organisation |o--o{ coaching_session : "sponsor_organisation_id"
  organisation |o--o{ leaderboard_snapshot : "organisation_id"
  organisation |o--o{ learning_path : "owner_organisation_id"
  organisation |o--o{ practice_question_set : "owner_organisation_id"
  organisation |o--o{ question_bank : "owner_organisation_id"
  organisation ||--o{ learning_assignment : "organisation_id"
  practice_question_set |o--o{ mock_interview : "practice_question_set_id"
  practice_question_set ||--o{ practice_question_set_item : "practice_question_set_id"
  question_bank |o--o{ course_lesson : "quiz_question_bank_id"
  question_bank ||--o{ assessment_section : "question_bank_id"
  question_bank ||--o{ quiz_question : "question_bank_id"
  quiz_question |o--o{ quiz_question : "supersedes_quiz_question_id"
  quiz_question ||--o{ assessment_answer : "quiz_question_id"
  quiz_question ||--o{ mock_interview_answer : "quiz_question_id"
  quiz_question ||--o{ practice_question_set_item : "quiz_question_id"
  quiz_question ||--o{ quiz_question_option : "quiz_question_id"
  quiz_question ||--o{ quiz_question_skill : "quiz_question_id"
  quiz_question ||--o{ quiz_question_translation : "quiz_question_id"
  quiz_question_option ||--o{ assessment_answer_option : "quiz_question_option_id"
  quiz_question_option ||--o{ quiz_question_option_translation : "quiz_question_option_id"
  resume_tip ||--o{ resume_tip_translation : "resume_tip_id"
  salary_benchmark ||--o{ article_salary_benchmark : "salary_benchmark_id"
  skill |o--o{ assessment_section : "skill_id"
  skill |o--o{ certificate : "skill_id"
  skill |o--o{ leaderboard_snapshot : "skill_id"
  skill |o--o{ learning_goal : "skill_id"
  skill |o--o{ learning_path : "primary_skill_id"
  skill |o--o{ practice_question_set : "skill_id"
  skill |o--o{ question_bank : "primary_skill_id"
  skill |o--o{ quiz_question : "primary_skill_id"
  skill |o--o{ resume_tip : "skill_id"
  skill ||--o{ assessment_result_detail : "skill_id"
  skill ||--o{ course_skill : "skill_id"
  skill ||--o{ mentor_skill : "skill_id"
  skill ||--o{ quiz_question_skill : "skill_id"
  skill ||--o{ skill_badge : "skill_id"
  skill ||--o{ skill_gap_item : "skill_id"
  skill_badge ||--o{ candidate_skill_badge : "skill_badge_id"
  skill_gap_item |o--o{ learning_recommendation : "skill_gap_item_id"
  skill_gap_report ||--o{ skill_gap_item : "skill_gap_report_id"
  wallet_ledger_entry |o--o{ coaching_session : "wallet_ledger_entry_id"
  wallet_ledger_entry |o--o{ mentorship_session : "wallet_ledger_entry_id"
```

## Lookup tables in this module

| Lookup | Values | Description |
|---|---|---|
| `article_kind` | 8 | Editorial type of a career resource. |
| `article_status` | 5 | Editorial lifecycle of an article. |
| `assessment_session_status` | 7 | Lifecycle of a timed assessment sitting. |
| `badge_tier` | 4 | Tier of a skill badge. |
| `certificate_revocation_reason` | 5 | Why a certificate was revoked. |
| `certificate_status` | 4 | State of an issued certificate. |
| `coaching_session_type` | 6 | Type of career coaching session. |
| `content_review_status` | 4 | Moderation state of user-generated learning content (reviews). |
| `course_access_model` | 5 | How a learner gets access to a course or path. |
| `course_instructor_role` | 4 | Role of an instructor on a course. |
| `course_offer_kind` | 6 | Type of promotional offer on course prices. |
| `credential_share_target` | 6 | Destination a credential is shared to. |
| `evaluator_kind` | 4 | Who produced a mock-interview evaluation. |
| `gap_priority` | 4 | Priority of closing a skill gap. |
| `gap_report_status` | 4 | State of a skill-gap report. |
| `leaderboard_period` | 4 | Time window of a leaderboard. |
| `leaderboard_scope` | 5 | Population a leaderboard ranks. |
| `learning_activity_kind` | 6 | Kind of learning activity that counts toward streaks and XP. |
| `learning_assignment_status` | 7 | State of an employer-assigned learning item. |
| `learning_content_type` | 8 | Kind of learning content a lesson delivers. |
| `learning_goal_metric` | 5 | Metric a learning goal tracks. |
| `learning_goal_status` | 4 | State of a learning goal. |
| `learning_progress_status` | 5 | Progress state of a lesson, path item or path enrolment. |
| `learning_session_status` | 5 | State of a scheduled mentoring or coaching session. |
| `learning_target_type` | 3 | What a learning assignment points to. |
| `mentor_status` | 5 | Lifecycle of a mentor application. |
| `mentorship_focus_area` | 7 | Topics a mentor can help with. |
| `mentorship_request_status` | 5 | State of a mentorship request. |
| `mock_interview_status` | 5 | State of a mock interview. |
| `proctoring_event_type` | 11 | Signals recorded during a proctored sitting. |
| `proctoring_mode` | 4 | Level of proctoring applied to a sitting. |
| `proctoring_review_outcome` | 4 | Human decision after reviewing proctoring evidence. |
| `proctoring_severity` | 3 | Severity of a proctoring signal. |
| `provider_sync_event_type` | 6 | Type of provider event. |
| `provider_sync_mode` | 4 | How a provider integration exchanges data. |
| `provider_sync_status` | 4 | Processing state of an inbound provider sync event. |
| `question_difficulty` | 4 | Calibrated difficulty bands for questions. |
| `question_status` | 5 | Lifecycle of a question in the bank. |
| `quiz_question_format` | 10 | Answer format of a question-bank question. |
| `recommendation_outcome` | 5 | What happened to a learning recommendation. |
| `resume_tip_section` | 7 | Resume section a tip applies to. |
| `session_mode` | 4 | Delivery mode of a session. |
| `translation_origin` | 4 | How a translated text was produced. |

## Lookup references (enumeration FKs)

| Table | Lookup tables referenced |
|---|---|
| `article` | `article_kind`, `article_status` |
| `article_translation` | `translation_origin`, `translation_status` |
| `assessment_blueprint` | `proctoring_mode` |
| `assessment_result_detail` | `proficiency_level` |
| `assessment_session` | `assessment_session_status`, `proctoring_mode` |
| `certificate` | `certificate_revocation_reason`, `certificate_status` |
| `certificate_share` | `credential_share_target` |
| `coaching_session` | `coaching_session_type`, `learning_session_status`, `session_mode` |
| `course_instructor` | `course_instructor_role` |
| `course_lesson` | `learning_content_type` |
| `course_list_price` | `course_access_model` |
| `course_offer` | `course_offer_kind` |
| `course_review` | `content_review_status` |
| `course_skill` | `proficiency_level` |
| `course_sync_event` | `provider_sync_event_type`, `provider_sync_status` |
| `leaderboard_snapshot` | `leaderboard_period`, `leaderboard_scope` |
| `learning_activity_event` | `learning_activity_kind` |
| `learning_assignment` | `learning_assignment_status`, `learning_target_type` |
| `learning_goal` | `learning_goal_metric`, `learning_goal_status` |
| `learning_path` | `course_access_model`, `proficiency_level`, `visibility_level` |
| `learning_path_enrolment` | `learning_progress_status` |
| `learning_path_item_progress` | `learning_progress_status` |
| `learning_provider_integration` | `provider_sync_mode` |
| `learning_recommendation` | `recommendation_outcome` |
| `lesson_progress` | `learning_progress_status` |
| `mentor_focus` | `mentorship_focus_area` |
| `mentor_profile` | `mentor_status` |
| `mentor_skill` | `proficiency_level` |
| `mentorship_feedback` | `content_review_status` |
| `mentorship_request` | `mentorship_focus_area`, `mentorship_request_status` |
| `mentorship_session` | `learning_session_status`, `session_mode` |
| `mock_interview` | `mock_interview_status`, `session_mode` |
| `mock_interview_answer` | `evaluator_kind` |
| `practice_question_set` | `question_difficulty` |
| `proctoring_event` | `proctoring_event_type`, `proctoring_severity` |
| `proctoring_review` | `proctoring_review_outcome` |
| `question_bank` | `visibility_level` |
| `quiz_question` | `question_difficulty`, `question_status`, `quiz_question_format` |
| `quiz_question_translation` | `translation_origin`, `translation_status` |
| `resume_tip` | `resume_tip_section` |
| `skill_badge` | `badge_tier` |
| `skill_gap_item` | `gap_priority`, `proficiency_level` |
| `skill_gap_report` | `gap_report_status` |

## Tables

| Table | Kind | Delete | Volume | Description |
|---|---|---|---|---|
| `article` | entity | soft | medium | Career resource article (advice, news, salary guide, how-to); localised text lives in article_translation. |
| `article_author` | entity | soft | low | Author of career resources (staff, guest expert, partner organisation). |
| `article_category` | entity | soft | low | Hierarchical category of career resources. |
| `article_salary_benchmark` | link | hard | low | Salary benchmark datasets referenced by a salary-guide article. |
| `article_tag` | entity | soft | low | Free-form tag for career resources. |
| `article_tag_assignment` | link | hard | medium | Tags attached to articles. |
| `article_translation` | entity | soft | medium | Localised title and body of an article. |
| `article_view` | event | append_only | very_high | Append-only article view for analytics and recommendations. |
| `assessment_answer` | event | hard | very_high | A question served in a sitting and the learner response (highest-volume assessment table). |
| `assessment_answer_option` | link | hard | very_high | Options selected for choice questions. |
| `assessment_blueprint` | config | soft | low | Versioned test design for an assessment (timer, shuffle, adaptive rules, proctoring level, attempt limits). |
| `assessment_result_detail` | entity | hard | high | Per section and skill score breakdown of a sitting (feeds badges, gap analysis, percentile). |
| `assessment_section` | config | hard | low | Section of a blueprint: question source, count, difficulty mix and optional section timer. |
| `assessment_session` | entity | hard | very_high | One timed, optionally proctored sitting of an assessment. |
| `candidate_skill_badge` | entity | soft | high | Badge awarded to a candidate. |
| `certificate` | entity | soft | high | Digital certificate issued for a course, path or assessment, publicly verifiable by code. |
| `certificate_share` | event | append_only | medium | Append-only record of a holder sharing a certificate or badge. |
| `certificate_template` | config | soft | low | Certificate layout and signing metadata owned by a provider or the platform. |
| `certificate_verification` | event | append_only | high | Append-only log of public verification lookups by code (employers, background checkers). |
| `coaching_session` | entity | soft | medium | Career counselling / coaching session with a coach (staff or partner), optionally sponsored by an organisation (college, employer). |
| `course_instructor` | link | hard | low | Link course to its instructors with role and display order. |
| `course_instructor_profile` | entity | soft | low | Instructor / author identity for catalogue courses and articles (external or platform-native); optionally linked to a candidate profile. |
| `course_language_availability` | link | hard | low | Languages a course is available in (audio, subtitles, transcript). |
| `course_lesson` | entity | soft | high | Lesson inside a module: video, reading, quiz, lab or live session. |
| `course_list_price` | config | soft | medium | Effective-dated course price per country, currency and access model. |
| `course_module` | entity | soft | medium | Section/module of a course (LinkedIn Learning chapter, Coursera week). |
| `course_offer` | config | soft | low | Promotional offer or discount rule applied to course prices. |
| `course_prerequisite` | link | hard | low | Course that should be completed before another. |
| `course_review` | entity | soft | medium | Learner rating and review of a course. |
| `course_skill` | link | hard | medium | Skills a course teaches (skill-to-course mapping powering recommendations and gap closure). |
| `course_sync_event` | staging | hard | high | Inbound provider event; idempotent by (integration, external event id) and replayable. |
| `leaderboard_entry` | snapshot | hard | high | Ranked learner row of a leaderboard snapshot. |
| `leaderboard_snapshot` | snapshot | hard | low | Computed leaderboard header for a scope, period and scope key. |
| `learning_activity_day` | agg | hard | high | Daily roll-up per learner (minutes, XP) derived from activity events; basis of streaks and leaderboards. |
| `learning_activity_event` | event | append_only | very_high | Append-only learning activity feeding XP, streaks, goals and leaderboards. |
| `learning_assignment` | entity | soft | medium | Employer-sponsored learning assigned to a candidate/employee (mandatory training, upskilling). |
| `learning_goal` | entity | soft | medium | Learner-defined learning goal with a measurable target. |
| `learning_path` | entity | soft | medium | Curated sequence of courses and assessments toward a role or skill (LinkedIn Learning path, Coursera specialisation). |
| `learning_path_enrolment` | entity | soft | high | A learner enrolled in a learning path. |
| `learning_path_item` | link | hard | medium | Ordered course or assessment step within a learning path. |
| `learning_path_item_progress` | event | hard | high | Progress of an enrolled learner on each path step. |
| `learning_provider_integration` | config | soft | low | Integration boundary to an external course platform: sync mode and cursor. No live calls are made by the database. |
| `learning_recommendation` | event | hard | very_high | Course/path/assessment recommended to a candidate with impression and outcome tracking. |
| `learning_streak` | entity | hard | high | Current practice streak per learner. |
| `lesson_progress` | event | hard | very_high | Per learner per lesson progress (resume position, completion). |
| `mentor_availability` | config | hard | low | Recurring weekly availability windows of a mentor. |
| `mentor_focus` | link | hard | low | Focus areas a mentor supports. |
| `mentor_profile` | entity | soft | low | Mentor listing (a candidate who offers guidance), Naukri/LinkedIn mentorship style. |
| `mentor_skill` | link | hard | low | Skills a mentor can coach. |
| `mentorship_feedback` | entity | soft | medium | Rating and comment after a session (either direction). |
| `mentorship_request` | entity | soft | medium | A mentee asks a mentor for guidance. |
| `mentorship_session` | entity | soft | medium | A scheduled mentoring session. |
| `mock_interview` | entity | soft | high | A mock interview a candidate takes (automated, mentor or peer led). |
| `mock_interview_answer` | event | hard | high | Answer to a question during a mock interview with recording and evaluation references. |
| `practice_question_set` | entity | soft | low | Curated interview or practice question set (by role, skill, difficulty). |
| `practice_question_set_item` | link | hard | medium | Ordered questions in a practice set. |
| `proctoring_event` | event | append_only | very_high | Append-only proctoring signal (tab switch, face not detected...) with evidence reference. |
| `proctoring_review` | entity | hard | medium | Human or automated review decision on a flagged sitting. |
| `question_bank` | entity | soft | low | Named, owned pool of questions from which assessment sections draw (platform or employer private). |
| `quiz_question` | entity | soft | high | Question item with calibration statistics (IRT-style difficulty and discrimination); text lives in translations. |
| `quiz_question_option` | entity | hard | high | Answer option (choice, pair or ordering key) of a question. |
| `quiz_question_option_translation` | entity | hard | high | Localised label of an answer option. |
| `quiz_question_skill` | link | hard | high | Skills a question measures (multi-skill items, weights for sub-scores). |
| `quiz_question_translation` | entity | hard | high | Localised stem and explanation of a question (one row per language). |
| `resume_tip` | entity | soft | low | Short, structured resume advice (do/don't) by section and role; localised text in resume_tip_translation. |
| `resume_tip_translation` | entity | soft | low | Localised tip text with before/after example. |
| `skill_badge` | config | soft | low | Badge definition awarded for passing a skill assessment at a tier (Skill IQ / LinkedIn skill badge style). |
| `skill_gap_item` | entity | hard | high | Per-skill line of a gap report. |
| `skill_gap_report` | snapshot | soft | high | Generated analysis of the gap between a candidate profile and a target role or job requirement. |
| `article_kind` | lookup | hard | low | Editorial type of a career resource. |
| `article_status` | lookup | hard | low | Editorial lifecycle of an article. |
| `assessment_session_status` | lookup | hard | low | Lifecycle of a timed assessment sitting. |
| `badge_tier` | lookup | hard | low | Tier of a skill badge. |
| `certificate_revocation_reason` | lookup | hard | low | Why a certificate was revoked. |
| `certificate_status` | lookup | hard | low | State of an issued certificate. |
| `coaching_session_type` | lookup | hard | low | Type of career coaching session. |
| `content_review_status` | lookup | hard | low | Moderation state of user-generated learning content (reviews). |
| `course_access_model` | lookup | hard | low | How a learner gets access to a course or path. |
| `course_instructor_role` | lookup | hard | low | Role of an instructor on a course. |
| `course_offer_kind` | lookup | hard | low | Type of promotional offer on course prices. |
| `credential_share_target` | lookup | hard | low | Destination a credential is shared to. |
| `evaluator_kind` | lookup | hard | low | Who produced a mock-interview evaluation. |
| `gap_priority` | lookup | hard | low | Priority of closing a skill gap. |
| `gap_report_status` | lookup | hard | low | State of a skill-gap report. |
| `leaderboard_period` | lookup | hard | low | Time window of a leaderboard. |
| `leaderboard_scope` | lookup | hard | low | Population a leaderboard ranks. |
| `learning_activity_kind` | lookup | hard | low | Kind of learning activity that counts toward streaks and XP. |
| `learning_assignment_status` | lookup | hard | low | State of an employer-assigned learning item. |
| `learning_content_type` | lookup | hard | low | Kind of learning content a lesson delivers. |
| `learning_goal_metric` | lookup | hard | low | Metric a learning goal tracks. |
| `learning_goal_status` | lookup | hard | low | State of a learning goal. |
| `learning_progress_status` | lookup | hard | low | Progress state of a lesson, path item or path enrolment. |
| `learning_session_status` | lookup | hard | low | State of a scheduled mentoring or coaching session. |
| `learning_target_type` | lookup | hard | low | What a learning assignment points to. |
| `mentor_status` | lookup | hard | low | Lifecycle of a mentor application. |
| `mentorship_focus_area` | lookup | hard | low | Topics a mentor can help with. |
| `mentorship_request_status` | lookup | hard | low | State of a mentorship request. |
| `mock_interview_status` | lookup | hard | low | State of a mock interview. |
| `proctoring_event_type` | lookup | hard | low | Signals recorded during a proctored sitting. |
| `proctoring_mode` | lookup | hard | low | Level of proctoring applied to a sitting. |
| `proctoring_review_outcome` | lookup | hard | low | Human decision after reviewing proctoring evidence. |
| `proctoring_severity` | lookup | hard | low | Severity of a proctoring signal. |
| `provider_sync_event_type` | lookup | hard | low | Type of provider event. |
| `provider_sync_mode` | lookup | hard | low | How a provider integration exchanges data. |
| `provider_sync_status` | lookup | hard | low | Processing state of an inbound provider sync event. |
| `question_difficulty` | lookup | hard | low | Calibrated difficulty bands for questions. |
| `question_status` | lookup | hard | low | Lifecycle of a question in the bank. |
| `quiz_question_format` | lookup | hard | low | Answer format of a question-bank question. |
| `recommendation_outcome` | lookup | hard | low | What happened to a learning recommendation. |
| `resume_tip_section` | lookup | hard | low | Resume section a tip applies to. |
| `session_mode` | lookup | hard | low | Delivery mode of a session. |
| `translation_origin` | lookup | hard | low | How a translated text was produced. |

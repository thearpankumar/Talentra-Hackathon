# ER diagram: Profile

Module key `profile`, owner Team, 90 tables (38 lookups). Generated from `05_data_dictionary.xlsx` (do not edit by hand). Columns shown: primary key, foreign keys, unique/natural keys and a few business columns; see the dictionary for all columns. Edges to lookup (enumeration) tables are listed under *Lookup references* rather than drawn.

## Design notes

<!-- profile.yaml -->
## Scope and entity list (B4/B5)
Profile module = 52 entity tables + 40 own lookups (92 total). Groups: (1) identity/contact: candidate_profile, candidate_contact_phone, candidate_contact_email, candidate_address,
candidate_social_link, candidate_identity_verification, candidate_diversity_declaration; (2) core sections: candidate_education, candidate_experience, candidate_skill,
candidate_skill_endorsement, candidate_language, candidate_project(+_skill); (3) LinkedIn/Naukri depth: certification, award, publication, patent, volunteer_experience,
test_score, recommendation, follow, profile_section; (4) preferences: candidate_job_preference + five candidate_preferred_* bridges, candidate_work_authorisation,
candidate_open_to_work_signal; (5) documents: candidate_media_file, resume, resume_template, resume_parse_result, profile_import; (6) scoring: profile_completion_rule,
candidate_profile_completion_snapshot, assessment, assessment_attempt, candidate_self_assessment, course_provider, course, course_enrolment; (7) history/engagement:
candidate_profile_view, candidate_activity_log, candidate_shortlist_folder/_item/_history; (8) growth/compliance: referral, referral_reward, candidate_erasure_request.
Out of scope on purpose: candidate job search/application history (jobs_applications), notification/privacy/alert preferences (notifications_community), download
history (subscriptions_payments.profile_download; views reference wallet_ledger_entry), blocking lists (administration), gig worker profile (gig_marketplace).

## Key decisions
* **1:1 profile.** candidate_profile.app_user_id has a non-partial UNIQUE constraint (a user has exactly one profile ever; an erased tombstone keeps the slot).
* **Projects: one table, nullable owner pair.** candidate_project has candidate_profile_id and gig_worker_profile_id with CHECK exactly-one, plus optional gig_assignment_id
  (UNIQUE, requires a gig owner) so completed gigs become verified projects. Justification: identical attributes (role, duration, outcome, skills), one display section and
  one search path; two tables would duplicate DDL and the project-skill bridge. Both FKs are real (no polymorphic type/id pair), partial indexes serve each owner side.
* **Salary disclosure (D-09).** Amounts are NULL-able; salary_disclosure lookup (disclosed/undisclosed/not_applicable) says why. Recruiter-facing view masks amounts unless
  disclosed. Zero is never used as 'missing'. Notice period 0 is a genuine value.
* **Contact data.** candidate_profile holds the primary phone/email mirror (E.164 CHECK + country; lowercase CHECK) for fast search cards; candidate_contact_phone/_email hold
  the multi-value set with partial UNIQUE one-primary. Mirror is written in the same txn (BR-PRF-04).
* **National id.** Never stored. candidate_identity_verification keeps vendor_token, keyed HMAC blind index (dedupe: partial UNIQUE where is_active_binding), outcome, vendor,
  idempotency key. Visa/permit numbers likewise token only.
* **Special-category data** (disability/veteran) isolated in candidate_diversity_declaration, linked to a user_consent row, application-level AES-GCM for free text.
* **Resumes.** resume = version chain (root_resume_id/parent_resume_id/version_no, linear: parent UNIQUE), one default per profile (partial UNIQUE), file metadata separated in
  candidate_media_file (object key, SHA-256, size, MIME, virus-scan status; downloads allowed only when scan_status = clean). body_text is the only unbounded STRING
  (Rule 3 allows resume bodies) and is Personal, encrypted at rest. resume_parse_result stores parser output JSONB + parser name/version; raw JSON is cleared after 90 days.
  No JSONB inverted index: heavy resume search is delegated to the search service fed by changefeed (D-16); inverted index on parsed_json was rejected for write amplification.
* **Views/activity/shortlist history** are append_only, INSERT/SELECT grants only, hash-sharded time indexes (bucket 16-32), and **row-level TTL** (24/24/36 months). CockroachDB
  docs (verified): TTL = ttl_expire_after / ttl_expiration_expression, deletes run as normal DELETEs so inbound FKs would block them; hence TTL is used only on leaf tables with no
  inbound FKs (view, activity log, completion snapshot, shortlist history); TTL on many-index tables adds write cost (view table has 5 indexes: accepted, noted). Hash-sharded
  indexes (CREATE INDEX ... USING HASH WITH (bucket_count=n)) are used for monotonically increasing columns; the docs warn that range scans over them fan out across buckets, so
  per-candidate/per-tenant reads use composite indexes led by the entity id instead. The credit deduction is the FK wallet_ledger_entry_id (UNIQUE: one ledger entry pays one view).
* **Shortlist tenancy (D-11).** candidate_shortlist_item(organisation_id, folder) uses a composite FK to candidate_shortlist_folder(organisation_id, id) so a folder can never be
  from another tenant; shortlist_history is the append-only trail and is written in the same txn as every item change.
* **Retention & erasure (Rule 4).** Every table states its rule. Erasure = anonymise-in-place: candidate_profile and soft entities have PII columns overwritten/NULLed and
  anonymised_at set; hard multi-value tables (contact, address, links, preferences, languages) are deleted; append-only history keeps rows referencing the tombstone with IPs NULLed.
  Inactivity: profiles with last_active_at older than 36 months get a 30-day warning notification then an inactivity_purge candidate_erasure_request. candidate_erasure_request is
  the evidence log (7 years, no content).
* **Legacy sync (boundary).** candidate_profile.legacy_candidate_ref (partial UNIQUE) + profile_import(payload_sha256 UNIQUE per profile+source) give idempotency; field_mapping_version
  names the per-field direction matrix (identity/contact: source->platform on first import then platform wins; skills/experience: newest_wins; verification flags: platform wins);
  sync_conflict_policy lookup holds the rule per run. Audit = the import row + candidate_activity_log.
* **Lookups from master data** assumed to be lookup-kind tables: gender, proficiency_level, salary_period, verification_status, visibility_level, address_type, document_type,
  employment_type, work_type. Assumed ordinary tables: country, state_region, city, language, currency, skill, qualification, industry, job_role.
* **Names > 63 characters** are avoided so the schema is portable to PostgreSQL tooling.
* **Extension points:** employee-referral rewards for employers; skill-graph embeddings (vector) intentionally omitted; candidate-visible 'who viewed' paywall is a plan entitlement.

## Competitor coverage observations (B12/B13)
* **LinkedIn** (help-centre export + Profile Edit API fields: certifications, honors, patents, publications, volunteering, section ordering fields, endorsements with accepted/
  rejected/pending status, recommendations given/received, following): all modelled (candidate_certification, _award, _patent, _publication, _volunteer_experience,
  candidate_profile_section ordering, candidate_skill_endorsement with endorsement_status, candidate_recommendation, candidate_follow, open-to-work signal, top-skill pinning,
  profile photo and cover media). Exceeds LinkedIn: assessment verification link on skills, disclosure-aware salary, work-authorisation per country, erasure evidence.
  Falls short (deliberately): groups, newsletters, creator mode, activity feed posts (community module).
* **Naukri**: resume headline, profile summary, key skills with experience, IT skills, employment (notice period, current CTC, expected CTC), education, projects, online profile
  links, work samples, desired job type/shift/location, differently-abled flag, profile completeness meter, resume upload with parsed fields, profile views by recruiters,
  'recruiter actions'. Matches all (resume_headline, years_of_experience, notice_period_days, salary pairs, candidate_job_preference, candidate_diversity_declaration, snapshot,
  candidate_profile_view). Exceeds with version chain, scan status and parse provenance.
* **Monster**: multiple resumes (up to 5) with visibility per resume, work-authorisation, relocation, security clearance, resume builder templates, career-level, saved jobs.
  Matches (resume.is_visible_to_recruiters, work_authorisation, relocation flags, resume_template). Not modelled: security clearance (add as certification type), saved jobs (jobs module).
* **Indeed**: resume privacy (public/private), 'ready to start' availability, pay expectations, work authorisation. Matches via visibility_level, availability_status.

## Diagram

```mermaid
erDiagram
  assessment {
    UUID id PK
    STRING_60 code
    STRING_200 title
    UUID assessment_type_id FK
    UUID skill_id FK
    UUID course_provider_id FK
    UUID language_id FK
    DECIMAL_8_2 max_score
  }
  assessment_attempt {
    UUID id PK
    UUID candidate_profile_id FK
    UUID assessment_id FK
    UUID attempt_status_id FK
    INT2 attempt_no
    TIMESTAMPTZ started_at
    TIMESTAMPTZ submitted_at
    UUID awarded_proficiency_level_id FK
  }
  candidate_activity_log {
    UUID id PK
    UUID candidate_profile_id FK
    UUID activity_type_id FK
    TIMESTAMPTZ occurred_at
    UUID actor_app_user_id FK
    UUID resume_id FK
    UUID device_id FK
    UUID identity_session_id FK
    STRING_64 subject_table_code
    UUID subject_row_ref
  }
  candidate_address {
    UUID id PK
    UUID candidate_profile_id FK
    UUID address_type_id FK
    STRING_200 line1
    STRING_200 line2
    STRING_120 locality
    UUID city_id FK
    UUID state_region_id FK
    UUID country_id FK
  }
  candidate_award {
    UUID id PK
    UUID candidate_profile_id FK
    STRING_200 title
    STRING_200 issuer_name
    DATE award_date
  }
  candidate_certification {
    UUID id PK
    UUID candidate_profile_id FK
    STRING_200 name
    STRING_200 issuing_organisation_name
    UUID issuing_organisation_id FK
    STRING_120 credential_ref
    UUID verification_status_id FK
    UUID candidate_media_file_id FK
  }
  candidate_contact_email {
    UUID id PK
    UUID candidate_profile_id FK
    STRING_254 email UK
    UUID contact_purpose_id FK
    BOOL is_primary
    TIMESTAMPTZ verified_at
    BOOL is_marketing_allowed
  }
  candidate_contact_phone {
    UUID id PK
    UUID candidate_profile_id FK
    STRING_16 phone UK
    UUID phone_country_id FK
    UUID phone_type_id FK
    UUID contact_purpose_id FK
    BOOL is_primary
    TIMESTAMPTZ verified_at
    BOOL is_sms_allowed
  }
  candidate_diversity_declaration {
    UUID id PK
    UUID candidate_profile_id FK
    UUID user_consent_id FK
    BOOL has_disability
    BYTES disability_description_encrypted
    BOOL is_veteran
  }
  candidate_education {
    UUID id PK
    UUID candidate_profile_id FK
    UUID qualification_id FK
    STRING_150 degree_name
    STRING_200 institution_name
    STRING_150 field_of_study
    UUID study_mode_id FK
    UUID country_id FK
    UUID city_id FK
    UUID verification_status_id FK
  }
  candidate_erasure_request {
    UUID id PK
    UUID candidate_profile_id FK
    UUID erasure_trigger_id FK
    UUID erasure_request_status_id FK
    TIMESTAMPTZ requested_at
    TIMESTAMPTZ identity_confirmed_at
    DATE scheduled_date
  }
  candidate_experience {
    UUID id PK
    UUID candidate_profile_id FK
    STRING_150 job_title
    STRING_200 employer_name
    UUID employer_organisation_id FK
    UUID job_role_id FK
    UUID industry_id FK
    UUID employment_type_id FK
    UUID work_type_id FK
    UUID country_id FK
    UUID city_id FK
    DATE start_date
    UUID verification_status_id FK
  }
  candidate_follow {
    UUID id PK
    UUID candidate_profile_id FK
    UUID followed_organisation_id FK
    UUID followed_app_user_id FK
    BOOL is_notify_enabled
  }
  candidate_identity_verification {
    UUID id PK
    UUID candidate_profile_id FK
    UUID document_type_id FK
    UUID country_id FK
    UUID identity_vendor_id FK
    STRING_128 vendor_token
    BYTES id_number_hmac
    UUID verification_status_id FK
    DECIMAL_5_2 name_match_score
    STRING_100 request_idempotency_key UK
  }
  candidate_job_preference {
    UUID id PK
    UUID candidate_profile_id FK
    BOOL is_willing_to_relocate
    BOOL is_willing_to_travel
    UUID work_shift_id FK
    INT2 max_hours_per_week
  }
  candidate_language {
    UUID id PK
    UUID candidate_profile_id FK
    UUID language_id FK
    UUID proficiency_level_id FK
    BOOL can_speak
    BOOL can_read
    BOOL can_write
  }
  candidate_media_file {
    UUID id PK
    UUID candidate_profile_id FK
    UUID media_kind_id FK
    STRING_512 storage_object_key
    STRING_255 original_file_name
    STRING_100 mime_type
    UUID scan_status_id FK
    UUID visibility_level_id FK
  }
  candidate_open_to_work_signal {
    UUID id PK
    UUID candidate_profile_id FK
    TIMESTAMPTZ valid_from
    TIMESTAMPTZ valid_to
    UUID visibility_level_id FK
    UUID availability_status_id FK
    STRING_300 note
  }
  candidate_patent {
    UUID id PK
    UUID candidate_profile_id FK
    STRING_300 title
    STRING_64 patent_number
    UUID patent_status_id FK
    UUID issuing_country_id FK
    DATE filing_date
  }
  candidate_preferred_employment_type {
    UUID id PK
    UUID candidate_profile_id FK
    UUID employment_type_id FK
    INT2 rank_no
  }
  candidate_preferred_industry {
    UUID id PK
    UUID candidate_profile_id FK
    UUID industry_id FK
    INT2 rank_no
  }
  candidate_preferred_location {
    UUID id PK
    UUID candidate_profile_id FK
    UUID city_id FK
    INT2 rank_no
  }
  candidate_preferred_role {
    UUID id PK
    UUID candidate_profile_id FK
    UUID job_role_id FK
    INT2 rank_no
  }
  candidate_preferred_work_type {
    UUID id PK
    UUID candidate_profile_id FK
    UUID work_type_id FK
    INT2 rank_no
  }
  candidate_profile {
    UUID id PK
    UUID app_user_id FK
    STRING_150 given_name
    STRING_150 family_name
    STRING_150 display_name
    UUID gender_id FK
    UUID nationality_country_id FK
    UUID profile_language_id FK
    UUID current_country_id FK
    UUID current_state_region_id FK
    UUID current_city_id FK
    UUID availability_status_id FK
    UUID salary_currency_id FK
    UUID salary_period_id FK
    UUID current_salary_disclosure_id FK
    UUID expected_salary_disclosure_id FK
    UUID visibility_level_id FK
    UUID profile_strength_id FK
    UUID contact_phone_country_id FK
    UUID import_source_id FK
  }
  candidate_profile_completion_snapshot {
    UUID id PK
    UUID candidate_profile_id FK
    INT4 rule_set_version
    DECIMAL_5_2 score
    UUID profile_strength_id FK
    JSONB breakdown
  }
  candidate_profile_section {
    UUID id PK
    UUID candidate_profile_id FK
    UUID profile_section_type_id FK
    INT2 sort_order
    BOOL is_visible
    UUID visibility_level_id FK
  }
  candidate_profile_view {
    UUID id PK
    UUID candidate_profile_id FK
    UUID viewer_app_user_id FK
    UUID viewer_organisation_id FK
    UUID view_source_id FK
    UUID job_requirement_id FK
    UUID wallet_ledger_entry_id FK
    TIMESTAMPTZ viewed_at
    BOOL is_contact_revealed
    BOOL is_viewer_anonymous
  }
  candidate_project {
    UUID id PK
    UUID candidate_profile_id FK
    UUID gig_worker_profile_id FK
    UUID gig_assignment_id FK
    UUID candidate_experience_id FK
    UUID project_origin_id FK
    STRING_200 title
    STRING_150 role_title
    STRING_200 client_name
    UUID verification_status_id FK
  }
  candidate_project_skill {
    UUID id PK
    UUID candidate_project_id FK
    UUID skill_id FK
  }
  candidate_publication {
    UUID id PK
    UUID candidate_profile_id FK
    UUID publication_type_id FK
    STRING_300 title
    STRING_200 publisher_name
    DATE publication_date
  }
  candidate_recommendation {
    UUID id PK
    UUID candidate_profile_id FK
    UUID author_app_user_id FK
    UUID relationship_type_id FK
    UUID recommendation_status_id FK
    UUID candidate_experience_id FK
    UUID language_id FK
    STRING_3000 body
    TIMESTAMPTZ requested_at
    TIMESTAMPTZ written_at
  }
  candidate_self_assessment {
    UUID id PK
    UUID candidate_profile_id FK
    UUID skill_id FK
    UUID assessment_id FK
    INT2 self_rating
    UUID proficiency_level_id FK
    JSONB responses
    TIMESTAMPTZ assessed_at UK
  }
  candidate_shortlist_folder {
    UUID id PK
    UUID organisation_id FK
    UUID owner_app_user_id FK
    UUID job_requirement_id FK
    STRING_150 name
    STRING_500 description
    BOOL is_shared
  }
  candidate_shortlist_history {
    UUID id PK
    UUID candidate_shortlist_item_id FK
    UUID organisation_id FK
    UUID candidate_profile_id FK
    UUID actor_app_user_id FK
    UUID shortlist_action_id FK
    UUID from_shortlist_stage_id FK
    UUID to_shortlist_stage_id FK
    UUID from_candidate_shortlist_folder_id FK
    UUID to_candidate_shortlist_folder_id FK
    TIMESTAMPTZ occurred_at
  }
  candidate_shortlist_item {
    UUID id PK
    UUID organisation_id FK
    UUID candidate_profile_id FK
    UUID added_by_app_user_id FK
    UUID candidate_shortlist_folder_id FK
    UUID job_requirement_id FK
    UUID shortlist_kind_id FK
    UUID shortlist_stage_id FK
    INT2 rating
    STRING_1000 note
  }
  candidate_skill {
    UUID id PK
    UUID candidate_profile_id FK
    UUID skill_id FK
    UUID proficiency_level_id FK
    DECIMAL_4_1 years_of_experience
    DATE last_used_date
    UUID skill_source_id FK
    BOOL is_top_skill
    UUID assessment_attempt_id FK
  }
  candidate_skill_endorsement {
    UUID id PK
    UUID candidate_skill_id FK
    UUID endorser_app_user_id FK
    UUID endorsement_status_id FK
    UUID relationship_type_id FK
    TIMESTAMPTZ endorsed_at
  }
  candidate_social_link {
    UUID id PK
    UUID candidate_profile_id FK
    UUID link_type_id FK
    STRING_500 url UK
    STRING_80 label
    BOOL is_public
    INT2 sort_order
  }
  candidate_test_score {
    UUID id PK
    UUID candidate_profile_id FK
    STRING_150 test_name
    DECIMAL_10_2 score_value
    DECIMAL_10_2 max_score
  }
  candidate_volunteer_experience {
    UUID id PK
    UUID candidate_profile_id FK
    STRING_200 organisation_name
    STRING_150 role_title
    STRING_120 cause
  }
  candidate_work_authorisation {
    UUID id PK
    UUID candidate_profile_id FK
    UUID country_id FK
    UUID work_authorisation_type_id FK
    BOOL requires_sponsorship
    DATE valid_from_date
    DATE valid_to_date
    UUID verification_status_id FK
  }
  course {
    UUID id PK
    UUID course_provider_id FK
    STRING_100 external_course_ref
    STRING_250 title
    UUID primary_skill_id FK
    UUID proficiency_level_id FK
    UUID language_id FK
    INT4 duration_minutes
    UUID price_currency_id FK
  }
  course_enrolment {
    UUID id PK
    UUID candidate_profile_id FK
    UUID course_id FK
    UUID enrolment_status_id FK
    TIMESTAMPTZ enrolled_at
    DECIMAL_5_2 progress_percent
    TIMESTAMPTZ completed_at
    UUID candidate_certification_id FK
    UUID wallet_ledger_entry_id FK
  }
  course_provider {
    UUID id PK
    STRING_40 code
    STRING_150 name
    STRING_300 website_url
    UUID country_id FK
  }
  profile_completion_rule {
    UUID id PK
    INT4 rule_set_version
    STRING_60 rule_code
    UUID profile_section_type_id FK
    STRING_60 profile_field_code
  }
  profile_import {
    UUID id PK
    UUID candidate_profile_id FK
    UUID import_source_id FK
    UUID import_status_id FK
    UUID sync_conflict_policy_id FK
    STRING_200 source_account_ref
    STRING_100 legacy_record_ref
    BYTES payload_sha256 UK
    JSONB raw_payload
  }
  referral {
    UUID id PK
    UUID referrer_app_user_id FK
    UUID referee_app_user_id FK
    UUID job_requirement_id FK
    UUID referral_status_id FK
    UUID referral_channel_id FK
    STRING_24 referral_code
    STRING_40 campaign_code
    STRING_254 referee_email
  }
  referral_reward {
    UUID id PK
    UUID referral_id FK
    UUID beneficiary_app_user_id FK
    UUID reward_type_id FK
    UUID referral_reward_status_id FK
    INT4 credit_quantity
    INT2 reward_days
    UUID wallet_ledger_entry_id FK
    STRING_100 idempotency_key UK
    TIMESTAMPTZ granted_at
  }
  resume {
    UUID id PK
    UUID candidate_profile_id FK
    UUID resume_template_id FK
    UUID resume_format_id FK
    UUID resume_origin_id FK
    UUID candidate_media_file_id FK
    UUID root_resume_id FK
    UUID parent_resume_id FK
    INT4 version_no
    STRING_150 title
    UUID language_id FK
    STRING body_text
  }
  resume_parse_result {
    UUID id PK
    UUID resume_id FK
    STRING_60 parser_name UK
    STRING_40 parser_version UK
    UUID parse_status_id FK
    JSONB parsed_json
    DECIMAL_5_2 confidence
    UUID detected_language_id FK
    INT4 duration_ms
  }
  resume_template {
    UUID id PK
    STRING_40 code
    STRING_120 name
    UUID resume_format_id FK
    UUID language_id FK
    JSONB layout_config
  }
  app_user {
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
  device {
    UUID id PK
  }
  gig_assignment {
    UUID id PK
  }
  gig_worker_profile {
    UUID id PK
  }
  identity_session {
    UUID id PK
  }
  industry {
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
  qualification {
    UUID id PK
  }
  skill {
    UUID id PK
  }
  state_region {
    UUID id PK
  }
  user_consent {
    UUID id PK
  }
  wallet_ledger_entry {
    UUID id PK
  }
  app_user |o--o{ candidate_activity_log : "actor_app_user_id"
  app_user |o--o{ candidate_follow : "followed_app_user_id"
  app_user |o--o{ candidate_profile_view : "viewer_app_user_id"
  app_user |o--o{ referral : "referee_app_user_id"
  app_user ||--o{ candidate_recommendation : "author_app_user_id"
  app_user ||--o{ candidate_shortlist_folder : "owner_app_user_id"
  app_user ||--o{ candidate_shortlist_history : "actor_app_user_id"
  app_user ||--o{ candidate_shortlist_item : "added_by_app_user_id"
  app_user ||--o{ candidate_skill_endorsement : "endorser_app_user_id"
  app_user ||--o{ referral : "referrer_app_user_id"
  app_user ||--o{ referral_reward : "beneficiary_app_user_id"
  app_user ||--o| candidate_profile : "app_user_id"
  assessment |o--o{ candidate_self_assessment : "assessment_id"
  assessment ||--o{ assessment_attempt : "assessment_id"
  assessment_attempt |o--o{ candidate_skill : "assessment_attempt_id"
  candidate_certification |o--o{ course_enrolment : "candidate_certification_id"
  candidate_experience |o--o{ candidate_project : "candidate_experience_id"
  candidate_experience |o--o{ candidate_recommendation : "candidate_experience_id"
  candidate_media_file |o--o{ candidate_certification : "candidate_media_file_id"
  candidate_media_file |o--o{ resume : "candidate_media_file_id"
  candidate_profile |o--o{ candidate_project : "candidate_profile_id"
  candidate_profile ||--o{ assessment_attempt : "candidate_profile_id"
  candidate_profile ||--o{ candidate_activity_log : "candidate_profile_id"
  candidate_profile ||--o{ candidate_address : "candidate_profile_id"
  candidate_profile ||--o{ candidate_award : "candidate_profile_id"
  candidate_profile ||--o{ candidate_certification : "candidate_profile_id"
  candidate_profile ||--o{ candidate_contact_email : "candidate_profile_id"
  candidate_profile ||--o{ candidate_contact_phone : "candidate_profile_id"
  candidate_profile ||--o{ candidate_education : "candidate_profile_id"
  candidate_profile ||--o{ candidate_erasure_request : "candidate_profile_id"
  candidate_profile ||--o{ candidate_experience : "candidate_profile_id"
  candidate_profile ||--o{ candidate_follow : "candidate_profile_id"
  candidate_profile ||--o{ candidate_identity_verification : "candidate_profile_id"
  candidate_profile ||--o{ candidate_language : "candidate_profile_id"
  candidate_profile ||--o{ candidate_media_file : "candidate_profile_id"
  candidate_profile ||--o{ candidate_open_to_work_signal : "candidate_profile_id"
  candidate_profile ||--o{ candidate_patent : "candidate_profile_id"
  candidate_profile ||--o{ candidate_preferred_employment_type : "candidate_profile_id"
  candidate_profile ||--o{ candidate_preferred_industry : "candidate_profile_id"
  candidate_profile ||--o{ candidate_preferred_location : "candidate_profile_id"
  candidate_profile ||--o{ candidate_preferred_role : "candidate_profile_id"
  candidate_profile ||--o{ candidate_preferred_work_type : "candidate_profile_id"
  candidate_profile ||--o{ candidate_profile_completion_snapshot : "candidate_profile_id"
  candidate_profile ||--o{ candidate_profile_section : "candidate_profile_id"
  candidate_profile ||--o{ candidate_profile_view : "candidate_profile_id"
  candidate_profile ||--o{ candidate_publication : "candidate_profile_id"
  candidate_profile ||--o{ candidate_recommendation : "candidate_profile_id"
  candidate_profile ||--o{ candidate_self_assessment : "candidate_profile_id"
  candidate_profile ||--o{ candidate_shortlist_history : "candidate_profile_id"
  candidate_profile ||--o{ candidate_shortlist_item : "candidate_profile_id"
  candidate_profile ||--o{ candidate_skill : "candidate_profile_id"
  candidate_profile ||--o{ candidate_social_link : "candidate_profile_id"
  candidate_profile ||--o{ candidate_test_score : "candidate_profile_id"
  candidate_profile ||--o{ candidate_volunteer_experience : "candidate_profile_id"
  candidate_profile ||--o{ candidate_work_authorisation : "candidate_profile_id"
  candidate_profile ||--o{ course_enrolment : "candidate_profile_id"
  candidate_profile ||--o{ profile_import : "candidate_profile_id"
  candidate_profile ||--o{ resume : "candidate_profile_id"
  candidate_profile ||--o| candidate_diversity_declaration : "candidate_profile_id"
  candidate_profile ||--o| candidate_job_preference : "candidate_profile_id"
  candidate_project ||--o{ candidate_project_skill : "candidate_project_id"
  candidate_shortlist_folder |o--o{ candidate_shortlist_history : "from_candidate_shortlist_folder_id"
  candidate_shortlist_folder |o--o{ candidate_shortlist_history : "to_candidate_shortlist_folder_id"
  candidate_shortlist_folder |o--o{ candidate_shortlist_item : "candidate_shortlist_folder_id"
  candidate_shortlist_folder ||--o{ candidate_shortlist_item : "organisation_id"
  candidate_shortlist_item ||--o{ candidate_shortlist_history : "candidate_shortlist_item_id"
  candidate_skill ||--o{ candidate_skill_endorsement : "candidate_skill_id"
  city |o--o{ candidate_education : "city_id"
  city |o--o{ candidate_experience : "city_id"
  city |o--o{ candidate_profile : "current_city_id"
  city ||--o{ candidate_address : "city_id"
  city ||--o{ candidate_preferred_location : "city_id"
  country |o--o{ candidate_education : "country_id"
  country |o--o{ candidate_experience : "country_id"
  country |o--o{ candidate_patent : "issuing_country_id"
  country |o--o{ candidate_profile : "contact_phone_country_id"
  country |o--o{ candidate_profile : "current_country_id"
  country |o--o{ candidate_profile : "nationality_country_id"
  country |o--o{ course_provider : "country_id"
  country ||--o{ candidate_address : "country_id"
  country ||--o{ candidate_contact_phone : "phone_country_id"
  country ||--o{ candidate_identity_verification : "country_id"
  country ||--o{ candidate_work_authorisation : "country_id"
  course ||--o{ course_enrolment : "course_id"
  course_provider |o--o{ assessment : "course_provider_id"
  course_provider ||--o{ course : "course_provider_id"
  currency |o--o{ candidate_profile : "salary_currency_id"
  currency |o--o{ course : "price_currency_id"
  device |o--o{ candidate_activity_log : "device_id"
  gig_assignment |o--o{ candidate_project : "gig_assignment_id"
  gig_worker_profile |o--o{ candidate_project : "gig_worker_profile_id"
  identity_session |o--o{ candidate_activity_log : "identity_session_id"
  industry |o--o{ candidate_experience : "industry_id"
  industry ||--o{ candidate_preferred_industry : "industry_id"
  job_requirement |o--o{ candidate_profile_view : "job_requirement_id"
  job_requirement |o--o{ candidate_shortlist_folder : "job_requirement_id"
  job_requirement |o--o{ candidate_shortlist_item : "job_requirement_id"
  job_requirement |o--o{ referral : "job_requirement_id"
  job_role |o--o{ candidate_experience : "job_role_id"
  job_role ||--o{ candidate_preferred_role : "job_role_id"
  language |o--o{ assessment : "language_id"
  language |o--o{ candidate_profile : "profile_language_id"
  language |o--o{ candidate_recommendation : "language_id"
  language |o--o{ course : "language_id"
  language |o--o{ resume : "language_id"
  language |o--o{ resume_parse_result : "detected_language_id"
  language |o--o{ resume_template : "language_id"
  language ||--o{ candidate_language : "language_id"
  organisation |o--o{ candidate_certification : "issuing_organisation_id"
  organisation |o--o{ candidate_experience : "employer_organisation_id"
  organisation |o--o{ candidate_follow : "followed_organisation_id"
  organisation |o--o{ candidate_profile_view : "viewer_organisation_id"
  organisation ||--o{ candidate_shortlist_folder : "organisation_id"
  organisation ||--o{ candidate_shortlist_history : "organisation_id"
  organisation ||--o{ candidate_shortlist_item : "organisation_id"
  qualification |o--o{ candidate_education : "qualification_id"
  referral ||--o{ referral_reward : "referral_id"
  resume |o--o{ candidate_activity_log : "resume_id"
  resume |o--o{ resume : "parent_resume_id"
  resume |o--o{ resume : "root_resume_id"
  resume ||--o{ resume_parse_result : "resume_id"
  resume_template |o--o{ resume : "resume_template_id"
  skill |o--o{ assessment : "skill_id"
  skill |o--o{ course : "primary_skill_id"
  skill ||--o{ candidate_project_skill : "skill_id"
  skill ||--o{ candidate_self_assessment : "skill_id"
  skill ||--o{ candidate_skill : "skill_id"
  state_region |o--o{ candidate_address : "state_region_id"
  state_region |o--o{ candidate_profile : "current_state_region_id"
  user_consent ||--o| candidate_diversity_declaration : "user_consent_id"
  wallet_ledger_entry |o--o{ candidate_profile_view : "wallet_ledger_entry_id"
  wallet_ledger_entry |o--o{ course_enrolment : "wallet_ledger_entry_id"
  wallet_ledger_entry |o--o{ referral_reward : "wallet_ledger_entry_id"
```

## Lookup tables in this module

| Lookup | Values | Description |
|---|---|---|
| `activity_type` | 15 | Candidate activity events recorded in candidate_activity_log. |
| `assessment_type` | 7 | Kind of assessment instrument. |
| `attempt_status` | 6 | Lifecycle of an assessment attempt. |
| `availability_status` | 6 | Candidate availability to join. |
| `contact_purpose` | 4 | Purpose label of a phone number or email. |
| `endorsement_status` | 5 | Moderation state of a skill endorsement (LinkedIn export: accepted/rejected/pending). |
| `enrolment_status` | 5 | State of a course enrolment. |
| `erasure_request_status` | 7 | State of an erasure request. |
| `erasure_trigger` | 4 | What started an erasure/anonymisation run. |
| `identity_vendor` | 4 | External identity-verification providers (boundary: token + outcome only). |
| `import_source` | 7 | Origin of profile data (native sign-up or an imported/synced source). |
| `import_status` | 6 | State of a profile import/sync run. |
| `link_type` | 8 | Kind of external profile/portfolio link. |
| `media_kind` | 7 | Kind of uploaded profile media. |
| `parse_status` | 5 | Outcome of resume parsing. |
| `patent_status` | 4 | Patent lifecycle status. |
| `profile_section_type` | 18 | Profile sections a candidate can show, reorder or hide (LinkedIn-style section list). |
| `profile_strength` | 4 | Profile strength band derived from the completeness score. |
| `project_origin` | 4 | How a profile project came to exist. |
| `publication_type` | 6 | Kind of publication. |
| `recommendation_status` | 6 | State of a given/received recommendation. |
| `referral_channel` | 6 | Channel through which a referral invite was shared. |
| `referral_reward_status` | 5 | State of a referral reward. |
| `referral_status` | 7 | Lifecycle of a referral. |
| `relationship_type` | 8 | Working relationship between recommender/endorser and candidate. |
| `resume_format` | 6 | File or render format of a resume. |
| `resume_origin` | 5 | How a resume version came to exist. |
| `reward_type` | 4 | Kind of referral reward. |
| `salary_disclosure` | 5 | Disclosure semantics for salary values (unknown vs not applicable vs withheld; D-09). |
| `scan_status` | 5 | Malware scan state of an uploaded file. |
| `shortlist_action` | 6 | Action recorded in shortlist history. |
| `shortlist_kind` | 3 | Employer-side saved-candidate type. |
| `shortlist_stage` | 6 | Light-weight stage of a saved candidate before a formal application pipeline. |
| `skill_source` | 5 | How a candidate_skill row was added. |
| `study_mode` | 4 | Mode of study for an education entry. |
| `sync_conflict_policy` | 4 | Rule applied when imported and platform values differ. |
| `view_source` | 9 | Where a profile view originated (analytics + credit-policy driver). |
| `work_authorisation_type` | 7 | Right-to-work categories (country given separately). |

## Lookup references (enumeration FKs)

| Table | Lookup tables referenced |
|---|---|
| `assessment` | `assessment_type` |
| `assessment_attempt` | `attempt_status`, `proficiency_level` |
| `candidate_activity_log` | `activity_type` |
| `candidate_address` | `address_type` |
| `candidate_certification` | `verification_status` |
| `candidate_contact_email` | `contact_purpose` |
| `candidate_contact_phone` | `contact_purpose`, `phone_type` |
| `candidate_education` | `study_mode`, `verification_status` |
| `candidate_erasure_request` | `erasure_request_status`, `erasure_trigger` |
| `candidate_experience` | `employment_type`, `verification_status`, `work_type` |
| `candidate_identity_verification` | `document_type`, `identity_vendor`, `verification_status` |
| `candidate_job_preference` | `work_shift` |
| `candidate_language` | `proficiency_level` |
| `candidate_media_file` | `media_kind`, `scan_status`, `visibility_level` |
| `candidate_open_to_work_signal` | `availability_status`, `visibility_level` |
| `candidate_patent` | `patent_status` |
| `candidate_preferred_employment_type` | `employment_type` |
| `candidate_preferred_work_type` | `work_type` |
| `candidate_profile` | `availability_status`, `gender`, `import_source`, `profile_strength`, `salary_disclosure`, `salary_period`, `visibility_level` |
| `candidate_profile_completion_snapshot` | `profile_strength` |
| `candidate_profile_section` | `profile_section_type`, `visibility_level` |
| `candidate_profile_view` | `view_source` |
| `candidate_project` | `project_origin`, `verification_status` |
| `candidate_publication` | `publication_type` |
| `candidate_recommendation` | `recommendation_status`, `relationship_type` |
| `candidate_self_assessment` | `proficiency_level` |
| `candidate_shortlist_history` | `shortlist_action`, `shortlist_stage` |
| `candidate_shortlist_item` | `shortlist_kind`, `shortlist_stage` |
| `candidate_skill` | `proficiency_level`, `skill_source` |
| `candidate_skill_endorsement` | `endorsement_status`, `relationship_type` |
| `candidate_social_link` | `link_type` |
| `candidate_work_authorisation` | `verification_status`, `work_authorisation_type` |
| `course` | `proficiency_level` |
| `course_enrolment` | `enrolment_status` |
| `profile_completion_rule` | `profile_section_type` |
| `profile_import` | `import_source`, `import_status`, `sync_conflict_policy` |
| `referral` | `referral_channel`, `referral_status` |
| `referral_reward` | `referral_reward_status`, `reward_type` |
| `resume` | `resume_format`, `resume_origin` |
| `resume_parse_result` | `parse_status` |
| `resume_template` | `resume_format` |

## Tables

| Table | Kind | Delete | Volume | Description |
|---|---|---|---|---|
| `assessment` | entity | soft | low | Assessment definitions (skill tests, aptitude, language proficiency) with pass mark and validity. |
| `assessment_attempt` | entity | soft | very_high | A candidate's attempt at an assessment with score, percentile, outcome and proctoring flags; verified skills link back here. |
| `candidate_activity_log` | event | append_only | very_high | Candidate activity events: login, profile update, resume download/upload, privacy changes, searches. |
| `candidate_address` | entity | hard | high | Postal addresses of a candidate (current, permanent, ...). |
| `candidate_award` | entity | soft | medium | Honours and awards. |
| `candidate_certification` | entity | soft | high | Licences and certifications (LinkedIn: name, issuer, credential id/url, issue and expiry dates). |
| `candidate_contact_email` | entity | hard | high | Additional email addresses of a candidate (multi-value; primary mirrored to candidate_profile). |
| `candidate_contact_phone` | entity | hard | high | Additional phone numbers of a candidate (multi-value; primary mirrored to candidate_profile). |
| `candidate_diversity_declaration` | entity | hard | medium | Optional self-declaration of disability / veteran status for inclusive hiring (Naukri 'differently abled'). Special-category data. |
| `candidate_education` | entity | soft | high | Education entries (degree, institution, dates, grade) of a candidate. |
| `candidate_erasure_request` | entity | soft | medium | Deletion/anonymisation requests and purge runs: trigger, grace period, legal hold and per-entity result (compliance evidence). |
| `candidate_experience` | entity | soft | high | Work experience entries (employer, title, dates, description). |
| `candidate_follow` | entity | hard | high | Follow relationships from a candidate to a company or to another person. |
| `candidate_identity_verification` | entity | soft | medium | Outcome of an external national-identity check (boundary: vendor token + outcome only). Raw national id numbers are NEVER stored; only an HMAC blind index for dedupe. |
| `candidate_job_preference` | entity | hard | medium | Career preferences of a candidate (1:1): relocation, travel, shift, weekly hours. Multi-value preferences live in candidate_preferred_*. |
| `candidate_language` | entity | hard | high | Languages spoken/written by a candidate with proficiency (Tier 1: languages). |
| `candidate_media_file` | entity | soft | very_high | File metadata for profile media (resume files, photo, intro video, certificate scans): object-storage reference, scan status, size, mime. Bytes live in object storage. |
| `candidate_open_to_work_signal` | entity | hard | medium | Open-to-work signals with validity window and audience (LinkedIn 'Open to work' / Naukri 'actively looking'). |
| `candidate_patent` | entity | soft | medium | Patents (LinkedIn export: title, number, status, URL, description). |
| `candidate_preferred_employment_type` | entity | hard | high | Preferred employment type. (multi-value job preference). |
| `candidate_preferred_industry` | entity | hard | high | Preferred industry. (multi-value job preference). |
| `candidate_preferred_location` | entity | hard | high | Preferred work city. (multi-value job preference). |
| `candidate_preferred_role` | entity | hard | high | Preferred job role. (multi-value job preference). |
| `candidate_preferred_work_type` | entity | hard | high | Preferred onsite/hybrid/remote mode. (multi-value job preference). |
| `candidate_profile` | entity | soft | high | Master record of a candidate (1:1 with app_user): identity, headline, current location, availability, salary expectations with disclosure semantics, visibility, completeness and denormalised search fields. Child tables hold the repeating sections. |
| `candidate_profile_completion_snapshot` | snapshot | append_only | very_high | History of computed completeness scores with per-rule breakdown (append-only). |
| `candidate_profile_section` | entity | hard | high | Per-candidate ordering and visibility of profile sections (LinkedIn 'section order' fields). |
| `candidate_profile_view` | event | append_only | very_high | Append-only history of which employer/recruiter viewed which candidate profile, from where, and under which credit deduction (docket Rule 10: actor, subject, timestamp, deduction). |
| `candidate_project` | entity | soft | high | Profile projects of candidates AND gig workers (Tier 1 'Profile projects': role, duration, outcome). One table with a nullable owner pair (candidate_profile_id XOR gig_worker_profile_id) enforced by CHECK; completed gig assignments can be linked for verified projects. |
| `candidate_project_skill` | entity | hard | high | Skills used in a project (bridge). |
| `candidate_publication` | entity | soft | medium | Publications (LinkedIn export: title, description, publisher, date, contributors, URL). |
| `candidate_recommendation` | entity | soft | medium | Written recommendations given/received (author, recipient, relationship, text, approval state). |
| `candidate_self_assessment` | entity | hard | high | Self-rating of a skill (1..5) with optional questionnaire responses; history preserved as new rows. |
| `candidate_shortlist_folder` | entity | soft | medium | Employer-side folders / talent pools for saved candidates (tenant scoped). |
| `candidate_shortlist_history` | event | append_only | very_high | Append-only history of shortlist/bookmark changes (add, remove, move, stage change). |
| `candidate_shortlist_item` | entity | soft | very_high | Employer/recruiter saved candidate (bookmark, shortlist or talent-pool entry) with stage, rating and private note. Distinct from jobs_applications.job_shortlist (pipeline of applicants). |
| `candidate_skill` | entity | soft | very_high | Skills of a candidate with proficiency, experience and provenance (own, parsed, assessed). |
| `candidate_skill_endorsement` | entity | hard | high | Endorsement of a candidate skill by another user (LinkedIn endorsements: endorser, skill, date, status). |
| `candidate_social_link` | entity | hard | high | Social, portfolio and website links on a profile (outbound only). |
| `candidate_test_score` | entity | soft | medium | Standardised test scores (LinkedIn 'Test scores' and Naukri entrance-exam scores: exam, score, date). |
| `candidate_volunteer_experience` | entity | soft | medium | Volunteer experience (organisation, role, cause, dates). |
| `candidate_work_authorisation` | entity | soft | medium | Right-to-work / visa status per country (LinkedIn Jobs and Monster work-authorisation questions). |
| `course` | entity | soft | medium | Courses offered by providers (catalogue; mapped to a primary skill). |
| `course_enrolment` | entity | soft | high | A candidate's enrolment in a course with progress and completion; completed courses can be shown on the profile (LinkedIn Courses). |
| `course_provider` | entity | soft | low | Learning/assessment content providers (catalogue integration, e.g. MOOC/LMS vendors). |
| `profile_completion_rule` | config | soft | low | Versioned rule set for profile completeness: weight per section/field, minimum item count, validity. |
| `profile_import` | entity | hard | high | Import/sync run from LinkedIn export, legacy systems, CSV or ATS into a profile, with idempotency, conflict policy and result counts. |
| `referral` | entity | soft | high | Referral invitations from a user to another person (candidate invite or employee referral to a job) with attribution and fraud guard. |
| `referral_reward` | entity | soft | medium | Reward granted for a qualified referral, with idempotency and link to the wallet ledger entry that granted credits. |
| `resume` | entity | soft | very_high | Resume versions of a candidate: uploaded file or built from a template, with version chain, default flag and searchable text. |
| `resume_parse_result` | entity | hard | high | Output of the resume parser: structured JSONB plus parser name/version, confidence and whether it was applied to the profile. |
| `resume_template` | entity | soft | low | Resume builder templates (layout, locale, premium flag). |
| `activity_type` | lookup | hard | low | Candidate activity events recorded in candidate_activity_log. |
| `assessment_type` | lookup | hard | low | Kind of assessment instrument. |
| `attempt_status` | lookup | hard | low | Lifecycle of an assessment attempt. |
| `availability_status` | lookup | hard | low | Candidate availability to join. |
| `contact_purpose` | lookup | hard | low | Purpose label of a phone number or email. |
| `endorsement_status` | lookup | hard | low | Moderation state of a skill endorsement (LinkedIn export: accepted/rejected/pending). |
| `enrolment_status` | lookup | hard | low | State of a course enrolment. |
| `erasure_request_status` | lookup | hard | low | State of an erasure request. |
| `erasure_trigger` | lookup | hard | low | What started an erasure/anonymisation run. |
| `identity_vendor` | lookup | hard | low | External identity-verification providers (boundary: token + outcome only). |
| `import_source` | lookup | hard | low | Origin of profile data (native sign-up or an imported/synced source). |
| `import_status` | lookup | hard | low | State of a profile import/sync run. |
| `link_type` | lookup | hard | low | Kind of external profile/portfolio link. |
| `media_kind` | lookup | hard | low | Kind of uploaded profile media. |
| `parse_status` | lookup | hard | low | Outcome of resume parsing. |
| `patent_status` | lookup | hard | low | Patent lifecycle status. |
| `profile_section_type` | lookup | hard | low | Profile sections a candidate can show, reorder or hide (LinkedIn-style section list). |
| `profile_strength` | lookup | hard | low | Profile strength band derived from the completeness score. |
| `project_origin` | lookup | hard | low | How a profile project came to exist. |
| `publication_type` | lookup | hard | low | Kind of publication. |
| `recommendation_status` | lookup | hard | low | State of a given/received recommendation. |
| `referral_channel` | lookup | hard | low | Channel through which a referral invite was shared. |
| `referral_reward_status` | lookup | hard | low | State of a referral reward. |
| `referral_status` | lookup | hard | low | Lifecycle of a referral. |
| `relationship_type` | lookup | hard | low | Working relationship between recommender/endorser and candidate. |
| `resume_format` | lookup | hard | low | File or render format of a resume. |
| `resume_origin` | lookup | hard | low | How a resume version came to exist. |
| `reward_type` | lookup | hard | low | Kind of referral reward. |
| `salary_disclosure` | lookup | hard | low | Disclosure semantics for salary values (unknown vs not applicable vs withheld; D-09). |
| `scan_status` | lookup | hard | low | Malware scan state of an uploaded file. |
| `shortlist_action` | lookup | hard | low | Action recorded in shortlist history. |
| `shortlist_kind` | lookup | hard | low | Employer-side saved-candidate type. |
| `shortlist_stage` | lookup | hard | low | Light-weight stage of a saved candidate before a formal application pipeline. |
| `skill_source` | lookup | hard | low | How a candidate_skill row was added. |
| `study_mode` | lookup | hard | low | Mode of study for an education entry. |
| `sync_conflict_policy` | lookup | hard | low | Rule applied when imported and platform values differ. |
| `view_source` | lookup | hard | low | Where a profile view originated (analytics + credit-policy driver). |
| `work_authorisation_type` | lookup | hard | low | Right-to-work categories (country given separately). |

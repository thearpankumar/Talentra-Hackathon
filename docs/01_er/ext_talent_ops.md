# ER diagram: Extension: Talent operations (onboarding, background checks, CRM, trust and safety)

Module key `ext_talent_ops`, owner Team, 125 tables (61 lookups). Generated from `05_data_dictionary.xlsx` (do not edit by hand). Columns shown: primary key, foreign keys, unique/natural keys and a few business columns; see the dictionary for all columns. Edges to lookup (enumeration) tables are listed under *Lookup references* rather than drawn.

## Design notes

<!-- ext_talent_ops.yaml -->
**Scope.** EXTENSION module (tier X), beyond the docket's 11 modules and Tier 3. It fills the product areas an ATS/HRIS/staffing suite
(Greenhouse, Lever, Workday Recruiting, Bullhorn, Naukri campus/TPO, LinkedIn Talent) has but the core modules only touch lightly.
Everything stays inside docket Section 5: lookup tables not CHECK-IN, FKs on every `*_id`, PII classification, retention per table,
vendor tokens instead of secrets, no UI, no live integration, no ETL. No new money, ledger, consent or audit mechanism is invented here.

**Areas and table families**
1. Onboarding: `onboarding_plan_template(+_task)` -> `onboarding_plan` -> `onboarding_plan_task` -> `onboarding_task_assignment`; `onboarding_document_request/_submission`,
   `e_sign_request` (vendor envelope token only), `equipment_request`. It supersedes, not duplicates, jobs_applications `onboarding_task`
   (flat checklist per joining); `onboarding_plan_task.legacy_onboarding_task_id` bridges the two so employers can adopt gradually.
2. Background/reference checks: `background_check_order/_result` follow the identity-verification boundary (vendor ref + outcome + consent FK, no raw
   criminal/credit report). `reference_check_request/_response` keep the referee's answers encrypted.
3. Campus/apprenticeship (TPO): `placement_drive`, `drive_institution` (TPO contact), `student_cohort(+_member)`, `drive_eligibility_rule`,
   `drive_registration`, `drive_round(+_result)`, `drive_result`; `apprenticeship_programme`, `apprentice_enrolment`, `apprentice_stipend_schedule`.
   Eligibility rule kinds are restricted by lookup to academic/skill criteria; gender, caste, religion, marital status or disability can not be encoded
   as eligibility (Art. 15/16 Constitution of India, Equality Act 2010, Title VII analogues).
4. Staffing and managed remote workforce: `msp_vendor` (vendor tiers), `workforce_request(+_skill)`, `vendor_submission`, `staffing_assignment`
   (bill/pay rate, window CHECK), `contract_document`, `timesheet(+_entry,+_approval)`, `remote_workforce_team`, `performance_review_cycle/_feedback`.
   `client_relationship` (organisation_account) remains the commercial agency-client contract; `msp_vendor` is the client's programme tier on top of it.
   `gig_timesheet_entry` belongs to the gig subsystem (D-15); staffing timesheets are separate because they bill by period under contract.
   Non-overlap of a worker's assignments is deliberately NOT enforced (concurrent part-time placements are legal and common).
5. Talent CRM/branding: `talent_segment(+_member)`, `talent_campaign`, `talent_sequence(+_step)`, `campaign_enrolment`, `campaign_step_execution`,
   `career_page_section` (extends organisation_career_page). Messages are produced through notifications_community (`notification_template`, `notification`);
   no sending logic here. `talent_pool` (sourcing_consultant/jobs) = manual folders; `talent_segment` = rule-based audience for campaigns.
   Enrolment requires a `user_consent` FK; `talent_campaign.required_consent_type_id` names the consent type.
6. Employer referral: `employer_referral_programme`, `employer_referral`, `employer_referral_bonus`. Distinct from `profile.referral/referral_reward`
   (platform user invitations rewarded with credits through the wallet ledger); here bonuses are paid by the employer's payroll, only a reference is stored.
7. Trust and safety: `scam_job_report`, `fraud_signal`, `trust_score(+_history)`, `spam_rule(+_hit)`, `account_restriction`, `appeal`,
   `verification_badge`, `reputation_list_entry`. Case workflow stays in administration `moderation_case` (+ `moderation_case_action`); this module adds
   specialised intake (scam report), machine signals, enforcement and redress, and links to the case instead of duplicating it. IP/device values are HMACs.
8. Compliance: `equal_opportunity_survey/_response`, `data_processing_agreement`, `subprocessor`, `dpa_subprocessor`, `subprocessor_data_category`.

**Equal-opportunity data (justification).** Voluntary self-identification is collected because (a) US federal contractors invite EEO-1/OFCCP self-ID
(race, gender, disability CC-305, veteran) and the UK/EU equality monitoring practice does the same on employer request, (b) India's apprenticeship and public
sector hiring track reserved categories and the Rights of Persons with Disabilities Act 2016 requires employers to report. The data is special-category
(GDPR Art. 9; DPDP Act 2023 requires consent and purpose limitation), so: separate table, `answers_encrypted` with a dedicated KMS key, separate DB role,
audited reads, no FK path from ranking/screening/recruiter views, a 'prefer not to say' option, purge date, and aggregate reporting only with minimum cell size 10.
`profile.candidate_diversity_declaration` is the candidate-profile-side opt-in (disability/veteran for inclusive hiring); this table is the employer-side regulatory survey.

**Patterns used.** D-11 composite FK `(onboarding_plan_id, organisation_id)` etc. keep children in the parent's tenant (needs `uq_*_id_organisation_id`);
partial unique indexes for one-open-row rules (D-12 P3: one primary assignee, one open appeal per restriction, one active badge per kind, one active
reputation entry per key); hash-sharded indexes for time-ordered high-volume tables (`fraud_signal`, `spam_rule_hit`, `timesheet_entry`,
`campaign_step_execution`, `trust_score_history`); row-level TTL for signals/hits/history (P6); append-only for approvals and signals (P5).
Exactly-one-subject rules (`app_user_id` XOR `organisation_id`) use CHECK `(a IS NULL) <> (b IS NULL)`; `num_nonnulls` is avoided (unverified in CRDB docs).

**Deviations / trade-offs.** `staffing_assignment.worker_candidate_profile_id` reuses candidate_profile instead of a separate worker table (staffing workers are
candidates; avoids a third person entity); payroll, invoices to clients and payments to workers are integration boundaries (references only).
`vendor_submission.proposed_pay_rate` is vendor-confidential: clients read `vw_client_vendor_submission` (no pay rate); the base table is grant-restricted.
Column-level privileges do not exist in CockroachDB, so confidentiality uses views plus table-level grants (see BR-OPS-12, BR-OPS-21).
Cross-table rules (referrer is not the referee, approver is not the submitter, appeal reviewer differs from original decider) need APP_LAYER/TXN checks.

**Open extension points.** Interview kits/scorecards stay in jobs_applications; ext_network owns events; background-check adjudication letters would be
a document_type + notification template; payroll integration would hang off `staffing_assignment.id` and `employer_referral_bonus.id`.

## Diagram

```mermaid
erDiagram
  account_restriction {
    UUID id PK
    UUID app_user_id FK
    UUID organisation_id FK
    UUID restriction_kind_id FK
    UUID restriction_status_id FK
    UUID moderation_reason_id FK
    UUID moderation_case_id FK
    TIMESTAMPTZ starts_at
    TIMESTAMPTZ ends_at
    STRING_500 user_visible_reason
    UUID lifted_by_app_user_id FK
  }
  appeal {
    UUID id PK
    UUID account_restriction_id FK
    UUID moderation_case_id FK
    UUID appellant_app_user_id FK
    UUID appeal_status_id FK
    STRING_4000 statement_text
    TIMESTAMPTZ submitted_at
    TIMESTAMPTZ decided_at
    UUID decided_by_app_user_id FK
  }
  apprentice_enrolment {
    UUID id PK
    UUID apprenticeship_programme_id FK
    UUID candidate_profile_id FK
    UUID apprentice_status_id FK
    UUID mentor_app_user_id FK
    DATE enrolment_start_date
    DATE enrolment_end_date
    DECIMAL_18_4 stipend_override
    UUID currency_id FK
    UUID converted_joining_id FK
  }
  apprentice_stipend_schedule {
    UUID id PK
    UUID apprentice_enrolment_id FK
    DATE due_date UK
    DECIMAL_18_4 amount
    UUID currency_id FK
    UUID stipend_payment_status_id FK
    TIMESTAMPTZ paid_at
    STRING_120 payout_ref
  }
  apprenticeship_programme {
    UUID id PK
    UUID organisation_id FK
    STRING_200 name
    UUID programme_kind_id FK
    UUID programme_status_id FK
    STRING_150 regulatory_scheme
    UUID qualification_id FK
    UUID job_role_id FK
    INT2 duration_months
    UUID currency_id FK
  }
  background_check_order {
    UUID id PK
    UUID organisation_id FK
    UUID candidate_profile_id FK
    UUID job_application_id FK
    UUID joining_id FK
    UUID background_check_vendor_id FK
    UUID background_check_status_id FK
    STRING_60 package_code
    STRING_120 vendor_order_ref
    STRING_100 idempotency_key
    UUID user_consent_id FK
    UUID ordered_by_app_user_id FK
    UUID overall_background_check_outcome_id FK
    UUID adjudicated_by_app_user_id FK
  }
  background_check_result {
    UUID id PK
    UUID background_check_order_id FK
    UUID background_check_type_id FK
    UUID background_check_outcome_id FK
    STRING_120 vendor_check_ref
    TIMESTAMPTZ completed_at
    STRING_500 discrepancy_summary
  }
  campaign_enrolment {
    UUID id PK
    UUID talent_campaign_id FK
    UUID talent_sequence_id FK
    UUID candidate_profile_id FK
    UUID campaign_enrolment_status_id FK
    UUID user_consent_id FK
    INT2 current_step_no
    TIMESTAMPTZ next_step_at
    TIMESTAMPTZ enrolled_at
  }
  campaign_step_execution {
    UUID id PK
    UUID campaign_enrolment_id FK
    UUID talent_sequence_step_id FK
    TIMESTAMPTZ executed_at
    UUID delivery_status_id FK
    UUID notification_id FK
  }
  career_page_section {
    UUID id PK
    UUID organisation_career_page_id FK
    UUID career_section_kind_id FK
    UUID language_id FK
    STRING_200 heading
    JSONB content
    INT2 sort_order UK
    BOOL is_published
  }
  contract_document {
    UUID id PK
    UUID staffing_assignment_id FK
    UUID organisation_id FK
    UUID contract_document_kind_id FK
    STRING_200 title
    INT4 version_no
    UUID supersedes_contract_document_id FK
    UUID e_sign_request_id FK
    STRING_500 object_ref
  }
  data_processing_agreement {
    UUID id PK
    UUID organisation_id FK
    STRING_200 counterparty_name
    STRING_40 version_label
    UUID dpa_status_id FK
    UUID governing_law_country_id FK
    UUID transfer_mechanism_id FK
    STRING_500 processing_purpose
    UUID e_sign_request_id FK
  }
  dpa_subprocessor {
    UUID id PK
    UUID data_processing_agreement_id FK
    UUID subprocessor_id FK
    UUID transfer_mechanism_id FK
    TIMESTAMPTZ approved_at
    TIMESTAMPTZ removed_at
  }
  drive_eligibility_rule {
    UUID id PK
    UUID placement_drive_id FK
    UUID eligibility_rule_kind_id FK
    UUID screening_operator_id FK
    DECIMAL_8_2 threshold_value
    JSONB allowed_values
    BOOL is_hard_requirement
  }
  drive_institution {
    UUID id PK
    UUID placement_drive_id FK
    UUID institution_id FK
    UUID tpo_app_user_id FK
    UUID invitation_status_id FK
    TIMESTAMPTZ responded_at
    DATE campus_visit_date
    STRING_300 venue_text
  }
  drive_registration {
    UUID id PK
    UUID placement_drive_id FK
    UUID candidate_profile_id FK
    UUID student_cohort_member_id FK
    UUID institution_id FK
    UUID job_application_id FK
    UUID drive_registration_status_id FK
    JSONB eligibility_snapshot
    TIMESTAMPTZ registered_at
    TIMESTAMPTZ withdrawn_at
  }
  drive_result {
    UUID id PK
    UUID drive_registration_id FK
    UUID drive_result_decision_id FK
    TIMESTAMPTZ decided_at
    UUID offer_id FK
    UUID joining_id FK
    DECIMAL_18_4 offered_ctc
    UUID currency_id FK
    DATE expected_joining_date
  }
  drive_round {
    UUID id PK
    UUID placement_drive_id FK
    INT2 round_no UK
    UUID drive_round_kind_id FK
    UUID interview_mode_id FK
    STRING_150 name
    TIMESTAMPTZ scheduled_start_at
    TIMESTAMPTZ scheduled_end_at
  }
  drive_round_result {
    UUID id PK
    UUID drive_round_id FK
    UUID drive_registration_id FK
    UUID drive_round_outcome_id FK
    DECIMAL_6_2 score
    UUID evaluator_app_user_id FK
    TIMESTAMPTZ evaluated_at
  }
  e_sign_request {
    UUID id PK
    UUID organisation_id FK
    UUID onboarding_plan_id FK
    UUID offer_id FK
    UUID signer_candidate_profile_id FK
    UUID signer_app_user_id FK
    UUID e_sign_vendor_id FK
    STRING_200 vendor_envelope_ref
    STRING_200 document_title
    UUID e_sign_status_id FK
    TIMESTAMPTZ sent_at
  }
  employer_referral {
    UUID id PK
    UUID organisation_id FK
    UUID employer_referral_programme_id FK
    UUID job_requirement_id FK
    UUID referrer_app_user_id FK
    UUID candidate_profile_id FK
    STRING_150 referee_name
    STRING_254 referee_email
    UUID job_application_id FK
    UUID employer_referral_status_id FK
    TIMESTAMPTZ referee_consent_at
  }
  employer_referral_bonus {
    UUID id PK
    UUID employer_referral_id FK
    UUID bonus_trigger_id FK
    DECIMAL_18_4 amount
    UUID currency_id FK
    UUID referral_bonus_status_id FK
    DATE eligible_on_date
    UUID approved_by_app_user_id FK
    TIMESTAMPTZ approved_at
  }
  employer_referral_programme {
    UUID id PK
    UUID organisation_id FK
    STRING_150 name
    UUID programme_status_id FK
    UUID bonus_trigger_id FK
    DECIMAL_18_4 default_bonus_amount
    UUID currency_id FK
    INT2 waiting_days_after_joining
  }
  equal_opportunity_response {
    UUID id PK
    UUID equal_opportunity_survey_id FK
    UUID candidate_profile_id FK
    UUID organisation_id FK
    UUID job_application_id FK
    UUID user_consent_id FK
    BOOL declined_to_answer
    BYTES answers_encrypted
    TIMESTAMPTZ submitted_at
  }
  equal_opportunity_survey {
    UUID id PK
    UUID organisation_id FK
    UUID country_id FK
    STRING_150 name
    INT4 survey_version
    UUID eeo_legal_basis_id FK
    STRING_500 purpose_text
  }
  equipment_request {
    UUID id PK
    UUID onboarding_plan_id FK
    UUID equipment_kind_id FK
    UUID equipment_request_status_id FK
    UUID requested_by_app_user_id FK
    INT2 quantity
    DATE needed_by_date
    STRING_60 asset_tag
  }
  fraud_signal {
    UUID id PK
    UUID trust_signal_type_id FK
    UUID event_severity_id FK
    UUID app_user_id FK
    UUID organisation_id FK
    UUID job_requirement_id FK
    INET ip_address
    STRING_64 device_fingerprint_hmac
    DECIMAL_6_2 score_impact
    UUID moderation_case_id FK
  }
  msp_vendor {
    UUID id PK
    UUID client_organisation_id FK
    UUID vendor_organisation_id FK
    UUID client_relationship_id FK
    UUID vendor_tier_id FK
    UUID payment_term_id FK
    DECIMAL_5_2 max_markup_percent
    JSONB rate_card
    DATE effective_from_date
  }
  onboarding_document_request {
    UUID id PK
    UUID onboarding_plan_id FK
    UUID candidate_profile_id FK
    UUID document_type_id FK
    UUID document_request_status_id FK
    BOOL is_mandatory
    DATE due_date
    STRING_500 instructions_text
  }
  onboarding_document_submission {
    UUID id PK
    UUID onboarding_document_request_id FK
    STRING_500 object_ref
    STRING_255 file_name
    STRING_100 content_type
    UUID review_approval_status_id FK
    UUID reviewed_by_app_user_id FK
  }
  onboarding_plan {
    UUID id PK
    UUID organisation_id FK
    UUID joining_id FK
    UUID candidate_profile_id FK
    UUID onboarding_plan_template_id FK
    UUID onboarding_plan_status_id FK
    UUID hiring_manager_app_user_id FK
    UUID buddy_app_user_id FK
    DATE plan_start_date
    DATE plan_end_date
    DECIMAL_5_2 completion_percent
  }
  onboarding_plan_task {
    UUID id PK
    UUID onboarding_plan_id FK
    UUID organisation_id FK
    UUID onboarding_plan_template_task_id FK
    UUID legacy_onboarding_task_id FK
    INT2 sequence_no
    STRING_200 title
    UUID onboarding_task_type_id FK
    UUID onboarding_task_status_id FK
    DATE due_date
    UUID completed_by_app_user_id FK
  }
  onboarding_plan_template {
    UUID id PK
    UUID organisation_id FK
    STRING_150 name
    UUID employment_type_id FK
    BOOL is_active
    STRING_500 description_text
  }
  onboarding_plan_template_task {
    UUID id PK
    UUID onboarding_plan_template_id FK
    INT2 sequence_no UK
    STRING_200 title
    UUID onboarding_task_type_id FK
    UUID onboarding_assignee_role_id FK
    INT2 due_offset_days
    BOOL is_required
    UUID document_type_id FK
  }
  onboarding_task_assignment {
    UUID id PK
    UUID onboarding_plan_task_id FK
    UUID assignee_app_user_id FK
    UUID onboarding_assignee_role_id FK
    BOOL is_primary
    TIMESTAMPTZ assigned_at
    TIMESTAMPTZ acknowledged_at
  }
  performance_feedback {
    UUID id PK
    UUID performance_review_cycle_id FK
    UUID staffing_assignment_id FK
    UUID feedback_kind_id FK
    UUID author_app_user_id FK
    INT2 rating
    STRING_2000 strengths_text
    STRING_2000 improvement_text
  }
  performance_review_cycle {
    UUID id PK
    UUID organisation_id FK
    STRING_150 name
    UUID performance_cycle_status_id FK
    DATE review_start_date
    DATE review_end_date
  }
  placement_drive {
    UUID id PK
    UUID organisation_id FK
    UUID job_requirement_id FK
    UUID job_role_id FK
    STRING_200 title
    UUID drive_type_id FK
    UUID drive_status_id FK
    UUID employment_type_id FK
    UUID owner_app_user_id FK
    TIMESTAMPTZ registration_open_at
    TIMESTAMPTZ registration_close_at
    UUID currency_id FK
    UUID salary_period_id FK
  }
  reference_check_request {
    UUID id PK
    UUID organisation_id FK
    UUID candidate_profile_id FK
    UUID job_application_id FK
    STRING_150 referee_name
    STRING_254 referee_email
    STRING_16 referee_phone
    UUID referee_phone_country_id FK
    UUID referee_relationship_id FK
    UUID reference_check_status_id FK
    UUID user_consent_id FK
    UUID requested_by_app_user_id FK
  }
  reference_check_response {
    UUID id PK
    UUID reference_check_request_id FK
    TIMESTAMPTZ submitted_at
    INT2 overall_rating
    BOOL would_rehire
  }
  remote_workforce_team {
    UUID id PK
    UUID client_organisation_id FK
    STRING_150 name
    UUID manager_app_user_id FK
    STRING_64 home_time_zone
    TIME core_hours_start
  }
  reputation_list_entry {
    UUID id PK
    UUID reputation_list_kind_id FK
    STRING_64 value_hmac
    INET ip_network
    UUID reputation_verdict_id FK
    STRING_300 reason_text
  }
  scam_job_report {
    UUID id PK
    UUID job_requirement_id FK
    UUID reporter_app_user_id FK
    UUID scam_report_category_id FK
    UUID scam_report_status_id FK
    STRING_2000 description_text
    BOOL is_money_lost
    DECIMAL_18_4 reported_loss_amount
    UUID currency_id FK
    UUID moderation_case_id FK
  }
  spam_rule {
    UUID id PK
    STRING_150 name
    INT4 version_no
    UUID spam_rule_scope_id FK
    UUID spam_rule_action_id FK
    JSONB rule_expression
  }
  spam_rule_hit {
    UUID id PK
    UUID spam_rule_id FK
    UUID moderation_target_type_id FK
    STRING_64 target_ref
    UUID app_user_id FK
    UUID applied_spam_rule_action_id FK
    TIMESTAMPTZ hit_at
    JSONB match_details
  }
  staffing_assignment {
    UUID id PK
    UUID client_organisation_id FK
    UUID agency_organisation_id FK
    UUID worker_candidate_profile_id FK
    UUID workforce_request_id FK
    UUID vendor_submission_id FK
    UUID remote_workforce_team_id FK
    UUID supervisor_app_user_id FK
    UUID staffing_engagement_type_id FK
    UUID staffing_assignment_status_id FK
    DATE assignment_start_date
    DATE assignment_end_date
    DECIMAL_18_4 bill_rate
    UUID currency_id FK
    UUID salary_period_id FK
    UUID work_country_id FK
    UUID staffing_end_reason_id FK
  }
  student_cohort {
    UUID id PK
    UUID institution_id FK
    UUID qualification_id FK
    UUID field_of_study_id FK
    UUID tpo_app_user_id FK
    STRING_150 name
    INT2 graduation_year
    BOOL is_placement_open
  }
  student_cohort_member {
    UUID id PK
    UUID student_cohort_id FK
    UUID candidate_profile_id FK
    STRING_64 roll_number_hmac
    DECIMAL_4_2 cgpa
    DECIMAL_5_2 tenth_percent
  }
  subprocessor {
    UUID id PK
    STRING_200 name
    STRING_500 service_description
    UUID country_id FK
    STRING_200 security_certification
  }
  subprocessor_data_category {
    UUID id PK
    UUID subprocessor_id FK
    UUID processed_data_category_id FK
  }
  talent_campaign {
    UUID id PK
    UUID organisation_id FK
    STRING_150 name
    UUID talent_campaign_goal_id FK
    UUID talent_campaign_status_id FK
    UUID talent_segment_id FK
    UUID job_requirement_id FK
    UUID required_consent_type_id FK
    UUID owner_app_user_id FK
    INT2 max_touches_per_week
    TIMESTAMPTZ scheduled_start_at
  }
  talent_segment {
    UUID id PK
    UUID organisation_id FK
    STRING_150 name
    UUID segment_kind_id FK
    JSONB criteria
    UUID owner_app_user_id FK
    INT4 member_count
  }
  talent_segment_member {
    UUID id PK
    UUID talent_segment_id FK
    UUID candidate_profile_id FK
    TIMESTAMPTZ added_at
    UUID added_by_app_user_id FK
  }
  talent_sequence {
    UUID id PK
    UUID talent_campaign_id FK
    STRING_150 name UK
    BOOL is_default
  }
  talent_sequence_step {
    UUID id PK
    UUID talent_sequence_id FK
    INT2 step_no UK
    UUID sequence_step_kind_id FK
    UUID notification_channel_id FK
    UUID notification_template_id FK
    INT4 delay_hours
    BOOL exit_on_reply
  }
  timesheet {
    UUID id PK
    UUID staffing_assignment_id FK
    DATE period_start_date
    DATE period_end_date
    UUID timesheet_status_id FK
    DECIMAL_7_2 total_hours
    UUID submitted_by_app_user_id FK
  }
  timesheet_approval {
    UUID id PK
    UUID timesheet_id FK
    UUID approver_app_user_id FK
    INT2 submission_no UK
    INT2 approval_level UK
    UUID approval_status_id FK
    TIMESTAMPTZ decided_at
    STRING_500 decision_note
  }
  timesheet_entry {
    UUID id PK
    UUID timesheet_id FK
    DATE work_date
    DECIMAL_5_2 hours
    UUID timesheet_entry_kind_id FK
    BOOL is_billable
  }
  trust_score {
    UUID id PK
    UUID app_user_id FK
    UUID organisation_id FK
    DECIMAL_5_2 score
    UUID trust_band_id FK
    STRING_40 model_version
    JSONB components
  }
  trust_score_history {
    UUID id PK
    UUID trust_score_id FK
    DECIMAL_5_2 score
    UUID trust_band_id FK
    STRING_40 model_version
    TIMESTAMPTZ computed_at
  }
  vendor_submission {
    UUID id PK
    UUID workforce_request_id FK
    UUID msp_vendor_id FK
    UUID candidate_profile_id FK
    UUID submitted_by_app_user_id FK
    UUID vendor_submission_status_id FK
    DECIMAL_18_4 proposed_bill_rate
    DECIMAL_18_4 proposed_pay_rate
    UUID currency_id FK
    UUID salary_period_id FK
    DATE available_from_date
  }
  verification_badge {
    UUID id PK
    UUID app_user_id FK
    UUID organisation_id FK
    UUID verification_badge_kind_id FK
    UUID candidate_identity_verification_id FK
    UUID organisation_verification_id FK
    UUID decided_by_app_user_id FK
    TIMESTAMPTZ granted_at
    TIMESTAMPTZ expires_at
    TIMESTAMPTZ revoked_at
  }
  workforce_request {
    UUID id PK
    UUID client_organisation_id FK
    UUID requested_by_app_user_id FK
    UUID job_requirement_id FK
    UUID job_role_id FK
    STRING_200 title
    UUID workforce_request_status_id FK
    UUID staffing_engagement_type_id FK
    UUID work_type_id FK
    INT4 headcount
    INT4 filled_count
    UUID work_country_id FK
    UUID currency_id FK
    UUID salary_period_id FK
  }
  workforce_request_skill {
    UUID id PK
    UUID workforce_request_id FK
    UUID skill_id FK
    UUID skill_importance_id FK
    UUID proficiency_level_id FK
    INT2 min_years_experience
  }
  app_user {
    UUID id PK
  }
  candidate_identity_verification {
    UUID id PK
  }
  candidate_profile {
    UUID id PK
  }
  client_relationship {
    UUID id PK
  }
  country {
    UUID id PK
  }
  currency {
    UUID id PK
  }
  field_of_study {
    UUID id PK
  }
  institution {
    UUID id PK
  }
  job_application {
    UUID id PK
  }
  job_requirement {
    UUID id PK
  }
  job_role {
    UUID id PK
  }
  joining {
    UUID id PK
  }
  language {
    UUID id PK
  }
  moderation_case {
    UUID id PK
  }
  notification {
    UUID id PK
  }
  notification_template {
    UUID id PK
  }
  offer {
    UUID id PK
  }
  onboarding_task {
    UUID id PK
  }
  organisation {
    UUID id PK
  }
  organisation_career_page {
    UUID id PK
  }
  organisation_verification {
    UUID id PK
  }
  qualification {
    UUID id PK
  }
  skill {
    UUID id PK
  }
  user_consent {
    UUID id PK
  }
  account_restriction |o--o{ appeal : "account_restriction_id"
  app_user |o--o{ account_restriction : "app_user_id"
  app_user |o--o{ account_restriction : "lifted_by_app_user_id"
  app_user |o--o{ appeal : "decided_by_app_user_id"
  app_user |o--o{ apprentice_enrolment : "mentor_app_user_id"
  app_user |o--o{ background_check_order : "adjudicated_by_app_user_id"
  app_user |o--o{ drive_institution : "tpo_app_user_id"
  app_user |o--o{ drive_round_result : "evaluator_app_user_id"
  app_user |o--o{ e_sign_request : "signer_app_user_id"
  app_user |o--o{ employer_referral_bonus : "approved_by_app_user_id"
  app_user |o--o{ fraud_signal : "app_user_id"
  app_user |o--o{ onboarding_document_submission : "reviewed_by_app_user_id"
  app_user |o--o{ onboarding_plan : "buddy_app_user_id"
  app_user |o--o{ onboarding_plan : "hiring_manager_app_user_id"
  app_user |o--o{ onboarding_plan_task : "completed_by_app_user_id"
  app_user |o--o{ spam_rule_hit : "app_user_id"
  app_user |o--o{ staffing_assignment : "supervisor_app_user_id"
  app_user |o--o{ talent_segment_member : "added_by_app_user_id"
  app_user |o--o{ timesheet : "submitted_by_app_user_id"
  app_user |o--o{ trust_score : "app_user_id"
  app_user |o--o{ verification_badge : "app_user_id"
  app_user |o--o{ verification_badge : "decided_by_app_user_id"
  app_user ||--o{ appeal : "appellant_app_user_id"
  app_user ||--o{ background_check_order : "ordered_by_app_user_id"
  app_user ||--o{ employer_referral : "referrer_app_user_id"
  app_user ||--o{ equipment_request : "requested_by_app_user_id"
  app_user ||--o{ onboarding_task_assignment : "assignee_app_user_id"
  app_user ||--o{ performance_feedback : "author_app_user_id"
  app_user ||--o{ placement_drive : "owner_app_user_id"
  app_user ||--o{ reference_check_request : "requested_by_app_user_id"
  app_user ||--o{ remote_workforce_team : "manager_app_user_id"
  app_user ||--o{ scam_job_report : "reporter_app_user_id"
  app_user ||--o{ student_cohort : "tpo_app_user_id"
  app_user ||--o{ talent_campaign : "owner_app_user_id"
  app_user ||--o{ talent_segment : "owner_app_user_id"
  app_user ||--o{ timesheet_approval : "approver_app_user_id"
  app_user ||--o{ vendor_submission : "submitted_by_app_user_id"
  app_user ||--o{ workforce_request : "requested_by_app_user_id"
  apprentice_enrolment ||--o{ apprentice_stipend_schedule : "apprentice_enrolment_id"
  apprenticeship_programme ||--o{ apprentice_enrolment : "apprenticeship_programme_id"
  background_check_order ||--o{ background_check_result : "background_check_order_id"
  campaign_enrolment ||--o{ campaign_step_execution : "campaign_enrolment_id"
  candidate_identity_verification |o--o{ verification_badge : "candidate_identity_verification_id"
  candidate_profile |o--o{ e_sign_request : "signer_candidate_profile_id"
  candidate_profile |o--o{ employer_referral : "candidate_profile_id"
  candidate_profile ||--o{ apprentice_enrolment : "candidate_profile_id"
  candidate_profile ||--o{ background_check_order : "candidate_profile_id"
  candidate_profile ||--o{ campaign_enrolment : "candidate_profile_id"
  candidate_profile ||--o{ drive_registration : "candidate_profile_id"
  candidate_profile ||--o{ equal_opportunity_response : "candidate_profile_id"
  candidate_profile ||--o{ onboarding_document_request : "candidate_profile_id"
  candidate_profile ||--o{ onboarding_plan : "candidate_profile_id"
  candidate_profile ||--o{ reference_check_request : "candidate_profile_id"
  candidate_profile ||--o{ staffing_assignment : "worker_candidate_profile_id"
  candidate_profile ||--o{ student_cohort_member : "candidate_profile_id"
  candidate_profile ||--o{ talent_segment_member : "candidate_profile_id"
  candidate_profile ||--o{ vendor_submission : "candidate_profile_id"
  client_relationship |o--o{ msp_vendor : "client_relationship_id"
  contract_document |o--o{ contract_document : "supersedes_contract_document_id"
  country |o--o{ equal_opportunity_survey : "country_id"
  country |o--o{ reference_check_request : "referee_phone_country_id"
  country |o--o{ staffing_assignment : "work_country_id"
  country |o--o{ workforce_request : "work_country_id"
  country ||--o{ data_processing_agreement : "governing_law_country_id"
  country ||--o{ subprocessor : "country_id"
  currency |o--o{ apprentice_enrolment : "currency_id"
  currency |o--o{ drive_result : "currency_id"
  currency |o--o{ placement_drive : "currency_id"
  currency |o--o{ scam_job_report : "currency_id"
  currency |o--o{ workforce_request : "currency_id"
  currency ||--o{ apprentice_stipend_schedule : "currency_id"
  currency ||--o{ apprenticeship_programme : "currency_id"
  currency ||--o{ employer_referral_bonus : "currency_id"
  currency ||--o{ employer_referral_programme : "currency_id"
  currency ||--o{ staffing_assignment : "currency_id"
  currency ||--o{ vendor_submission : "currency_id"
  data_processing_agreement ||--o{ dpa_subprocessor : "data_processing_agreement_id"
  drive_registration ||--o{ drive_result : "drive_registration_id"
  drive_registration ||--o{ drive_round_result : "drive_registration_id"
  drive_round ||--o{ drive_round_result : "drive_round_id"
  e_sign_request |o--o{ contract_document : "e_sign_request_id"
  e_sign_request |o--o{ data_processing_agreement : "e_sign_request_id"
  employer_referral ||--o{ employer_referral_bonus : "employer_referral_id"
  employer_referral_programme ||--o{ employer_referral : "employer_referral_programme_id"
  equal_opportunity_survey ||--o{ equal_opportunity_response : "equal_opportunity_survey_id"
  field_of_study |o--o{ student_cohort : "field_of_study_id"
  institution |o--o{ drive_registration : "institution_id"
  institution ||--o{ drive_institution : "institution_id"
  institution ||--o{ student_cohort : "institution_id"
  job_application |o--o{ background_check_order : "job_application_id"
  job_application |o--o{ drive_registration : "job_application_id"
  job_application |o--o{ employer_referral : "job_application_id"
  job_application |o--o{ equal_opportunity_response : "job_application_id"
  job_application |o--o{ reference_check_request : "job_application_id"
  job_requirement |o--o{ fraud_signal : "job_requirement_id"
  job_requirement |o--o{ placement_drive : "job_requirement_id"
  job_requirement |o--o{ talent_campaign : "job_requirement_id"
  job_requirement |o--o{ workforce_request : "job_requirement_id"
  job_requirement ||--o{ employer_referral : "job_requirement_id"
  job_requirement ||--o{ scam_job_report : "job_requirement_id"
  job_role |o--o{ apprenticeship_programme : "job_role_id"
  job_role |o--o{ placement_drive : "job_role_id"
  job_role |o--o{ workforce_request : "job_role_id"
  joining |o--o{ apprentice_enrolment : "converted_joining_id"
  joining |o--o{ background_check_order : "joining_id"
  joining |o--o{ drive_result : "joining_id"
  joining ||--o{ onboarding_plan : "joining_id"
  language |o--o{ career_page_section : "language_id"
  moderation_case |o--o{ account_restriction : "moderation_case_id"
  moderation_case |o--o{ appeal : "moderation_case_id"
  moderation_case |o--o{ fraud_signal : "moderation_case_id"
  moderation_case |o--o{ scam_job_report : "moderation_case_id"
  msp_vendor ||--o{ vendor_submission : "msp_vendor_id"
  notification |o--o{ campaign_step_execution : "notification_id"
  notification_template |o--o{ talent_sequence_step : "notification_template_id"
  offer |o--o{ drive_result : "offer_id"
  offer |o--o{ e_sign_request : "offer_id"
  onboarding_document_request ||--o{ onboarding_document_submission : "onboarding_document_request_id"
  onboarding_plan |o--o{ e_sign_request : "onboarding_plan_id"
  onboarding_plan ||--o{ equipment_request : "onboarding_plan_id"
  onboarding_plan ||--o{ onboarding_document_request : "onboarding_plan_id"
  onboarding_plan ||--o{ onboarding_plan_task : "onboarding_plan_id"
  onboarding_plan_task ||--o{ onboarding_task_assignment : "onboarding_plan_task_id"
  onboarding_plan_template |o--o{ onboarding_plan : "onboarding_plan_template_id"
  onboarding_plan_template ||--o{ onboarding_plan_template_task : "onboarding_plan_template_id"
  onboarding_plan_template_task |o--o{ onboarding_plan_task : "onboarding_plan_template_task_id"
  onboarding_task |o--o{ onboarding_plan_task : "legacy_onboarding_task_id"
  organisation |o--o{ account_restriction : "organisation_id"
  organisation |o--o{ equal_opportunity_response : "organisation_id"
  organisation |o--o{ equal_opportunity_survey : "organisation_id"
  organisation |o--o{ fraud_signal : "organisation_id"
  organisation |o--o{ staffing_assignment : "agency_organisation_id"
  organisation |o--o{ trust_score : "organisation_id"
  organisation |o--o{ verification_badge : "organisation_id"
  organisation ||--o{ apprenticeship_programme : "organisation_id"
  organisation ||--o{ background_check_order : "organisation_id"
  organisation ||--o{ contract_document : "organisation_id"
  organisation ||--o{ data_processing_agreement : "organisation_id"
  organisation ||--o{ e_sign_request : "organisation_id"
  organisation ||--o{ employer_referral_programme : "organisation_id"
  organisation ||--o{ msp_vendor : "client_organisation_id"
  organisation ||--o{ msp_vendor : "vendor_organisation_id"
  organisation ||--o{ onboarding_plan : "organisation_id"
  organisation ||--o{ onboarding_plan_template : "organisation_id"
  organisation ||--o{ performance_review_cycle : "organisation_id"
  organisation ||--o{ placement_drive : "organisation_id"
  organisation ||--o{ reference_check_request : "organisation_id"
  organisation ||--o{ remote_workforce_team : "client_organisation_id"
  organisation ||--o{ staffing_assignment : "client_organisation_id"
  organisation ||--o{ talent_campaign : "organisation_id"
  organisation ||--o{ talent_segment : "organisation_id"
  organisation ||--o{ workforce_request : "client_organisation_id"
  organisation_career_page ||--o{ career_page_section : "organisation_career_page_id"
  organisation_verification |o--o{ verification_badge : "organisation_verification_id"
  performance_review_cycle ||--o{ performance_feedback : "performance_review_cycle_id"
  placement_drive ||--o{ drive_eligibility_rule : "placement_drive_id"
  placement_drive ||--o{ drive_institution : "placement_drive_id"
  placement_drive ||--o{ drive_registration : "placement_drive_id"
  placement_drive ||--o{ drive_round : "placement_drive_id"
  qualification |o--o{ apprenticeship_programme : "qualification_id"
  qualification |o--o{ student_cohort : "qualification_id"
  reference_check_request ||--o| reference_check_response : "reference_check_request_id"
  remote_workforce_team |o--o{ staffing_assignment : "remote_workforce_team_id"
  skill ||--o{ workforce_request_skill : "skill_id"
  spam_rule ||--o{ spam_rule_hit : "spam_rule_id"
  staffing_assignment |o--o{ contract_document : "staffing_assignment_id"
  staffing_assignment ||--o{ performance_feedback : "staffing_assignment_id"
  staffing_assignment ||--o{ timesheet : "staffing_assignment_id"
  student_cohort ||--o{ student_cohort_member : "student_cohort_id"
  student_cohort_member |o--o{ drive_registration : "student_cohort_member_id"
  subprocessor ||--o{ dpa_subprocessor : "subprocessor_id"
  subprocessor ||--o{ subprocessor_data_category : "subprocessor_id"
  talent_campaign ||--o{ campaign_enrolment : "talent_campaign_id"
  talent_campaign ||--o{ talent_sequence : "talent_campaign_id"
  talent_segment |o--o{ talent_campaign : "talent_segment_id"
  talent_segment ||--o{ talent_segment_member : "talent_segment_id"
  talent_sequence ||--o{ campaign_enrolment : "talent_sequence_id"
  talent_sequence ||--o{ talent_sequence_step : "talent_sequence_id"
  talent_sequence_step ||--o{ campaign_step_execution : "talent_sequence_step_id"
  timesheet ||--o{ timesheet_approval : "timesheet_id"
  timesheet ||--o{ timesheet_entry : "timesheet_id"
  trust_score ||--o{ trust_score_history : "trust_score_id"
  user_consent |o--o{ reference_check_request : "user_consent_id"
  user_consent ||--o{ background_check_order : "user_consent_id"
  user_consent ||--o{ campaign_enrolment : "user_consent_id"
  user_consent ||--o{ equal_opportunity_response : "user_consent_id"
  vendor_submission |o--o{ staffing_assignment : "vendor_submission_id"
  workforce_request |o--o{ staffing_assignment : "workforce_request_id"
  workforce_request ||--o{ vendor_submission : "workforce_request_id"
  workforce_request ||--o{ workforce_request_skill : "workforce_request_id"
```

## Lookup tables in this module

| Lookup | Values | Description |
|---|---|---|
| `appeal_status` | 6 | Lifecycle and outcome of an appeal. |
| `apprentice_status` | 6 | State of an apprentice in a programme. |
| `background_check_outcome` | 5 | Vendor outcome of a check or of the whole order (no raw report content). |
| `background_check_status` | 6 | Lifecycle of a background-check order. |
| `background_check_type` | 9 | Category of one verification inside a background-check order. |
| `background_check_vendor` | 7 | Background-screening vendors (boundary: order reference and outcome only). |
| `bonus_trigger` | 4 | Milestone at which a referral bonus accrues. |
| `campaign_enrolment_status` | 7 | State of a candidate inside a campaign. |
| `career_section_kind` | 9 | Content block types of an employer career page. |
| `contract_document_kind` | 6 | Kind of contractual document attached to an assignment. |
| `document_request_status` | 6 | State of a document requested from a joiner. |
| `dpa_status` | 5 | State of a data-processing agreement. |
| `drive_registration_status` | 8 | Student registration state in a drive. |
| `drive_result_decision` | 5 | Final decision for a student in a drive. |
| `drive_round_kind` | 7 | Type of a campus selection round. |
| `drive_round_outcome` | 4 | Result of a candidate in one round. |
| `drive_status` | 5 | Lifecycle of a placement drive. |
| `drive_type` | 5 | Mode of a placement drive. |
| `e_sign_status` | 7 | Lifecycle of an e-signature envelope as reported by the vendor. |
| `e_sign_vendor` | 6 | E-signature vendors integrated at the boundary (no signature material is stored here). |
| `eeo_legal_basis` | 3 | Legal basis under which voluntary equal-opportunity data is collected. |
| `eligibility_rule_kind` | 8 | Objective academic/skill eligibility criteria for a drive. Protected attributes (gender, caste, religion, marital status, disability) are deliberately NOT available as rule kinds. |
| `employer_referral_status` | 7 | State of an employee referral. |
| `equipment_kind` | 8 | Kind of equipment or access provisioned for a joiner. |
| `equipment_request_status` | 6 | Fulfilment state of an equipment request. |
| `feedback_kind` | 5 | Who gives feedback on whom. |
| `onboarding_assignee_role` | 7 | Function of the person who owns an onboarding task. |
| `onboarding_plan_status` | 4 | Lifecycle of a joiner's onboarding plan. |
| `onboarding_task_status` | 5 | State of one onboarding plan task. |
| `performance_cycle_status` | 4 | State of a performance review cycle. |
| `processed_data_category` | 9 | Category of personal data a sub-processor handles. |
| `programme_kind` | 5 | Kind of early-career programme. |
| `programme_status` | 4 | Lifecycle of a structured programme (apprenticeship, referral). |
| `referee_relationship` | 7 | How the referee knows the candidate. |
| `reference_check_status` | 6 | Lifecycle of a reference request. |
| `referral_bonus_status` | 6 | State of a referral bonus. |
| `reputation_list_kind` | 4 | Key type of an IP/device/domain reputation entry. |
| `reputation_verdict` | 3 | Reputation decision for a list entry. |
| `restriction_kind` | 6 | Kind of enforcement restriction on an account. |
| `restriction_status` | 4 | Lifecycle of a restriction. |
| `scam_report_category` | 7 | Reason a job posting is reported as scam/fraud. |
| `scam_report_status` | 5 | Triage state of a scam report. |
| `segment_kind` | 2 | How a talent segment gets its members. |
| `sequence_step_kind` | 5 | Action of one step in a nurture sequence. |
| `spam_rule_action` | 4 | Automatic action a spam rule triggers. |
| `spam_rule_scope` | 6 | Surface a spam rule inspects. |
| `staffing_assignment_status` | 5 | State of a worker's assignment. |
| `staffing_end_reason` | 7 | Why an assignment ended. |
| `staffing_engagement_type` | 5 | Commercial form of a staffing engagement. |
| `stipend_payment_status` | 4 | State of one scheduled stipend payment (payout happens outside the DB). |
| `talent_campaign_goal` | 6 | Objective of a talent-nurture campaign. |
| `talent_campaign_status` | 6 | Lifecycle of a talent campaign. |
| `timesheet_entry_kind` | 5 | Kind of time booked. |
| `timesheet_status` | 5 | State of a weekly/monthly timesheet. |
| `transfer_mechanism` | 5 | Cross-border transfer safeguard. |
| `trust_band` | 4 | Banding of a numeric trust score. |
| `trust_signal_type` | 10 | Detector signal types feeding fraud and trust scoring. |
| `vendor_submission_status` | 7 | State of a vendor's candidate submission against a workforce request. |
| `vendor_tier` | 4 | Client's preferred-vendor tier (MSP/VMS practice). |
| `verification_badge_kind` | 5 | Visible trust badge types. |
| `workforce_request_status` | 7 | Lifecycle of a managed-workforce request. |

## Lookup references (enumeration FKs)

| Table | Lookup tables referenced |
|---|---|
| `account_restriction` | `moderation_reason`, `restriction_kind`, `restriction_status` |
| `appeal` | `appeal_status` |
| `apprentice_enrolment` | `apprentice_status` |
| `apprentice_stipend_schedule` | `stipend_payment_status` |
| `apprenticeship_programme` | `programme_kind`, `programme_status` |
| `background_check_order` | `background_check_outcome`, `background_check_status`, `background_check_vendor` |
| `background_check_result` | `background_check_outcome`, `background_check_type` |
| `campaign_enrolment` | `campaign_enrolment_status` |
| `campaign_step_execution` | `delivery_status` |
| `career_page_section` | `career_section_kind` |
| `contract_document` | `contract_document_kind` |
| `data_processing_agreement` | `dpa_status`, `transfer_mechanism` |
| `dpa_subprocessor` | `transfer_mechanism` |
| `drive_eligibility_rule` | `eligibility_rule_kind`, `screening_operator` |
| `drive_institution` | `invitation_status` |
| `drive_registration` | `drive_registration_status` |
| `drive_result` | `drive_result_decision` |
| `drive_round` | `drive_round_kind`, `interview_mode` |
| `drive_round_result` | `drive_round_outcome` |
| `e_sign_request` | `e_sign_status`, `e_sign_vendor` |
| `employer_referral` | `employer_referral_status` |
| `employer_referral_bonus` | `bonus_trigger`, `referral_bonus_status` |
| `employer_referral_programme` | `bonus_trigger`, `programme_status` |
| `equal_opportunity_survey` | `eeo_legal_basis` |
| `equipment_request` | `equipment_kind`, `equipment_request_status` |
| `fraud_signal` | `event_severity`, `trust_signal_type` |
| `msp_vendor` | `payment_term`, `vendor_tier` |
| `onboarding_document_request` | `document_request_status`, `document_type` |
| `onboarding_document_submission` | `approval_status` |
| `onboarding_plan` | `onboarding_plan_status` |
| `onboarding_plan_task` | `onboarding_task_status`, `onboarding_task_type` |
| `onboarding_plan_template` | `employment_type` |
| `onboarding_plan_template_task` | `document_type`, `onboarding_assignee_role`, `onboarding_task_type` |
| `onboarding_task_assignment` | `onboarding_assignee_role` |
| `performance_feedback` | `feedback_kind` |
| `performance_review_cycle` | `performance_cycle_status` |
| `placement_drive` | `drive_status`, `drive_type`, `employment_type`, `salary_period` |
| `reference_check_request` | `referee_relationship`, `reference_check_status` |
| `reputation_list_entry` | `reputation_list_kind`, `reputation_verdict` |
| `scam_job_report` | `scam_report_category`, `scam_report_status` |
| `spam_rule` | `spam_rule_action`, `spam_rule_scope` |
| `spam_rule_hit` | `moderation_target_type`, `spam_rule_action` |
| `staffing_assignment` | `salary_period`, `staffing_assignment_status`, `staffing_end_reason`, `staffing_engagement_type` |
| `subprocessor_data_category` | `processed_data_category` |
| `talent_campaign` | `consent_type`, `talent_campaign_goal`, `talent_campaign_status` |
| `talent_segment` | `segment_kind` |
| `talent_sequence_step` | `notification_channel`, `sequence_step_kind` |
| `timesheet` | `timesheet_status` |
| `timesheet_approval` | `approval_status` |
| `timesheet_entry` | `timesheet_entry_kind` |
| `trust_score` | `trust_band` |
| `trust_score_history` | `trust_band` |
| `vendor_submission` | `salary_period`, `vendor_submission_status` |
| `verification_badge` | `verification_badge_kind` |
| `workforce_request` | `salary_period`, `staffing_engagement_type`, `work_type`, `workforce_request_status` |
| `workforce_request_skill` | `proficiency_level`, `skill_importance` |

## Tables

| Table | Kind | Delete | Volume | Description |
|---|---|---|---|---|
| `account_restriction` | entity | soft | medium | Enforcement restriction on a user/organisation (posting block, messaging block, suspension) with window and reason. |
| `appeal` | entity | soft | medium | Appeal against a restriction or moderation outcome (DSA-style statement of reasons and redress). |
| `apprentice_enrolment` | entity | soft | medium | An apprentice's enrolment in a programme. |
| `apprentice_stipend_schedule` | entity | hard | medium | Scheduled stipend instalment; payout executes outside the DB (reference only). |
| `apprenticeship_programme` | entity | soft | medium | Structured apprenticeship/internship/trainee programme (Apprentices Act / NATS-NAPS style scheme or employer programme). |
| `background_check_order` | entity | soft | medium | Background-screening order to an external vendor. Platform stores order reference, consent proof and outcome only (boundary, like candidate_identity_verification); no raw criminal/credit reports. |
| `background_check_result` | entity | hard | medium | Per-check outcome inside an order (identity, address, education...). Vendor reference and verdict only. |
| `campaign_enrolment` | entity | hard | high | A candidate's progress through a campaign sequence. Created only when the required consent exists. |
| `campaign_step_execution` | event | append_only | high | Append-only record that a step ran for an enrolment (idempotent per step). |
| `career_page_section` | entity | hard | medium | Content block of an employer career page (employer branding). |
| `contract_document` | entity | soft | medium | Contractual document attached to an assignment or organisation (MSA, SOW, work order, NDA). Versioned; signature via e_sign_request. |
| `data_processing_agreement` | entity | soft | medium | Data-processing agreement between the platform and an employer/customer (controller-processor) with transfer terms. |
| `dpa_subprocessor` | entity | hard | medium | Which sub-processors are authorised under a DPA (with transfer safeguard). |
| `drive_eligibility_rule` | entity | hard | medium | Objective eligibility rule of a drive. Rule kinds are limited to academic/skill criteria (see lookup); protected characteristics cannot be encoded. |
| `drive_institution` | entity | hard | medium | Institution invited to / participating in a drive; carries the TPO contact (placement officer). |
| `drive_registration` | entity | soft | medium | Student registration for a drive with a frozen eligibility evaluation. |
| `drive_result` | entity | soft | medium | Final drive decision per student (one per registration), linking to the offer/joining pipeline. |
| `drive_round` | entity | hard | medium | Selection round of a drive. |
| `drive_round_result` | entity | hard | medium | A student's result in one round. |
| `e_sign_request` | entity | soft | medium | Boundary record of an e-signature envelope (offer letter, joining forms, contract). Only vendor reference and status are stored - never signature images or keys. |
| `employer_referral` | entity | soft | medium | An employee's referral of a candidate to a job inside the employer's programme. Candidate may be off-platform (name+email) until they apply. |
| `employer_referral_bonus` | entity | soft | medium | Bonus accrued for a referral (staged by trigger). Payout executes in the employer's payroll; only reference stored. |
| `employer_referral_programme` | entity | soft | medium | Employer's employee-referral programme (rules, bonus). Distinct from profile.referral (platform invitations with credit rewards). |
| `equal_opportunity_response` | entity | hard | medium | A candidate's voluntary self-ID answers (race/ethnicity, gender identity, disability, veteran, caste category for statutory reporting). Special-category data: stored apart from application data, field-encrypted, readable only by the compliance role, NEVER joined to ranking, screening or recruiter views. |
| `equal_opportunity_survey` | entity | soft | medium | Definition of a voluntary equal-opportunity self-identification survey per jurisdiction/employer. Questions always include a 'prefer not to say' option. |
| `equipment_request` | entity | soft | medium | Equipment/access provisioning requested for a joiner (asset request). |
| `fraud_signal` | event | append_only | very_high | Detector output (velocity, duplicate content, disposable email, IP/device reuse) attached to a subject. Immutable; escalation goes to moderation_case. |
| `msp_vendor` | entity | soft | medium | A staffing vendor enrolled in a client's managed-service-provider / vendor-management programme with a tier (Bullhorn VMS / Beeline-style). Commercial relationship terms stay in client_relationship. |
| `onboarding_document_request` | entity | soft | medium | Document requested from the joiner (ID proof, degree, bank details form). |
| `onboarding_document_submission` | entity | hard | medium | File uploaded against a document request. Content lives in object storage (object_ref); only metadata and a content hash are stored. |
| `onboarding_plan` | entity | soft | medium | Per-joiner onboarding plan created after offer acceptance/joining record (Greenhouse Onboarding 'new hire', Workday onboarding event). Distinct from the flat jobs_applications.onboarding_task checklist, which it supersedes for employers that opt in. |
| `onboarding_plan_task` | entity | soft | medium | Instantiated task of one onboarding plan. Composite FK keeps the task in the same tenant as its plan. |
| `onboarding_plan_template` | entity | soft | medium | Employer's reusable onboarding checklist template (Workday Onboarding / Greenhouse Onboarding task templates). |
| `onboarding_plan_template_task` | entity | hard | medium | One checklist item inside a template; copied into onboarding_plan_task when a plan is instantiated. |
| `onboarding_task_assignment` | entity | hard | medium | Who owns a task (task_assignment): several owners possible (new hire, IT, manager). |
| `performance_feedback` | entity | soft | medium | Feedback on a staffing assignment inside a cycle (client-on-worker or worker-on-client). |
| `performance_review_cycle` | entity | soft | medium | Lightweight review cycle for an organisation's managed workers. |
| `placement_drive` | entity | soft | medium | Campus/pooled/virtual placement drive run by an employer (Naukri campus, TPO drive). |
| `reference_check_request` | entity | soft | medium | Reference request sent to a candidate-nominated referee (Greenhouse/Checkster-style). Referee contact is third-party personal data. |
| `reference_check_response` | entity | hard | medium | Referee's answers (structured). Encrypted because free-text opinions are personal data about the candidate. |
| `remote_workforce_team` | entity | soft | medium | A managed remote team a client assembles from staffing workers (distributed pod with core hours). |
| `reputation_list_entry` | entity | soft | medium | IP network / device fingerprint / email-domain reputation entry (allow/watch/block) fed by detectors and vendor feeds. Values are HMACs except IP networks. |
| `scam_job_report` | entity | soft | medium | User report that a specific job posting is a scam (Naukri/LinkedIn 'report job'). Specialised intake; escalates into administration.moderation_case which owns the decision workflow. |
| `spam_rule` | entity | soft | medium | Configurable spam/abuse rule (patterns, thresholds) per surface with versioning. |
| `spam_rule_hit` | event | append_only | very_high | A rule firing on content/account; append-only for tuning and appeals. |
| `staffing_assignment` | entity | soft | medium | A worker placed with a client (contract, contract-to-hire, managed remote team member): bill/pay rate, window, PO. Agency may be absent for platform-managed direct engagements. Non-overlap per worker is intentionally not enforced (concurrent part-time engagements are legitimate). |
| `student_cohort` | entity | soft | medium | A graduating batch of an institution (course + branch + year) maintained by the TPO. |
| `student_cohort_member` | entity | hard | medium | Student in a cohort with TPO-verified academic data used for drive eligibility. Academic data only: no caste/religion/gender fields are held here. |
| `subprocessor` | entity | soft | medium | Register of third-party sub-processors (cloud, e-sign, background-check, email vendors) per DPDP/GDPR transparency duty. |
| `subprocessor_data_category` | entity | hard | medium | Categories of personal data a sub-processor handles. |
| `talent_campaign` | entity | soft | medium | Nurture campaign to a segment (talent community, silver-medallist re-engagement). Requires a consent type so only opted-in candidates are enrolled. |
| `talent_segment` | entity | soft | medium | Saved audience of candidates for nurture campaigns (LinkedIn Talent Pools / Lever Nurture lists). Distinct from talent_pool (manual folders): segments can be rule-based. |
| `talent_segment_member` | entity | hard | high | Membership of a candidate in a static segment (or materialised dynamic result). |
| `talent_sequence` | entity | hard | medium | Ordered nurture steps of a campaign (A/B variants as separate sequences). |
| `talent_sequence_step` | entity | hard | medium | One step of a sequence: message, wait, task or tag. |
| `timesheet` | entity | soft | medium | Periodic timesheet of an assignment (weekly/monthly) with approval workflow. |
| `timesheet_approval` | event | append_only | medium | Approval decision on a timesheet submission (append-only decision log). |
| `timesheet_entry` | entity | hard | high | Hours booked on one day of a timesheet. |
| `trust_score` | entity | hard | medium | Current trust score of a user or organisation (0-100) computed from signals; consumed by ranking, posting limits and moderation queues. |
| `trust_score_history` | audit | append_only | high | Append-only history of trust score changes. |
| `vendor_submission` | entity | soft | medium | Vendor's candidate submission against a workforce request (client requisition vendor submission). Pay rate is vendor-confidential and hidden from the client by view. |
| `verification_badge` | entity | soft | medium | Visible trust badge granted to a user/organisation, with decision basis (identity vendor outcome, organisation document verification). |
| `workforce_request` | entity | soft | medium | Client request for a managed remote workforce / contract staff (headcount, skills, rate budget, overlap hours). Unlike job_requirement it is not a public posting. |
| `workforce_request_skill` | entity | hard | medium | Skill requirement of a workforce request. |
| `appeal_status` | lookup | hard | low | Lifecycle and outcome of an appeal. |
| `apprentice_status` | lookup | hard | low | State of an apprentice in a programme. |
| `background_check_outcome` | lookup | hard | low | Vendor outcome of a check or of the whole order (no raw report content). |
| `background_check_status` | lookup | hard | low | Lifecycle of a background-check order. |
| `background_check_type` | lookup | hard | low | Category of one verification inside a background-check order. |
| `background_check_vendor` | lookup | hard | low | Background-screening vendors (boundary: order reference and outcome only). |
| `bonus_trigger` | lookup | hard | low | Milestone at which a referral bonus accrues. |
| `campaign_enrolment_status` | lookup | hard | low | State of a candidate inside a campaign. |
| `career_section_kind` | lookup | hard | low | Content block types of an employer career page. |
| `contract_document_kind` | lookup | hard | low | Kind of contractual document attached to an assignment. |
| `document_request_status` | lookup | hard | low | State of a document requested from a joiner. |
| `dpa_status` | lookup | hard | low | State of a data-processing agreement. |
| `drive_registration_status` | lookup | hard | low | Student registration state in a drive. |
| `drive_result_decision` | lookup | hard | low | Final decision for a student in a drive. |
| `drive_round_kind` | lookup | hard | low | Type of a campus selection round. |
| `drive_round_outcome` | lookup | hard | low | Result of a candidate in one round. |
| `drive_status` | lookup | hard | low | Lifecycle of a placement drive. |
| `drive_type` | lookup | hard | low | Mode of a placement drive. |
| `e_sign_status` | lookup | hard | low | Lifecycle of an e-signature envelope as reported by the vendor. |
| `e_sign_vendor` | lookup | hard | low | E-signature vendors integrated at the boundary (no signature material is stored here). |
| `eeo_legal_basis` | lookup | hard | low | Legal basis under which voluntary equal-opportunity data is collected. |
| `eligibility_rule_kind` | lookup | hard | low | Objective academic/skill eligibility criteria for a drive. Protected attributes (gender, caste, religion, marital status, disability) are deliberately NOT available as rule kinds. |
| `employer_referral_status` | lookup | hard | low | State of an employee referral. |
| `equipment_kind` | lookup | hard | low | Kind of equipment or access provisioned for a joiner. |
| `equipment_request_status` | lookup | hard | low | Fulfilment state of an equipment request. |
| `feedback_kind` | lookup | hard | low | Who gives feedback on whom. |
| `onboarding_assignee_role` | lookup | hard | low | Function of the person who owns an onboarding task. |
| `onboarding_plan_status` | lookup | hard | low | Lifecycle of a joiner's onboarding plan. |
| `onboarding_task_status` | lookup | hard | low | State of one onboarding plan task. |
| `performance_cycle_status` | lookup | hard | low | State of a performance review cycle. |
| `processed_data_category` | lookup | hard | low | Category of personal data a sub-processor handles. |
| `programme_kind` | lookup | hard | low | Kind of early-career programme. |
| `programme_status` | lookup | hard | low | Lifecycle of a structured programme (apprenticeship, referral). |
| `referee_relationship` | lookup | hard | low | How the referee knows the candidate. |
| `reference_check_status` | lookup | hard | low | Lifecycle of a reference request. |
| `referral_bonus_status` | lookup | hard | low | State of a referral bonus. |
| `reputation_list_kind` | lookup | hard | low | Key type of an IP/device/domain reputation entry. |
| `reputation_verdict` | lookup | hard | low | Reputation decision for a list entry. |
| `restriction_kind` | lookup | hard | low | Kind of enforcement restriction on an account. |
| `restriction_status` | lookup | hard | low | Lifecycle of a restriction. |
| `scam_report_category` | lookup | hard | low | Reason a job posting is reported as scam/fraud. |
| `scam_report_status` | lookup | hard | low | Triage state of a scam report. |
| `segment_kind` | lookup | hard | low | How a talent segment gets its members. |
| `sequence_step_kind` | lookup | hard | low | Action of one step in a nurture sequence. |
| `spam_rule_action` | lookup | hard | low | Automatic action a spam rule triggers. |
| `spam_rule_scope` | lookup | hard | low | Surface a spam rule inspects. |
| `staffing_assignment_status` | lookup | hard | low | State of a worker's assignment. |
| `staffing_end_reason` | lookup | hard | low | Why an assignment ended. |
| `staffing_engagement_type` | lookup | hard | low | Commercial form of a staffing engagement. |
| `stipend_payment_status` | lookup | hard | low | State of one scheduled stipend payment (payout happens outside the DB). |
| `talent_campaign_goal` | lookup | hard | low | Objective of a talent-nurture campaign. |
| `talent_campaign_status` | lookup | hard | low | Lifecycle of a talent campaign. |
| `timesheet_entry_kind` | lookup | hard | low | Kind of time booked. |
| `timesheet_status` | lookup | hard | low | State of a weekly/monthly timesheet. |
| `transfer_mechanism` | lookup | hard | low | Cross-border transfer safeguard. |
| `trust_band` | lookup | hard | low | Banding of a numeric trust score. |
| `trust_signal_type` | lookup | hard | low | Detector signal types feeding fraud and trust scoring. |
| `vendor_submission_status` | lookup | hard | low | State of a vendor's candidate submission against a workforce request. |
| `vendor_tier` | lookup | hard | low | Client's preferred-vendor tier (MSP/VMS practice). |
| `verification_badge_kind` | lookup | hard | low | Visible trust badge types. |
| `workforce_request_status` | lookup | hard | low | Lifecycle of a managed-workforce request. |

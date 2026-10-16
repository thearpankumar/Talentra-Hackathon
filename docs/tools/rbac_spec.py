"""Rule-set specification for docs/13_rbac_matrix.xlsx (data only; assembled by build_rbac.py).

Cell grammar:  <letters>[*]|<enforcement>
  letters : subset of C R U D, or '-' (none).  C=INSERT, R=SELECT, U=UPDATE, D=DELETE (soft-delete = UPDATE deleted_at for soft entities).
  '*'     : scoped (own rows / own tenant / mapped organisations / own campus / public-published only), never 'all rows'.
  enforcement : G DB grant to a (persona/service) role, V restricted view or vw_ function, A application layer, T txn/constraint, N n/a;
                combined with '+' in order of strength (V+A = view plus app predicate).
Spec string grammar:  '@TEMPLATE ACT=cell ACT=cell ...'   (later tokens override the template)
Short actor keys: S super_admin, A admin, AA account_admin, G guest, C candidate, E employer, R recruiter, K consultant, P placement_officer.
"""

HUMAN = ["super_admin", "admin", "account_admin", "guest", "candidate", "employer", "recruiter", "consultant", "placement_officer"]
SHORT = dict(S="super_admin", A="admin", AA="account_admin", G="guest", C="candidate", E="employer", R="recruiter", K="consultant", P="placement_officer")
SERVICE = ["app_service", "auth_svc", "billing_svc", "audit_writer", "purge_svc", "compliance_svc",
           "etl_service", "analytics_reader", "audit_reader", "backup_operator", "migration_owner"]
ACTORS = HUMAN + SERVICE

# ---------------------------------------------------------------- templates (human actors only)
TPL = {
    "LOOKUP_PUBLIC": "S=CRU|A A=R|A AA=R|A G=R|A C=R|A E=R|A R=R|A K=R|A P=R|A",
    "LOOKUP_STAFF": "S=CRU|A A=R|A",
    "MASTER_PUBLIC": "S=CRU|A+T A=CRU|A AA=R|A G=R|A C=R|A E=R|A R=R|A K=R|A P=R|A",
    "MASTER_STAFF": "S=CRU|A A=CRU|A",
    "STAFF_ONLY": "S=CRU|A+T A=CRU|A+T",
    "STAFF_READ": "S=R|A A=R|A",
    "SYSTEM_ONLY": "S=R|A A=R|A",
    "REF_AUTHED_READ": "S=CRU|A A=R|V AA=R|V C=R|V E=R|V R=R|V K=R|V P=R|V",
    "PUBLIC_CONTENT": "S=CRU|A A=CRU|A AA=R|V G=R|V C=R|V E=R|V R=R|V K=R|V P=R|V",
    "AUTHED_CONTENT": "S=CRU|A A=CRU|A AA=R|V C=R|V E=R|V R=R|V K=R|V P=R|V",
    # candidate-owned
    "CAND_PRIVATE": "S=R|V A=R|V C=CRUD*|G+A",
    "CAND_VISIBLE": "S=R|V A=R|V C=CRUD*|G+A E=R*|V R=R*|V K=R*|V P=R*|V",
    "CAND_EVENT_OWN": "S=R|V A=R|V C=R*|V+A",
    # tenant (organisation) owned
    "TENANT_PRIVATE": "S=R|V A=R|V AA=R*|A E=CRUD*|G+A R=CRUD*|V+A",
    "TENANT_CONFIG": "S=R|A A=CRU|A AA=CRUD*|G+A E=R*|A R=R*|A",
    "TENANT_EVENT": "S=R|A A=R|A AA=R*|A E=R*|A R=R*|A",
    "TENANT_HR": "S=R|V A=R|V AA=R*|A E=CRUD*|G+A R=CRU*|V+A",
    # user owned (every authenticated persona owns its own rows)
    "USER_OWNED": "S=R|V A=R|V AA=CRUD*|G+A C=CRUD*|G+A E=CRUD*|G+A R=CRUD*|G+A K=CRUD*|G+A P=CRUD*|G+A",
    "USER_EVENT_OWN": "S=R|V A=R|V AA=R*|V+A C=R*|V+A E=R*|V+A R=R*|V+A K=R*|V+A P=R*|V+A",
    "UGC_PUBLIC": "S=R|A A=RU|A+T AA=CRUD*|G+A G=R*|V C=CRUD*|G+A E=CRUD*|G+A R=CRUD*|G+A K=CRUD*|G+A P=CRUD*|G+A",
    "PARTICIPANTS_ONLY": "AA=CRU*|G+A C=CRU*|G+A E=CRU*|G+A R=CRU*|G+A K=CRU*|G+A P=CRU*|G+A",
    "AUTH_SELF": "AA=C*|A C=C*|A E=C*|A R=C*|A K=C*|A P=C*|A",
    "ANALYTICS_ADMIN": "S=R|V A=R|V",
    "ANALYTICS_TENANT": "S=R|V A=R|V AA=R*|V E=R*|V R=R*|V",
    "GIG_PARTY": "S=R|A A=RU|A AA=R*|A C=CRU*|G+A E=CRU*|G+A",
    "GIG_EVENT": "S=R|A A=R|A AA=R*|A C=R*|A E=R*|A",
}

# ---------------------------------------------------------------- explicit groups (first match wins; order = rule id)
# (rule_id, description, owner_scope, spec, [table names])
GROUPS = []


def G(desc, owner, spec, names):
    GROUPS.append((f"RB-{len(GROUPS) + 1:02d}", desc, owner, spec, [n.strip() for n in names.replace("\n", " ").split(",") if n.strip()]))


# ---- administration -------------------------------------------------------------------------------------------------
G("Account identity row: self-service read/update of own row; registration constrained by registration_policy composite FK (BR-ADM-01); staff see only the masked view vw_app_user_public",
  "user", "@USER_OWNED C=RU*|G+A E=RU*|G+A R=RU*|G+A K=RU*|G+A P=RU*|G+A AA=CRU*|T+A G=C|T A=CRU|V+T S=CRU|V+T",
  "app_user")
G("Password credential: only the owner may set a new one (via auth service, step-up MFA); never readable by any human actor or view (BR-ADM-04)",
  "user", "@AUTH_SELF AA=CU*|A C=CU*|A E=CU*|A R=CU*|A K=CU*|A P=CU*|A G=C|A",
  "user_credential")
G("One-time secrets (password reset / e-mail verification tokens): requested by the owner or by an unauthenticated visitor (forgot password), redeemed by auth_svc only, hashes never returned",
  "user", "@AUTH_SELF G=C|A",
  "password_reset_token, email_verification_token")
G("MFA recovery codes: generated for the authenticated owner, shown once, hashes never returned",
  "user", "@AUTH_SELF",
  "mfa_recovery_code")
G("MFA factor: owner enrols/disables own factors; secret ciphertext readable only inside auth_svc",
  "user", "@AUTH_SELF AA=CUD*|A C=CUD*|A E=CUD*|A R=CUD*|A K=CUD*|A P=CUD*|A",
  "mfa_factor")
G("Refresh-token family: no human reads; staff may revoke (UPDATE) through auth_svc, audited (BR-ADM-16)",
  "user", "A=U|A+T",
  "identity_session")
G("Append-only credential history: written by auth_svc only",
  "user", "",
  "user_credential_history")
G("API client/key: organisation integration credentials; account_admin manages, key plaintext shown once, hash only stored (BR-ADM-24)",
  "account", "AA=CRUD*|G+A E=R*|V+A A=R|V S=R|V",
  "api_client")
G("API key hashes: created/revoked by account_admin via auth_svc; no read of hashes",
  "account", "AA=CU*|A A=U|A+T",
  "api_key")
G("Authorisation catalogue (roles, permissions, hierarchy, eligibility, registration/block policy): super_admin only; admin read; four-eyes via admin_action_approval",
  "platform", "S=CRU|A+T A=R|A",
  "app_role, app_role_inheritance, app_role_eligibility, permission_group, permission, app_role_permission, registration_policy, block_policy")
G("Role assignment: super_admin full; admin non-admin roles; account_admin only organisation-scoped roles of own account; holders read own roles (BR-ADM-05/06, role_grant audited)",
  "platform", "S=CRU|A+T A=CRU|A+T AA=CRU*|A+T C=R*|A E=R*|A R=R*|A K=R*|A P=R*|A",
  "app_user_role")
G("Consent documents: staff manage, everyone reads the current text",
  "platform", "S=CRU|A A=CRU|A AA=R|A G=R|A C=R|A E=R|A R=R|A K=R|A P=R|A",
  "consent_document")
G("Consent events (append-only): the user grants/withdraws own consent; staff read via vw_user_consent_current (BR-ADM-15)",
  "user", "S=R|V A=R|V AA=CR*|T+A C=CR*|T+A E=CR*|T+A R=CR*|T+A K=CR*|T+A P=CR*|T+A",
  "user_consent")
G("Blocking: candidate blocks employers/recruiters/consultants; organisations block candidates; blocked party has no read path (BR-ADM-12/13/14)",
  "user", "S=R|A A=R|A AA=CRU*|T+A C=CRU*|T+A E=CRU*|T+A",
  "user_block, organisation_block")
G("Data subject requests: any authenticated user raises and tracks own request; staff process under SLA",
  "user", "S=R|A A=CRU|A+T AA=CR*|A C=CR*|A E=CR*|A R=CR*|A K=CR*|A P=CR*|A",
  "data_subject_request")
G("Governance policy objects (retention policy, legal hold): super_admin writes, admin reads; purge_svc reads",
  "platform", "S=CRU|A+T A=R|A",
  "data_retention_policy, legal_hold")
G("Privileged workflows with different requester/approver (CHECK): admin requests, super_admin approves; both audited",
  "platform", "S=CRU|T+A A=CRU|T+A",
  "admin_action_approval, privileged_access_request, user_merge_request, duplicate_candidate_match")
G("Support impersonation: admin starts (write mode needs approval), super_admin reviews (BR-ADM-19)",
  "platform", "S=R|A A=CRU|T+A",
  "impersonation_session")
G("Moderation cases: admin handles, super_admin reads; reporters file through report tables elsewhere",
  "platform", "S=R|A A=CRU|A", "moderation_case, moderation_case_action")
G("Audit tier: append-only, staff read through the redacted view; full table only through approved audit_export",
  "platform", "S=R|V A=R|V", "audit_event, audit_chain_checkpoint, security_event, error_log, user_status_history, purge_job_run, audit_chain_head")
G("Audit export (approval required, exported file itself audited)",
  "platform", "S=CR|T+A A=CR|T+A", "audit_export")
G("Trusted IP list of an account: account_admin manages, staff read",
  "account", "AA=CRUD*|G+A A=R|A S=R|A", "trusted_ip")
G("Legacy source-system registry (connection reference only, no secrets): super_admin registers/changes, admin reads; the sync service reads it",
  "platform", "S=CRU|A+T A=R|A", "legacy_source_system")
G("Legacy sync runs, record map and conflict log (written by the sync service; conflicts append-only): staff read, admin resolves conflicts as new rows",
  "platform", "S=R|A A=R|A", "legacy_sync_run, legacy_record_map, legacy_sync_conflict")

# ---- licences, devices -----------------------------------------------------------------------------------------------
G("Licence header: account/organisation admins read; staff and billing create/update (limits are commercial data)",
  "account", "S=CRU|A+T A=CRU|A AA=R*|A E=R*|A", "licence")
G("Licence slots (pre-created counters): system-maintained inside seat/limit transactions; staff read",
  "account", "S=R|A A=R|A AA=R*|V", "licence_seat_slot, device_slot")
G("Licence seat: account_admin assigns/revokes (BR-LIC-03); seat holder reads own; staff correct",
  "account", "S=R|A A=CRU|A+T AA=CRU*|T+A C=R*|A E=R*|A R=R*|A K=R*|A P=R*|A", "licence_seat")
G("Seat invitation (token hash): account_admin issues; invitee redeems with the token",
  "account", "S=R|V A=R|V AA=CRU*|A G=U|A", "licence_user_invitation")
G("Licence history/usage/transfer (append/snapshot): account_admin reads own, staff read",
  "account", "S=R|A A=R|A AA=R*|A", "licence_status_history, licence_usage_snapshot")
G("Licence transfer and feature override: account_admin requests, staff approve",
  "account", "S=CRU|A+T A=CRU|A+T AA=CR*|A", "licence_transfer, licence_feature_override")
G("Licence IP allowlist: account_admin manages",
  "account", "S=R|A A=R|A AA=CRUD*|G+A", "licence_ip_allowlist")
G("Registered device: owner lists and revokes own devices; staff read masked (BR-LIC-09)",
  "user", "S=R|V A=R|V AA=RU*|V+A C=RU*|V+A E=RU*|V+A R=RU*|V+A K=RU*|V+A P=RU*|V+A", "device")
G("Device session and login history: owner and licence admin read through vw_active_device_session / vw_login_history; written by auth_svc only",
  "user", "S=R|V A=R|V AA=R*|V C=R*|V E=R*|V R=R*|V K=R*|V P=R*|V",
  "device_session, session_activity_sample, login_attempt")
G("Revocation / limit-violation evidence (append-only): owner and licence admin read; staff read; written by auth_svc in the revoke/login transaction",
  "user", "S=R|V A=R|V AA=R*|V C=R*|V E=R*|V R=R*|V K=R*|V P=R*|V",
  "device_revocation, session_revocation, device_limit_violation")
G("Device challenge code (hash only): auth_svc internal",
  "user", "", "device_trust_challenge")
G("Push registration: owner registers/removes own device token; dispatcher reads",
  "user", "@USER_OWNED A=R|V S=R|V", "push_registration")

# ---- master data / tier3 ---------------------------------------------------------------------------------------------
G("Employer name canonicalisation (data-quality internals): staff only",
  "platform", "@MASTER_STAFF", "employer_name_canonical, employer_name_alias")
G("Feature flags and their audit: staff only; services read flags through vw_feature_flag_effective",
  "platform", "@STAFF_ONLY", "feature_flag, feature_flag_rule, feature_flag_override, feature_flag_audit")
G("Reference data provenance: staff read",
  "platform", "@STAFF_READ", "reference_data_source, reference_data_refresh")

# ---- profile ---------------------------------------------------------------------------------------------------------
G("Candidate profile header: candidate owns; employers/recruiters/consultants/TPO read only through vw_candidate_profile_recruiter filtered by vw_candidate_visible_to_user (blocked parties invisible; salary/contact masked) (BR-PRF-06, BR-ADM-12). Nobody but the candidate or an admin can create it (BR-ADM-01)",
  "candidate", "@CAND_VISIBLE A=RU|V+A S=R|V",
  "candidate_profile")
G("Profile sections that recruiters may see (education, experience, skills, projects, certifications, resumes, contact rows masked): view-only reads",
  "candidate", "@CAND_VISIBLE",
  """candidate_contact_phone, candidate_contact_email, candidate_address, candidate_social_link, candidate_language, candidate_education,
  candidate_experience, candidate_skill, candidate_skill_endorsement, candidate_project, candidate_project_skill, candidate_certification,
  candidate_award, candidate_publication, candidate_patent, candidate_volunteer_experience, candidate_test_score, candidate_recommendation,
  candidate_follow, candidate_job_preference, candidate_preferred_location, candidate_preferred_role, candidate_preferred_industry,
  candidate_preferred_employment_type, candidate_preferred_work_type, candidate_open_to_work_signal, candidate_media_file, resume""")
G("Candidate-only profile data (identity verification token, work authorisation, special-category declaration, import log, parse output, erasure, completion): no recruiter path; staff masked/compliance only",
  "candidate", "@CAND_PRIVATE",
  """candidate_identity_verification, candidate_work_authorisation, candidate_diversity_declaration, candidate_erasure_request, profile_import,
  resume_parse_result, candidate_self_assessment, candidate_profile_section, referral, referral_reward, course_enrolment, assessment_attempt""")
G("Profile activity trail (append-only): candidate reads own history; recruiters create view events as a side effect of an entitled view (billing writes credit linkage)",
  "candidate", "@CAND_EVENT_OWN E=C*|A R=C*|A K=C*|A",
  "candidate_profile_view, candidate_activity_log, candidate_profile_completion_snapshot")
G("Recruiter workspace on candidates (folders, saved items, history): tenant-private; candidate has no read path; blocked-candidate sweep removes items (BR-SRC-15)",
  "org", "@TENANT_PRIVATE",
  "candidate_shortlist_folder, candidate_shortlist_item, candidate_shortlist_history")
G("Learning/assessment catalogue and resume templates in the profile module: staff-managed, public read to authenticated",
  "platform", "@AUTHED_CONTENT",
  "course_provider, course, assessment, resume_template")
G("Profile scoring rules: staff only", "platform", "@STAFF_ONLY", "profile_completion_rule")

# ---- jobs & applications ---------------------------------------------------------------------------------------------
G("Published job requirement and public children: employer tenant edits; recruiters only through vw_recruiter_job_requirement (mapped organisations); consultants only through vw_consultant_job_requirement (open-to-consultant windows); candidates/guests/TPO read live listings via vw_live_job_requirement (BR-ORG-03, BR-SRC-01)",
  "org", "S=R|A A=RU|A+V AA=R*|A G=R*|V C=R*|V E=CRUD*|G+A R=CRU*|V+A K=R*|V P=R*|V",
  """job_requirement, job_requirement_skill, job_requirement_location, job_requirement_qualification, job_requirement_benefit,
  job_requirement_screening, questionnaire, questionnaire_question, questionnaire_question_option, job_walk_in_session""")
G("Job internals (templates, positions, assignment, approvals, history, revisions, publishing/syndication, boosts, counters): tenant only; recruiters via mapping view; no candidate/consultant path",
  "org", "S=R|A A=R|A AA=R*|A E=CRUD*|G+A R=CRU*|V+A",
  """job_template, job_position, job_requirement_assignment, job_requirement_approval, job_requirement_status_history, job_requirement_revision,
  job_publish_history, job_syndication, job_boost, job_posting_channel, job_requirement_stat""")
G("Consultant openness window (jobs side): employer opens, consultant sees only live windows through vw_consultant_job_requirement (BR-SRC-02)",
  "org", "S=R|A A=R|A E=CRUD*|T+A R=CRU*|T+V K=R*|V",
  "job_consultant_openness")
G("Application and candidate-visible pipeline artefacts: candidate applies/withdraws own; employers and mapped recruiters read/update through vw_application_pipeline (blocked-candidate anti-join); TPO sees campus students' statuses only",
  "candidate", "S=R|V A=R|V AA=R*|V C=CRU*|G+A E=RU*|V+A R=RU*|V+A K=R*|V P=R*|V",
  """job_application, job_application_document, questionnaire_answer, questionnaire_answer_option, job_application_status_history,
  interview, interview_slot, interview_reschedule, offer, offer_revision, offer_component, offer_letter, joining, onboarding_task, rejection""")
G("Employer-internal evaluation artefacts: notes, scorecards, feedback, panels, screening results, approvals, status matrix; candidate never reads",
  "org", "S=R|V A=R|V AA=R*|V E=CRUD*|G+A R=CRU*|V+A",
  """application_note, application_scorecard, application_scorecard_score, application_view_event, interview_feedback, interview_panel,
  interview_panel_member, interview_interviewer, offer_approval, job_application_screening_result, application_status_transition""")
G("Recruiting workspace (job shortlists, talent pools): tenant-private, holds candidate Personal data; blocked-candidate sweep (BR-SRC-15)",
  "org", "@TENANT_PRIVATE K=CRUD*|A",
  "job_shortlist, job_shortlist_item, talent_pool, talent_pool_member")
G("Candidate-owned job-search data: saved jobs, search history (append-only), reports from any user, share events from any visitor",
  "candidate", "S=R|V A=R|V C=CRUD*|G+A",
  "job_bookmark, candidate_job_search_history")
G("Job view/share events: any visitor, including guests, creates events (rate limited); staff read",
  "public", "S=R|A A=R|A G=C|A C=C|A E=C|A R=C|A K=C|A P=C|A AA=C|A",
  "job_view_event, job_share_event")
G("Job report (abuse/scam): any authenticated user files; admin triages",
  "user", "S=R|A A=RU|A+T AA=CR*|A C=CR*|A E=CR*|A R=CR*|A K=CR*|A P=CR*|A",
  "job_report")

# ---- organisation & account ------------------------------------------------------------------------------------------
G("Organisation public page: employer/account_admin edit own; public reads only the live public projection vw_organisation_public (no identifiers, notes)",
  "org", "S=CRU|A A=CRU|A AA=CRU*|G+A G=R*|V C=R*|V E=CRU*|G+A R=R*|V K=R*|V P=R*|V",
  """organisation, organisation_location, organisation_social_link, organisation_media, organisation_benefit, organisation_brand,
  organisation_career_page, organisation_industry, organisation_rating_summary""")
G("Organisation identifiers, verification, documents, verified domains (Sensitive): owner reads masked values only; staff masked; decrypt only in verification/compliance service with audit (BR-ORG-14)",
  "org", "S=R|V A=CRU|V+T AA=CR*U*|V+A E=CR*U*|V+A",
  "organisation_identifier, organisation_verification, organisation_document, organisation_domain")
G("Organisation contacts, departments, hierarchy, client relationships: tenant managed",
  "org", "S=R|A A=CRU|A AA=CRUD*|G+A E=CRUD*|G+A R=R*|A",
  "organisation_contact, organisation_department, organisation_hierarchy, organisation_hierarchy_closure, client_relationship")
G("Organisation history (append-only): tenant reads, staff read",
  "org", "S=R|A A=R|A AA=R*|A E=R*|A", "organisation_hierarchy_change, organisation_size_history, organisation_status_history, account_status_history")
G("Staff-only organisation data-quality objects (notes, tags, aliases, merges)",
  "platform", "S=CRU|A+T A=CRU|A+T", "organisation_note, organisation_tag, organisation_tag_assignment, organisation_alias, organisation_merge")
G("Commercial account and its links: account_admin manages own; employer reads; staff manage",
  "account", "S=R|A A=CRU|A AA=CRU*|T+A E=R*|A", "account, account_organisation, account_admin, account_setting")
G("Recruiter-to-company mapping: account_admin opens/closes mappings (chain constraint BR-ORG-02); recruiter reads own; staff can correct",
  "org", "S=R|A A=CRU|A+T AA=CRU*|T+A E=R*|A R=R*|A", "recruiter_organisation_mapping")
G("Organisation invitation (token hash): account_admin/employer invite, invitee redeems",
  "org", "S=R|V A=R|V AA=CRU*|A E=CRU*|A G=U|A", "organisation_invitation")
G("Company reviews: authenticated users write own review; public sees moderated rows via vw_organisation_review_public (reviewer hidden); employer replies",
  "user", "S=R|A A=RU|A+T AA=R*|V G=R*|V C=CRUD*|G+A E=CR*U*|A R=R*|V K=CRUD*|G+A P=CRUD*|G+A",
  "organisation_review, organisation_review_response, organisation_review_vote, organisation_follow")
G("Review moderation log (append-only)", "platform", "S=R|A A=CR|A", "organisation_review_moderation")

# ---- gig marketplace --------------------------------------------------------------------------------------------------
G("Gig worker profile and offers (worker = candidate persona): worker owns; requesters read vw_gig_worker_public only (KYC token hidden)",
  "gig_worker", "S=R|V A=R|V AA=R*|V C=CRUD*|G+A E=R*|V",
  """gig_worker_profile, gig_worker_skill, gig_worker_language, gig_worker_certification, gig_worker_portfolio_item, gig_worker_project,
  gig_service_offering, gig_service_package, gig_service_offering_skill, gig_availability_rule, gig_availability_window, gig_time_off,
  gig_search_alert, gig_earnings_snapshot, gig_worker_rating_summary, gig_payout_reference""")
G("Gig request: requester organisation owns; open public requests visible via vw_gig_open_request to candidates and guests",
  "org", "S=R|A A=RU|A AA=R*|A G=R*|V C=R*|V E=CRUD*|G+A",
  "gig_request, gig_request_skill, gig_request_attachment, gig_request_milestone, gig_requester_worker_list")
G("Gig bid/invite/assignment/rating/cancellation/timesheet/report: parties only",
  "gig_worker", "@GIG_PARTY", "gig_application, gig_invite, gig_assignment, gig_rating, gig_cancellation, gig_timesheet_entry, gig_report")
G("Gig dispute: parties raise/respond, admin resolves with four-eyes if money moves",
  "gig_worker", "@GIG_PARTY A=RU|A+T", "gig_dispute")
G("Gig histories and fulfilment events (append-only): parties read",
  "gig_worker", "@GIG_EVENT", "gig_request_status_history, gig_assignment_status_history, gig_fulfilment_event, gig_fulfilment_event_attempt")
G("Gig slot rows: system maintained in the assignment transaction (overlap guard)",
  "gig_worker", "S=R|A A=R|A", "gig_slot")

# ---- sourcing & consultant ------------------------------------------------------------------------------------------
G("Consultant profile and desk: consultant owns; employers/recruiters read a public projection; admin verifies",
  "consultant", "S=R|A A=RU|A K=CRUD*|G+A E=R*|V R=R*|V",
  "consultant_profile, consultant_specialisation, consultant_desk")
G("Consultant rating by employer", "consultant", "S=R|A A=RU|A K=R*|A E=CR*|A", "consultant_rating")
G("Sourcing request: only against an open openness row of the same requirement (composite FK, BR-SRC-01); employer decides",
  "org", "S=R|A A=R|A K=CRU*|T+V E=RU*|T+A R=R*|V", "sourcing_request")
G("Sourcing decisions (append-only, reasoned): employer decides, consultant reads",
  "org", "S=R|A A=R|A K=R*|V E=CR*|T+A", "sourcing_request_decision")
G("Consultant shortlist/submission and employer review: consent FK required (BR-SRC-11)",
  "consultant", "S=R|A A=R|A K=CRUD*|T+A E=RU*|V+A R=R*|V", "sourcing_shortlist, sourcing_shortlist_item, sourcing_submission")
G("Commission on a hire: parties read, staff administer, posting by billing",
  "consultant", "S=R|A A=CRU|A K=R*|A E=R*|A AA=R*|A", "sourcing_commission")
G("Outreach templates/batches/recipients: sender tenant or consultant owns; blocked recipients rejected at insert (BR-SRC-06); sending requires credits via billing",
  "org", "S=R|V A=R|V AA=R*|A E=CRUD*|G+A R=CRUD*|V+A K=CRUD*|V+A",
  "outreach_template, outreach_template_translation, outreach_batch, outreach_recipient, outreach_exclusion")
G("Outreach messages (append-only, body TTL 90 d): sender reads; recipient candidate reads own inbox",
  "org", "S=R|V A=R|V E=R*|V+A R=R*|V+A K=R*|V+A C=R*|V+A", "outreach_message")
G("Outreach replies (append-only): candidate writes reply, sender reads",
  "org", "S=R|V A=R|V E=R*|V+A R=R*|V+A K=R*|V+A C=CR*|A", "outreach_reply")
G("Outreach credit usage (ledger-like, append-only): posted with the wallet ledger by billing_svc",
  "org", "S=R|A A=R|A AA=R*|A E=R*|A R=R*|A K=R*|A", "outreach_credit_usage")
G("Saved searches and their runs / search history: sender-owned; history is append-only",
  "org", "S=R|V A=R|V E=CRUD*|G+A R=CRUD*|G+A K=CRUD*|G+A",
  "saved_search, saved_search_run, candidate_search_history")
G("Recruiter-private candidate notes/tags/ratings: author/tenant only; the candidate cannot read them but erasure anonymises (BR-SRC-15)",
  "org", "S=R|V A=R|V E=CRUD*|G+A R=CRUD*|G+A K=CRUD*|G+A",
  "candidate_note, candidate_tag, candidate_tag_assignment, candidate_rating")
G("Candidate contact attempts (append-only): sender reads, candidate sees who contacted them",
  "candidate", "S=R|V A=R|V C=R*|V E=R*|V R=R*|V K=R*|V", "candidate_contact_attempt")

# ---- subscriptions & money --------------------------------------------------------------------------------------------
G("Public pricing catalogue (plans, versions, prices, entitlements, credit packs, rate cards): staff manage, everybody reads",
  "platform", "S=CRU|A+T A=CRU|A AA=R|A G=R|V C=R|V E=R|V R=R|V K=R|V P=R|V",
  "subscription_plan, plan_version, plan_price, plan_entitlement, credit_rate_card, credit_pack, credit_pack_price")
G("Tax rates, FX rates, invoice number series: finance reference, staff only (series row is billing-locked)",
  "platform", "S=CRU|A+T A=R|A", "tax_rate, currency_exchange_rate, invoice_number_series")
G("Coupons: staff manage; buyers redeem",
  "platform", "S=CRU|A+T A=CRU|A AA=R|A E=R|A", "coupon")
G("Coupon redemption (append-only): account/employer redeem in the billing transaction",
  "account", "S=R|A A=R|A AA=CR*|T+A E=CR*|T+A", "coupon_redemption")
G("Subscription contract and periods: purchase/renewal run in billing_svc (CHAIN_FK BR-SUB-03); account side reads",
  "account", "S=R|A A=R|A AA=R*|V+A E=R*|A",
  "subscription, subscription_period, subscription_status_history, subscription_entitlement, subscription_renewal_attempt")
G("Subscription cancellation: account_admin requests, staff process",
  "account", "S=R|A A=CRU|A AA=CRU*|T+A E=R*|A", "subscription_cancellation")
G("Wallet and ledger family: account/employer/recruiter read their own organisation's wallet; every write is billing_svc (BR-SUB-08); staff adjustment is a new ledger row under four-eyes (BR-SUB-07)",
  "org", "S=R|V A=R|V AA=R*|V+A E=R*|V+A R=R*|V+A K=R*|V+A",
  "wallet, wallet_expiry_bucket, wallet_bucket_consumption")
G("Wallet ledger entries (append-only hash chain): readable per tenant; staff adjustment as appended reversal/adjust rows",
  "org", "S=R|V A=CR|T+A AA=R*|V+A E=R*|V+A R=R*|V+A K=R*|V+A", "wallet_ledger_entry")
G("Spend limits: account_admin configures per actor/licence",
  "account", "S=R|A A=R|A AA=CRU*|T+A E=R*|A", "spend_limit")
G("Payment orders/attempts/outcomes: buyer creates order with idempotency key; gateway webhooks processed by billing_svc; staff read",
  "account", "S=R|A A=R|A AA=CR*|T+A E=CR*|T+A", "payment_order, payment_attempt, payment_event_outcome")
G("Raw payment events (Sensitive, encrypted payload): billing_svc and reconciliation only",
  "platform", "", "payment_event")
G("Refunds: account requests, admin approves (approver differs from requester, CHECK), super_admin can approve higher tiers",
  "account", "S=CRU|T+A A=CRU|T+A AA=CR*|A E=R*|A", "refund")
G("Stored payment instruments (gateway tokens only): account_admin adds/removes; others see last4 via view",
  "account", "S=R|V A=R|V AA=CRD*|V+A E=R*|V", "payment_method_token")
G("Tax registrations (encrypted + blind index): account_admin manages, masked reads",
  "account", "S=R|V A=R|V AA=CRU*|V+A E=R*|V", "tax_registration")
G("Billing address (Personal) and invoices/credit notes: account side reads own",
  "account", "S=R|V A=R|V AA=CRUD*|G+A E=R*|V+A", "billing_address")
G("Invoices and tax lines (immutable after issue): account side reads own, staff read",
  "account", "S=R|A A=R|A AA=R*|V+A E=R*|V+A", "invoice, invoice_line, invoice_tax_line, credit_note")
G("Profile download history (append-only; actor, subject, time, credits): tenant reads own via vw_organisation_download_spend, staff read; written with the ledger row",
  "org", "S=R|A A=R|A AA=R*|V E=R*|V R=R*|V K=R*|V", "profile_download")
G("Consultant payouts (payout_ref token): consultant reads own, finance staff read, billing_svc writes",
  "consultant", "S=R|V A=R|V K=R*|V", "consultant_payout")
G("Reconciliation (gateway vs orders): finance staff resolve exceptions",
  "platform", "S=R|A A=RU|A+T", "reconciliation_run, reconciliation_item")

# ---- notifications & community --------------------------------------------------------------------------------------
G("Notification catalogue and providers: staff only (webhook secret stored as vault reference)",
  "platform", "@STAFF_ONLY", "notification_type, notification_template, notification_template_translation, notification_provider, support_sla_policy, in_app_survey, in_app_survey_question")
G("Notification inbox: recipient reads and marks read; created by the notification service",
  "user", "S=R|V A=R|V AA=RU*|V+A C=RU*|V+A E=RU*|V+A R=RU*|V+A K=RU*|V+A P=RU*|V+A", "notification")
G("Delivery path (append-only, provider receipts): staff/ops read; recipient never reads provider internals",
  "platform", "S=R|V A=R|V",
  "notification_delivery, notification_delivery_event, provider_webhook_event, notification_delivery_daily_summary, digest_batch, digest_batch_item")
G("Suppression list and unsubscribe tokens: unauthenticated unsubscribe by token (hash lookup); owner sees own status",
  "user", "S=R|V A=R|V G=C|A C=R*|A E=R*|A R=R*|A K=R*|A P=R*|A AA=R*|A", "notification_suppression")
G("Unsubscribe token (hash only): redeemed by an unauthenticated visitor following the e-mail link",
  "user", "S=R|V A=R|V G=U|A", "unsubscribe_token")
G("User preferences (notification, quiet hours, language, privacy, hidden organisations, alerts): owner only; recruiters honour privacy_setting only through vw_recruiter_searchable_candidate",
  "user", "@USER_OWNED",
  """notification_preference, quiet_period, language_preference, privacy_setting, privacy_hidden_organisation, job_alert_preference,
  job_alert_skill, job_alert_location, push_subscription""")
G("Job alert runs and matches (append-only, system written): owner reads",
  "user", "@USER_EVENT_OWN", "job_alert_run, job_alert_match, referral_event")
G("Public FAQ/announcement content: staff author, everyone reads",
  "platform", "@PUBLIC_CONTENT",
  "faq_category, faq_item, faq_item_translation, announcement, announcement_translation")
G("FAQ feedback and announcement dismissal: any visitor/user records",
  "user", "S=R|A A=R|A G=C|A AA=C*|A C=C*|A E=C*|A R=C*|A K=C*|A P=C*|A", "faq_feedback, announcement_dismissal")
G("Community content (groups, posts, comments, reactions, polls, hashtags, follows): authors own; public posts via view; admin moderates (soft-removes)",
  "user", "@UGC_PUBLIC",
  """community_group, community_group_member, community_post, community_post_stat, community_post_media, community_comment, community_reaction,
  community_share, community_follow, community_poll, community_poll_option, community_poll_vote, hashtag, community_post_hashtag,
  community_mention, feed_item""")
G("Community and message reports: reporter files, admin triages",
  "user", "S=R|A A=RU|A AA=CR*|A C=CR*|A E=CR*|A R=CR*|A K=CR*|A P=CR*|A", "community_report")
G("Moderation action log and status histories (append-only): admin writes in the action transaction",
  "platform", "S=R|A A=CR|A", "moderation_action_log")
G("Private messaging: participants only; admin has NO read path (break-glass only, audited)",
  "user", "@PARTICIPANTS_ONLY", "message_thread, message_thread_participant, message, message_attachment, message_read_receipt")
G("Support tickets: requester raises/follows own; admin (support) works tickets; super_admin reads",
  "user", "S=R|A A=CRU|A AA=CR*U*|A C=CR*U*|A E=CR*U*|A R=CR*U*|A K=CR*U*|A P=CR*U*|A",
  "support_ticket, support_ticket_message, support_ticket_attachment, support_csat_response, support_ticket_status_history")
G("In-app survey answers: user answers once; staff read aggregated",
  "user", "S=R|A A=R|A AA=C*|A C=C*|A E=C*|A R=C*|A K=C*|A P=C*|A", "in_app_survey_response, in_app_survey_answer")

# ---- tier 3 snapshots -----------------------------------------------------------------------------------------------
G("Published reference snapshots (salary, ratings, trends): authenticated read via vw_*_published/latest; ETL refreshes",
  "platform", "@REF_AUTHED_READ",
  "salary_benchmark, salary_benchmark_source, company_rating_snapshot, trending_job_snapshot, skill_demand_snapshot, industry_growth_snapshot, location_cost_index, dashboard_aggregate")

# ---- extension: network ---------------------------------------------------------------------------------------------
G("Network graph caches and internals (PYMK, second degree, graph stats, sitemap, contact match hashes): system only",
  "platform", "@SYSTEM_ONLY",
  """pymk_candidate, pymk_candidate_reason, network_second_degree, mutual_connection_cache, network_graph_stat, network_cache_config,
  profile_visitor_insight_weekly, sitemap_entry, contact_match_key, reserved_slug, public_profile_slug_history""")
G("Public network content (newsletters, events, speakers, rooms, organisation updates, alumni pages): authors own, public reads",
  "user", "@UGC_PUBLIC",
  """newsletter, newsletter_issue, event, event_session, event_speaker, event_ticket_type, live_room, organisation_update,
  institution_alumni_page, event_reminder, event_invite""")
G("Imported address-book contacts of third parties (high PII risk): importer only, never shared, TTL",
  "user", "S=R|A A=R|A AA=CRUD*|G+A C=CRUD*|G+A E=CRUD*|G+A R=CRUD*|G+A K=CRUD*|G+A P=CRUD*|G+A", "contact_import, contact_import_entry")
G("Public profile slug/visibility/share links: owner manages, anyone with the link visits (visit events append-only)",
  "user", "@USER_OWNED G=C|A", "profile_share_link, profile_share_visit, public_profile_slug, public_profile_setting, invite_link_redemption")

# ---- extension: matching --------------------------------------------------------------------------------------------
G("Candidate-facing recommendations: owner reads own feed via vw_candidate_job_recommendation_live",
  "candidate", "S=R|V A=R|V C=R*|V", "job_recommendation, job_recommendation_reason, similar_job, user_interest_affinity")
G("Recruiter-facing candidate suggestions: tenant reads via visibility view only",
  "org", "S=R|V A=R|V E=R*|V R=R*|V K=R*|V", "candidate_suggestion, candidate_suggestion_reason, similar_candidate")
G("Recommendation feedback events (append-only): the user's own interactions are recorded",
  "user", "S=R|V A=R|V C=C*|A E=C*|A R=C*|A K=C*|A", "recommendation_impression, recommendation_click_event, recommendation_feedback, resume_parse_feedback")
G("Search/ranking/ML configuration and logs: staff and curators only",
  "platform", "@STAFF_ONLY",
  """recommendation_model, recommendation_model_version, recommendation_model_deployment, search_typeahead_term, search_synonym_group,
  search_synonym_term, search_stopword, search_boost_rule, ranking_profile, ranking_rule, ab_metric, ab_experiment, ab_variant, interest_topic,
  raw_title_normalisation""")
G("ML internals and logs (embeddings, features, scores, quality, risk, query logs): system only; staff read",
  "platform", "@SYSTEM_ONLY",
  """embedding_ref, candidate_feature_snapshot, job_feature_snapshot, match_score, match_score_component, skill_supply_demand_signal,
  search_query_normalised, search_zero_result_log, search_index_outbox, search_index_state, search_index_watermark, ab_assignment,
  ab_experiment_metric_result, duplicate_job_cluster, duplicate_job_cluster_member, job_quality_score, candidate_quality_score,
  content_risk_score, skill_inference, salary_prediction""")

# ---- extension: ads --------------------------------------------------------------------------------------------------
G("Advertiser workspace (accounts, campaigns, groups, audiences, creatives, schedules, sponsored placements, exports): advertiser tenant owns",
  "org", "S=R|A A=RU|A+T AA=CRUD*|G+A E=CRUD*|G+A R=R*|A",
  """ad_account, ad_account_member, ad_campaign, ad_group, ad_group_placement, ad_conversion_goal, ad_audience, ad_audience_rule,
  ad_audience_member_list, ad_audience_list_entry, ad_creative, ad_creative_asset, sponsored_job, ad_schedule, ad_schedule_slot, ad_frequency_cap,
  ad_budget_shard, ad_report_export, brand_page_promotion, employer_branding_purchase, sponsored_candidate_slot, ad_approval_request""")
G("Ad money events (append-only): billing posts, advertiser reads own",
  "org", "S=R|A A=R|A AA=R*|A E=R*|A", "ad_budget_ledger, ad_billing_event, ad_billing_event_item, ad_invoice_line_link, ad_invalid_traffic_adjustment")
G("Ad serving logs (impression/click/conversion/auction): system append; staff read; advertisers read aggregates only",
  "platform", "S=R|V A=R|V", "ad_impression, ad_click, ad_conversion, ad_auction_log")
G("Ad performance aggregates: advertiser tenant reads via vw_ad_campaign_report",
  "org", "S=R|A A=R|A AA=R*|V E=R*|V", "ad_performance_hourly, ad_performance_daily")
G("Ad review/policy objects (reviews, workflows, decisions, strikes, fraud flags, quality, branding packages): staff only; advertiser sees own strikes and status history",
  "platform", "S=R|A A=CRU|A+T AA=R*|A E=R*|A",
  """ad_advertiser_policy_strike, ad_campaign_status_history, ad_creative_review, ad_approval_workflow, ad_approval_workflow_step,
  ad_approval_decision, ad_fraud_flag, ad_creative_quality_score, employer_branding_package, employer_branding_package_item""")

# ---- extension: learning ---------------------------------------------------------------------------------------------
G("Learning catalogue (courses, lessons, paths, questions, templates, badges, articles, tips): staff/authors manage, public listing",
  "platform", "@PUBLIC_CONTENT",
  """course_instructor_profile, course_instructor, course_module, course_lesson, course_skill, course_list_price, course_offer,
  course_prerequisite, course_language_availability, learning_path, learning_path_item, certificate_template, skill_badge, article_author,
  article_category, article_tag, article, article_tag_assignment, article_translation, article_salary_benchmark, resume_tip, resume_tip_translation,
  mentor_skill, mentor_focus, practice_question_set, practice_question_set_item""")
G("Assessment authoring objects (question banks, quizzes, blueprints): staff only, answer keys must not leak",
  "platform", "@STAFF_ONLY",
  """question_bank, quiz_question, quiz_question_option, quiz_question_translation, quiz_question_option_translation, quiz_question_skill,
  assessment_blueprint, assessment_section, learning_provider_integration, course_sync_event""")
G("Employer-assigned learning: employer assigns, learner reads and completes; employer compliance view is tenant-scoped",
  "org", "S=R|A A=R|A AA=R*|V E=CRU*|G+A C=RU*|A", "learning_assignment")
G("Certificates: owner reads; anyone verifies through vw_certificate_public_verification (no contact data)",
  "candidate", "S=R|V A=R|V C=R*|V E=R*|V R=R*|V", "certificate")
G("Certificate verification and article view events (append-only): anonymous visitors create",
  "public", "S=R|A A=R|A G=C|A C=C|A E=C|A R=C|A K=C|A P=C|A AA=C|A", "certificate_verification, article_view")
G("Public sharing of certificates by the owner",
  "candidate", "S=R|A A=R|A C=C*|A", "certificate_share")
G("Mentors and sessions: mentor/mentee own; directory excludes private fields",
  "user", "@USER_OWNED", "mentor_profile, mentor_availability, mentorship_request, mentorship_session, mentorship_feedback, coaching_session, mock_interview, mock_interview_answer")
G("Proctoring evidence: candidate does not read (integrity); staff review",
  "candidate", "S=R|A A=RU|A C=C*|A", "proctoring_event, proctoring_review")
G("Course reviews (Personal): author owns, public sees published",
  "user", "@UGC_PUBLIC", "course_review")

# ---- extension: talent operations -------------------------------------------------------------------------------------
G("Candidate-participating onboarding artefacts: employer tenant drives, candidate completes own tasks/uploads",
  "org", "@TENANT_HR C=CRU*|G+A K=-|A",
  """onboarding_plan, onboarding_plan_task, onboarding_task_assignment, onboarding_document_request, onboarding_document_submission,
  e_sign_request, equipment_request, contract_document, performance_feedback""")
G("Background/reference checks (Sensitive vendor refs; consent-gated): employer orders/reads outcome; report content only at vendor; candidate sees own order status; referee writes answers by token",
  "org", "S=R|V A=R|V AA=R*|V E=CRU*|V+A R=CR*|V+A C=R*|V+A",
  "background_check_order, background_check_result, reference_check_request")
G("Reference answers (Sensitive, encrypted): the referee (unauthenticated, one-time link) submits; the employer reads through the compliance path only",
  "org", "S=R|V A=R|V AA=R*|V E=R*|V+A R=R*|V+A G=C|A",
  "reference_check_response")
G("Placement drives (campus hiring): TPO owns own institution's drives and cohorts, employers participate, students register",
  "campus", "S=R|A A=RU|A AA=R*|A C=CR*|A E=CRU*|A P=CRUD*|G+A",
  """placement_drive, drive_institution, student_cohort, student_cohort_member, drive_eligibility_rule, drive_registration, drive_round,
  drive_round_result, drive_result""")
G("Apprenticeship, stipends and referral bonuses (payout_ref tokens): employer owns, apprentice reads own",
  "org", "S=R|V A=R|V AA=R*|V E=CRUD*|G+A C=R*|V P=R*|V",
  "apprenticeship_programme, apprentice_enrolment, apprentice_stipend_schedule, employer_referral_programme, employer_referral, employer_referral_bonus")
G("Staffing/MSP/workforce/timesheets/performance/talent CRM/templates: employer tenant",
  "org", "@TENANT_HR",
  """onboarding_plan_template, onboarding_plan_template_task, msp_vendor, workforce_request, workforce_request_skill, vendor_submission,
  remote_workforce_team, staffing_assignment, timesheet, timesheet_entry, timesheet_approval, performance_review_cycle, talent_segment,
  talent_segment_member, talent_campaign, talent_sequence, talent_sequence_step, campaign_enrolment, campaign_step_execution, career_page_section""")
G("Trust and safety (scam reports, fraud signals, trust scores, spam rules, restrictions, appeals, badges, reputation lists): staff; reporters and restricted users see their own case",
  "platform", "S=CRU|A+T A=CRU|A+T AA=CR*|A C=CR*|A E=CR*|A R=CR*|A K=CR*|A P=CR*|A",
  """fraud_signal, trust_score, trust_score_history, spam_rule, spam_rule_hit, account_restriction, appeal, verification_badge,
  reputation_list_entry""")
G("Scam/abuse report on a job or employer: anyone, including a guest, can report (rate limited); staff triage",
  "platform", "S=CRU|A+T A=CRU|A+T G=C|A AA=CR*|A C=CR*|A E=CR*|A R=CR*|A K=CR*|A P=CR*|A",
  "scam_job_report")
G("Equal-opportunity survey (employer configures) and responses (encrypted, compliance role only)",
  "org", "S=-|A A=-|A E=CRU*|A C=C*|A", "equal_opportunity_survey")
G("EEO responses: candidates submit; only compliance_svc reads/decrypts; no staff read path",
  "candidate", "C=C*|A", "equal_opportunity_response")
G("Data-processing agreements and subprocessors: compliance/staff manage; employers read their DPA",
  "platform", "S=CRU|A+T A=CRU|A E=R*|A AA=R*|A",
  "data_processing_agreement, subprocessor, dpa_subprocessor, subprocessor_data_category")

# ---- remaining ext_network user-owned set --------------------------------------------------------------------------
G("Social graph objects owned by the member (connections, requests, notes, tags, follows, invitations, RSVPs, tickets, badges, signals, quotas)",
  "user", "@USER_OWNED",
  """connection_request, connection_request_quota, connection, connection_event, connection_note, connection_tag, connection_tag_assignment,
  user_topic_follow, pymk_dismissal, network_invitation, invite_link, endorsement_request, recommendation_request, user_badge, user_signal,
  profile_visitor_daily, newsletter_subscription, event_rsvp, event_ticket, event_attendance, live_room_participant,
  organisation_update_reaction, alumni_membership""")

# ---------------------------------------------------------------- sets used by the service-role generator (build_rbac.py)
AUTH_TABLES = ["user_credential", "mfa_factor", "mfa_recovery_code", "password_reset_token", "email_verification_token", "identity_session",
               "api_key", "user_credential_history"]
SESSION_TABLES = ["device", "device_slot", "device_session", "login_attempt", "device_limit_violation", "device_revocation",
                  "session_revocation", "device_trust_challenge", "session_activity_sample"]
AUDIT_CORE = ["audit_event", "audit_chain_head", "audit_chain_checkpoint"]
MONEY_CATALOGUE = ["subscription_plan", "plan_version", "plan_price", "plan_entitlement", "credit_rate_card", "credit_pack", "credit_pack_price",
                   "coupon", "tax_rate", "currency_exchange_rate", "invoice_number_series", "spend_limit", "billing_address"]
MONEY_VAULT = ["payment_method_token", "payment_event", "tax_registration", "consultant_payout"]
COMPLIANCE_VAULT = ["equal_opportunity_response", "candidate_diversity_declaration", "background_check_result", "reference_check_response",
                    "candidate_identity_verification", "candidate_work_authorisation", "organisation_identifier", "student_cohort_member"]
BILLING_READS = ["organisation", "account", "licence", "licence_seat", "job_requirement", "candidate_profile", "app_user", "outreach_batch",
                 "gig_request", "gig_assignment", "gig_dispute", "sourcing_submission", "job_boost", "ad_campaign", "wallet", "consultant_profile"]

# ---- late additions (learning progress, sourcing openness) ---------------------------------------------------------------
G("Learner progress and results (candidate-owned): lesson/path progress, assessment sessions/answers/results, goals, streaks, skill-gap reports, badges, recommendations",
  "candidate", "S=R|V A=R|V C=CRUD*|G+A E=R*|V",
  """lesson_progress, learning_path_enrolment, learning_path_item_progress, assessment_session, assessment_answer, assessment_answer_option,
  assessment_result_detail, candidate_skill_badge, skill_gap_report, skill_gap_item, learning_recommendation, learning_goal,
  learning_activity_event, learning_activity_day, learning_streak""")
G("Leaderboards (derived snapshots; names masked when anonymised)",
  "platform", "@REF_AUTHED_READ", "leaderboard_snapshot, leaderboard_entry")
G("Consultant openness window (sourcing side, authoritative for consultant access): employer/mapped recruiter open and close; consultant reads via vw_consultant_open_requirement only (BR-SRC-01/02)",
  "org", "S=R|A A=R|A E=CRUD*|T+A R=CRU*|T+V K=R*|V", "consultant_job_openness")

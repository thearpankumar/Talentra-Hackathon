"""Generate docs/10_competitor_benchmark.xlsx. Every Talentra reference is verified against the dictionary (assert)."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from collections import Counter, defaultdict
from dictlib import load_model, ROOT
from xl import new_wb, add_sheet

M = load_model()
T = {t["name"]: {c["name"] for c in t["columns"]} for t in M["tables"]}


BAD = []


def chk(refs):
    for part in refs.split(" + "):
        part = part.strip()
        t, _, cols = part.partition(".")
        if t not in T:
            BAD.append(f"unknown table {t}"); continue
        for c in [x.strip() for x in cols.split(",") if x.strip()]:
            if c not in T[t]:
                BAD.append(f"unknown column {t}.{c}")


# ---- sources: key -> (label, url, verification) -----------------------------------------------------------------------
SRC = {
    "LI-PROFILE": ("LinkedIn r_fullprofile field reference (Microsoft Learn)", "https://learn.microsoft.com/en-us/linkedin/shared/references/v2/profile/full-profile", "Verified (page fetched 2026-10)"),
    "LI-ADS": ("LinkedIn Ads targeting options (help centre)", "https://www.linkedin.com/help/linkedin/answer/722", "Verified (page fetched 2026-10)"),
    "LI-RECRUITER": ("LinkedIn Recruiter advanced search filters (training.talent.linkedin.com + third-party summaries)", "https://training.talent.linkedin.com/advanced-search-filters", "Partly verified (search snippets only; filter names vary by plan)"),
    "IND-APPLY": ("Indeed Apply application data reference (JSON POST body)", "https://docs.indeed.com/indeed-apply/application-data", "Verified (page fetched 2026-10)"),
    "IND-JOBSYNC": ("Indeed Job Sync API overview (categories only)", "https://docs.indeed.com/job-sync-api/", "Partly verified (category list only, no field names)"),
    "NK-RESDEX": ("Naukri Recruiter blog: Resdex filters", "https://www.naukri.com/blog/recruiter-resdex/", "Partly verified (search snippet)"),
    "NK-NOTICE": ("Naukri recruiter zone: notice period search", "https://recruiterzone.naukri.com/hire-faster-with-notice-period-search", "Partly verified (search snippet)"),
    "NK-POSTING": ("Naukri recruiter FAQ / blog on job posting and questionnaires", "https://recruiterfaq.naukri.com/category/naukri-job-posting/", "Partly verified (search snippet; blog page returned HTTP 403 on fetch)"),
    "MN-RESUME": ("Monster Resume Search / SearchMonster help", "https://hiring.monster.com/products/resume-search-2024", "Partly verified (pages fetched; they list few field names)"),
    "NOTES": ("Talentra module research notes (docs/_staging/*.yaml notes: competitor coverage sections)", "docs/_staging/", "Secondary (compiled earlier by module agents from public docs; not re-fetched)"),
    "GK": ("General product knowledge (public UI, no single citable page)", "", "UNVERIFIED"),
}

ROWS = []  # platform, domain, element, src, refs, verdict, reason


def E(platform, domain, element, src, refs, verdict, reason):
    chk(refs)
    assert verdict in ("Matches", "Exceeds", "Falls short")
    assert src in SRC
    ROWS.append((platform, domain, element, src, refs, verdict, reason))


CP = "Candidate profile"; EP = "Employer profile"; JB = "Job posting"; AP = "Application and pipeline"
RT = "Recruiter tools"; CM = "Community and messaging"; LR = "Learning and assessment"; AD = "Ads and promotion"

# ============================== LinkedIn ==========================================================================
L = "LinkedIn"
E(L, CP, "First/last name, maiden name, phonetic names (localizable)", "LI-PROFILE", "candidate_profile.given_name,family_name,display_name", "Falls short", "Names stored once (UTF-8, no script assumption). Maiden name and phonetic/localised name variants are not modelled; extension would be a candidate_name_variant table.")
E(L, CP, "Headline and summary", "LI-PROFILE", "candidate_profile.headline,summary,resume_headline", "Matches", "Headline and summary columns; Naukri-style resume headline added.")
E(L, CP, "Birth date", "LI-PROFILE", "candidate_profile.birth_date", "Matches", "DATE, Personal class, hidden from recruiters by vw_candidate_profile_recruiter.")
E(L, CP, "Geo location, address", "LI-PROFILE", "candidate_profile.current_country_id,current_state_region_id,current_city_id + candidate_address.city_id,postal_code", "Matches", "Normalised country/state/city plus multi-address table.")
E(L, CP, "Phone numbers (Contact Info)", "LI-PROFILE", "candidate_contact_phone.phone,phone_country_id", "Exceeds", "E.164 plus country, primary flag via partial unique, verification timestamps; LinkedIn lists numbers only.")
E(L, CP, "Positions (employment history)", "LI-PROFILE", "candidate_experience.job_title,employer_name,employer_organisation_id,start_date,end_date,is_current,description", "Matches", "Includes link to canonical organisation when known.")
E(L, CP, "Educations", "LI-PROFILE", "candidate_education.institution_name", "Matches", "Institution, qualification, field of study, dates.")
E(L, CP, "Skills (with endorsements)", "LI-PROFILE", "candidate_skill.skill_id,proficiency_level_id,years_of_experience,is_top_skill,endorsement_count + candidate_skill_endorsement.candidate_skill_id", "Exceeds", "Adds proficiency, years used, last-used date and assessment_attempt_id link (verified skill).")
E(L, CP, "Certifications", "LI-PROFILE", "candidate_certification.candidate_profile_id", "Matches", "Dedicated table with issuer, dates, credential reference.")
E(L, CP, "Honors and awards", "LI-PROFILE", "candidate_award.candidate_profile_id", "Matches", "Dedicated table.")
E(L, CP, "Patents", "LI-PROFILE", "candidate_patent.candidate_profile_id", "Matches", "Dedicated table.")
E(L, CP, "Publications", "LI-PROFILE", "candidate_publication.candidate_profile_id", "Matches", "Dedicated table.")
E(L, CP, "Courses taken", "LI-PROFILE", "course_enrolment.candidate_profile_id + course.course_provider_id", "Matches", "Course catalogue and enrolments with provider.")
E(L, CP, "Projects", "LI-PROFILE", "candidate_project.candidate_profile_id,gig_worker_profile_id,gig_assignment_id + candidate_project_skill.skill_id", "Exceeds", "One table for candidate and gig projects; completed gig can become a verified project (gig_assignment_id).")
E(L, CP, "Test scores", "LI-PROFILE", "candidate_test_score.test_name,score_value,max_score,test_date", "Matches", "Dedicated table.")
E(L, CP, "Volunteering experiences", "LI-PROFILE", "candidate_volunteer_experience.organisation_name,role_title,cause", "Matches", "Dedicated table.")
E(L, CP, "Volunteering interests", "LI-PROFILE", "candidate_volunteer_experience.cause", "Falls short", "Interests are only captured through experience cause, not as a separate interest list; low value, accepted.")
E(L, CP, "Websites / social links", "LI-PROFILE", "candidate_social_link.link_type_id,url,is_public", "Matches", "Typed links with public flag.")
E(L, CP, "Vanity name (public profile URL)", "LI-PROFILE", "public_profile_slug.id + public_profile_slug_history.id", "Matches", "Slug with redirect history (extension module ext_network).")
E(L, CP, "Profile and background picture", "LI-PROFILE", "candidate_media_file.mime_type,scan_status_id + candidate_profile.profile_photo_object_key", "Exceeds", "Object key, SHA-256, size, MIME and virus-scan status per file.")
E(L, CP, "Supported locales", "LI-PROFILE", "candidate_profile.profile_language_id + language_preference.app_user_id", "Matches", "Profile language and UI language preference.")
E(L, CP, "Last modified", "LI-PROFILE", "candidate_profile.last_profile_update_at,updated_at", "Matches", "Both business and audit timestamps.")
E(L, CP, "Open To Work (workplace type, titles, visibility)", "LI-RECRUITER", "candidate_open_to_work_signal.valid_from,valid_to,visibility_level_id + candidate_preferred_work_type.work_type_id + candidate_preferred_role.job_role_id", "Matches", "Dated signal with visibility; preferred work type and roles in bridges.")
E(L, CP, "Recommendations given and received", "NOTES", "candidate_recommendation.author_app_user_id,recommendation_status_id,body", "Matches", "Status workflow (requested/accepted/hidden).")
E(L, CP, "Follow members/companies", "NOTES", "candidate_follow.candidate_profile_id + organisation_follow.organisation_id", "Matches", "Both person and organisation follow.")
E(L, CP, "Work authorisation (not in public profile API)", "GK", "candidate_work_authorisation.country_id,work_authorisation_type_id,requires_sponsorship", "Exceeds", "Per-country authorisation with validity and verification; LinkedIn exposes this only as job-application questions (unverified).")
E(L, EP, "Company page: name, industry, size, HQ, website, description", "GK", "organisation.display_name,primary_industry_id,organisation_size_band_id,hq_city_id,website_url,description", "Matches", "Core company page attributes; size history tracked in organisation_size_history.")
E(L, EP, "Company locations", "GK", "organisation_location.city_id,address_line1,latitude,longitude,is_headquarters", "Matches", "Multi-location with geo source and place_ref.")
E(L, EP, "Company followers and updates", "GK", "organisation_follow.organisation_id + organisation_update.id", "Matches", "Follow table; updates in extension module.")
E(L, EP, "Company verification badge", "GK", "organisation.verification_status_id,verified_at + organisation_verification.vendor_code,vendor_reference", "Exceeds", "Per-attempt verification events with vendor reference and idempotency key, tax identifiers stored encrypted + lookup hash.")
E(L, EP, "Employer brand / career page", "GK", "organisation_career_page.organisation_id + organisation_brand.organisation_id", "Matches", "Dedicated tables.")
E(L, JB, "Job title, description, company, location", "GK", "job_requirement.title,description,organisation_id,primary_city_id", "Matches", "Core fields; trigram-searchable title.")
E(L, JB, "Workplace type (on-site, hybrid, remote), employment type, seniority", "GK", "job_requirement.work_type_id,employment_type_id,seniority_level_id", "Matches", "Lookup-backed enumerations.")
E(L, JB, "Job skills", "GK", "job_requirement_skill.skill_id,requirement_importance_id,min_experience_months", "Exceeds", "Importance (must/nice) and minimum experience per skill.")
E(L, JB, "Screening questions with must-have answers", "GK", "questionnaire_question.questionnaire_id + job_requirement_screening.is_knockout,expected_value", "Matches", "Questionnaire plus knockout rules with weights.")
E(L, JB, "Easy Apply vs external apply", "GK", "job_requirement.apply_mode_id,external_apply_url", "Matches", "apply_mode lookup and external URL.")
E(L, JB, "Salary range", "GK", "job_requirement.salary_min,salary_max,currency_id,salary_period_id,salary_disclosure_id", "Exceeds", "Disclosure state stored separately (unknown vs undisclosed vs not applicable) per Section 5 rule group 5.")
E(L, JB, "Job promotion (Promoted jobs)", "GK", "job_boost.job_boost_type_id,starts_at,ends_at,wallet_ledger_entry_id + sponsored_job.id", "Matches", "Boost paid from wallet; ads placement in ext_ads.")
E(L, AP, "Applicant pipeline stages and ratings", "GK", "job_application.application_status_id + job_application_status_history.to_application_status_id + application_scorecard.id", "Exceeds", "Gap-free transition chain via composite FKs, scorecards with per-criterion scores.")
E(L, AP, "Hiring team", "GK", "job_requirement_assignment.recruiter_app_user_id,requirement_assignment_role_id", "Falls short", "Assignment role exists, but sub-roles inside an employer organisation are out of scope in the docket; extension documented in jobs notes.")
E(L, RT, "Recruiter search filters (skills, companies, schools, industry, function, years of experience)", "LI-RECRUITER", "candidate_profile.total_experience_months,current_city_id + candidate_skill.skill_id + candidate_education.institution_name + candidate_experience.industry_id,job_role_id", "Matches", "All filter dimensions have indexed columns; ranking delegated to the search service via search_index_outbox.")
E(L, RT, "Saved searches and alerts", "GK", "saved_search.id + saved_search_run.id", "Matches", "Saved search with run history.")
E(L, RT, "InMail and credits", "GK", "outreach_message.id + outreach_credit_usage.id + wallet_ledger_entry.id", "Matches", "Outreach messages debit the append-only wallet ledger.")
E(L, RT, "Profile view history (who viewed)", "GK", "candidate_profile_view.viewer_organisation_id,viewed_at,is_viewer_anonymous + profile_visitor_daily.id", "Exceeds", "Anonymous viewing flag, credit link, dwell time, daily aggregates.")
E(L, RT, "Talent pipeline / project folders", "GK", "talent_pool.id + talent_pool_member.id + candidate_shortlist_folder.id", "Matches", "Pools and shortlist folders with history.")
E(L, CM, "Feed posts, reactions, comments, reshares", "NOTES", "community_post.id + community_reaction.id + community_comment.id + community_share.share_token", "Matches", "Hybrid fan-out feed, counters off the post row.")
E(L, CM, "Groups, polls, hashtags, mentions", "NOTES", "community_group.id + community_poll.id + hashtag.id + community_mention.id", "Matches", "All present.")
E(L, CM, "Messaging with read receipts and who-can-message settings", "NOTES", "message.id + message_read_receipt.id + privacy_setting.app_user_id", "Matches", "Encrypted bodies, receipts only if allowed.")
E(L, CM, "Connections (1st/2nd degree), People You May Know", "GK", "connection.id + pymk_candidate.id + network_second_degree.id", "Matches", "ext_network module (extension).")
E(L, CM, "Newsletters, events, live audio", "GK", "newsletter.id + event.id + live_room.id", "Matches", "ext_network module.")
E(L, CM, "Creator mode / creator analytics", "GK", "community_post_stat.community_post_id", "Falls short", "Only post counters; creator analytics dashboards are not modelled (not a hiring-domain priority).")
E(L, LR, "LinkedIn Learning courses, learning paths, certificates", "GK", "course.id + learning_path.id + certificate.id + course_lesson.id", "Matches", "ext_learning module covers catalogue, paths, progress, certificates.")
E(L, LR, "Skill assessments (badge)", "GK", "assessment.id + skill_badge.id + candidate_skill_badge.id + assessment_session.id", "Matches", "Assessments with proctoring and badges.")
E(L, AD, "Ad targeting facets (company, demographics, education, job experience, skills, interests, device)", "LI-ADS", "ad_audience.id + ad_audience_rule.id", "Matches", "Rules stored as typed rows; demographic targeting limited by policy (see gap register G-06).")
E(L, AD, "Matched audiences (uploaded lists, retargeting)", "LI-ADS", "ad_audience_member_list.id + ad_audience_list_entry.id", "Matches", "Hashed list entries.")
E(L, AD, "Campaign budget, bid strategy, schedule, frequency cap, pacing", "LI-ADS", "ad_campaign.budget_amount,ad_bid_strategy_id,ad_pacing_mode_id + ad_budget_shard.shard_no + ad_frequency_cap.id + ad_schedule.id", "Exceeds", "Sharded budget counters avoid a hot campaign row; ledger-linked billing.")
E(L, AD, "Lead gen forms", "LI-ADS", "ad_conversion_goal.id + ad_conversion.id", "Falls short", "Conversions are tracked but form-based lead capture is not modelled; accepted (hiring platform, not B2B marketing).")

# ============================== Naukri ============================================================================
N = "Naukri"
E(N, CP, "Resume headline", "NK-RESDEX", "candidate_profile.resume_headline", "Matches", "Dedicated column.")
E(N, CP, "Profile summary", "NK-RESDEX", "candidate_profile.summary", "Matches", "Column present.")
E(N, CP, "Key skills with experience", "NK-RESDEX", "candidate_skill.skill_id,years_of_experience,last_used_date", "Matches", "Years and last-used per skill.")
E(N, CP, "Notice period (search filter)", "NK-NOTICE", "candidate_profile.notice_period_days,earliest_start_date", "Matches", "INT2 days (0 is a real value) plus earliest start date.")
E(N, CP, "Current and expected salary (CTC)", "NK-RESDEX", "candidate_profile.current_salary_amount,expected_salary_min,expected_salary_max,salary_currency_id,salary_period_id,current_salary_disclosure_id,expected_salary_disclosure_id", "Exceeds", "Disclosure state separated from the amount; currency and period explicit.")
E(N, CP, "Employment details (current employer, designation)", "NK-RESDEX", "candidate_profile.current_job_title,current_employer_name + candidate_experience.is_current", "Matches", "Denormalised current job for search cards.")
E(N, CP, "Location and relocation preference", "NK-RESDEX", "candidate_profile.current_city_id + candidate_job_preference.is_willing_to_relocate + candidate_preferred_location.city_id", "Matches", "Current city, relocation flag and preferred cities.")
E(N, CP, "Desired job type, shift, industry, role", "GK", "candidate_job_preference.work_shift_id + candidate_preferred_employment_type.employment_type_id + candidate_preferred_industry.industry_id", "Matches", "Bridge tables.")
E(N, CP, "Differently-abled flag", "GK", "candidate_diversity_declaration.has_disability,disability_description_encrypted", "Exceeds", "Isolated special-category table with consent link and encrypted free text.")
E(N, CP, "Resume upload with parsed fields", "GK", "resume.id + resume_parse_result.parsed_json + candidate_media_file.scan_status_id", "Exceeds", "Parse provenance (parser version), version chain, scan status.")
E(N, CP, "Profile completeness meter", "GK", "candidate_profile.completeness_score + profile_completion_rule.id + candidate_profile_completion_snapshot.id", "Matches", "Rule-driven score plus daily snapshots.")
E(N, CP, "Online profile / work samples", "GK", "candidate_social_link.url + candidate_media_file.id", "Matches", "Links and media.")
E(N, CP, "IT skills with version and last used", "GK", "candidate_skill.last_used_date,years_of_experience", "Falls short", "No software-version attribute on a skill; accepted, skill_alias covers naming only.")
E(N, EP, "Company profile, size, industry, type", "GK", "organisation.organisation_type_id,primary_industry_id,organisation_size_band_id", "Matches", "Lookup-backed.")
E(N, EP, "Company reviews and ratings (AmbitionBox style)", "GK", "organisation_review.rating_overall,rating_culture,rating_work_life,rating_compensation,rating_management,rating_career + organisation_rating_summary.organisation_id", "Exceeds", "Moderation status, verified-employment flag, anonymous flag, employer response table.")
E(N, EP, "Recruiter verification / KYC", "GK", "organisation_verification.verification_kind_id,verification_status_id + organisation_identifier.tax_id_type_id,value_lookup_hash", "Matches", "Tax IDs encrypted with HMAC lookup.")
E(N, JB, "Department, role, industry type, function area", "NK-POSTING", "job_requirement.job_role_id,job_category_id,industry_id", "Matches", "Role, category and industry lookups.")
E(N, JB, "Key skills, education, experience range", "NK-POSTING", "job_requirement_skill.skill_id + job_requirement_qualification.qualification_id,min_score_percent + job_requirement.experience_min_months,experience_max_months", "Matches", "Normalised child tables.")
E(N, JB, "Salary range with hide-from-candidates option", "NK-POSTING", "job_requirement.salary_min,salary_max,salary_disclosure_id", "Matches", "salary_disclosure lookup models hidden/undisclosed.")
E(N, JB, "Benefits", "NK-POSTING", "job_requirement_benefit.job_benefit_type_id,details", "Matches", "Typed benefits.")
E(N, JB, "Walk-in details (venue, dates, timing, contact, documents)", "NK-POSTING", "job_walk_in_session.venue_name,address_line,walk_in_start_at,walk_in_end_at,contact_person_name,documents_to_carry", "Matches", "Dedicated session table with time zone.")
E(N, JB, "Freshers flag, hot vacancy", "GK", "job_requirement.is_open_to_freshers,hiring_urgency_id", "Matches", "Flag and urgency lookup.")
E(N, JB, "Consultant (agency) postings", "GK", "job_requirement.is_open_to_consultants + job_consultant_openness.id", "Exceeds", "Openness windows with commission and guarantee; sourcing requests are DB-enforced.")
E(N, AP, "Questionnaire with auto-screening by star rating, location, experience, CTC, education, industry", "NK-POSTING", "job_application_screening_result.job_application_id + job_requirement_screening.job_screening_criterion_type_id + job_application.match_score,screening_score,is_screened_out", "Matches", "Screening criteria and stored results per application.")
E(N, AP, "Response management folders", "GK", "talent_pool.id + job_shortlist.id", "Matches", "Shortlists and talent pools.")
E(N, RT, "Resdex search: experience, location, salary, department/role, industry, designation, notice period", "NK-RESDEX", "candidate_profile.total_experience_months,current_city_id,expected_salary_min,notice_period_days + candidate_experience.job_role_id,industry_id,job_title", "Matches", "Indexed on city/availability partial index and skill index.")
E(N, RT, "System-computed 'May also know' skills", "NK-RESDEX", "skill_inference.id", "Matches", "ext_matching skill_inference table.")
E(N, RT, "Profile download with credits", "GK", "profile_download.credits_charged,wallet_ledger_entry_id,subscription_entitlement_id", "Exceeds", "Append-only download history tied to the ledger entry that paid for it (unique).")
E(N, RT, "Bulk mailers / SMS to candidates", "GK", "outreach_batch.id + outreach_recipient.id + outreach_message.id + outreach_exclusion.id", "Exceeds", "Exclusion table enforces blocks and opt-outs before send.")
E(N, CM, "Job alerts (keywords, location, salary, experience, frequency)", "GK", "job_alert_preference.keyword_list,min_salary,min_experience_years,alert_frequency_id + job_alert_match.job_alert_preference_id", "Exceeds", "Match table prevents duplicate alerts; per-channel preferences.")
E(N, CM, "Messenger / chat with recruiters", "GK", "message_thread.id + message.id", "Matches", "Block check in same txn.")
E(N, LR, "Naukri Campus / learning content (Code360, courses)", "GK", "course.id + placement_drive.id", "Matches", "Courses and campus drives (extension).")
E(N, AD, "Sponsored/promoted job listings", "GK", "sponsored_job.id + job_boost.id", "Matches", "ext_ads and jobs module.")

# ============================== Monster ===========================================================================
Mo = "Monster"
E(Mo, CP, "Multiple resumes (up to five) with per-resume visibility", "NOTES", "resume.is_visible_to_recruiters,is_default", "Matches", "Version chain plus visibility flag; max count is application-level.")
E(Mo, CP, "Resume builder templates", "NOTES", "resume_template.id", "Matches", "Template table.")
E(Mo, CP, "Resume updated recency (search filter)", "MN-RESUME", "candidate_profile.last_profile_update_at,last_active_at", "Matches", "Both timestamps; hash-sharded index on last_active_at.")
E(Mo, CP, "Skills, location, years of experience (search filters)", "MN-RESUME", "candidate_skill.skill_id + candidate_profile.current_city_id,total_experience_months", "Matches", "Indexed.")
E(Mo, CP, "Work authorisation, security clearance, relocation", "NOTES", "candidate_work_authorisation.work_authorisation_type_id + candidate_job_preference.is_willing_to_relocate", "Falls short", "Security clearance only as certification type, no dedicated field.")
E(Mo, CP, "Career level", "NOTES", "candidate_profile.total_experience_months", "Falls short", "Career level not stored on profile; derivable from seniority on experience only if captured. Accepted.")
E(Mo, CP, "Interest in similar jobs (routing)", "MN-RESUME", "candidate_job_preference.candidate_profile_id + job_alert_preference.job_category_id", "Matches", "Via alerts and preferences.")
E(Mo, EP, "Company profile and employer branding", "GK", "organisation_brand.organisation_id + organisation_career_page.organisation_id", "Matches", "Dedicated tables.")
E(Mo, JB, "Job templates (prewritten descriptions)", "MN-RESUME", "job_template.id + job_requirement.job_template_id", "Matches", "Template reference on requirement.")
E(Mo, JB, "Promoted postings and automatic distribution", "MN-RESUME", "job_boost.id + job_syndication.last_synced_at + job_posting_channel.id", "Matches", "Boost and syndication channels.")
E(Mo, JB, "Job type and shift", "GK", "job_requirement.employment_type_id,shift_type_id", "Matches", "Lookups.")
E(Mo, AP, "Applicant dashboard: rate candidates, contact, no limit per posting", "MN-RESUME", "application_scorecard.id + application_note.id + job_requirement.max_applications", "Exceeds", "Scorecards and notes; optional cap column.")
E(Mo, RT, "Resume search: view/download/forward costs credits", "MN-RESUME", "profile_download.credits_charged,wallet_ledger_entry_id", "Exceeds", "Credits consumed through ledger, idempotent per download.")
E(Mo, RT, "Notifications when matching candidates join (saved criteria)", "MN-RESUME", "saved_search.id + saved_search_run.id", "Matches", "Saved search runs.")
E(Mo, RT, "Side-by-side candidate comparison", "MN-RESUME", "candidate_shortlist_item.id", "Falls short", "Comparison is a UI feature; data supported but no stored comparison set. Accepted.")
E(Mo, RT, "Built-in performance analytics", "MN-RESUME", "agg_funnel_daily.applications,viewed,shortlisted + agg_recruiter_activity_daily.id", "Exceeds", "Star-schema aggregates with idempotent grain keys.")
E(Mo, CM, "Career advice articles / forums", "GK", "article.id + community_group.id", "Matches", "ext_learning articles and community groups.")
E(Mo, LR, "Salary tools / compensation data", "GK", "salary_benchmark.p50_amount,sample_size,refreshed_at + salary_benchmark_source.source_record_ref", "Exceeds", "Percentile bands with source attribution and refresh date (docket boundary 9).")
E(Mo, AD, "Monster+ / sponsored placement packages", "GK", "employer_branding_package.id + ad_campaign.id", "Matches", "ext_ads.")

# ============================== Indeed ============================================================================
I = "Indeed"
E(I, AP, "Application: applicant full name, email, phone, DOB, gender, cover letter, resume (file/text/html/json)", "IND-APPLY", "candidate_profile.display_name,contact_email,contact_phone + job_application.candidate_message + job_application_document.id + resume.id", "Matches", "Candidate data referenced by id (not copied); cover letter in candidate_message.")
E(I, AP, "Applicant verified flag", "IND-APPLY", "candidate_profile.contact_email_verified_at,contact_phone_verified_at", "Matches", "Verification timestamps.")
E(I, AP, "Screener questions and answers", "IND-APPLY", "questionnaire_answer.questionnaire_question_id + questionnaire_answer_option.id", "Matches", "Typed answers.")
E(I, AP, "Demographic questions and answers (EEO)", "IND-APPLY", "equal_opportunity_survey.id + equal_opportunity_response.id", "Matches", "Separate from application, consent-linked (ext_talent_ops).")
E(I, AP, "Application analytics: device, IP, referer, sponsored flag, source attribution, smart fit score", "IND-APPLY", "job_application.job_source_id,match_score + application_view_event.id", "Falls short", "Device/IP/referer are not stored on the application row (kept in fact_application_event.dim_device_platform_id only). Accepted for privacy; IP lives on sessions.")
E(I, AP, "appliedOnMillis (applied timestamp)", "IND-APPLY", "job_application.applied_at", "Matches", "TIMESTAMPTZ UTC.")
E(I, CP, "Resume JSON: skills, positions, educations, languages, links, awards, certifications, associations, patents, publications", "IND-APPLY", "candidate_skill.skill_id + candidate_experience.job_title + candidate_education.institution_name + candidate_language.language_id + candidate_social_link.url + candidate_award.id + candidate_certification.id + candidate_patent.id + candidate_publication.id", "Matches", "Each section is a table.")
E(I, CP, "Military service", "IND-APPLY", "candidate_diversity_declaration.is_veteran", "Falls short", "Only veteran flag; branch, rank, service country not modelled. Accepted (jurisdiction-specific).")
E(I, CP, "Licenses (driver licence etc.)", "IND-APPLY", "candidate_certification.candidate_profile_id", "Matches", "Modelled as certification type.")
E(I, CP, "Relocation statuses, willingness to travel", "IND-APPLY", "candidate_job_preference.is_willing_to_relocate,is_willing_to_travel", "Matches", "Booleans plus preferred locations.")
E(I, CP, "Highest education level, career level, employment status", "IND-APPLY", "candidate_education.candidate_profile_id + candidate_profile.availability_status_id", "Falls short", "Highest education derived by query; career level not stored.")
E(I, CP, "Citizenship / employment eligibility / EU work permit", "IND-APPLY", "candidate_work_authorisation.country_id,work_authorisation_type_id + candidate_profile.nationality_country_id", "Matches", "Per-country authorisation.")
E(I, CP, "ID number", "IND-APPLY", "candidate_identity_verification.vendor_token,id_number_hmac", "Exceeds", "Never stored in clear: vendor token and keyed HMAC only.")
E(I, JB, "Job feed categories: qualifications, hours, salary, benefits, job type, description, occupation, skills", "IND-JOBSYNC", "job_requirement_qualification.qualification_id + job_requirement_benefit.job_benefit_type_id + job_requirement.employment_type_id,shift_type_id,job_role_id + job_requirement_skill.skill_id", "Matches", "All categories normalised.")
E(I, JB, "Apply URL / external job id", "IND-JOBSYNC", "job_requirement.external_apply_url,external_job_ref", "Matches", "external_job_ref is scoped by legacy_source_system_id and unique per source among live jobs (09 Legacy sync gap L-01 resolved).")
E(I, RT, "Resume search with availability 'ready to start' and pay expectations", "NOTES", "candidate_profile.availability_status_id,earliest_start_date,expected_salary_min", "Matches", "Columns present.")
E(I, EP, "Company ratings and reviews", "NOTES", "organisation_review.rating_overall + company_rating_snapshot.overall_rating,review_count,refreshed_at", "Exceeds", "Own reviews plus third-party snapshots with attribution.")
E(I, AD, "Sponsored jobs, pay-per-click budgets", "GK", "sponsored_job.id + ad_click.id + ad_impression.id + ad_budget_ledger.id", "Matches", "Impression/click events and budget ledger.")
E(I, LR, "Career guide / skill tests", "GK", "assessment.id + article.id", "Matches", "Assessments and articles.")
E(I, CM, "Company Q&A / community", "GK", "community_post.id + organisation_review_response.id", "Matches", "Posts and employer responses.")

# ---- gap register ------------------------------------------------------------------------------------------------------
GAPS = [
    ("G-01", CP, "Maiden name, phonetic/localised name variants (LinkedIn)", "Low", "Accepted", "Person name domain stores one UTF-8 name; localisation of names is a display concern. Extension: candidate_name_variant(candidate_profile_id, language_id, given_name, family_name, phonetic_name).", "candidate_profile.given_name"),
    ("G-02", CP, "Software version on IT skills (Naukri)", "Low", "Accepted", "Skill taxonomy keeps product versions out; skill_alias covers naming variants.", "candidate_skill"),
    ("G-03", CP, "Military service detail and security clearance (Indeed, Monster)", "Low", "Accepted", "Jurisdiction-specific and sensitive; veteran flag kept in candidate_diversity_declaration. Clearance can be a candidate_certification type.", "candidate_diversity_declaration.is_veteran"),
    ("G-04", CP, "Career level on profile (Monster, Indeed)", "Medium", "Proposed", "Add candidate_profile.seniority_level_id (lookup already used by job_requirement) in a later migration; derivable from experience until then.", "candidate_profile"),
    ("G-05", AP, "Application device/IP/referer analytics (Indeed)", "Low", "Accepted", "Privacy decision: not stored on application; device platform kept in analytics only; IP only on sessions with TTL.", "job_application"),
    ("G-06", AD, "Demographic ad targeting (LinkedIn age/gender)", "Medium", "Accepted", "Excluded deliberately for discrimination risk in employment advertising; audience rules limited to skills, location, seniority.", "ad_audience_rule"),
    ("G-07", AD, "Lead-gen forms (LinkedIn)", "Low", "Accepted", "Marketing feature outside the hiring workflows in docket Section 2.", "ad_conversion"),
    ("G-08", CM, "Creator analytics (LinkedIn)", "Low", "Accepted", "Counters exist (community_post_stat); dashboards can be built on analytics marts.", "community_post_stat"),
    ("G-09", AP, "Hiring-team sub-roles (LinkedIn Recruiter)", "Medium", "Accepted", "Docket Section 3 states sub-roles inside an employer organisation are out of scope; extension is hiring_team_role_id on job_requirement_assignment.", "job_requirement_assignment"),
    ("G-10", RT, "Stored side-by-side comparison sets (Monster)", "Low", "Accepted", "UI feature over shortlist items; no persistence needed.", "candidate_shortlist_item"),
    ("G-11", JB, "Source-system keyed external job reference (Indeed feed id, legacy job id)", "High", "Proposed", "Gap shared with legacy sync: add source_system column and unique (source_system, external_job_ref); see 09_integration_boundaries.xlsx sheet 'Legacy sync'.", "job_requirement.external_job_ref"),
    ("G-12", CP, "Volunteering interests list (LinkedIn)", "Low", "Accepted", "Cause captured on the volunteer experience; interest topics exist as user_topic_follow in ext_network.", "candidate_volunteer_experience.cause"),
]


def main():
    assert not BAD, chr(10).join(BAD)
    plat_order = ["LinkedIn", "Naukri", "Monster", "Indeed"]
    domains = [CP, EP, JB, AP, RT, CM, LR, AD]
    wb = new_wb()
    # README
    rows = [
        ["Purpose", "Competitive coverage benchmark required by docket Section 4: for Naukri, Monster and LinkedIn (plus Indeed) list the data elements known to be captured and show where Talentra matches, exceeds or falls short."],
        ["Method", "Web research on 2026-10 using public documentation and help pages. Sources and their verification status are listed on sheet 'Sources'. Each element row carries a source key and the verification state of that source. Where no citable page was found the row is marked UNVERIFIED (general product knowledge)."],
        ["Honesty note", "Several vendor pages (Naukri recruiter blog, Monster help pages) returned little field-level detail or HTTP 403; those rows are 'Partly verified' or 'UNVERIFIED'. LinkedIn r_fullprofile and Indeed Apply application data were fetched and field lists are verified."],
        ["Verdicts", "Matches = equivalent data captured. Exceeds = captured with extra integrity, history, disclosure or privacy structure that the competitor element does not show. Falls short = element not modelled or only partly; each is in 'Gap register' with why accepted."],
        ["Talentra references", "All table.column references were machine-checked against docs/_staging (docs/tools/gen_competitor_benchmark.py asserts every one). 'Extension' tables belong to the five EXTENSION modules."],
        ["Regenerate", "python docs/tools/gen_competitor_benchmark.py"],
    ]
    add_sheet(wb, "README", ["Item", "Description"], rows, widths=[22, 120], wrap_cols=("Description",))
    # summary
    cnt = Counter((r[0], r[1], r[5]) for r in ROWS)
    srows = []
    for d in domains:
        for p in plat_order:
            m = cnt[(p, d, "Matches")]; e = cnt[(p, d, "Exceeds")]; f = cnt[(p, d, "Falls short")]
            if m + e + f:
                srows.append([d, p, m + e + f, m, e, f, round((m + e) / (m + e + f) * 100, 1)])
    tot = []
    for p in plat_order:
        m = cnt_p = sum(v for (pp, d, vv), v in cnt.items() if pp == p and vv == "Matches")
        e = sum(v for (pp, d, vv), v in cnt.items() if pp == p and vv == "Exceeds")
        f = sum(v for (pp, d, vv), v in cnt.items() if pp == p and vv == "Falls short")
        tot.append(["ALL DOMAINS", p, m + e + f, m, e, f, round((m + e) / (m + e + f) * 100, 1)])
    add_sheet(wb, "Summary", ["Domain", "Platform", "Elements compared", "Matches", "Exceeds", "Falls short", "Coverage % (Matches+Exceeds)"], tot + srows, widths=[28, 12, 12, 10, 10, 11, 16])
    hdr = ["Domain", "Data element (competitor)", "Source key", "Verification", "Talentra tables / columns", "Verdict", "Reason"]
    for p in plat_order:
        rs = [[r[1], r[2], r[3], SRC[r[3]][2], r[4].replace(" + ", " ; "), r[5], r[6]] for r in ROWS if r[0] == p]
        add_sheet(wb, p, hdr, rs, widths=[24, 52, 14, 30, 70, 11, 70], wrap_cols=("Data element (competitor)", "Talentra tables / columns", "Reason", "Verification"),
                  validations={"Verdict": ["Matches", "Exceeds", "Falls short"]})
    add_sheet(wb, "Gap register", ["Gap id", "Domain", "Coverage gap", "Impact", "Status", "Why accepted / remedy", "Talentra reference"],
              [list(g[:6]) + [g[6]] for g in GAPS], widths=[8, 24, 56, 9, 11, 90, 36], wrap_cols=("Coverage gap", "Why accepted / remedy"), status_col="Status")
    add_sheet(wb, "Sources", ["Source key", "Source", "URL", "Verification"], [[k, v[0], v[1], v[2]] for k, v in SRC.items()], widths=[14, 70, 80, 50], wrap_cols=("Source",))
    out = os.path.join(ROOT, "docs", "10_competitor_benchmark.xlsx")
    wb.save(out)
    print("wrote", out, len(ROWS), "rows")
    for p in plat_order:
        print(p, Counter(r[5] for r in ROWS if r[0] == p))


if __name__ == "__main__":
    main()

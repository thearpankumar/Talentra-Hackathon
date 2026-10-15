# Northbridge Workforce Systems

# Talentra: Database Design Hackathon

**Project Docket for a Job Search and Demand Management Platform**

Prepared by Northbridge Workforce Systems | Duration: 2 days | Team size: 3 to 5 | Engine: PostgreSQL or MySQL

*Confidential: For participant use within this hackathon only.*

---

## 1. Overview

### Purpose

Design, build, and defend the operational and analytical database for Talentra, a job search and demand management platform. The platform serves candidates, employers, recruiters, consultants, placement officers, and administrators. It must support both transactional workflows and analytics at a scale of 10 million candidate profiles.

### Problem Statement

Northbridge Workforce Systems operates several legacy systems that hold job requirements, candidate profiles, and applications in inconsistent formats. Candidate retention on existing channels is low, and there is no single view of the hiring pipeline. Your team must design the database that resolves this, and prove the design meets the stated non-functional requirements.

### Scope

**In scope:** logical and physical data model, seed data generation, performance validation, security design, analytics layer, licence and device management, payments and subscriptions, multilingual UI data support, and a panel defence.

**Out of scope:** building the user interface, integrating with live external services, loading the full 10 million profiles on a laptop (see Section 12), ETL and data migration (to be issued as a separate requirement).

---

## 2. Business Context

### Job Categories in Scope

- Gig and project-based work (the primary revenue category, described in the gig subsection below)
- General staffing (blue and grey collar, semi-white collar contract roles)
- Professional staffing (white collar IT and non-IT contract roles)
- Permanent full-time roles
- Apprentice hiring
- Campus and fresher hiring
- Consultant marketplace, where freelance recruiters source candidates
- Managed remote workforce requests

### Gig Work: Distinct Business Rules

- Gig requests are short-duration, project-based, and carry a start and end window and a defined price (fixed or rate-based)
- Gig workers apply to gig requests. Bids do not flow in both directions. The requester accepts or rejects the application
- Gig workers have a separate profile from permanent and temporary candidates, with availability, service offerings, and charge rates
- Requesters can maintain favourite and blacklisted gig workers
- Once a gig application is accepted, fulfilment happens in an external system. The platform stores the handoff reference and reconciles status; it does not run fulfilment itself
- Earnings figures (estimated, total, and projected) are sourced from the external fulfilment system and must be modelled as an integration boundary
- Requesters rate completed gigs

Your design must decide whether gigs share the core job and application model with other job types, or sit in a separate subsystem. You must justify the decision, including the trade-offs for reporting, integrity, and future change.

### Core Workflows the Database Must Support

- Candidate registration, login, profile building, resume creation, and self-assessment
- Job search, application, and application status tracking
- Employer and client registration, verification, and job requirement posting
- Questionnaire creation attached to job requirements
- Candidate sourcing by employers and recruiters, including bulk outreach
- Consultant requests to source for a job, and acceptance or decline by the employer
- Blocking of users and companies
- Wallet recharge and credit consumption for postings, profile downloads, and outreach
- Notifications and job alerts
- Support tickets and FAQs

> **Note:** In this hackathon, the workflows above are the reference. You decide which are central to the schema and which are secondary, and you must justify that choice.

---

## 3. Actors and Privileges

### Actors

Super Admin, Admin, Account Admin, Guest, Candidate, Employer, Recruiter, Consultant, Placement Officer (TPO).

### Rules You Must Honour

- Every privilege must be enforced in the database or the access layer, not only in the interface. Describe where and how.
- A recruiter may only see and act on job requirements assigned to their own company.
- A consultant may only work on job requirements that are open to consultants.
- A candidate may block employers, recruiters, or consultants, and a blocked party must not see that candidate.
- Recruiters cannot register candidates on a candidate's behalf. They may only contact candidates.
- Every sensitive action must be recorded in an audit trail.

Explicitly out of scope for this version: sub-roles inside an employer organisation (for example separate posting, interviewing, and pipeline roles). Note this in your design and explain how you would extend it later.

---

## 4. Data Elements and Coverage

**Purpose:** This section tells you which data domains the platform must model, and what the inputs do and do not cover. It does not give you a table list or a complete field list. Deriving the schema is your job.

### Tier 1: Mandatory domains (must be modelled)

- **Identity and accounts:** users, credentials, roles, sessions, consent records
- **Candidate profile core:** personal details, contact details, education, work experience, skills, languages
- **Employer and client organisation:** company details, locations, tax and registration identifiers, verification status, mapped recruiters
- **Job requirement lifecycle:** posting, status, positions, requirements, publish state, consultant openness
- **Applications and pipeline:** applied, shortlisted, interviewed, offered, joined, rejected
- **Sourcing and consultant requests:** sourcing requests, acceptance or decline, candidate shortlists
- **Wallet and credits:** wallet balances, recharges, credit consumption, transactions
- **Audit and error logging:** audit trail of sensitive actions, system errors
- **Master data:** cities, states, countries, skills, qualifications, industries, job roles, employment and work types
- **Notifications and alert preferences**
- **Gig marketplace:** gig requests with windows and pricing, gig worker profiles, availability, gig applications, assignments and handoff references, lists and blacklists, ratings, and earnings references from external systems
- **Licences and seats:** subscription licences, number of users allowed per licence, number of devices allowed per user, and seat allocation
- **Devices and sessions:** registered devices per user, active sessions, login history, and the rule enforced when a device limit is reached
- **Subscriptions and plans:** plans, subscription periods, renewals, cancellations, and entitlements per plan
- **Payments and transaction history:** payment orders, payment status, invoices, refunds, and wallet ledger entries
- **Account and organisation:** account profiles, account administrators, account status, and organisation hierarchy
- **Preferences:** notification preferences, language preference, job alert preferences, and privacy settings
- **Profile projects:** candidate projects and gig projects, with their role, duration, and outcome
- **Profile download history:** which recruiter or employer downloaded which candidate profile, when, and under which credit deduction

### Tier 2: Expected domains (should be modelled, or the omission must be justified)

- Resume versions and templates, community posts and reactions, assessments and scores, course enrolments, support tickets, FAQs, referral tracking, blocking lists, profile completion scoring
- **Profile view history:** which employers or recruiters viewed a candidate profile, and when
- Job search and application history for candidates
- **Candidate activity log:** login, profile update, and resume download events
- Shortlist and bookmark history for employers and recruiters

### Tier 3: Reference domains (optional, scored as additional coverage)

- Salary benchmarks, company ratings, trending job snapshots, dashboard aggregates, feature flags

### Competitive coverage benchmark

Participants must compare their data coverage against at least three competitor job platforms: Naukri, Monster, and LinkedIn. For each one, list the data elements the platform is known to capture on candidate and employer profiles, and show where your own design matches, exceeds, or falls short. The aim is to exceed the coverage of all three in the domains that matter to this platform.

---

## 5. Data Standards and Domain Rules

These rules are mandatory for every design submitted. They exist so that every team is assessed against the same baseline, and so that decisions can be compared fairly. A team may depart from a rule only when it records the deviation, the reason, and the risk in its schema justification document. A justified deviation is scored well. An undocumented deviation is scored as a defect.

### Rule group 1: Naming conventions

- All table and column names use snake_case.
- Table names are singular nouns (for example, `candidate_profile`, not `candidate_profiles`).
- Primary keys are named `id`, and foreign keys use the referenced table name followed by `_id` (for example, `job_requirement_id`).
- No reserved words, no abbreviations that are not defined in the module design, and no spaces or special characters.
- Reserved keywords for the chosen engine must not be used as identifiers. Participants must check the engine's reserved word list for both MySQL and PostgreSQL where the design targets both.
- Index names follow the pattern `idx_table_column`, and unique constraint names follow `uq_table_column`.

### Rule group 2: Keys and constraints

- Every entity has a surrogate primary key. The type must be stated in the design (for example, 64-bit integer or UUID) and applied consistently.
- Natural keys, such as a national identifier or a source record identifier, are kept as unique constraints, not as primary keys.
- Every foreign key is enforced by a database constraint, with a stated rule for update and delete behaviour.
- Check constraints must be used for business rules that can be expressed in the database, such as an end date that is not earlier than its start date, or a minimum salary that does not exceed a maximum salary.
- Concurrency and licence limits must be enforced by the database through constraints or transactional locking, not only by the application. The design must state which mechanism is used.
- Device limits must be enforced at the point of login. The design must state how an active session is counted against the device limit and how a session is released.

### Rule group 3: Standard domains

Use these domains for every column that holds the matching kind of data. Where a column does not fit a domain, the design must explain why.

- **Identifier:** surrogate key as defined in rule group 2.
- **Person name:** text, up to 150 characters, UTF-8, with no assumption about script or word order.
- **Free text:** text, UTF-8, with a stated maximum length. Unbounded text is only allowed for content such as job descriptions and resume bodies, and must be justified.
- **Email address:** text, up to 254 characters, stored in lowercase, with a unique constraint where the email identifies a single account.
- **Phone number:** stored in E.164 format with the country code, with a separate field for the country.
- **Tax and registration identifiers:** stored as text with a country and type code. Validation rules must be stated.
- **Money:** fixed-precision decimal, never floating point. Every money column has a companion currency code column.
- **Percentage and ratio:** fixed-precision decimal with a stated range.
- **Date:** calendar date, without time.
- **Timestamp:** stored in UTC, with the time zone of the source recorded where it matters for display.
- **Date range:** start and end values with a check constraint that the end is not earlier than the start.
- **Enumeration:** any fixed set of values is held in a lookup table with a code, a display name per supported UI language, an active flag, and a sort order. Free-text enumerations are not allowed.
- **Boolean flag:** not null, with a stated default.
- **Coordinates:** latitude and longitude, with a stated precision and a stated source.
- **UI label:** a key that identifies a UI string, with translations held separately per language. Keys are never displayed to users.

### Rule group 4: Audit and lifecycle

- Every table that holds business data has `created_at`, `created_by`, `updated_at`, and `updated_by`.
- Each entity is classified as soft-deleted or hard-deleted, with the rule stated in the design. Soft-deleted entities carry a `deleted_at` column and are excluded from default queries.
- Audit records are append-only. Application users cannot update or delete them.
- Retention rules for candidate data, including deletion on request and on inactivity, are stated per entity and implemented in the schema or in a scheduled process.

### Rule group 5: Nullability and unknown values

- Every nullable column must have a written justification.
- Where the difference matters, "unknown" and "not applicable" are stored differently. For example, a salary that was not disclosed is not the same as a salary of zero.
- Sentinel values such as zero, the text "NA", or an empty string must not be used to mean "missing".

### Rule group 6: Sensitive data classification

Every column is tagged with one of the following classes in the data dictionary:

- **Public:** may be shown without restriction
- **Internal:** available to staff and to the owning organisation
- **Personal:** identifies a person and requires access control and audit
- **Sensitive:** includes national identifiers, tax identifiers, bank or payment references, and credentials. Requires encryption, tokenisation, or masking, with the approach stated in the security design note.

### Rule group 7: Multilingual data

- All text is stored in UTF-8 end to end.
- UI strings are stored by key in a translation table with one row per key and language, and fallback to the default language is defined in the design.
- User-entered content, such as job descriptions and resume text, is stored once in the language it was entered, with the language recorded where it is known.

### Rule group 8: Naming of physical objects

- Partitions, views, and materialised views follow a stated naming pattern.
- Analytical objects are prefixed to distinguish them from transactional tables.

### Rule group 9: Licensing, devices, and sessions

- Every licence has a maximum number of users and a maximum number of devices per user. Both values are stored as explicit columns with check constraints that they are positive integers.
- A user cannot hold more active sessions than the number of devices allowed by their licence. The attempt to exceed the limit is recorded in the audit trail.
- Sessions are stored with device identifier, start time, last activity time, and end time. Expired or revoked sessions are not counted against the limit.
- Revoking a device or session is an auditable action.

### Rule group 10: Money, subscriptions, and history

- Payment and wallet ledgers are append-only. A correction is recorded as a new entry that references the original, never as an update.
- Subscription periods must not overlap for the same licence, and this is enforced by a constraint or exclusion rule where the engine supports it.
- Download and view history records are append-only and carry the actor, the subject, the timestamp, and the credit deduction where one applies.

### Compliance check

Before submission, the team must complete a rule compliance table that lists every rule group, whether the design complies, and any deviation with its reason. The module design checklist in Section 7 includes this check.

| Rule group | Design complies | Deviation with reason |
|---|---|---|
| 1: Naming conventions | | |
| 2: Keys and constraints | | |
| 3: Standard domains | | |
| 4: Audit and lifecycle | | |
| 5: Nullability and unknown values | | |
| 6: Sensitive data classification | | |
| 7: Multilingual data | | |
| 8: Naming of physical objects | | |
| Reserved keyword check | | |
| 9: Licensing, devices, and sessions | | |
| 10: Money, subscriptions, and history | | |

---

## 6. Integration Boundaries

The platform depends on several external systems. Your schema must reference data from these systems, but it does not own their internal processes. For each boundary below, you must decide what the database stores, what it only references, and how it handles the external system being unavailable.

### External boundaries

- **Payment gateway:** stores transaction references, status, and amounts. Does not store card or bank data.
- **Identity verification service (national identity checks):** stores verification outcome and a token. Does not store raw identity numbers in clear text.
- **Company registration and tax verification:** stores verification status and the identifiers used for lookup.
- **Gig fulfilment system:** stores the handoff reference, assignment status, and reconciled earnings. Does not run fulfilment.
- **Legacy job and candidate systems:** a source of migration and periodic sync. Participants must define the sync direction for each field and the rule for conflicts.
- **Messaging and notification providers (email, SMS, messaging app):** stores delivery status and preferences. Does not store message bodies longer than required for audit.
- **Social sharing:** outbound links only. Stores share events, not the external post content.
- **Mapping and geolocation service:** stores coordinates and place references. Treat as optional.
- **Company review and salary data:** reference only, with source attribution and refresh date.

### Required for each boundary

- **Data ownership:** which system is the source of truth
- **Stored versus referenced data**
- **Failure behaviour:** what the platform does when the external system is down
- **Idempotency:** how duplicate events are prevented
- **Audit:** how the exchange is recorded

Participants must document these in a table, and the table is reviewed at the panel.

| Boundary | Data Ownership | Stored vs. Referenced | Failure Behavior | Idempotency | Audit |
|---|---|---|---|---|---|
| Payment gateway | (Participant decision) | Stores transaction references, status, and amounts. Does not store card or bank data. | (Participant decision) | (Participant decision) | (Participant decision) |
| Identity verification service | (Participant decision) | Stores verification outcome and a token. Does not store raw identity numbers in clear text. | (Participant decision) | (Participant decision) | (Participant decision) |
| Company registration & tax verification | (Participant decision) | Stores verification status and the identifiers used for lookup. | (Participant decision) | (Participant decision) | (Participant decision) |
| Gig fulfilment system | (Participant decision) | Stores the handoff reference, assignment status, and reconciled earnings. Does not run fulfilment. | (Participant decision) | (Participant decision) | (Participant decision) |
| Legacy job & candidate systems | (Participant decision) | Source of migration and periodic sync. Participants must define sync direction for each field and conflict rule. | (Participant decision) | (Participant decision) | (Participant decision) |
| Messaging & notification providers | (Participant decision) | Stores delivery status and preferences. Does not store message bodies longer than required for audit. | (Participant decision) | (Participant decision) | (Participant decision) |
| Social sharing | (Participant decision) | Outbound links only. Stores share events, not the external post content. | (Participant decision) | (Participant decision) | (Participant decision) |
| Mapping & geolocation service | (Participant decision) | Stores coordinates and place references. Treat as optional. | (Participant decision) | (Participant decision) | (Participant decision) |
| Company review & salary data | (Participant decision) | Reference only, with source attribution and refresh date. | (Participant decision) | (Participant decision) | (Participant decision) |

### Environment and Sizing

Participants bring their own laptops. The minimum recommended specification is:

- 16 GB RAM (32 GB recommended)
- 8 CPU cores
- 512 GB free SSD space
- Docker or a local PostgreSQL or MySQL installation
- A scripting language of the participant's choice for data generation and ETL

**Laptop-scale seed:** the seeded dataset on the laptop is 1 million candidate profiles, 100,000 job requirements, and 5 million applications. This is the scale on which search and performance are measured directly.

**Full-scale target:** 10 million candidate profiles, 1 million job requirements, and 100 million applications. These are not loaded on laptops. Participants must produce an extrapolation.

**Extrapolation method:** participants must measure at laptop scale, then project to full scale using a stated model (for example, index size growth, buffer cache hit ratio change, and query plan changes at larger row counts). The projection must identify which query will degrade first and why. Projections without a measured basis at laptop scale are not accepted.

**Optional:** if a cloud credit is provided, a team may run one query set at 10 million profiles and compare the result against their extrapolation. This is bonus evidence, not a requirement.

> **Note:** the data generator is part of the deliverable. Participants must produce synthetic data that respects the schema, the data-quality issues they identified, and the distributions they state in their design.

---

## 7. Delivery Plan: Milestones and Backlogs

The work is split into phases, milestones and backlogs. Each phase contains milestones, and each milestone is broken into backlogs with a deliverable. Complete the milestones in order. A milestone is done when its deliverable exists.

### Modules

There are eleven modules in total. Three are common and designed by the whole team in Phase 1. Six are individual modules. Each member owns one module in Phase 2 or Phase 3, as shown in the Owner column. Two further items are optional stretch coverage and are not assigned to a module. Every item in Section 4 is owned by one module.

| Module | Type | Scope | Owner |
|---|---|---|---|
| Master data | Common (Phase 1) | Cities, states, countries, skills, qualifications, industries, job roles, employment types, work types, languages, reference codes for every enumeration, UI translation keys | Team |
| Profile | Common (Phase 1) | Candidate identity and contact, education, work experience, skills, languages, resume versions and templates, profile projects, assessments and scores, course enrolments, profile completion scoring, profile view history, candidate activity log, shortlist and bookmark history, referral tracking | Team |
| Jobs and Applications | Common (Phase 1) | Job requirements, positions, questionnaires, publish state, applications, status pipeline, shortlists, interviews, offers, joins, rejections, candidate job search and application history | Team |
| Administration | Individual (Phase 2) | Users, credentials, roles, permissions, sessions (identity only), audit trail, consent records, blocking lists for candidates, employers, recruiters and consultants | Member A |
| Organisation and account | Individual (Phase 2) | Employer and client organisations, company details, locations, tax and registration identifiers, verification status, recruiter mapping (the owning module for mapped recruiters), account administrators, account status, organisation hierarchy | Member B |
| Gig marketplace | Individual (Phase 3) | Gig requests with windows and pricing, gig worker profiles, availability, gig applications, assignments, handoff references, earnings references from external systems, lists and blacklists for gig workers, ratings | Member C |
| Licences, seats, devices and sessions | Individual (Phase 3) | Subscription licences, users allowed per licence, devices allowed per user, seat allocation, device registration, active sessions, login history, session limits, revocation | Assigned member |
| Sourcing and consultant requests | Individual (Phase 3) | Sourcing requests, consultant openness, acceptance or decline, bulk outreach batches and recipients, candidate sourcing shortlists (referencing the Jobs and Applications shortlist) | Assigned member |
| Subscriptions, wallet, payments and download history | Individual (Phase 3) | Plans, subscription periods, renewals, cancellations, entitlements, payment orders and status, invoices, refunds, wallet balances and ledger, credit consumption for downloads and outreach, profile download history | Assigned member |
| Notifications, preferences and community | Individual (Phase 3) | Notification preferences, job alert preferences, language preference, privacy settings, notification delivery status, community posts and reactions, support tickets, FAQs, referral events | Assigned member |
| Analytics layer and reporting marts | Individual (Phase 3) | Star schema, dimensions, facts, aggregates for the KPIs in Section 8 | Assigned member |

### Optional stretch coverage (not assigned to a module)

Tier 3 domains are optional and are not assigned to a module: salary benchmarks, company ratings, trending job snapshots, dashboard aggregates, and feature flags. Teams that take them on must add them as a separate, clearly labelled module and record the decision in their schema justification document.

---

### Phase 1: Foundation (whole team, common modules Profile and Jobs and Applications)

#### Milestone 1: Identify and fix the DB provider

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B1 | Identify and fix the DB provider (MySQL or PostgreSQL) with a reason | Team | DB provider decision |
| B2 | Read the requirement document and agree the module list | Team | Agreed module list |
| B3 | Assign the common modules: Profile, and Jobs and Applications | Team | Common module assignment |

#### Milestone 2: Entities for Profile and Jobs and Applications

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B4 | Identify entities for Profile | Team | Profile entity list |
| B5 | Validate the Profile entities | Team | Validated Profile entity list |
| B6 | Identify entities for Jobs and Applications | Team | Jobs and Applications entity list |
| B7 | Validate the Jobs and Applications entities | Team | Validated Jobs and Applications entity list |

#### Milestone 3: Relationships for Profile and Jobs and Applications

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B8 | Identify relationships for Profile | Team | Profile relationship model |
| B9 | Validate the Profile relationships | Team | Validated Profile relationships |
| B10 | Identify relationships for Jobs and Applications | Team | Jobs and Applications relationship model |
| B11 | Validate the Jobs and Applications relationships | Team | Validated Jobs and Applications relationships |

#### Milestone 4: Columns for Profile and Jobs and Applications

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B12 | Identify columns for Profile | Team | Profile column list |
| B13 | Validate the Profile columns | Team | Validated Profile columns |
| B14 | Identify columns for Jobs and Applications | Team | Jobs and Applications column list |
| B15 | Validate the Jobs and Applications columns | Team | Validated Jobs and Applications columns |

#### Milestone 5: Design for Profile and Jobs and Applications

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B16 | Combine entities and columns into the Profile design | Team | Profile design draft |
| B17 | Validate the Profile design | Team | Validated Profile design |
| B18 | Combine entities and columns into the Jobs and Applications design | Team | Jobs and Applications design draft |
| B19 | Validate the Jobs and Applications design | Team | Validated Jobs and Applications design |

#### Milestone 6: Keys and constraints for Profile and Jobs and Applications

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B20 | Add keys and constraints to Profile | Team | Profile keys and constraints |
| B21 | Validate the Profile keys and constraints | Team | Validated Profile keys and constraints |
| B22 | Add keys and constraints to Jobs and Applications | Team | Jobs and Applications keys and constraints |
| B23 | Validate the Jobs and Applications keys and constraints | Team | Signed Phase 1 design |

---

### Phase 2: Individual Modules (Administration, Organisation and account, Gig marketplace)

#### Milestone 7: Entities across the three modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B24 | Identify entities for Administration: users, roles, permissions and the audit trail | Member A | Administration entity list |
| B25 | Identify entities for Organisation and account: employer and client organisations, locations, and account administrators | Member B | Organisation entity list |
| B26 | Identify entities for Gig marketplace: gig requests, gig worker profiles, availability and gig applications | Member C | Gig entity list |

#### Milestone 8: Relationships across the three modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B27 | Identify relationships for Administration, including user to role and role to permission | Member A | Administration relationship model |
| B28 | Identify relationships for Organisation and account, including the organisation hierarchy and recruiter mapping | Member B | Organisation relationship model |
| B29 | Identify relationships for Gig marketplace, including assignments, handoff references and ratings | Member C | Gig relationship model |

#### Milestone 9: Columns across the three modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B30 | Identify columns for Administration, with data type, domain, nullability and sensitive data class | Member A | Administration column list |
| B31 | Identify columns for Organisation and account, with data type, domain, nullability and sensitive data class | Member B | Organisation column list |
| B32 | Identify columns for Gig marketplace, with data type, domain, nullability and sensitive data class | Member C | Gig column list |

#### Milestone 10: Keys and constraints across the three modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B33 | Add keys and constraints for Administration, including role-based access enforcement and the append-only audit trail | Member A | Administration keys and constraints |
| B34 | Add keys and constraints for Organisation and account, including effective-dated recruiter mapping | Member B | Organisation keys and constraints |
| B35 | Add keys and constraints for Gig marketplace, including the rule that gig windows cannot overlap for the same gig worker | Member C | Gig keys and constraints |

---

### Phase 3: Individual Modules (Licences, Sourcing, Subscriptions)

#### Milestone 11: Entities across the three modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B36 | Identify entities for Licences, seats, devices and sessions: licences, seats, devices, sessions, login attempts and revocation events | Member A | Licences entity list |
| B37 | Identify entities for Sourcing and consultant requests: sourcing requests, consultant openness, acceptance or decline decisions, outreach batches and recipients | Member B | Sourcing entity list |
| B38 | Identify entities for Subscriptions, wallet, payments and download history: plans, subscription periods, payment orders, wallet ledger, refunds, invoices and profile download events | Member C | Subscriptions entity list |

#### Milestone 12: Relationships across the three modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B39 | Identify relationships for Licences, seats, devices and sessions, including licence to user, user to device, and session to device | Member A | Licences relationship model |
| B40 | Identify relationships for Sourcing and consultant requests, including request to requirement, request to consultant, and recipient to candidate | Member B | Sourcing relationship model |
| B41 | Identify relationships for Subscriptions, wallet, payments and download history, including subscription to licence, payment to subscription, and ledger entry to wallet | Member C | Subscriptions relationship model |

#### Milestone 13: Columns across the three modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B42 | Identify columns for Licences, seats, devices and sessions, with data type, domain, nullability and sensitive data class | Member A | Licences column list |
| B43 | Identify columns for Sourcing and consultant requests, with data type, domain, nullability and sensitive data class | Member B | Sourcing column list |
| B44 | Identify columns for Subscriptions, wallet, payments and download history, with data type, domain, nullability and sensitive data class | Member C | Subscriptions column list |

#### Milestone 14: Keys and constraints across the three modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B45 | Add keys and constraints for Licences, seats, devices and sessions, including the mechanism that enforces the device limit under concurrent logins | Member A | Licences keys and constraints |
| B46 | Add keys and constraints for Sourcing and consultant requests, including the rule that a consultant can act only on a requirement open to consultants | Member B | Sourcing keys and constraints |
| B47 | Add keys and constraints for Subscriptions, wallet, payments and download history, including non-overlapping subscription periods and the append-only ledger rule | Member C | Subscriptions keys and constraints |

---

### Phase 4: Notifications, Analytics and Shared Team Items

#### Milestone 15: Entities across the modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B48 | Identify entities for Notifications, preferences and community: notification preferences, job alert preferences, language preference, privacy settings, notification deliveries, community posts, reactions, support tickets, FAQs and referral events | Team | Notifications entity list |
| B49 | Identify entities for Analytics layer and reporting marts: dimensions, facts and aggregates for the KPIs in Section 8 | Team | Analytics entity list |
| B50 | Identify entities for the shared team items: credentials, consent records, identity sessions, the audit trail and blocking lists for candidates, employers, recruiters and consultants | Team | Shared team entity list |

#### Milestone 16: Relationships across the modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B51 | Identify relationships for Notifications, preferences and community, including preference to user, delivery to notification, and post to reaction | Team | Notifications relationship model |
| B52 | Identify relationships for Analytics layer and reporting marts, including fact to dimension and aggregate to fact | Team | Analytics relationship model |
| B53 | Identify relationships for the shared team items, including credential to user, consent to user, session to user, audit event to actor, and blocking entry to blocker and blocked party | Team | Shared team relationship model |

#### Milestone 17: Columns across the modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B54 | Identify columns for Notifications, preferences and community, with data type, domain, nullability and sensitive data class | Team | Notifications column list |
| B55 | Identify columns for Analytics layer and reporting marts, with data type, domain, nullability and sensitive data class | Team | Analytics column list |
| B56 | Identify columns for the shared team items, with data type, domain, nullability and sensitive data class, including the sensitive class for credentials | Team | Shared team column list |

#### Milestone 18: Keys and constraints across the modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B57 | Add keys and constraints for Notifications, preferences and community, including the high-volume write path for deliveries | Team | Notifications keys and constraints |
| B58 | Add keys and constraints for Analytics layer and reporting marts, including slowly changing dimension keys and aggregate refresh rules | Team | Analytics keys and constraints |
| B59 | Add keys and constraints for the shared team items, including the append-only audit trail, the unique constraint on credentials, and the rule that a blocked party cannot see the blocker's candidate | Team | Shared team keys and constraints |

---

### Phase 5: Seeding, Performance and Security

#### Milestone 19: Seed data across all modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B60 | Seed Master data and Profile at laptop scale: 1 million candidate profiles with duplicates and multilingual names, and all reference lookups populated | Team | Seeded Master data and Profile |
| B61 | Seed Jobs and Applications at laptop scale: 100,000 job requirements and 5 million applications across the full status pipeline | Team | Seeded Jobs and Applications |
| B62 | Seed Administration, Organisation and account, and Gig marketplace: users, roles, organisations, mapped recruiters, gig requests and assignments including overlap attempts | Member A | Seeded Administration, Organisation and Gig data |
| B63 | Seed Licences, Sourcing and Subscriptions: licences, sessions, sourcing requests, subscriptions, payments, refunds and downloads | Member B | Seeded Licences, Sourcing and Subscriptions data |
| B64 | Seed Notifications, Analytics and the shared team items: notification deliveries, community posts, credentials, consent records, audit events and blocking entries | Member C | Seeded Notifications, Analytics and shared team data |

#### Milestone 20: Queries and validation across all modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B65 | Write and run the fixed query set (about twelve queries) covering functional, transactional, lookup and performance cases, with execution plans for the top ten | Team | Query set with output and execution plans |
| B66 | Validate the seeded data against the data standards in Section 5 and the coverage gap register, and record any failures | Team | Seed validation report |
| B67 | Write module-specific queries and validate the seeded data for each module, and record the results | Member A | Module query results for Administration, Organisation and Gig |
| B68 | Write module-specific queries and validate the seeded data for each module, and record the results | Member B | Module query results for Licences, Sourcing and Subscriptions |
| B69 | Write module-specific queries and validate the seeded data for each module, and record the results | Member C | Module query results for Notifications, Analytics and shared items |

#### Milestone 21: Performance across all modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B70 | Measure baseline query latency for the top ten queries using the method in Section 10 (hardware, dataset size, query text, number of runs, p50, p95, p99) | Team | Performance baseline report |
| B71 | Identify and justify one index per module with a before-and-after measurement | Member A | Index justification for Administration, Organisation and Gig |
| B72 | Identify and justify one index per module with a before-and-after measurement | Member B | Index justification for Licences, Sourcing and Subscriptions |
| B73 | Identify and justify one index per module with a before-and-after measurement, and the partitioning strategy for the application and notification tables | Member C | Index justification for Notifications, Analytics and partitioning |
| B74 | Write the full-scale extrapolation to 10 million profiles and 100 million applications, identifying the query expected to degrade first and why | Team | Extrapolation to full scale |

#### Milestone 22: Security across all modules

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B75 | Define PII handling for each module: encryption, tokenisation or masking for contact details, national identifiers and tax identifiers, with the approach stated | Member A | PII handling notes for Administration, Organisation and Gig |
| B76 | Define PII handling for each module: encryption, tokenisation or masking for payment references and credentials, with the approach stated | Member B | PII handling notes for Licences, Sourcing and Subscriptions |
| B77 | Define PII handling for each module: encryption, tokenisation or masking for candidate contact details in notifications and community, with the approach stated | Member C | PII handling notes for Notifications, Analytics and shared items |
| B78 | Write the threat model for the schema and the controls applied: access control by role, audit trail, password hashing, and session controls | Team | Threat model and controls |
| B79 | Test that access rules hold for each module: a user cannot read or change data outside their role, and the audit trail records every sensitive action | Team | Access and audit test results |

#### Milestone 23: Backup, restore and sign-off

| ID | Backlog | Owner | Deliverable |
|---|---|---|---|
| B80 | Perform a backup and restore of the seeded database, and record the time taken against the RTO in Section 10 | Team | Backup and restore log |
| B81 | Write the security design note, combining the module notes, the threat model and the audit design | Team | Security design note |
| B82 | Gate 2 review: confirm the seeded database, query set, performance evidence, backup restore and security note are complete | Team | Gate 2 sign-off |

---

## Good luck

Build it properly, measure what you build, and be honest about what you could not finish. The best designs are the ones that can defend themselves. We look forward to seeing what you create. All the best.

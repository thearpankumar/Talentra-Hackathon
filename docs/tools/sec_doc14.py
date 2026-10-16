"""Content of docs/14_threat_model.md (rendered by build_security_docs.py)."""

# id, abuse case, adversary, assets, attack path, controls (tables / columns / rules), inherent, residual, tests
THREATS = [
 ("T-01", "Scraping candidate PII at scale", "Competitor / data broker posing as recruiter or using many trial accounts",
  "candidate_profile, candidate_contact_*, resume, candidate_media_file",
  "Paid or trial licence, automated search paging, contact reveal, profile downloads; or scraping the public job/company pages for names.",
  "Contact columns exposed only through vw_candidate_profile_recruiter (masked unless is_contact_visible_to_recruiters) and after a metered unlock; profile_download append-only with actor, subject, credit (BR-SUB-19); wallet debit idempotent and balance-checked (BR-SUB-08); per-licence quotas (subscription_entitlement, spend_limit BR-SUB-22); security_event types mass_download / anomalous_export; page caps and rate limits at edge; licence_seat + device limits (BR-LIC-05); organisation verification before purchase (F-07).",
  "High", "Medium", "AT-37, AT-21, AT-11"),
 ("T-02", "Recruiter enumeration (candidates, contact details, who blocked whom, account existence)", "Malicious recruiter, spammer",
  "user_block, organisation_block, app_user (email/phone), search results",
  "Probe vw_candidate_visible_to_user to learn who blocked me (difference between 'no result' and 'hidden'); register/reset/login responses reveal whether an e-mail exists; guess candidate ids.",
  "Blocked candidates are simply absent (no 'blocked' marker, BR-ADM-12); UUID v4 ids (D-02) are unguessable; uniform responses for login/reset/register with timing equalisation; login_attempt stores identifier_hmac only; per-IP/identifier throttling; unique e-mail only among live rows (BR-ADM-02) but never echoed; outreach exclusion reasons are visible only to the sender's own tenant and do not distinguish 'blocked' from 'opted out' externally (outreach_exclusion).",
  "Medium", "Low", "AT-05, AT-36"),
 ("T-03", "Credential stuffing and password spraying", "Botnet with breach lists",
  "user_credential, identity_session, app_user",
  "Replay of leaked e-mail/password pairs against the login API across many IPs.",
  "argon2id (m=64 MiB, t=3, p=4) slows offline attack and online throughput (user_credential.argon2_*); breach-password check (breach_checked_at / breach_detected_at, must_change); MFA (mfa_factor) and step-up; throttling per identifier and IP with login_attempt evidence (BR-LIC-12); security_event(credential_stuffing, mfa_fatigue); device trust challenge for new devices (device_trust_challenge); licence device limit denies parallel abuse (BR-LIC-05).",
  "High", "Medium", "AT-36, AT-15"),
 ("T-04", "Fake employers and scam jobs", "Fraudsters (advance-fee, data harvesting, ghost jobs)",
  "organisation, job_requirement, outreach_batch, candidate_profile",
  "Register a company with stolen/invented identifiers, post attractive jobs, harvest applicants' PII or money.",
  "organisation_identifier unique per (country, tax id type, HMAC) (BR-ORG-13), organisation_verification per attempt with vendor reference (BR-ORG-15), organisation_domain verified (BR-ORG-16), verification_status gating (recommended rule R-02, finding F-07), job_report and scam_job_report (trust and safety), fraud_signal, trust_score, account_restriction, spam_rule; confidential employer display only by approved status; payment_order/wallet makes posting costly; moderation_case; reputation_list_entry.",
  "High", "Medium", "AT-51, AT-37"),
 ("T-05", "Wallet fraud and double spend", "Insider, malicious customer, race-condition exploiter",
  "wallet, wallet_ledger_entry, wallet_expiry_bucket, profile_download, outreach_credit_usage",
  "Replay a debit, fire parallel debits, tamper with idempotency keys, write the balance directly, forge a reversal.",
  "Single writer role billing_svc (GR-17/18); SERIALIZABLE + SELECT FOR UPDATE on wallet + CHECK credit_balance >= 0 + UNIQUE (wallet_id, sequence_no) + UNIQUE idempotency_key (BR-SUB-06..09, D-14); append-only ledger with hash chain (BR-SUB-10) and reversal uniqueness (reverses_wallet_ledger_entry_id); nightly vw_wallet_balance_drift; staff adjustment as new ledger row with four-eyes (admin_action_approval); changefeed to immutable sink.",
  "High", "Low", "AT-10, AT-11, AT-41, AT-53"),
 ("T-06", "Refund and chargeback abuse", "Customer or colluding staff",
  "refund, payment_order, wallet_ledger_entry, coupon_redemption",
  "Request refunds beyond amount paid, self-approve, refund after consuming credits, reuse coupons.",
  "SUM(refund) <= amount paid under row lock (BR-SUB-15); CHECK approved_by <> requested_by; credit clawback ledger row; coupon max redemptions and per-organisation limits (BR-SUB-21); reconciliation vs gateway (BR-SUB-27); audit_event(approval_decision) and recommended refund_issue code.",
  "Medium", "Low", "AT-50"),
 ("T-07", "Licence sharing and device-limit bypass", "Customers sharing seats; malicious users",
  "licence, licence_seat, device_slot, device_session",
  "Share credentials across many devices, race parallel logins, clone device fingerprints, reuse a stolen refresh token elsewhere.",
  "Slot tables make the limit declarative (uq_device_session_device_slot_id WHERE ended_at IS NULL) plus seat-row FOR UPDATE in the login txn (BR-LIC-05/06); violations recorded with audit_event (BR-LIC-07); fingerprints stored as HMAC; device_trust_challenge; refresh rotation with family revocation (BR-ADM-16); licence_ip_allowlist; revoke is auditable (BR-LIC-09).",
  "Medium", "Low", "AT-13, AT-14, AT-48"),
 ("T-08", "Session fixation, token theft and replay", "Network or XSS attacker, malware",
  "identity_session, refresh tokens, access tokens",
  "Plant a session id before login; steal a refresh token from storage; replay a rotated token.",
  "New session id at login/step-up; refresh_token_hash only (SHA-256 of 256-bit random) so DB leak does not yield tokens; single-use rotation with token_family_ref; reuse revokes the family and raises security_event(refresh_token_reuse); absolute_expires_at/idle timeouts from licence; httpOnly+Secure+SameSite cookies, CSRF tokens; TTL purge (BR-ADM-17); acting_organisation_id bound in session.",
  "High", "Low", "AT-15, AT-49"),
 ("T-09", "Insider bulk export (DBA, support, analyst)", "Malicious or coerced insider",
  "all Personal data, backups, audit",
  "Direct SELECT, COPY, backup copy, BI export, support unmasking, break-glass misuse.",
  "No human has standing DB access; roles per tier; staff read masked views only (vw_app_user_public) and unmask is audited with reason (view_sensitive); analytics_reader sees no Personal keys; audit_export needs approval and is itself audited; break-glass window <= 4 h, approver <> requester (BR-ADM-18), SQL audit logs shipped to SIEM; backups encrypted, backup_operator has no SELECT; mass_download/anomalous_export security_event for staff too; separation: no one holds both KMS decrypt and DB read except compliance_svc.",
  "High", "Medium", "AT-23, AT-29, AT-30, AT-31, AT-46"),
 ("T-10", "SQL injection / ORM bypass of tenant scope", "External attacker, buggy developer",
  "any tenant or owner data",
  "Inject through search filters or sort keys; omit the tenant predicate in a new endpoint; ORM lazy loading crossing tenants.",
  "Persona pool roles hold view-only access for recruiter/consultant paths (no base table grant), so an injection on that pool cannot read other tenants; PF-05 removes table-wide grants on Personal tables for persona pools; composite tenant FKs (D-11) stop cross-tenant writes; DDL/GRANT impossible (no owner role); statement_timeout and row limits per pool; parameterised queries enforced by lint; owner predicate generated by one data-access layer; AT-26 fuzzing; viewer-scoped views require the viewer argument.",
  "High", "Medium", "AT-01, AT-24, AT-25, AT-26, AT-20"),
 ("T-11", "Ledger tampering", "Insider with DB access, compromised billing service",
  "wallet_ledger_entry, payment_*, invoice",
  "Edit amounts/balances, delete debits, rewrite history.",
  "INSERT/SELECT only grants (BR-SUB-07); hash chain per wallet verified nightly (BR-SUB-10); changefeed copy in immutable bucket (P5); balance-drift view; CHECK constraints (BR-SUB-09); invoice numbers gapless (BR-SUB-12) with vw_invoice_number_gap; reconciliation with gateway (BR-SUB-27) gives an independent source of truth.",
  "High", "Low", "AT-09, AT-41"),
 ("T-12", "Audit tampering or silent deletion", "Insider or attacker covering tracks",
  "audit_event, audit_chain_head, audit_chain_checkpoint, security_event",
  "UPDATE/DELETE audit rows, rewind chain heads, stop audit writes, TTL abuse.",
  "Append-only grants (BR-ADM-08); 32 hash chains + signed checkpoints in an object-lock sink (BR-ADM-09); TTL only after sink confirmation and honouring legal hold (BR-ADM-10); verifier job raises security_event(audit_chain_break); audit INSERT in the same txn as the action so 'no audit, no action' (BR-ADM-11); audit_writer membership explicit (F-02) and monitored; SIEM receives DB auth logs independently.",
  "High", "Low", "AT-09, AT-40, AT-44, AT-42"),
 ("T-13", "Backup theft or restore into an untrusted place", "Cloud-account intruder, insider, lost laptop disk",
  "backup archives, KMS keys",
  "Copy backup files, mount them elsewhere, restore, read all Personal data.",
  "Backups encrypted (KMS or passphrase) with a key distinct from the data key, stored in a separate account/bucket with object lock and access logging; backup_operator has no SELECT; Sensitive columns are already app-encrypted or hashed so a stolen backup does not reveal them; short backup retention (35 d) with erasure re-apply after restore; restore only on the backup cluster; laptop disks encrypted (BitLocker/FileVault).",
  "High", "Medium", "AT-29, AT-45"),
 ("T-14", "Supply-chain compromise (dependencies, images, CI, migrations)", "Upstream attacker",
  "application code, CockroachDB image, migration scripts, ETL jobs",
  "Poisoned library or container image; malicious migration adds a trigger or grants; CI secrets stolen.",
  "Pinned image tags (cockroachdb/cockroach:v26.2.x LTS, not floating), digest pinning and signature check; dependency lock files + SCA scanning; migrations reviewed (four eyes) and executed only through schema_owner window (E-27); grants diffed against the matrix in CI (GR-28, AT-09); no secrets in the repo (vault references); least-privilege CI identity; SBOM per release.",
  "Medium", "Medium", "AT-32, AT-45"),
 ("T-15", "Privilege escalation through roles", "Malicious account_admin or low-privilege user",
  "app_user_role, app_role_permission, admin_action_approval",
  "Grant self admin, edit role permissions, forge actor_type.",
  "app_role_eligibility composite FKs (BR-ADM-06); organisation-scoped roles need organisation_id (BR-ADM-05); authorisation catalogue writable only by super_admin with four-eyes; actor_type immutable; audit role_grant; vw_user_effective_permission cached per session with TTL and invalidation on role change.",
  "High", "Low", "AT-27, AT-28"),
 ("T-16", "Blocked-party evasion", "Recruiter or employer that was blocked",
  "user_block, organisation_block, search index",
  "Create a new recruiter account, use a colleague's account, query the stale search index, use outreach to reach the blocked candidate.",
  "Blocks apply to the organisation too (organisation_block, BR-ADM-14) and to every viewer acting for it; search authority is the query-time view (E-25); outreach admission and dispatch both re-check (BR-SRC-06); new accounts need verified e-mail/phone and organisation mapping; candidate_contact_attempt shows suppressed attempts; blocked-candidate sweep removes pool/shortlist rows (BR-SRC-15).",
  "Medium", "Low", "AT-05, AT-06, AT-08"),
 ("T-17", "Recruiter registers fake candidates / impersonates a candidate", "Recruiter gaming metrics",
  "app_user, candidate_profile",
  "Create candidate accounts on a candidate's behalf or in bulk to inflate pipelines.",
  "registration_policy composite FK rejects (candidate, recruiter|employer|consultant, *) (BR-ADM-01); no INSERT on candidate_profile for those personas; bulk_import/legacy_migration channels only for service actors; email/phone verification; duplicate_candidate_match and user_merge_request detection; BR-PRF-01 one profile per user.",
  "Medium", "Low", "AT-07"),
 ("T-18", "Consultant works outside open requirements or inflates commission", "Rogue consultant",
  "consultant_job_openness, sourcing_request, sourcing_commission",
  "Reference a closed requirement, submit candidates without consent, double-claim commission.",
  "Composite FK to the requirement's own openness row (BR-SRC-01); closing txn closes dependent rows (BR-SRC-02); consent FK on submission (BR-SRC-11); one commission per hire (BR-SRC-16); outreach requires a live sourcing_request (BR-SRC-08); decisions append-only (BR-SRC-05).",
  "Medium", "Low", "AT-03, AT-04, AT-22"),
 ("T-19", "Payment webhook forgery or replay", "External attacker",
  "payment_event, payment_order, wallet",
  "POST fake 'payment succeeded' events, replay old ones, race with checkout.",
  "Signature verification before any write; UNIQUE (payment_gateway_id, gateway_event_ref) (BR-SUB-14); payload allow-listed and encrypted; order state machine; reconciliation_run/item (BR-SUB-27); no card/bank data stored (BR-SUB-16).",
  "High", "Low", "AT-52"),
 ("T-20", "Erasure/DSR abuse or failure", "Malicious requester; internal error",
  "data_subject_request, candidate_erasure_request, backups",
  "Fraudulent erasure of someone else's data; erased data resurrected from a backup; legal hold ignored.",
  "DSR requires authenticated owner + step-up; every run audited (erasure_execute); legal_hold checked first (BR-ADM-20); anonymise-in-place keeps referential integrity; backup retention <= 35 d and re-apply job after restores; purge_job_run evidence.",
  "Medium", "Low", "AT-39"),
 ("T-21", "Account takeover via recovery channel (SIM swap, mailbox compromise)", "Fraudster",
  "app_user, password_reset_token, mfa_factor",
  "Request password reset to a hijacked phone/e-mail.",
  "Reset tokens SHA-256 hashed, short expiry, single use, 30-day TTL purge (BR-ADM-17); MFA remains required; security_event + notification to all channels on credential_change; device_trust_challenge; cooling-off for changing e-mail/phone; recovery codes hashed.",
  "High", "Medium", "AT-15, AT-49"),
 ("T-22", "Malicious uploads (resume, attachments)", "Attacker",
  "candidate_media_file, resume, message_attachment",
  "Malware, parser exploits, huge files.",
  "scan_status gating (BR-PRF-13): files served only when clean; object storage private with short-lived signed URLs; size/MIME limits; parser sandbox; resume_parse_result stored as data only, raw JSON cleared after 90 days.",
  "Medium", "Low", "AT-34"),
 ("T-23", "Gig fraud (fake assignments, overlapping windows, payout diversion)", "Fraudulent worker or requester",
  "gig_assignment, gig_slot, gig_payout_reference, gig_dispute",
  "Double-book windows, collude to inflate ratings, change payout reference.",
  "gig_slot UNIQUE (worker, slot) in the assignment txn (P2); rating only after completion (gig notes); payout reference token only, change requires re-verification and audit; dispute resolution with four-eyes; gig_fulfilment_event idempotent.",
  "Medium", "Low", "AT-17"),
 ("T-24", "Misuse of impersonation / support access", "Malicious support agent",
  "impersonation_session, any user data",
  "Impersonate a customer to read or change data.",
  "Read-only by default, write mode needs approval (BR-ADM-19); every audit row carries impersonator_app_user_id; one open session per agent; session length cap; user notified.",
  "Medium", "Low", "AT-47"),
 ("T-25", "Denial of service via expensive queries or unfiltered pair views", "Abusive user, attacker",
  "cluster availability",
  "Unbounded search, the cross-join pair view without viewer filter, mass notifications.",
  "statement_timeout per pool role (GR-29), pagination caps, viewer-context views (F-14), hash-sharded time indexes avoid hot ranges (D-02), notification fan-out via changefeed workers, edge rate limits, admission control of CockroachDB.",
  "Medium", "Medium", "AT-25, AT-36"),
 ("T-26", "Third-party personal data in imports (address books, referrals)", "Any user",
  "contact_import_entry, referral, network_invitation",
  "Upload other people's contacts to build a graph or spam.",
  "Hash-only matching (contact_match_key), 1-hour TTL on raw entries, quotas (connection_request_quota), privacy notice, no export; DPIA entry (F-12).",
  "Medium", "Medium", "AT-34"),
 ("T-27", "Key compromise (KMS, pepper, HMAC keys)", "Cloud intruder, insider",
  "encryption_key_ref, pepper_version, HMAC blind-index keys",
  "Steal a data key to decrypt tax numbers/MFA secrets or brute-force blind indexes.",
  "Envelope encryption: only wrapped DEKs in the DB; KEKs in KMS with IAM limited to compliance_svc/auth_svc; separate keys per purpose; rotation by key_version / pepper_version columns; dual-write during rotation; KMS access logging and alerts; break-glass key recovery with quorum.",
  "High", "Medium", "AT-46"),
 ("T-28", "CDC / search / analytics sink leaks", "Insider, misconfigured bucket",
  "changefeed sinks, search index, marts",
  "Read the immutable sink or the index and obtain PII outside DB controls.",
  "Sinks private, encrypted, per-sink IAM, retention-limited; changefeeds only on listed tables (GR-25); search documents from vw_candidate_search_document only and query-time visibility (E-25); marts pseudonymised (vw_etl_*), fact tables carry no raw PII.",
  "High", "Medium", "AT-30, AT-31"),
]

TEMPLATE = r"""# Threat model: Talentra schema and platform (B78)

Status: design document for the DB hackathon (CockroachDB v26.2, PostgreSQL dialect). Generated by `docs/tools/build_security_docs.py` from the data model (`docs/_staging/*.yaml`, {{N_TABLES}} tables) and the RBAC matrix (`docs/13_rbac_matrix.xlsx`). Companion documents: `docs/13_rbac_matrix.xlsx` (who can do what, where it is enforced), `docs/15_security_design_note.md` (PII handling, cryptography, audit, keys, incident response), `docs/20_backup_restore_log.xlsx` (recovery). Business-rule ids (BR-xxx) refer to the `business_rules` of the staging files; decision ids (D-nn) to `docs/_staging/FORMAT_AND_DECISIONS.md`; capability statements to `docs/_staging/crdb_capabilities.md`. Where a CockroachDB behaviour is not verified there it is marked **unverified** and has a test (AT-24, AT-25).

## 1. Scope, method and rating

* **In scope:** the schema, its constraints, views, DB roles, the data-access layer contract, backups and change feeds, and the boundaries to the nine external systems of docket section 6. **Out of scope:** UI hardening, physical security, the internals of external vendors.
* **Method:** assets and adversaries -> trust boundaries -> STRIDE per boundary and per module group -> abuse cases specific to a job platform -> controls mapped to concrete tables, columns and rules -> residual risk -> test cases (B79).
* **Rating:** *Likelihood* (L/M/H) of the attack succeeding without the control, *Impact* (1 low ... 3 severe: regulatory breach, loss of money, loss of trust). Inherent = L x I with no controls; Residual = after the controls named. Scale: **High** (act before go-live), **Medium** (accept with monitoring and an owner), **Low** (accept). Residual ratings assume the Findings of `13_rbac_matrix.xlsx` sheet Findings are closed or tracked.
* **Docket coverage (B78):** access control by role -> section 5 and the RBAC matrix; audit trail -> T-12 and security note section 10; password hashing -> T-03 and security note section 6; session controls -> T-08 and security note section 8.

## 2. Assets

| id | asset | where | classification | why it matters |
|---|---|---|---|---|
| A-01 | Candidate identity, contact and career data | profile module, `candidate_profile`, `candidate_contact_*`, `resume` | Personal | core product value; privacy law |
| A-02 | Special-category and vendor-check data | `candidate_diversity_declaration`, `equal_opportunity_response`, `background_check_result`, `candidate_identity_verification` | Sensitive | discrimination and legal exposure |
| A-03 | Credentials, MFA secrets, token hashes, API keys | `user_credential`, `mfa_factor`, `*_token`, `identity_session`, `api_key` | Sensitive | account takeover |
| A-04 | Money: wallet, ledger, payments, invoices, payouts | subscriptions_payments | Internal / Sensitive refs | direct financial loss, audit |
| A-05 | Tax and registration identifiers, payment instrument tokens | `organisation_identifier`, `tax_registration`, `payment_method_token` | Sensitive | fraud, compliance |
| A-06 | Tenant business data: requirements, pipeline, notes, scorecards, offers | jobs_applications, sourcing | Internal / Personal | competitive secrets, candidate fairness |
| A-07 | Audit and security evidence | `audit_event`, `security_event`, chain heads/checkpoints | Personal (IP, user agent) | accountability |
| A-08 | Block lists and privacy settings | `user_block`, `organisation_block`, `privacy_setting` | Personal | safety of candidates |
| A-09 | Encryption keys, peppers, HMAC keys, TLS CA | KMS / vault (outside DB) | Secret | root of trust |
| A-10 | Backups and change-feed sinks | object storage | all classes | second copy of everything |
| A-11 | Availability of search, login, billing | cluster, services | n/a | revenue and trust |
| A-12 | Reputation of company pages, reviews, trust scores | organisation_review, trust_score | Public/Personal | fraud and defamation target |

Data volumes: {{N_TABLES}} tables; {{N_PERS_COLS}} Personal and {{N_SENS_COLS}} Sensitive columns; {{N_AO}} append-only tables; {{N_TTL}} tables with row-level TTL.

## 3. Adversaries

| id | adversary | capability / motivation |
|---|---|---|
| ADV-1 | Anonymous internet user / bot | scripted requests, credential lists, scraping |
| ADV-2 | Malicious or careless customer user (candidate, employer, recruiter, consultant, TPO) | valid login, knows the API, tries to reach other tenants, abuse credits |
| ADV-3 | Fraudulent organisation (fake employer, scam poster) | creates accounts at scale, social engineering |
| ADV-4 | Malicious insider: support staff, admin, DBA, analyst, developer | legitimate access, privileged knowledge |
| ADV-5 | External attacker with an application bug (SQLi, SSRF, XSS, broken access control) | tries to turn a bug into data access |
| ADV-6 | Cloud/account intruder | steals backups, keys, CI secrets |
| ADV-7 | Compromised third party (payment, ID verification, messaging provider, dependency) | supply-chain and webhook abuse |
| ADV-8 | Nation-grade or advanced persistent threat | out of scope for residual ratings; handled by defence in depth |

## 4. Trust boundaries and data flows

| id | boundary | flows crossing it | authentication / protection |
|---|---|---|---|
| TB-1 | Internet <-> edge (CDN, WAF, API gateway) | all user traffic, webhooks | TLS 1.2+, WAF, rate limits, CAPTCHA on registration/reset |
| TB-2 | Edge <-> application tier | authenticated requests, job-queue messages | JWT access token + `identity_session`, `actor_type`, permission check |
| TB-3 | Application tier <-> CockroachDB | SQL over pgwire | TLS + client certificate per pool role (13_rbac E-29), persona/service roles, views, explicit viewer argument on pair views (AT-25) |
| TB-4 | Application <-> external providers | payment gateway, identity verification, company registry, e-mail/SMS/push, gig fulfilment, legacy systems, maps, review data | mTLS or signed webhooks, idempotency keys, allow-listed payloads (docket section 6 boundary table) |
| TB-5 | Operators <-> database | schema migrations, break-glass, SQL shell | `schema_owner` window, `dba_break_glass` via `privileged_access_request`, SQL audit logs to SIEM |
| TB-6 | Cluster <-> backup / archive / change-feed sinks | BACKUP, CHANGEFEED | KMS-encrypted objects, object lock, separate account |
| TB-7 | Cluster <-> ETL / BI / search index | reads of OLTP, writes to marts | `etl_service`, `analytics_reader`, pseudonymised views |
| TB-8 | Build and supply chain <-> production | images, dependencies, migrations, CI secrets | pinned digests, SCA, reviewed migrations |
| TB-9 | KMS / secrets vault <-> services | data keys, pepper, HMAC keys | IAM per service identity, key policies, audit log |

## 5. STRIDE per trust boundary

| boundary | S: spoofing | T: tampering | R: repudiation | I: information disclosure | D: denial of service | E: elevation of privilege |
|---|---|---|---|---|---|---|
| TB-1 | credential stuffing (T-03), bot registration (T-17) | request smuggling, parameter tampering | no non-repudiation without logs: edge logs + `login_attempt` | verbose errors, enumeration (T-02) | volumetric floods; per-IP and per-identifier limits (T-25) | missing auth on public endpoints: AT-16 |
| TB-2 | stolen/forged JWT, session fixation (T-08) | tampering with `acting_organisation_id`, ids in URL (IDOR) | actions without audit: BR-ADM-11 + AT-42 | cross-tenant leaks via predicates (T-10) | slow endpoints | role claim forged; role from DB, not from token (T-15) |
| TB-3 | stolen pool certificate; wrong persona pool used | SQL injection (T-10); direct UPDATE of ledger (T-05, T-11) | statements without actor: `created_by`, audit in same txn (T-12) | table-wide grants (PF-05 removes them for Personal tables); view bypass | cartesian pair view (F-14), statement_timeout | owner/admin bypass REVOKE (F-11, E-27) |
| TB-4 | forged webhook (T-19) | replayed or altered payloads | gateway events deduplicated and stored (`payment_event`) | PII sent to vendors: minimise, tokens only | provider down: orders stay pending (failure behaviour in boundary table) | provider callback authority: signature + reconciliation |
| TB-5 | shared DBA account | out-of-band DDL, malicious migration (T-14) | un-attributed SQL: named accounts + SQL audit | DBA reads PII (T-09) | long-running DDL on hot tables | owner window, break-glass quorum |
| TB-6 | attacker presents as backup service | modified backup/sink objects | backup runs logged in the backup log and SIEM | stolen backup (T-13), exposed sink (T-28) | deleted backups: object lock, second account | restore of a hostile backup: restore only own signed backups |
| TB-7 | ETL identity abuse | poisoned facts | marts reproducible from OLTP | re-identification from marts (T-28) | heavy BI queries on OLTP: separate role, follower reads | BI role reading OLTP: no grants (AT-31) |
| TB-8 | typosquatted dependency | malicious image, migration with grants (T-14) | signed commits, release records | secrets in repo | dependency outage | CI token abuse |
| TB-9 | impersonating a service to KMS | key policy edit | KMS audit log | key exfiltration (T-27) | KMS outage blocks login (cache wrapped DEKs, break-glass) | IAM role creep |

## 6. STRIDE per module group

| module group | main assets | S | T | R | I | D | E | key controls (tables / rules) |
|---|---|---|---|---|---|---|---|---|
| Administration (identity, authz, audit, governance) | A-03, A-07, A-08 | fake accounts: BR-ADM-01/02 | role edits: BR-ADM-05/06/18 | audit chain BR-ADM-08/09 | credentials only in `auth_svc` BR-ADM-04 | token TTL BR-ADM-17 | eligibility FKs, four-eyes | `registration_policy`, `app_role_eligibility`, `audit_event`, `admin_action_approval` |
| Organisation and account | A-05, A-06, A-12 | fake company: BR-ORG-13..16 | mapping chain BR-ORG-02 | status history append-only BR-ORG-19/23 | identifiers encrypted BR-ORG-14 | n/a | recruiter scope BR-ORG-03 | `recruiter_organisation_mapping`, `organisation_identifier`, `vw_recruiter_job_requirement` |
| Gig marketplace | A-01, A-04 | fake worker/requester | overlap manipulation: `gig_slot` | dispute/status histories | `kyc_token` vendor token only | spam bids | party scoping | `gig_assignment`, `gig_slot`, `gig_worker_profile` |
| Profile / Jobs and applications | A-01, A-02, A-06 | fake candidates: BR-ADM-01 | pipeline tamper: status history chain BR-JOB-12 | `job_application_status_history` append-only | masked views BR-PRF-06, blocks BR-ADM-12 | search load | tenant FKs D-11 | `vw_candidate_profile_recruiter`, `vw_candidate_visible_to_user`, `candidate_identity_verification` |
| Licences, seats, devices, sessions | A-03, A-11 | device cloning | slot tampering: unique partial index | `device_limit_violation`, revocation tables | HMAC fingerprints | login storm | seat takeover | `device_slot`, `device_session`, `login_attempt` |
| Sourcing and consultant | A-01, A-06 | rogue consultant | commission fraud BR-SRC-16 | `sourcing_request_decision` append-only | outreach messages TTL 90 d BR-SRC-10 | bulk outreach abuse | openness FK BR-SRC-01 | `consultant_job_openness`, `outreach_*`, `candidate_contact_attempt` |
| Subscriptions, wallet, payments | A-04, A-05 | fake payment events T-19 | ledger edits T-11 | append-only ledger BR-SUB-07 | tokens only BR-SUB-16 | webhook floods | refunds BR-SUB-15 | `wallet_ledger_entry`, `payment_event`, `refund`, `profile_download` |
| Notifications, preferences, community | A-01, A-12 | spoofed sender, phishing | template tampering | `notification_delivery` event log | recipient data minimised in deliveries | fan-out storms | mod tools: staff only | `notification_suppression`, `unsubscribe_token`, `message`, `community_report` |
| Analytics and reporting | A-01 (derived) | ETL identity | fact edits: append-only facts | ETL run log `dim_etl_run` | pseudonymised views, no FKs to OLTP | heavy BI | BI reading OLTP | `vw_dim_*_current`, `fact_*`, `agg_*` |
| Extension modules (network, matching, ads, learning, talent operations) | A-01, A-02, A-04 | fake profiles, click fraud | ad logs append-only | fraud flags, trust score history | contact import hashed, EEO responses compliance-only | ad auction load | advertiser tenancy | `contact_match_key`, `ad_*` logs, `equal_opportunity_response`, `scam_job_report` |

## 7. Abuse cases, controls and residual risk

{{THREAT_TABLE}}

### 7.1 Residual risk summary

{{RESIDUAL_SUMMARY}}

## 8. Control catalogue mapped to schema objects

| control | what | tables / columns / rules | layer (13_rbac codes) |
|---|---|---|---|
| C-01 | Persona/service DB roles, no per-user SQL roles | roles in `13_rbac_matrix.xlsx` sheet Actors; GR-01..GR-06 | G |
| C-02 | Recruiter only own company | `recruiter_organisation_mapping` P1 chain, `vw_recruiter_job_requirement` (BR-ORG-01..03) | V+T |
| C-03 | Consultant only open requirements | `consultant_job_openness`, composite FK in `sourcing_request`, `vw_consultant_open_requirement` (BR-SRC-01/02) | V+T |
| C-04 | Blocked party invisible | `user_block`, `organisation_block`, `vw_candidate_visible_to_user`, `vw_outreach_eligible_candidate` (BR-ADM-12..14, BR-SRC-06) | V+T |
| C-05 | Recruiters cannot register candidates | `registration_policy` composite FK in `app_user` (BR-ADM-01) | T |
| C-06 | Credentials only in auth service | `user_credential.password_hash`, `mfa_factor.secret_encrypted`, `*_token.token_hash` (BR-ADM-04, BR-ADM-23) | G |
| C-07 | Append-only evidence | 124 tables; REVOKE/INSERT-SELECT grants; GR-14/15 | G |
| C-08 | Audit hash chain and WORM sink | `audit_event.chain_no/chain_seq/prev_hash/row_hash`, `audit_chain_head`, `audit_chain_checkpoint` (BR-ADM-09/10) | T+G |
| C-09 | Sensitive actions audited atomically | `permission.is_sensitive`, `audit_action`, `audit_entity_type` (BR-ADM-11, BR-LIC-07/09) | A+T |
| C-10 | Wallet exactly-once and balance non-negative | `wallet`, `wallet_ledger_entry` (BR-SUB-06..10) | T+G |
| C-11 | Subscription periods non-overlapping | `subscription_period` chain (BR-SUB-03/04) | T |
| C-12 | Device limit with evidence | `device_slot`, `device_session`, `device_limit_violation` (BR-LIC-05..09) | T |
| C-13 | Refresh-token rotation and family revocation | `identity_session.token_family_ref`, `security_event` (BR-ADM-16) | T+A |
| C-14 | Tax / identity numbers never in clear | `organisation_identifier`, `tax_registration`, `candidate_identity_verification` (BR-ORG-13/14, BR-SUB-26, BR-PRF-14) | A |
| C-15 | No card or bank data | `payment_method_token`, `payment_event` (BR-SUB-16) | A |
| C-16 | Retention and erasure | `data_retention_policy`, `legal_hold`, `purge_job_run`, `candidate_erasure_request`, TTL on {{N_TTL}} tables (BR-ADM-20/21, BR-PRF-30) | A+G |
| C-17 | Four-eyes and break-glass | `admin_action_approval`, `privileged_access_request`, `impersonation_session` (BR-ADM-18/19) | T |
| C-18 | Consent gating | `user_consent`, `sourcing_submission.user_consent_id` (BR-ADM-15, BR-SRC-11) | T |
| C-19 | Idempotency for external events | unique idempotency keys / gateway event refs (BR-SUB-14, BR-ORG-15, BR-PRF-28) | T |
| C-20 | Tenant composite FKs | `(organisation_id, id)` pairs (D-11) | T |
| C-21 | Backup encryption and separation | `20_backup_restore_log.xlsx` plan, KMS | G+A |
| C-22 | Masked and pseudonymised views | sheet MaskedViews ({{N_MASKED}} tables) | V |

## 9. Test cases for B79 (access and audit)

These tests are also in sheet `Tests_B79` of `docs/13_rbac_matrix.xlsx`. "AT-09" is generated from the RoleMatrix (every append-only table x every service role). AT-24 and AT-25 must pass before the view-based model is declared final; AT-51 is expected to fail until finding F-07 is implemented.

{{TEST_TABLE}}

Pass criteria for the hackathon gate (B82): all tests pass except those explicitly marked as tracked findings; test output (SQLSTATE, row counts, audit rows) is attached to the Gate 2 pack.

## 10. Open items and assumptions

* Findings F-01 .. F-14 in `13_rbac_matrix.xlsx` sheet Findings; the ones that change this model: F-01 (no column-level GRANT), F-03 (erasure vs append-only), F-05 (view owner semantics unverified), F-07 (fake-employer rule), F-14 (pair view).
* CockroachDB behaviours that must be confirmed on the pinned image before sign-off (not stated in `crdb_capabilities.md`): SQL functions returning SETOF a view type (`fn_recruiter_job_requirement`), view owner-privilege semantics, `GRANT SYSTEM BACKUP` / `BACKUP` privilege names, `CHANGEFEED` privilege, backup encryption option names, `RESTORE SYSTEM USERS`, SQL audit logging syntax, `crc32c()`.
* The threat model assumes the application tier enforces the permission catalogue (`permission`, `app_role_permission`, `data_scope`) and writes the audit row in the same transaction; those parts are tested by AT-17, AT-42 and AT-44.
* No sub-roles inside an employer organisation (docket section 3 out of scope): the extension path (`app_user_role` with organisation scope, new `app_role` rows and `data_scope` values, optional `job_requirement_member`) adds rows but no new trust boundary; re-run T-10 and AT-20 when it is added.
"""

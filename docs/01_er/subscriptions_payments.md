# ER diagram: Subscriptions, wallet, payments and download history

Module key `subscriptions_payments`, owner Member C, 74 tables (35 lookups). Generated from `05_data_dictionary.xlsx` (do not edit by hand). Columns shown: primary key, foreign keys, unique/natural keys and a few business columns; see the dictionary for all columns. Edges to lookup (enumeration) tables are listed under *Lookup references* rather than drawn.

## Design notes

<!-- subscriptions_payments.yaml -->
## subscriptions_payments - design notes (milestones B38, B41, B44, B47, B76)
**Scope.** 39 tables + 35 lookups: catalogue (subscription_plan, plan_version, plan_price, plan_entitlement, credit_rate_card, credit_pack, credit_pack_price),
contract (subscription, subscription_period, subscription_status_history, subscription_cancellation, subscription_renewal_attempt, subscription_entitlement),
money (wallet, wallet_ledger_entry, wallet_expiry_bucket, wallet_bucket_consumption, spend_limit, coupon, coupon_redemption),
payments (payment_order, payment_attempt, payment_event, payment_event_outcome, payment_method_token, refund, reconciliation_run/item),
billing (invoice, invoice_line, invoice_tax_line, invoice_number_series, tax_rate, tax_registration, billing_address, credit_note, currency_exchange_rate),
usage/payout (profile_download, consultant_payout). Modelled on Stripe (price/subscription/invoice/credit note), Razorpay (order -> payment -> refund, signed webhooks),
Chargebee/Recurly (plan versions, dunning), LinkedIn Recruiter / Naukri RESDEX (credit-based downloads, plan quotas). Competitor sites were not re-fetched for this
pass; the CockroachDB behaviours below were checked against the CRDB docs (foreign-key, SELECT FOR UPDATE pages).

**Wallet unit = CREDITS (INT8), not money.** Recruiter products (download, outreach message, posting) are priced in credits that vary by plan (credit_rate_card)
and expire in lots; money appears only at purchase (payment_order/invoice, DECIMAL(18,4) + currency_id per D-06). A wallet is per owner AND currency because a pack
bought in INR is refunded in INR; credit balances are never converted. Integer credits make balance CHECKs and hash chains exact (no rounding).

**Ledger choice.** wallet_ledger_entry is a single-entry, signed, per-wallet ledger (the model LinkedIn/Naukri-style credit systems use) with running balance_after,
hash chain (prev_hash,row_hash), gapless sequence and reversals. Full double-entry accounting (Modern Treasury / Square style: debit and credit accounts per posting)
is deliberately OUT of the OLTP model: finance gets a nightly accounting export from invoice/payment_order/refund/wallet_ledger_entry (deferred revenue = credits
purchased minus consumed, breakage = expiry entries). Extension point: a `ledger_account` + `journal_line` pair can be added without touching this ledger.
Cross-ledger invariants are checked by vw_wallet_balance_drift (wallet.credit_balance = SUM(credits_delta)).

**Append-only enforcement (P5).** App roles get INSERT+SELECT only on wallet_ledger_entry, wallet_bucket_consumption, payment_event, payment_event_outcome, profile_download,
coupon_redemption, subscription_status_history, currency_exchange_rate (no UPDATE/DELETE grants; a separate migration role owns DDL). Tamper evidence: row_hash chain per wallet
verified by a nightly job; a changefeed ships ledger rows to an immutable object-store sink. Corrections: new `reversal` row with reverses_wallet_ledger_entry_id (UNIQUE, so once).
Refunds of credits use type `refund` + refund_id (partial amounts); a reversal uses reverses_wallet_ledger_entry_id only - hence exactly-one-of reference columns.
Reference design: wallet_ledger_entry has explicit nullable FKs (payment_order, refund, outreach_batch, job_requirement, coupon_redemption, wallet_expiry_bucket,
self-reverses) with CHECK num_nonnulls(...) = CASE WHEN has_fk_reference THEN 1 ELSE 0. Download deductions point the other way (profile_download.wallet_ledger_entry_id,
UNIQUE) because CRDB has no deferrable FKs and a two-way pointer would be unwritable; the download row is created in the same txn right after the ledger row.

**Idempotency.** Every money-moving write has a unique caller key: ledger.idempotency_key, payment_order.idempotency_key, refund.idempotency_key,
profile_download.idempotency_key, webhook UNIQUE(payment_gateway_id, gateway_event_ref). Replays hit the unique index and the app returns the original row.
Retries on SQLSTATE 40001 re-run the whole txn; idempotency keys make that safe.

**Locking caveat (verified in CRDB docs).** Under SERIALIZABLE, SELECT ... FOR UPDATE is best-effort (unreplicated locks may be lost on lease transfer/range split; the txn
then retries with 40001) - so correctness never depends on the lock alone: it comes from SERIALIZABLE + CHECK(credit_balance >= 0) + UNIQUE(wallet_id, sequence_no).
Optionally set `enable_durable_locking_for_serializable = true` for the billing role to make locks durable. The wallet row is a hot row by design (one writer per wallet).

**Wallet debit algorithm (BR-SUB-08).** BEGIN; 1) SELECT existing ledger by idempotency_key -> return if found. 2) SELECT * FROM wallet WHERE id=$w FOR UPDATE; require status active.
3) Resolve free quota: SELECT subscription_entitlement ... FOR UPDATE; if used_count < limit -> free path (UPDATE used_count+1, insert profile_download with subscription_entitlement_id, no ledger).
4) Else cost = credit_rate_card lookup; check spend_limit (SUM credits_delta of actor in window); require credit_balance >= cost.
5) seq = ledger_sequence+1; prev_hash = row_hash of seq-1; insert ledger (delta=-cost, balance_after, hashes, idempotency_key).
6) FIFO: SELECT lots WHERE credits_remaining>0 ORDER BY expires_at FOR UPDATE; decrement lots, insert wallet_bucket_consumption.
7) INSERT profile_download(wallet_ledger_entry_id, credits_charged). 8) UPDATE wallet SET credit_balance=credit_balance-cost, ledger_sequence=seq, last_entry_at=now().
COMMIT; on 40001 retry (backoff, max 5). A duplicate key on idempotency_key = replay, return the first result.

**subscription_period chain proof (P1 CHAIN_FK, BR-SUB-03).** Half-open periods [start,end). Constraints: UNIQUE(prior_period_id); partial UNIQUE(licence_id) WHERE prior_period_id IS NULL;
UNIQUE(licence_id, sequence_no); CHECK (sequence_no=1) = (prior_period_id IS NULL); composite FK (prior_period_id, licence_id, prior_period_end_date) ->
(id, licence_id, period_end_date) ON UPDATE RESTRICT (target UNIQUE(id, licence_id, period_end_date); we also declare the brief's UNIQUE(id, period_end_date)); CHECK period_start_date >= prior_period_end_date and start <= end.
Proof: each licence has exactly one root; each period has <= 1 successor (unique prior) and a predecessor inside the SAME licence (licence_id is part of the FK); the FK copy of the
predecessor's end is frozen by ON UPDATE RESTRICT, so start(n+1) >= end(n) holds forever; by induction start(n+k) >= end(n), so any two periods of a licence are ordered and disjoint.
Cycles are impossible because sequence is unique per licence, sequence 1 is the only root and prior_period_id is never UPDATEd (column-level UPDATE grant excludes it).
Gaps (lapse between periods) are allowed; overlap is not. Early cancellation: only the TAIL period (no successor) may be shortened (UPDATE period_end_date to an earlier date, set
original_period_end_date/shortened_at, status cancelled); a non-tail end cannot change (FK RESTRICT). If a scheduled future period exists it is first hard-deleted (allowed only for scheduled, uninvoiced rows - invoice_line FK
RESTRICT protects billed ones) and then the new tail is shortened. Renewal creates the next period in the same txn as the renewal payment_order and the status history row.
Edge cases: (a) zero-length period (start=end) is an empty interval and cannot overlap; (b) renewal on the same day as end (start = prior end) is fine; (c) lengthening a tail is overlap-safe;
(d) CRDB MATCH SIMPLE: first period has NULL prior columns so the FK is skipped (verified in docs); (e) licence moved between subscriptions - the chain follows the licence, composite FK (subscription_id, licence_id) keeps them consistent;
(f) concurrent renewals race on UNIQUE(prior_period_id)/UNIQUE(licence_id, sequence_no): the loser gets 23505 and re-reads; (g) backdated corrections use cancel-and-reissue, never UPDATE of start.
Validation SQL (expect 0 rows): see vw_subscription_period_overlap (self-join on a.start < b.end AND b.start < a.end for the same licence, excluding empty intervals).

**Invoice numbering (BR-SUB-12).** Gapless per (seller_country, kind, prefix, fiscal year) series. Drafts have NULL number (never reserved). Issue txn: SELECT next_number FROM invoice_number_series
WHERE id=$s FOR UPDATE; UPDATE next_number+1; set invoice_number/label/issued_at/issue_date in the same txn; rollback releases the number, so no gaps. Voiding keeps the number and a credit note
corrects it. Trade-off: the series row is a serialisation point (~hundreds/s is ample for B2B invoices); shard by series per fiscal year/entity if needed. Not a CRDB SEQUENCE (they skip on rollback).
Credit notes use their own series (series_document_kind).

**Integration boundary (docket sec. 6).** No PAN/CVV/bank account/UPI VPA is stored. payment_method_token keeps gateway tokens (Sensitive: encrypted) + display last4/expiry (Personal);
payment_event.payload is allow-list sanitised, encrypted, TTL 540 days. Gateway down: order stays `created/pending` (expires_at), checkout retried with the same idempotency_key; webhooks are
reconciled by reconciliation_run/item (gateway settlement vs paid orders) and periodic order-status polling.

**PII/Sensitive (B76).** Sensitive: payment_method_token.token_ref, tax_registration.tax_number_encrypted/_hmac, consultant_payout.payout_ref, payment_event.payload.
Personal: billing_address fields, invoice.address_snapshot, last4, cancellation feedback. All other columns Internal; credits/amounts are business data, not personal.

**Deviations/extension points.** (1) Hard delete on subscription_period only for scheduled uninvoiced rows. (2) FKs to registry tables are ON DELETE RESTRICT (financial history).
(3) Gig escrow/payouts reuse wallet/payment_order (D-15); gig-specific settlement tables belong to gig_marketplace. (4) Taxes: GST CGST/SGST vs IGST chosen by place of supply vs seller state; e-invoicing IRN fields can be added to invoice.
(5) Consultant payouts store references only - the transfer runs at the payout provider.

## TTL versus foreign keys (decision F-01)
CockroachDB row-level TTL deletes fail while a RESTRICT/NO ACTION child references the expiring row, so every foreign key that points at a TTL table now states
what happens when the parent expires:
* `profile_download.device_session_id` -> `device_session`: ON DELETE SET NULL (column now nullable)
* `payment_event_outcome.payment_event_id` -> `payment_event`: ON DELETE SET NULL (column now nullable)
* Foreign-key actions are executed by the engine, not by an application role, so the INSERT/SELECT-only grants of append-only children stay intact; the nulled column is
  excluded from any row_hash/prev_hash computation (hash only immutable business columns).
* Rule applied everywhere: evidence rows outlive the row they point at (SET NULL); high-volume samples that are only meaningful with the parent follow it (CASCADE).
  A TTL table must never be the target of a RESTRICT/NO ACTION foreign key (checked by rule check A09 in 08_rule_compliance).

## Diagram

```mermaid
erDiagram
  billing_address {
    UUID id PK
    UUID organisation_id FK
    UUID address_type_id FK
    STRING_200 legal_name
    STRING_200 address_line1
    STRING_200 address_line2
    UUID city_id FK
    UUID state_region_id FK
    UUID country_id FK
  }
  consultant_payout {
    UUID id PK
    UUID payee_app_user_id FK
    UUID sourcing_request_id FK
    DECIMAL_18_4 commission_basis_amount
    DECIMAL_5_2 commission_rate
    DECIMAL_18_4 commission_amount
    UUID currency_id FK
    UUID payout_status_id FK
  }
  coupon {
    UUID id PK
    STRING_40 coupon_code UK
    UUID discount_type_id FK
    DECIMAL_5_2 percent_off
    DECIMAL_18_4 amount_off
    UUID currency_id FK
    INT8 bonus_credits
  }
  coupon_redemption {
    UUID id PK
    UUID coupon_id FK
    UUID organisation_id FK
    UUID payment_order_id FK
    UUID invoice_id FK
    DECIMAL_18_4 discount_amount
    UUID currency_id FK
    INT8 bonus_credits_granted
    TIMESTAMPTZ redeemed_at
  }
  credit_note {
    UUID id PK
    UUID invoice_id FK
    UUID invoice_number_series_id FK
    INT8 credit_note_number UK
    STRING_40 credit_note_label UK
    UUID credit_note_reason_id FK
    UUID refund_id FK
    DECIMAL_18_4 subtotal_amount
    DECIMAL_18_4 tax_amount
    DECIMAL_18_4 total_amount
    UUID currency_id FK
  }
  credit_pack {
    UUID id PK
    STRING_40 pack_code UK
    JSONB name_i18n
    INT8 credits
    INT8 bonus_credits
  }
  credit_pack_price {
    UUID id PK
    UUID credit_pack_id FK
    UUID currency_id FK
    DECIMAL_18_4 amount
    DATE effective_from_date UK
    DATE effective_to_date
  }
  credit_rate_card {
    UUID id PK
    UUID plan_version_id FK
    UUID billable_action_id FK
    INT4 credits_per_unit
    DATE effective_from_date UK
    DATE effective_to_date
  }
  currency_exchange_rate {
    UUID id PK
    UUID base_currency_id FK
    UUID quote_currency_id FK
    DATE rate_date UK
    DECIMAL_18_8 rate
    UUID exchange_rate_source_id FK
    TIMESTAMPTZ fetched_at UK
  }
  invoice {
    UUID id PK
    UUID organisation_id FK
    UUID subscription_id FK
    UUID invoice_number_series_id FK
    INT8 invoice_number UK
    STRING_40 invoice_label UK
    UUID invoice_status_id FK
    UUID billing_address_id FK
    UUID buyer_tax_registration_id FK
    UUID seller_tax_registration_id FK
    UUID place_of_supply_state_region_id FK
    JSONB address_snapshot
    UUID currency_id FK
    UUID currency_exchange_rate_id FK
    DECIMAL_18_4 subtotal_amount
    DECIMAL_18_4 tax_amount
  }
  invoice_line {
    UUID id PK
    UUID invoice_id FK
    INT4 line_no UK
    UUID invoice_line_kind_id FK
    STRING_300 description
    STRING_10 hsn_sac_code
    INT4 quantity
    UUID currency_id FK
    UUID subscription_period_id FK
    UUID credit_pack_id FK
  }
  invoice_number_series {
    UUID id PK
    UUID seller_country_id FK
    UUID series_document_kind_id FK
    STRING_16 prefix UK
    STRING_9 fiscal_year_label UK
    INT8 next_number
    BOOL is_open
  }
  invoice_tax_line {
    UUID id PK
    UUID invoice_line_id FK
    UUID tax_component_id FK
    UUID tax_rate_id FK
    DECIMAL_5_2 rate_percent
    DECIMAL_18_4 taxable_amount
    DECIMAL_18_4 tax_amount
    UUID currency_id FK
  }
  payment_attempt {
    UUID id PK
    UUID payment_order_id FK
    INT4 attempt_no UK
    UUID payment_attempt_status_id FK
    UUID payment_method_kind_id FK
    UUID payment_method_token_id FK
    STRING_100 gateway_payment_ref
    STRING_64 error_code
    STRING_300 error_message
  }
  payment_event {
    UUID id PK
    UUID payment_gateway_id FK
    STRING_120 gateway_event_ref UK
    STRING_64 event_type
    UUID payment_order_id FK
    JSONB payload
    BOOL signature_valid
  }
  payment_event_outcome {
    UUID id PK
    UUID payment_event_id FK
    UUID webhook_outcome_id FK
    TIMESTAMPTZ processed_at
    STRING_300 detail
  }
  payment_method_token {
    UUID id PK
    UUID organisation_id FK
    UUID payment_gateway_id FK
    UUID payment_method_kind_id FK
    STRING_200 token_ref UK
    STRING_4 last4
    INT2 expiry_month
    INT2 expiry_year
  }
  payment_order {
    UUID id PK
    UUID organisation_id FK
    UUID wallet_id FK
    UUID subscription_id FK
    UUID credit_pack_id FK
    UUID invoice_id FK
    UUID payment_purpose_id FK
    UUID payment_gateway_id FK
    UUID payment_order_status_id FK
    DECIMAL_18_4 amount
    UUID currency_id FK
    STRING_100 gateway_order_ref UK
    STRING_120 idempotency_key UK
    UUID created_by_app_user_id FK
    TIMESTAMPTZ expires_at
    TIMESTAMPTZ paid_at
  }
  plan_entitlement {
    UUID id PK
    UUID plan_version_id FK
    UUID entitlement_feature_id FK
    INT8 limit_value
    UUID entitlement_reset_interval_id FK
    BOOL is_hard_limit
  }
  plan_price {
    UUID id PK
    UUID plan_version_id FK
    UUID currency_id FK
    UUID billing_interval_id FK
    DECIMAL_18_4 amount
  }
  plan_version {
    UUID id PK
    UUID subscription_plan_id FK
    INT4 version_no UK
    DATE effective_from_date
    DATE effective_to_date
    INT4 max_users
  }
  profile_download {
    UUID id PK
    UUID organisation_id FK
    UUID actor_app_user_id FK
    UUID candidate_profile_id FK
    UUID resume_id FK
    UUID download_kind_id FK
    UUID download_source_id FK
    UUID device_session_id FK
    UUID wallet_ledger_entry_id FK
    UUID subscription_entitlement_id FK
    UUID prior_profile_download_id FK
    INT4 credits_charged
    TIMESTAMPTZ downloaded_at
    STRING_120 idempotency_key UK
  }
  reconciliation_item {
    UUID id PK
    UUID reconciliation_run_id FK
    UUID payment_order_id FK
    STRING_100 gateway_payment_ref
    DECIMAL_18_4 gateway_amount
    DECIMAL_18_4 ledger_amount
    UUID currency_id FK
    UUID match_state_id FK
    UUID resolved_by_app_user_id FK
  }
  reconciliation_run {
    UUID id PK
    UUID payment_gateway_id FK
    DATE settlement_date UK
    UUID reconciliation_run_status_id FK
    DECIMAL_18_4 gateway_total_amount
    DECIMAL_18_4 ledger_total_amount
    UUID currency_id FK
    INT4 item_count
    TIMESTAMPTZ started_at UK
  }
  refund {
    UUID id PK
    UUID payment_order_id FK
    UUID refund_status_id FK
    UUID refund_reason_id FK
    DECIMAL_18_4 amount
    UUID currency_id FK
    INT8 credits_clawed_back
    STRING_100 gateway_refund_ref UK
    STRING_120 idempotency_key UK
    UUID requested_by_app_user_id FK
    UUID approved_by_app_user_id FK
    TIMESTAMPTZ processed_at
  }
  spend_limit {
    UUID id PK
    UUID organisation_id FK
    UUID spend_limit_scope_id FK
    UUID target_app_user_id FK
    UUID licence_id FK
    UUID spend_limit_period_id FK
    INT8 max_credits
    DECIMAL_18_4 max_amount
    UUID currency_id FK
    BOOL is_hard_block
  }
  subscription {
    UUID id PK
    UUID organisation_id FK
    UUID account_id FK
    UUID licence_id FK
    UUID plan_version_id FK
    UUID subscription_status_id FK
    UUID renewal_mode_id FK
    UUID billing_interval_id FK
    UUID currency_id FK
    DECIMAL_18_4 recurring_amount
    UUID payment_method_token_id FK
    TIMESTAMPTZ started_at
    TIMESTAMPTZ trial_ends_at
  }
  subscription_cancellation {
    UUID id PK
    UUID subscription_id FK
    UUID cancellation_reason_id FK
    UUID cancellation_mode_id FK
    TIMESTAMPTZ requested_at
    DATE effective_date
    UUID requested_by_app_user_id FK
    STRING_1000 feedback
  }
  subscription_entitlement {
    UUID id PK
    UUID subscription_id FK
    UUID entitlement_feature_id FK
    INT8 limit_value
    INT8 used_count
    TIMESTAMPTZ usage_window_start_at UK
    TIMESTAMPTZ usage_window_end_at
  }
  subscription_period {
    UUID id PK
    UUID subscription_id FK
    UUID licence_id FK
    INT4 sequence_no UK
    DATE period_start_date
    DATE period_end_date UK
    UUID prior_period_id FK
    DATE prior_period_end_date FK
    UUID subscription_period_status_id FK
    UUID plan_version_id FK
    DECIMAL_18_4 billed_amount
    UUID currency_id FK
    UUID invoice_id FK
    DATE original_period_end_date
  }
  subscription_plan {
    UUID id PK
    STRING_40 plan_code UK
    JSONB name_i18n
    UUID plan_audience_id FK
    BOOL is_active
    BOOL is_public
  }
  subscription_renewal_attempt {
    UUID id PK
    UUID subscription_id FK
    INT4 target_sequence_no UK
    INT4 attempt_no UK
    TIMESTAMPTZ scheduled_at
    TIMESTAMPTZ attempted_at
    UUID renewal_outcome_id FK
    UUID payment_order_id FK
    TIMESTAMPTZ next_retry_at
  }
  subscription_status_history {
    UUID id PK
    UUID subscription_id FK
    UUID from_subscription_status_id FK
    UUID to_subscription_status_id FK
    TIMESTAMPTZ changed_at
    UUID actor_app_user_id FK
    STRING_200 change_reason
  }
  tax_rate {
    UUID id PK
    UUID country_id FK
    UUID state_region_id FK
    UUID tax_component_id FK
    DECIMAL_5_2 rate_percent
    STRING_10 hsn_sac_code
    DATE effective_from_date
  }
  tax_registration {
    UUID id PK
    UUID organisation_id FK
    UUID country_id FK
    UUID state_region_id FK
    UUID tax_id_type_id FK
    BYTES tax_number_encrypted
    BYTES tax_number_hmac
    UUID verification_status_id FK
    TIMESTAMPTZ verified_at
  }
  wallet {
    UUID id PK
    UUID owner_organisation_id FK
    UUID owner_app_user_id FK
    UUID currency_id FK
    UUID wallet_status_id FK
    INT8 credit_balance
    INT8 ledger_sequence
    INT8 low_balance_threshold
  }
  wallet_bucket_consumption {
    UUID id PK
    UUID wallet_ledger_entry_id FK
    UUID wallet_expiry_bucket_id FK
    INT8 credits
  }
  wallet_expiry_bucket {
    UUID id PK
    UUID wallet_id FK
    UUID source_wallet_ledger_entry_id FK
    INT8 credits_granted
    INT8 credits_remaining
    TIMESTAMPTZ expires_at
  }
  wallet_ledger_entry {
    UUID id PK
    UUID wallet_id FK
    INT8 sequence_no UK
    INT8 prev_sequence_no FK
    UUID ledger_entry_type_id FK
    INT8 credits_delta
    BOOL is_debit
    INT8 balance_after
    STRING_120 idempotency_key UK
    UUID actor_app_user_id FK
    UUID reverses_wallet_ledger_entry_id FK
    UUID payment_order_id FK
    UUID refund_id FK
    UUID outreach_batch_id FK
    UUID job_requirement_id FK
    UUID coupon_redemption_id FK
    UUID wallet_expiry_bucket_id FK
  }
  account {
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
  device_session {
    UUID id PK
  }
  job_requirement {
    UUID id PK
  }
  licence {
    UUID id PK
  }
  organisation {
    UUID id PK
  }
  outreach_batch {
    UUID id PK
  }
  resume {
    UUID id PK
  }
  sourcing_request {
    UUID id PK
  }
  state_region {
    UUID id PK
  }
  tax_id_type {
    UUID id PK
  }
  account ||--o{ subscription : "account_id"
  app_user |o--o{ payment_order : "created_by_app_user_id"
  app_user |o--o{ reconciliation_item : "resolved_by_app_user_id"
  app_user |o--o{ refund : "approved_by_app_user_id"
  app_user |o--o{ spend_limit : "target_app_user_id"
  app_user |o--o{ subscription_status_history : "actor_app_user_id"
  app_user |o--o{ wallet : "owner_app_user_id"
  app_user |o--o{ wallet_ledger_entry : "actor_app_user_id"
  app_user ||--o{ consultant_payout : "payee_app_user_id"
  app_user ||--o{ profile_download : "actor_app_user_id"
  app_user ||--o{ refund : "requested_by_app_user_id"
  app_user ||--o{ subscription_cancellation : "requested_by_app_user_id"
  billing_address ||--o{ invoice : "billing_address_id"
  candidate_profile ||--o{ profile_download : "candidate_profile_id"
  city ||--o{ billing_address : "city_id"
  country ||--o{ billing_address : "country_id"
  country ||--o{ invoice_number_series : "seller_country_id"
  country ||--o{ tax_rate : "country_id"
  country ||--o{ tax_registration : "country_id"
  coupon ||--o{ coupon_redemption : "coupon_id"
  coupon_redemption |o--o{ wallet_ledger_entry : "coupon_redemption_id"
  credit_pack |o--o{ invoice_line : "credit_pack_id"
  credit_pack |o--o{ payment_order : "credit_pack_id"
  credit_pack ||--o{ credit_pack_price : "credit_pack_id"
  currency |o--o{ coupon : "currency_id"
  currency ||--o{ consultant_payout : "currency_id"
  currency ||--o{ coupon_redemption : "currency_id"
  currency ||--o{ credit_note : "currency_id"
  currency ||--o{ credit_pack_price : "currency_id"
  currency ||--o{ currency_exchange_rate : "base_currency_id"
  currency ||--o{ currency_exchange_rate : "quote_currency_id"
  currency ||--o{ invoice : "currency_id"
  currency ||--o{ invoice_line : "currency_id"
  currency ||--o{ invoice_tax_line : "currency_id"
  currency ||--o{ payment_order : "currency_id"
  currency ||--o{ plan_price : "currency_id"
  currency ||--o{ reconciliation_item : "currency_id"
  currency ||--o{ reconciliation_run : "currency_id"
  currency ||--o{ refund : "currency_id"
  currency ||--o{ spend_limit : "currency_id"
  currency ||--o{ subscription : "currency_id"
  currency ||--o{ subscription_period : "currency_id"
  currency ||--o{ wallet : "currency_id"
  currency_exchange_rate |o--o{ invoice : "currency_exchange_rate_id"
  device_session |o--o{ profile_download : "device_session_id"
  invoice |o--o{ coupon_redemption : "invoice_id"
  invoice |o--o{ payment_order : "invoice_id"
  invoice |o--o{ subscription_period : "invoice_id"
  invoice ||--o{ credit_note : "invoice_id"
  invoice ||--o{ invoice_line : "invoice_id"
  invoice_line ||--o{ invoice_tax_line : "invoice_line_id"
  invoice_number_series ||--o{ credit_note : "invoice_number_series_id"
  invoice_number_series ||--o{ invoice : "invoice_number_series_id"
  job_requirement |o--o{ wallet_ledger_entry : "job_requirement_id"
  licence |o--o{ spend_limit : "licence_id"
  licence ||--o{ subscription : "licence_id"
  licence ||--o{ subscription_period : "licence_id"
  organisation |o--o{ wallet : "owner_organisation_id"
  organisation ||--o{ billing_address : "organisation_id"
  organisation ||--o{ coupon_redemption : "organisation_id"
  organisation ||--o{ invoice : "organisation_id"
  organisation ||--o{ payment_method_token : "organisation_id"
  organisation ||--o{ payment_order : "organisation_id"
  organisation ||--o{ profile_download : "organisation_id"
  organisation ||--o{ spend_limit : "organisation_id"
  organisation ||--o{ subscription : "organisation_id"
  organisation ||--o{ tax_registration : "organisation_id"
  outreach_batch |o--o{ wallet_ledger_entry : "outreach_batch_id"
  payment_event |o--o{ payment_event_outcome : "payment_event_id"
  payment_method_token |o--o{ payment_attempt : "payment_method_token_id"
  payment_method_token |o--o{ subscription : "payment_method_token_id"
  payment_order |o--o{ coupon_redemption : "payment_order_id"
  payment_order |o--o{ payment_event : "payment_order_id"
  payment_order |o--o{ reconciliation_item : "payment_order_id"
  payment_order |o--o{ subscription_renewal_attempt : "payment_order_id"
  payment_order |o--o{ wallet_ledger_entry : "payment_order_id"
  payment_order ||--o{ payment_attempt : "payment_order_id"
  payment_order ||--o{ refund : "payment_order_id"
  plan_version |o--o{ credit_rate_card : "plan_version_id"
  plan_version ||--o{ plan_entitlement : "plan_version_id"
  plan_version ||--o{ plan_price : "plan_version_id"
  plan_version ||--o{ subscription : "plan_version_id"
  plan_version ||--o{ subscription_period : "plan_version_id"
  profile_download |o--o{ profile_download : "prior_profile_download_id"
  reconciliation_run ||--o{ reconciliation_item : "reconciliation_run_id"
  refund |o--o{ credit_note : "refund_id"
  refund |o--o{ wallet_ledger_entry : "refund_id"
  resume |o--o{ profile_download : "resume_id"
  sourcing_request |o--o{ consultant_payout : "sourcing_request_id"
  state_region |o--o{ billing_address : "state_region_id"
  state_region |o--o{ invoice : "place_of_supply_state_region_id"
  state_region |o--o{ tax_rate : "state_region_id"
  state_region |o--o{ tax_registration : "state_region_id"
  subscription |o--o{ invoice : "subscription_id"
  subscription |o--o{ payment_order : "subscription_id"
  subscription ||--o{ subscription_cancellation : "subscription_id"
  subscription ||--o{ subscription_entitlement : "subscription_id"
  subscription ||--o{ subscription_period : "subscription_id"
  subscription ||--o{ subscription_renewal_attempt : "subscription_id"
  subscription ||--o{ subscription_status_history : "subscription_id"
  subscription_entitlement |o--o{ profile_download : "subscription_entitlement_id"
  subscription_period |o--o{ invoice_line : "subscription_period_id"
  subscription_period |o--o{ subscription_period : "prior_period_id"
  subscription_plan ||--o{ plan_version : "subscription_plan_id"
  tax_id_type ||--o{ tax_registration : "tax_id_type_id"
  tax_rate ||--o{ invoice_tax_line : "tax_rate_id"
  tax_registration |o--o{ invoice : "buyer_tax_registration_id"
  tax_registration ||--o{ invoice : "seller_tax_registration_id"
  wallet |o--o{ payment_order : "wallet_id"
  wallet ||--o{ wallet_expiry_bucket : "wallet_id"
  wallet ||--o{ wallet_ledger_entry : "wallet_id"
  wallet_expiry_bucket |o--o{ wallet_ledger_entry : "wallet_expiry_bucket_id"
  wallet_expiry_bucket ||--o{ wallet_bucket_consumption : "wallet_expiry_bucket_id"
  wallet_ledger_entry |o--o| profile_download : "wallet_ledger_entry_id"
  wallet_ledger_entry |o--o| wallet_ledger_entry : "reverses_wallet_ledger_entry_id"
  wallet_ledger_entry ||--o{ wallet_bucket_consumption : "wallet_ledger_entry_id"
  wallet_ledger_entry ||--o{ wallet_ledger_entry : "wallet_id"
  wallet_ledger_entry ||--o| wallet_expiry_bucket : "source_wallet_ledger_entry_id"
```

## Lookup tables in this module

| Lookup | Values | Description |
|---|---|---|
| `billable_action` | 8 | Actions that consume credits / entitlements. |
| `billing_interval` | 5 | Billing cadence of a price point. |
| `cancellation_mode` | 2 | When a cancellation takes effect. |
| `cancellation_reason` | 8 | Customer-stated cancellation reason. |
| `credit_note_reason` | 5 | Reason for a credit note. |
| `discount_type` | 3 | Coupon discount mechanics. |
| `download_kind` | 4 | What was downloaded. |
| `download_source` | 5 | Where the download was initiated. |
| `entitlement_feature` | 7 | Plan features with a numeric limit. |
| `entitlement_reset_interval` | 4 | When a usage counter resets. |
| `exchange_rate_source` | 4 | Provider of an FX rate. |
| `invoice_line_kind` | 6 | Kind of invoice line. |
| `invoice_status` | 6 | Invoice lifecycle. |
| `ledger_entry_type` | 9 | Type of wallet ledger movement. |
| `match_state` | 5 | Reconciliation match result. |
| `payment_attempt_status` | 5 | Outcome of one attempt to pay an order. |
| `payment_gateway` | 5 | External payment gateways/rails we integrate with (boundary: only references are stored). |
| `payment_method_kind` | 6 | Payment instrument family (never instrument details). |
| `payment_order_status` | 9 | Lifecycle of a payment order. |
| `payment_purpose` | 4 | Why money is being collected. |
| `payout_status` | 6 | Consultant payout state. |
| `plan_audience` | 4 | Customer segment a plan targets. |
| `reconciliation_run_status` | 4 | Reconciliation run state. |
| `refund_reason` | 7 | Reason a refund was issued. |
| `refund_status` | 6 | Refund workflow state. |
| `renewal_mode` | 3 | How a subscription renews. |
| `renewal_outcome` | 6 | Result of a renewal/dunning attempt. |
| `series_document_kind` | 2 | Document kinds that get gapless numbers. |
| `spend_limit_period` | 3 | Window of a spend limit. |
| `spend_limit_scope` | 3 | Who a spend limit applies to. |
| `subscription_period_status` | 4 | State of one billing/entitlement period. |
| `subscription_status` | 6 | Subscription lifecycle state. |
| `tax_component` | 8 | Tax component (GST/VAT/...). |
| `wallet_status` | 3 | Wallet state. |
| `webhook_outcome` | 5 | Processing result of an inbound gateway event. |

## Lookup references (enumeration FKs)

| Table | Lookup tables referenced |
|---|---|
| `billing_address` | `address_type` |
| `consultant_payout` | `payout_status` |
| `coupon` | `discount_type` |
| `credit_note` | `credit_note_reason` |
| `credit_rate_card` | `billable_action` |
| `currency_exchange_rate` | `exchange_rate_source` |
| `invoice` | `invoice_status` |
| `invoice_line` | `invoice_line_kind` |
| `invoice_number_series` | `series_document_kind` |
| `invoice_tax_line` | `tax_component` |
| `payment_attempt` | `payment_attempt_status`, `payment_method_kind` |
| `payment_event` | `payment_gateway` |
| `payment_event_outcome` | `webhook_outcome` |
| `payment_method_token` | `payment_gateway`, `payment_method_kind` |
| `payment_order` | `payment_gateway`, `payment_order_status`, `payment_purpose` |
| `plan_entitlement` | `entitlement_feature`, `entitlement_reset_interval` |
| `plan_price` | `billing_interval` |
| `profile_download` | `download_kind`, `download_source` |
| `reconciliation_item` | `match_state` |
| `reconciliation_run` | `payment_gateway`, `reconciliation_run_status` |
| `refund` | `refund_reason`, `refund_status` |
| `spend_limit` | `spend_limit_period`, `spend_limit_scope` |
| `subscription` | `billing_interval`, `renewal_mode`, `subscription_status` |
| `subscription_cancellation` | `cancellation_mode`, `cancellation_reason` |
| `subscription_entitlement` | `entitlement_feature` |
| `subscription_period` | `subscription_period_status` |
| `subscription_plan` | `plan_audience` |
| `subscription_renewal_attempt` | `renewal_outcome` |
| `subscription_status_history` | `subscription_status` |
| `tax_rate` | `tax_component` |
| `tax_registration` | `verification_status` |
| `wallet` | `wallet_status` |
| `wallet_ledger_entry` | `ledger_entry_type` |

## Tables

| Table | Kind | Delete | Volume | Description |
|---|---|---|---|---|
| `billing_address` | entity | soft | medium | Billing address of an organisation (invoice recipient). |
| `consultant_payout` | entity | soft | medium | Commission payout to a sourcing consultant: basis, rate, TDS, payout reference (reference only; the transfer happens outside). |
| `coupon` | entity | soft | low | Promotion code: percent/amount discount or bonus credits, validity, redemption caps. |
| `coupon_redemption` | event | append_only | medium | One redemption of a coupon by an organisation, tied to the order/invoice it discounted. |
| `credit_note` | entity | soft | medium | Credit note against an invoice (correction/refund); numbered from its own gapless series. |
| `credit_pack` | config | soft | low | Sellable wallet recharge pack (credits + bonus + validity). |
| `credit_pack_price` | config | hard | low | Price of a credit pack per currency with effective dates. |
| `credit_rate_card` | config | hard | low | Credits charged per billable action, optionally per plan version (NULL plan = platform default). |
| `currency_exchange_rate` | config | append_only | medium | Date-effective FX rate (owned here; invoicing and analytics read it). |
| `invoice` | entity | soft | high | Tax invoice; number assigned at issue from invoice_number_series (gapless); totals; snapshot of buyer identity. |
| `invoice_line` | entity | hard | high | Invoice line: item, quantity, unit price, net amount. |
| `invoice_number_series` | config | hard | low | Gapless numbering counter per seller country, document kind and fiscal year; next_number locked FOR UPDATE at issue. |
| `invoice_tax_line` | entity | hard | high | Tax per invoice line and component (CGST+SGST or IGST or VAT) with the rate applied. |
| `payment_attempt` | entity | soft | high | One try at paying an order with an instrument; gateway payment reference and failure reason. |
| `payment_event` | event | append_only | high | Inbound gateway webhook stored once (idempotent on gateway_event_ref); processing result recorded in payment_event_outcome. |
| `payment_event_outcome` | event | append_only | high | Result of processing a webhook event (one row per processing try); keeps payment_event immutable. |
| `payment_method_token` | entity | soft | medium | Gateway-issued reusable instrument token (no PAN, no bank account): last4 and expiry for display only. |
| `payment_order` | entity | soft | high | Our record of an order for money at an external gateway: references, status, amount only - no card/bank data (integration boundary, docket sec. 6). |
| `plan_entitlement` | config | hard | low | Feature limits included in a plan version (downloads/month, outreach messages, postings...). |
| `plan_price` | config | hard | low | Price point of a plan version per currency and billing interval (the price-list lookup). |
| `plan_version` | config | hard | low | Versioned, immutable terms of a plan (limits, trial, credits included). Subscriptions snapshot a version so later price/limit changes never alter running contracts. |
| `profile_download` | event | append_only | very_high | Profile download history (Tier-1): which recruiter downloaded which candidate profile, when, and under which credit deduction or free quota. |
| `reconciliation_item` | entity | hard | high | One compared line: gateway amount vs our amount and match state. |
| `reconciliation_run` | entity | hard | low | Settlement reconciliation of a gateway day against orders/ledger. |
| `refund` | entity | soft | medium | Refund of a payment order (money back at gateway) and/or credit clawback via ledger entries. |
| `spend_limit` | config | soft | low | Spend control: max credits/amount per window for an organisation, user or licence; checked in the debit txn. |
| `subscription` | entity | soft | medium | A customer organisation's contract for a plan version, bound to exactly one licence; status, renewal mode, price snapshot, cancellation flag. |
| `subscription_cancellation` | entity | soft | low | Cancellation request: reason, mode, effective date; withdrawable before it takes effect. |
| `subscription_entitlement` | entity | hard | medium | Per-subscription usage counter for a feature in the current window (free quota consumption). |
| `subscription_period` | entity | hard | medium | One contiguous billing/entitlement period [period_start_date, period_end_date) of a licence. Non-overlap per licence via P1 CHAIN_FK (see notes). |
| `subscription_plan` | entity | soft | low | Sellable plan family (e.g. Recruiter Pro). Immutable commercial terms live in plan_version; this row is the stable identity. |
| `subscription_renewal_attempt` | event | soft | medium | Renewal / dunning attempt log: scheduled retries (day 1/3/5/7), outcome, payment order created. |
| `subscription_status_history` | event | append_only | medium | Every subscription status transition (who/when/why); the subscription row holds only the latest status. |
| `tax_rate` | config | hard | low | Tax rate per country (and state for GST place-of-supply) and component, date-effective. |
| `tax_registration` | entity | soft | low | Organisation's tax registration (GSTIN/VAT/PAN) - Sensitive, ciphertext + blind index only. |
| `wallet` | entity | soft | medium | Prepaid credit wallet per owner and currency; holds current balance and ledger_sequence (hot row, locked FOR UPDATE for every movement). Unit = CREDITS (see notes). |
| `wallet_bucket_consumption` | ledger | append_only | very_high | Which lots a consumption/expiry/reversal entry drew from or returned to (FIFO audit trail). |
| `wallet_expiry_bucket` | entity | hard | high | Credit grant lot with expiry; consumed FIFO by earliest expires_at; remaining credits mutate (derived state). |
| `wallet_ledger_entry` | ledger | append_only | very_high | Single-entry signed credit movement of a wallet; hash-chained; gapless sequence per wallet; corrections via reversal entries only. |
| `billable_action` | lookup | hard | low | Actions that consume credits / entitlements. |
| `billing_interval` | lookup | hard | low | Billing cadence of a price point. |
| `cancellation_mode` | lookup | hard | low | When a cancellation takes effect. |
| `cancellation_reason` | lookup | hard | low | Customer-stated cancellation reason. |
| `credit_note_reason` | lookup | hard | low | Reason for a credit note. |
| `discount_type` | lookup | hard | low | Coupon discount mechanics. |
| `download_kind` | lookup | hard | low | What was downloaded. |
| `download_source` | lookup | hard | low | Where the download was initiated. |
| `entitlement_feature` | lookup | hard | low | Plan features with a numeric limit. |
| `entitlement_reset_interval` | lookup | hard | low | When a usage counter resets. |
| `exchange_rate_source` | lookup | hard | low | Provider of an FX rate. |
| `invoice_line_kind` | lookup | hard | low | Kind of invoice line. |
| `invoice_status` | lookup | hard | low | Invoice lifecycle. |
| `ledger_entry_type` | lookup | hard | low | Type of wallet ledger movement. |
| `match_state` | lookup | hard | low | Reconciliation match result. |
| `payment_attempt_status` | lookup | hard | low | Outcome of one attempt to pay an order. |
| `payment_gateway` | lookup | hard | low | External payment gateways/rails we integrate with (boundary: only references are stored). |
| `payment_method_kind` | lookup | hard | low | Payment instrument family (never instrument details). |
| `payment_order_status` | lookup | hard | low | Lifecycle of a payment order. |
| `payment_purpose` | lookup | hard | low | Why money is being collected. |
| `payout_status` | lookup | hard | low | Consultant payout state. |
| `plan_audience` | lookup | hard | low | Customer segment a plan targets. |
| `reconciliation_run_status` | lookup | hard | low | Reconciliation run state. |
| `refund_reason` | lookup | hard | low | Reason a refund was issued. |
| `refund_status` | lookup | hard | low | Refund workflow state. |
| `renewal_mode` | lookup | hard | low | How a subscription renews. |
| `renewal_outcome` | lookup | hard | low | Result of a renewal/dunning attempt. |
| `series_document_kind` | lookup | hard | low | Document kinds that get gapless numbers. |
| `spend_limit_period` | lookup | hard | low | Window of a spend limit. |
| `spend_limit_scope` | lookup | hard | low | Who a spend limit applies to. |
| `subscription_period_status` | lookup | hard | low | State of one billing/entitlement period. |
| `subscription_status` | lookup | hard | low | Subscription lifecycle state. |
| `tax_component` | lookup | hard | low | Tax component (GST/VAT/...). |
| `wallet_status` | lookup | hard | low | Wallet state. |
| `webhook_outcome` | lookup | hard | low | Processing result of an inbound gateway event. |

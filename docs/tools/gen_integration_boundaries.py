"""Generate docs/09_integration_boundaries.xlsx (docket Section 6). Every table/column/constraint name is verified against the model."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from dictlib import load_model, ROOT
from xl import new_wb, add_sheet

M = load_model()
TB = {t["name"]: t for t in M["tables"]}
BAD = []


def cols(t):
    return {c["name"] for c in TB[t]["columns"]}


def ref(s):
    """verify 'table.col1,col2' tokens separated by ' ; ' in free text lists"""
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        t, _, cs = part.partition(".")
        if t not in TB:
            BAD.append(f"table {t}"); continue
        for c in [x.strip() for x in cs.split(",") if x.strip()]:
            if c not in cols(t):
                BAD.append(f"column {t}.{c}")
    return s


def named(t, n):
    """constraint or index name must exist on table t"""
    names = {c["name"] for c in TB[t]["constraints"]} | {i["name"] for i in TB[t]["indexes"]}
    if n not in names:
        BAD.append(f"constraint {t}.{n}")
    return n


def ttl(t):
    x = TB[t].get("ttl")
    if not x:
        return "none (retained per table retention rule)"
    return f"{x.get('expire_after')} on {x.get('column') or x.get('expression')}"


# ===================================================================================================================
# Sheet 1: the docket Section 6 table (9 boundaries)
# ===================================================================================================================
B = []
B.append(dict(
    name="Payment gateway",
    stored=ref("payment_order.gateway_order_ref,amount,currency_id,payment_order_status_id,idempotency_key ; payment_attempt.gateway_payment_ref,payment_attempt_status_id,error_code ; payment_event.gateway_event_ref,event_type,payload,signature_valid ; payment_event_outcome.webhook_outcome_id ; refund.gateway_refund_ref,amount ; payment_method_token.token_ref,last4,expiry_month,expiry_year ; reconciliation_item.gateway_payment_ref,gateway_amount,ledger_amount"),
    referenced="Card number, CVV, bank account and UPI handle live only at the gateway (hosted checkout). The platform stores only gateway references (gateway_order_ref, gateway_payment_ref, gateway_refund_ref, token_ref) and last4/expiry for display. payment_event.payload is allow-list sanitised and encrypted (BR-SUB-29, 540-day TTL).",
    owner="Payment gateway owns the payment truth (authorisation, capture, settlement, refund state). Talentra owns the order intent (payment_order), the credit/invoice consequences (wallet_ledger_entry, invoice, credit_note) and the reconciliation verdict (reconciliation_run, reconciliation_item).",
    failure="Create-order fails or times out: payment_order stays in status created/pending with expires_at set; checkout is retried with the SAME idempotency_key so no second order is created; user sees 'payment temporarily unavailable', no credits are granted. Webhook late or lost: nothing is credited until a verified paid event arrives; the daily reconciliation_run compares the gateway settlement file with the ledger and opens reconciliation_item rows for every mismatch; a poller calls the gateway status API for orders older than 15 minutes in pending. Credits are granted only inside the txn that records the paid event (never from the browser redirect).",
    idem="payment_order: uq_payment_order_idempotency_key (idempotency_key) and uq_payment_order_gateway_order_ref (payment_gateway_id, gateway_order_ref). Webhooks: uq_payment_event_gateway_event_ref (payment_gateway_id, gateway_event_ref) with INSERT ... ON CONFLICT DO NOTHING; attempts: uq_payment_attempt_payment_order_id_attempt_no and partial uq_payment_attempt_gateway_payment_ref_set. Refund: uq_refund_idempotency_key, uq_refund_gateway_refund_ref. Credit grant: uq_wallet_ledger_entry_idempotency_key. A replayed webhook therefore inserts nothing and the handler returns the stored outcome.",
    audit="payment_event is append-only (every inbound webhook incl. rejected signatures, signature_valid) and payment_event_outcome records what the handler did (processed, ignored_duplicate, ignored_unmatched, rejected_signature, failed). payment_attempt records each try. Manual refunds/adjustments write audit_event (audit_action_id) and wallet_ledger_entry.actor_app_user_id. Wallet ledger carries prev_hash/row_hash chain.",
))
B.append(dict(
    name="Identity verification service",
    stored=ref("candidate_identity_verification.identity_vendor_id,vendor_token,id_number_hmac,verification_status_id,name_match_score,is_active_binding,failure_reason_code,request_idempotency_key,verified_at,expires_at,document_type_id,country_id"),
    referenced="The national identifier itself (Aadhaar, PAN, passport number) is never stored: only vendor_token (vendor's opaque token) and id_number_hmac (keyed HMAC-SHA256 blind index for duplicate detection). Raw document images stay at the vendor. Visa/permit numbers likewise token-only (candidate_work_authorisation.document_token).",
    owner="Vendor (digilocker, ekyc_gateway, document_ocr_vendor per identity_vendor lookup) owns the identity truth. Talentra owns the outcome record and the binding rule (one active binding per identity per country/document type).",
    failure="Vendor down or timeout: verification_status stays 'pending'; the profile remains usable but verified-badge features and any privilege gated on verification (e.g. gig worker payouts, recruiter contact reveal where policy requires) are deferred; the request is retried by a job with exponential backoff (same request_idempotency_key) for 24 h then marked failed with failure_reason_code='vendor_unavailable'; fallback identity_vendor 'manual_review' routes to staff queue. Never fail open: an unverified result is never shown as verified.",
    idem="uq_candidate_identity_verification_request_idempotency_key (request_idempotency_key) makes a retried request return the original row. Partial unique uq_candidate_identity_verification_country_id_binding (country_id, document_type_id, id_number_hmac) WHERE deleted_at IS NULL AND is_active_binding stops two profiles binding the same identity.",
    audit="The row is the audit record (vendor, status, score, timestamps); status transitions and manual reviewer overrides write audit_event. Access to the table is Personal-class and audited; no history rows are updated in place - re-verification inserts a new row and flips is_active_binding.",
))
B.append(dict(
    name="Company registration and tax verification",
    stored=ref("organisation_identifier.country_id,tax_id_type_id,value_encrypted,value_lookup_hash,masked_value,key_version,verification_status_id ; organisation_verification.verification_kind_id,verification_status_id,vendor_code,vendor_reference,idempotency_key,attempt_no,requested_at,checked_at,expires_at,retry_after_at,registered_name_returned,name_match_score,outcome_detail,vendor_token ; organisation.verification_status_id,verified_at"),
    referenced="Authoritative registry data (legal name, status, directors) stays with the registry/tax authority; only registered_name_returned (for name match), name_match_score and a vendor_token/vendor_reference are kept. Tax identifiers are stored encrypted (value_encrypted + key_version) with a keyed hash (value_lookup_hash) used for lookup and uniqueness, and masked_value for display.",
    owner="Registry/tax authority (via verification vendor) is the source of truth for existence and status; Talentra owns organisation.verification_status_id and the decision to show a verified badge.",
    failure="Vendor or registry down: the attempt row is written with status pending and retry_after_at; organisation stays 'unverified' (can browse, cannot publish jobs where policy requires verification); retries until expires_at then marked inconclusive for manual review; results valid until expires_at and re-checked by a scheduled job. Existing verified status is NOT revoked because of an outage, only when a successful check returns a negative result.",
    idem="uq_organisation_verification_idempotency_key (idempotency_key); uq_organisation_verification_organisation_id_verification_kind_id_attempt_no (organisation_id, verification_kind_id, attempt_no); partial uq_organisation_verification_vendor_code_vendor_reference (vendor_code, vendor_reference) WHERE vendor_reference IS NOT NULL. Identifier uniqueness: uq_organisation_identifier_country_id_tax_id_type_id_value_lookup_hash (WHERE deleted_at IS NULL).",
    audit="organisation_verification is an append-only-style event table (one row per attempt, never updated except by verdict fields); organisation_status_history and audit_event record status changes and manual overrides; reading decrypted identifiers is audited.",
))
B.append(dict(
    name="Gig fulfilment system",
    stored=ref("gig_assignment.handoff_ref,handoff_idempotency_key,handoff_requested_at,handoff_acknowledged_at,external_status_code,gig_reconcile_state_id,status_reconciled_at,estimated_earnings,total_earnings,projected_earnings,earnings_currency_id,earnings_as_of_at,earnings_reconciled_at ; gig_fulfilment_event.source_system,idempotency_key,gig_fulfilment_event_type_id,handoff_ref,occurred_at,payload ; gig_fulfilment_event_attempt.attempt_no,gig_event_process_status_id ; gig_earnings_snapshot.source_system,idempotency_key,gig_earnings_kind_id,amount,as_of_at ; gig_payout_reference.source_system,payout_ref,gig_payout_status_id,amount"),
    referenced="The work itself (dispatch, check-in/out, pay calculation) is external. Talentra holds the opaque handoff_ref, the last external_status_code and reconciled earnings (estimated, total, projected) as snapshots with source and as-of time. It never computes earnings.",
    owner="External fulfilment system owns assignment execution status and earnings; Talentra owns the marketplace decision (request, application, accepted assignment) and the non-overlap/slot rule (gig_slot) that gates handoff.",
    failure="Handoff call fails or is not acknowledged: gig_assignment.gig_reconcile_state_id = 'pending' (handoff_acknowledged_at NULL); the outbound handoff is retried with the same handoff_idempotency_key; the requester/worker see 'confirmation pending'. Inbound events missing: a reconciliation job (index idx_gig_assignment_gig_reconcile_state_id_status_reconciled_at) polls the external API for stale assignments and sets 'drift_detected' / 'manual_review'. Events arriving out of order are stored first (gig_fulfilment_event) and applied by occurred_at; earnings figures shown with earnings_as_of_at so stale values are visible.",
    idem="Outbound: partial uq_gig_assignment_handoff_idempotency_key (handoff_idempotency_key) WHERE deleted_at IS NULL and uq_gig_assignment_handoff_ref (handoff_ref). Inbound: uq_gig_fulfilment_event_source_system_idempotency_key (source_system, idempotency_key); uq_gig_earnings_snapshot_source_system_idempotency_key; uq_gig_payout_reference_source_system_payout_ref; processing retries: uq_gig_fulfilment_event_attempt_gig_fulfilment_event_id_attempt_no.",
    audit="gig_fulfilment_event keeps every inbound message with signature_valid and schema_version (395-day TTL), gig_fulfilment_event_attempt records each processing attempt, gig_assignment_status_history records applied transitions, gig_earnings_snapshot is append-only history of external figures.",
))
B.append(dict(
    name="Legacy job and candidate systems",
    stored=ref("candidate_profile.legacy_candidate_ref,import_source_id ; profile_import.import_source_id,legacy_record_ref,payload_sha256,field_mapping_version,sync_conflict_policy_id,import_status_id,items_total,items_applied,error_summary ; organisation.legacy_source_ref ; job_requirement.external_job_ref,legacy_source_system_id ; legacy_source_system.code,import_source_id,sync_conflict_policy_id,connection_ref,schedule_expression,last_successful_run_at ; legacy_sync_run.run_key,watermark_from_ref,watermark_to_ref,import_status_id,items_read,items_created,items_updated,items_skipped,items_conflicted,items_failed ; legacy_record_map.source_record_ref,platform_record_ref,source_payload_sha256,is_tombstoned ; legacy_sync_conflict.field_name,legacy_value_hash,platform_value_hash,sync_conflict_policy_id,legacy_conflict_outcome_id ; duplicate_candidate_match.match_confidence"),
    referenced="Legacy systems remain read-only sources: Talentra stores the legacy record reference (not the legacy schema), a SHA-256 of the received payload, the field-mapping version and the conflict policy applied. raw_payload is optional and cleared by retention job. ETL is out of scope (docket Section 1); this boundary defines the periodic-sync contract only.",
    owner="Per field (see sheet 'Legacy sync'): identity/contact: legacy wins on first import, then Talentra wins; skills/experience/education: newest_wins; verification flags and credentials: Talentra only (never imported); job content: legacy wins until the posting is edited on the platform, then platform wins.",
    failure="Legacy source unavailable: the sync run is recorded failed and retried on next schedule; platform continues to operate on its own data; no deletes are propagated from a missing source (a record absent from a feed is not treated as deleted). Partial application is allowed (import_status 'partially_applied') and resumable by payload hash.",
    idem="Candidates: uq_candidate_profile_legacy_candidate_ref (legacy_candidate_ref) WHERE deleted_at IS NULL AND NOT NULL; imports: uq_profile_import_candidate_profile_id_import_source_id_pa_a579 (candidate_profile_id, import_source_id, payload_sha256). Organisations: uq_organisation_legacy_source_ref. Batch level: legacy_record_map UNIQUE (legacy_source_system_id, legacy_entity_kind_id, source_record_ref) (uq_legacy_record_map_legacy_source_system_id_legacy_entity_kind_id_source_record_ref); one open run per source and entity kind (uq_legacy_sync_run_legacy_source_system_id_entity_kind_open); jobs: uq_job_requirement_legacy_source_system_id_external_job_ref (legacy_source_system_id, external_job_ref) WHERE external_job_ref IS NOT NULL AND deleted_at IS NULL.",
    audit="legacy_sync_run per run (counts, watermark, status), legacy_sync_conflict (append-only, field-level decision with rule and outcome), profile_import row per payload (what was applied, by which mapping version and conflict policy), candidate_activity_log for profile changes, duplicate_candidate_match for suspected duplicates between legacy and native profiles (reviewed_by_app_user_id).",
))
B.append(dict(
    name="Messaging and notification providers",
    stored=ref("notification_provider.provider_code,failover_notification_provider_id,webhook_secret_ref ; notification_delivery.notification_provider_id,provider_message_ref,delivery_status_id,attempt_no,idempotency_key,recipient_hash,body_digest,error_code,expires_at ; notification_delivery_event.delivery_status_id,provider_event_ref ; provider_webhook_event.provider_event_ref,payload_hash,signature_valid,expires_at ; notification_suppression.recipient_hash,suppression_reason_id ; notification_preference.app_user_id ; push_subscription.endpoint_hash"),
    referenced="Message bodies live at the provider; Talentra keeps only body_digest (SHA-256), template id and minimal payload for audit, and the recipient as keyed HMAC recipient_hash (address resolved from contact tables at send time). Push endpoints are encrypted (endpoint_encrypted) with endpoint_hash for lookup. Raw webhook payloads are kept 7 days.",
    owner="Provider owns delivery fact (accepted, delivered, bounced, complaint); Talentra owns preference, consent and suppression (notification_preference, notification_suppression, unsubscribe_token) and the retry policy.",
    failure="Provider down: dispatcher fails over along notification_provider.failover_notification_provider_id, records the failed attempt as a notification_delivery row (attempt_no+1, max 10) and retries with backoff; in-app notification (notification table) is unaffected; OTP/security messages escalate channel (email<->sms). If all providers fail the notification remains visible in the inbox and the failure is counted in notification_delivery_daily_summary. Webhooks missed: delivery status stays 'sent' until a later event; no state is inferred.",
    idem="Dispatch: uq_notification_delivery_idempotency_key (sha256 of notification|channel|attempt_no) and uq_notification_delivery_notification_id_notification_channel_id_attempt_no; provider ref: partial uq_notification_delivery_notification_provider_id_provider_message_ref. Callbacks: uq_provider_webhook_event_notification_provider_id_provider_event_ref; each status once: uq_notification_delivery_event_notification_delivery_id_delivery_status_id. Inbox: uq_notification_recipient_app_user_id_dedupe_key. Suppression: partial uq_notification_suppression_notification_channel_id_recipient_hash WHERE lifted_at IS NULL.",
    audit="notification_delivery + notification_delivery_event are the audit of the exchange (30-day TTL); before expiry notification_delivery_daily_summary keeps counts, rows_digest and a hash chain for 7 years; provider_webhook_event stores signature_valid for rejected callbacks.",
))
B.append(dict(
    name="Social sharing",
    stored=ref("job_share_event.job_requirement_id,share_channel_id,share_token,shared_by_candidate_profile_id ; community_share.share_channel_id,share_object_type_id,shared_object_ref,share_link_url,share_token ; profile_share_link.short_code,share_link_purpose_id,expires_at ; profile_share_visit.profile_share_link_id,share_channel_id,visited_at"),
    referenced="Only outbound links and tracking tokens. The external post, its text, likes and comments are never ingested. Visits record channel and country, not external identity.",
    owner="Talentra owns the shared object and the link token; the social network owns the post.",
    failure="No dependency at share time: the link is generated locally and the share intent opened on the client. If the network is down the share simply is not posted; the share event is still recorded (client-reported) and no retry is attempted. Link redirects are served by Talentra and unaffected.",
    idem="uq_job_share_event_share_token (share_token); uq_community_share_share_token; uq_profile_share_link_short_code. A repeated click generates the same token only if the client resubmits it; otherwise a new event is a new share by design.",
    audit="The share event rows are append-only (job_share_event 400-day TTL); profile_share_visit is append-only with hash-sharded visited_at.",
))
B.append(dict(
    name="Mapping and geolocation service",
    stored=ref("city.latitude,longitude,geo_source_id,geoname_ref ; organisation_location.latitude,longitude,geo_source_id,place_ref ; job_requirement_location.latitude,longitude,geo_source_id ; gig_request.latitude,longitude,geo_source_id"),
    referenced="Coordinates (DECIMAL(9,6), range CHECK) and place_ref (provider place id) only. Map tiles, geocoding responses and route data are not stored. geo_source_id (user_entered, device_gps, geocoded_provider, postal_centroid, city_centroid, manual_admin, imported_dataset) records provenance; exact gig site coordinates are visible only to the accepted worker.",
    owner="Mapping provider owns geocoding quality and place identity; Talentra owns the stored coordinate value used for radius search. Optional boundary: every feature degrades to city-level search.",
    failure="Provider down or quota exhausted: write path stores the entered address with latitude/longitude NULL (unknown, null_why documented) or falls back to city centroid (geo_source 'city_centroid' from city.latitude,longitude); search falls back to city/state filters; a backfill job geocodes rows with NULL coordinates later. No user action is blocked.",
    idem="Deterministic: geocoding the same address overwrites the same columns, so replays are harmless. Provider place ids: city uq_city_geoname_ref (partial); organisation_location.place_ref is not unique (a place can host many organisations).",
    audit="geo_source_id on every coordinate row is the provenance trail; manual corrections write updated_by/updated_at and audit_event for organisation locations.",
))
B.append(dict(
    name="Company review and salary data",
    stored=ref("reference_data_source.source_code,publisher_name,licence_name,attribution_text,requires_attribution,refresh_cadence_days,max_staleness_days,last_refreshed_at ; reference_data_refresh.run_key,reference_refresh_status_id,rows_loaded,rows_rejected ; company_rating_snapshot.source_company_ref,source_page_url,overall_rating,review_count,match_confidence,refreshed_at,is_displayable ; salary_benchmark.benchmark_key,p10_amount,p50_amount,p90_amount,sample_size,refreshed_at,is_publishable ; salary_benchmark_source.reference_data_source_id,source_record_ref,retrieved_at"),
    referenced="Reference only: aggregated ratings and percentile bands with mandatory source attribution (reference_data_source.attribution_text) and refresh date (refreshed_at). Third-party review text is not copied. Rows older than max_staleness_days are hidden (is_displayable/is_publishable false). Talentra's own reviews (organisation_review) are a separate first-party dataset.",
    owner="Data publisher/vendor owns the data and its licence terms; Talentra owns the blend (salary_benchmark_source.blend_weight) and display rule.",
    failure="Source unavailable or stale: last good snapshot is shown with its refreshed_at until max_staleness_days, after which the figure is suppressed (not zeroed); refresh job failure is recorded in reference_data_refresh (status, rows_rejected) and alerts operations; contract end (contract_end_date) disables the source.",
    idem="uq_reference_data_refresh_run_key (run_key); uq_company_rating_snapshot_grain (organisation_id, reference_data_source_id, snapshot_date); uq_salary_benchmark_benchmark_key; uq_salary_benchmark_source_benchmark_source; uq_salary_benchmark_source_source_record_ref (reference_data_source_id, source_record_ref).",
    audit="reference_data_refresh is append-only (one row per run); snapshots carry retrieved_at/refreshed_at; source licence terms recorded on reference_data_source.",
))

# ===================================================================================================================
# Sheet: Boundary tables
# ===================================================================================================================
BT = [
    ("Payment gateway", "payment_order", "idempotency_key", "uq_payment_order_idempotency_key"),
    ("Payment gateway", "payment_order", "payment_gateway_id, gateway_order_ref", "uq_payment_order_gateway_order_ref"),
    ("Payment gateway", "payment_attempt", "payment_order_id, attempt_no", "uq_payment_attempt_payment_order_id_attempt_no"),
    ("Payment gateway", "payment_attempt", "gateway_payment_ref", "uq_payment_attempt_gateway_payment_ref_set"),
    ("Payment gateway", "payment_event", "payment_gateway_id, gateway_event_ref", "uq_payment_event_gateway_event_ref"),
    ("Payment gateway", "payment_event_outcome", "payment_event_id, webhook_outcome_id, processed_at", "idx_payment_event_outcome_payment_event_id_processed_at"),
    ("Payment gateway", "refund", "idempotency_key", "uq_refund_idempotency_key"),
    ("Payment gateway", "refund", "gateway_refund_ref", "uq_refund_gateway_refund_ref"),
    ("Payment gateway", "payment_method_token", "payment_gateway_id, token_ref", "uq_payment_method_token_gateway_token"),
    ("Payment gateway", "wallet_ledger_entry", "idempotency_key", "uq_wallet_ledger_entry_idempotency_key"),
    ("Payment gateway", "reconciliation_run", "payment_gateway_id, settlement_date, started_at", "uq_reconciliation_run_gateway_date_started"),
    ("Identity verification service", "candidate_identity_verification", "request_idempotency_key", "uq_candidate_identity_verification_request_idempotency_key"),
    ("Identity verification service", "candidate_identity_verification", "country_id, document_type_id, id_number_hmac (partial)", "uq_candidate_identity_verification_country_id_binding"),
    ("Company registration and tax verification", "organisation_verification", "idempotency_key", "uq_organisation_verification_idempotency_key"),
    ("Company registration and tax verification", "organisation_verification", "organisation_id, verification_kind_id, attempt_no", "uq_organisation_verification_organisation_id_verification_kind_id_attempt_no"),
    ("Company registration and tax verification", "organisation_verification", "vendor_code, vendor_reference (partial)", "uq_organisation_verification_vendor_code_vendor_reference"),
    ("Company registration and tax verification", "organisation_identifier", "country_id, tax_id_type_id, value_lookup_hash (partial)", "uq_organisation_identifier_country_id_tax_id_type_id_value_lookup_hash"),
    ("Gig fulfilment system", "gig_assignment", "handoff_idempotency_key (partial)", "uq_gig_assignment_handoff_idempotency_key"),
    ("Gig fulfilment system", "gig_assignment", "handoff_ref (partial)", "uq_gig_assignment_handoff_ref"),
    ("Gig fulfilment system", "gig_fulfilment_event", "source_system, idempotency_key", "uq_gig_fulfilment_event_source_system_idempotency_key"),
    ("Gig fulfilment system", "gig_fulfilment_event_attempt", "gig_fulfilment_event_id, attempt_no", "uq_gig_fulfilment_event_attempt_gig_fulfilment_event_id_attempt_no"),
    ("Gig fulfilment system", "gig_earnings_snapshot", "source_system, idempotency_key", "uq_gig_earnings_snapshot_source_system_idempotency_key"),
    ("Gig fulfilment system", "gig_payout_reference", "source_system, payout_ref", "uq_gig_payout_reference_source_system_payout_ref"),
    ("Legacy job and candidate systems", "candidate_profile", "legacy_candidate_ref (partial)", "uq_candidate_profile_legacy_candidate_ref"),
    ("Legacy job and candidate systems", "profile_import", "candidate_profile_id, import_source_id, payload_sha256", "uq_profile_import_candidate_profile_id_import_source_id_pa_a579"),
    ("Legacy job and candidate systems", "organisation", "legacy_source_ref (partial)", "uq_organisation_legacy_source_ref"),
    ("Legacy job and candidate systems", "job_requirement", "legacy_source_system_id, external_job_ref (partial)", "uq_job_requirement_legacy_source_system_id_external_job_ref"),
    ("Legacy job and candidate systems", "legacy_record_map", "legacy_source_system_id, legacy_entity_kind_id, source_record_ref", "uq_legacy_record_map_legacy_source_system_id_legacy_entity_kind_id_source_record_ref"),
    ("Legacy job and candidate systems", "legacy_sync_run", "legacy_source_system_id, run_key", "uq_legacy_sync_run_legacy_source_system_id_run_key"),
    ("Legacy job and candidate systems", "duplicate_candidate_match", "primary_app_user_id, duplicate_app_user_id, duplicate_match_rule_id", "uq_duplicate_candidate_match_pair_rule"),
    ("Messaging and notification providers", "notification_delivery", "idempotency_key", "uq_notification_delivery_idempotency_key"),
    ("Messaging and notification providers", "notification_delivery", "notification_id, notification_channel_id, attempt_no", "uq_notification_delivery_notification_id_notification_channel_id_attempt_no"),
    ("Messaging and notification providers", "notification_delivery", "notification_provider_id, provider_message_ref (partial)", "uq_notification_delivery_notification_provider_id_provider_message_ref"),
    ("Messaging and notification providers", "provider_webhook_event", "notification_provider_id, provider_event_ref", "uq_provider_webhook_event_notification_provider_id_provider_event_ref"),
    ("Messaging and notification providers", "notification_delivery_event", "notification_delivery_id, delivery_status_id", "uq_notification_delivery_event_notification_delivery_id_delivery_status_id"),
    ("Messaging and notification providers", "notification_suppression", "notification_channel_id, recipient_hash (partial: lifted_at IS NULL)", "uq_notification_suppression_notification_channel_id_recipient_hash"),
    ("Messaging and notification providers", "notification", "recipient_app_user_id, dedupe_key", "uq_notification_recipient_app_user_id_dedupe_key"),
    ("Messaging and notification providers", "notification_delivery_daily_summary", "summary_date, notification_channel_id, notification_provider_id, notification_type_id", "uq_notification_delivery_daily_summary_summary_date_notification_channel_id_notification_provider_id_notification_type_id"),
    ("Social sharing", "job_share_event", "share_token", "uq_job_share_event_share_token"),
    ("Social sharing", "community_share", "share_token", "uq_community_share_share_token"),
    ("Social sharing", "profile_share_link", "short_code", "uq_profile_share_link_short_code"),
    ("Mapping and geolocation service", "city", "geoname_ref (partial)", "uq_city_geoname_ref"),
    ("Mapping and geolocation service", "organisation_location", "organisation_id, id (composite FK target)", "uq_organisation_location_organisation_id_id"),
    ("Company review and salary data", "reference_data_refresh", "run_key", "uq_reference_data_refresh_run_key"),
    ("Company review and salary data", "company_rating_snapshot", "organisation_id, reference_data_source_id, snapshot_date", "uq_company_rating_snapshot_grain"),
    ("Company review and salary data", "salary_benchmark", "benchmark_key", "uq_salary_benchmark_benchmark_key"),
    ("Company review and salary data", "salary_benchmark_source", "reference_data_source_id, source_record_ref", "uq_salary_benchmark_source_source_record_ref"),
]
BT_ROWS = []
for b, t, c, n in BT:
    named(t, n)
    for part in c.split("(")[0].split(","):
        part = part.strip()
        if part and part not in cols(t):
            BAD.append(f"boundary col {t}.{part}")
    BT_ROWS.append([b, t, TB[t]["module"], c, n, ttl(t), TB[t]["delete_policy"]])

# ===================================================================================================================
# Sheet: Legacy sync (per-field direction and conflict rule)
# ===================================================================================================================
LS = []


def L(entity, table_col, direction, rule, key, note=""):
    ref(table_col)
    LS.append([entity, table_col, direction, rule, key, note])


KC = "candidate_profile.legacy_candidate_ref (uq_candidate_profile_legacy_candidate_ref) + import_source_id"
L("Candidate", "candidate_profile.given_name,family_name,display_name", "legacy -> platform on first import only", "platform_wins after first import (sync_conflict_policy 'platform_wins'); legacy changes land in profile_import.raw_payload for review", KC)
L("Candidate", "candidate_profile.birth_date,gender_id,nationality_country_id", "legacy -> platform on first import only", "platform_wins; never overwritten once candidate edited (updated_by <> system actor)", KC)
L("Candidate", "candidate_profile.contact_email,contact_phone,contact_phone_country_id", "legacy -> platform first import; thereafter bidirectional-read (platform authoritative)", "platform_wins; imported value stored UNVERIFIED (contact_*_verified_at NULL); if email collides with app_user.email partial unique -> duplicate_candidate_match, manual_review", KC)
L("Candidate", "candidate_profile.contact_email_verified_at,contact_phone_verified_at", "never imported", "platform only (verification cannot be inherited from another system)", "n/a")
L("Candidate", "candidate_experience.job_title,employer_name,start_date,end_date,is_current", "legacy -> platform (periodic)", "newest_wins by comparing source timestamp with candidate_experience.updated_at; deletes in legacy are not propagated", KC + " + profile_import payload_sha256")
L("Candidate", "candidate_education.institution_name", "legacy -> platform (periodic)", "newest_wins; qualification mapped through master data aliases", KC)
L("Candidate", "candidate_skill.skill_id,years_of_experience", "legacy -> platform (periodic)", "newest_wins per (candidate_profile_id, skill_id); skill_source_id records origin = import; assessed skills (assessment_attempt_id not null) are never overwritten", "uq_candidate_skill_candidate_profile_id_skill_id")
L("Candidate", "candidate_profile.current_salary_amount,expected_salary_min,expected_salary_max,current_salary_disclosure_id", "legacy -> platform first import only", "platform_wins; undisclosed in legacy -> salary_disclosure 'undisclosed' (never 0)", KC)
L("Candidate", "resume.body_text,candidate_media_file_id", "legacy -> platform (file import)", "imported as new resume version (parent_resume_id chain); never replaces default resume automatically", "profile_import payload_sha256")
L("Candidate", "candidate_profile.completeness_score", "platform only (derived)", "recomputed after import", "n/a")
L("Candidate", "app_user.email ; user_credential.password_hash", "never imported", "Credentials are not migrated; candidates re-set password via reset flow", "n/a")
L("Application", "job_application.application_status_id,applied_at,job_requirement_id,candidate_profile_id", "legacy -> platform (migration, history only)", "insert-if-absent by uq_job_application_candidate_profile_id_job_requirement_id; platform status wins if row exists; status history imported with idempotency_key on job_application_status_history", "uq_job_application_status_history_idempotency_key")
L("Organisation", "organisation.legal_name,display_name,website_url", "legacy -> platform first import", "platform_wins after claim (organisation.claimed_at not NULL)", "organisation.legacy_source_ref (uq_organisation_legacy_source_ref)")
L("Organisation", "organisation_identifier.value_encrypted,value_lookup_hash", "legacy -> platform (first import, re-encrypted)", "source_wins until verification succeeds, then platform_wins; verification status never imported", "uq_organisation_identifier_country_id_tax_id_type_id_value_lookup_hash")
L("Job", "job_requirement.title,description,job_category_id,employment_type_id,salary_min,salary_max,salary_disclosure_id", "legacy -> platform (periodic until first platform edit)", "source_wins while job_requirement.updated_by is the system actor; platform_wins afterwards; closed in legacy -> closed here only via explicit job_requirement_status transition with history", "job_requirement.legacy_source_system_id + external_job_ref (uq_job_requirement_legacy_source_system_id_external_job_ref); batch map legacy_record_map")
L("Job", "job_requirement.is_live,job_publish_state_id,published_at", "platform only", "legacy publish state is advisory; platform publish state decides visibility", "n/a")
L("Job", "job_requirement_skill.skill_id", "legacy -> platform (periodic)", "newest_wins; skills mapped through skill_alias", "uq_job_requirement_skill_job_requirement_id_skill_id")

GAPS = [
    ["L-01", "job_requirement.external_job_ref had no unique constraint and no source-system column; two legacy systems could produce colliding references and a re-run could create duplicate jobs.", "High", "RESOLVED: job_requirement.legacy_source_system_id (FK legacy_source_system) + partial UNIQUE (legacy_source_system_id, external_job_ref) WHERE external_job_ref IS NOT NULL AND deleted_at IS NULL + CHECK that a reference always names its source."],
    ["L-02", "candidate_profile.legacy_candidate_ref is unique globally and not scoped by source; with a second legacy system references could collide.", "Medium", "RESOLVED through legacy_record_map: UNIQUE (legacy_source_system_id, legacy_entity_kind_id, source_record_ref) is scoped by source; candidate_profile.legacy_candidate_ref stays as the first-source shortcut."],
    ["L-03", "profile_import.legacy_record_ref is not unique, no job-side import table existed and sync runs (schedule, cursor, counts) were not modelled.", "High", "RESOLVED: legacy_source_system (registry, schedule, connection reference), legacy_sync_run (watermark, status, counts, one open run per source and kind), legacy_record_map (idempotency anchor, tombstones). Per-field mapping remains the field_mapping_version string (configuration in code)."],
    ["L-04", "No table recorded a field-level conflict decision (which value won and why).", "Medium", "RESOLVED: legacy_sync_conflict (append-only, 24-month TTL) with field, keyed value hashes, sync_conflict_policy_id applied and legacy_conflict_outcome_id; manual decisions supersede the queued row."],
]

def implemented_ddl():
    out = ["-- Implemented in docs/_staging/legacy_sync.yaml (module administration); generated from the model"]
    for tn in ("legacy_source_system", "legacy_sync_run", "legacy_record_map", "legacy_sync_conflict"):
        t = TB[tn]
        out.append(f"TABLE {tn} ({t['delete_policy']}, {t['volume']}): " + ", ".join(c["name"] for c in t["columns"] if not c.get("injected")))
        for k in t["constraints"]:
            out.append(f"  {k['type'].upper()} {k['name']}" + (f" ({', '.join(k['columns'])})" if k.get('columns') else f": {k.get('expr')}"))
        for i in t["indexes"]:
            out.append(f"  {'UNIQUE ' if i.get('unique') else ''}INDEX {i['name']} ({', '.join(i['columns'])})" + (f" WHERE {i['where']}" if i.get('where') else ""))
    jr = TB["job_requirement"]
    assert any(i["name"] == "uq_job_requirement_legacy_source_system_id_external_job_ref" for i in jr["indexes"])
    out.append("ALTER job_requirement: legacy_source_system_id + uq_job_requirement_legacy_source_system_id_external_job_ref")
    return chr(10).join(out)

DDL = implemented_ddl()

# ===================================================================================================================
# Sheet: Outage playbook
# ===================================================================================================================
OP = [
    ["Payment gateway", "Create-order 5xx/timeouts > 3 in 60 s; webhook silence > 15 min while orders are pending; settlement file missing", "Orders remain pending with expires_at; checkout retried with same idempotency_key; no credits granted; banner 'payments delayed'", "payment_order, payment_event", "Poller: GET order status for payment_order WHERE payment_order_status_id IN (created,pending) AND created_at < now()-15m every 5 min; exponential backoff 1,2,4,8 min; stop at expires_at",
     "After recovery replay missed events from gateway (events API) into payment_event (ON CONFLICT DO NOTHING); run reconciliation_run for the outage day", "SELECT po.id FROM payment_order po WHERE po.paid_at IS NULL AND po.expires_at < now() AND po.payment_order_status_id IN (SELECT id FROM payment_order_status WHERE code IN ('created','pending')); and: reconciliation_item rows with match_state not matched", "Page on-call if pending orders > 50 or oldest > 30 min; finance notified for unmatched settlement items"],
    ["Identity verification service", "Vendor error rate > 20% over 5 min or p95 latency > 10 s", "Verification stays pending; features gated on 'verified' wait; candidate told 'verification in progress'; manual_review vendor queue opens", "candidate_identity_verification", "Retry with same request_idempotency_key: 1 min, 5 min, 30 min, 2 h, then every 6 h up to 24 h; then failure_reason_code='vendor_unavailable'",
     "Resubmit failed rows in batches of 200 after recovery; vendor duplicates are neutralised by vendor idempotency + our unique key", "SELECT count(*) FROM candidate_identity_verification v JOIN verification_status s ON s.id=v.verification_status_id WHERE s.code='pending' AND v.created_at < now()-interval '1 hour';", "Escalate to product if > 24 h; never auto-approve"],
    ["Company registration and tax verification", "Vendor/registry timeouts; HTTP 429/5xx", "Attempt rows written with retry_after_at; organisation unverified; publishing restricted per policy; existing verified status retained", "organisation_verification", "Scheduler picks rows WHERE retry_after_at <= now() AND checked_at IS NULL (idx_organisation_verification_expires_at is for expiry; scan by requested_at index); backoff doubling, cap 24 h; new attempt_no per retry",
     "No replay needed: next attempt creates attempt_no+1 under uq_organisation_verification_organisation_id_verification_kind_id_attempt_no", "SELECT organisation_id, count(*) FROM organisation_verification WHERE checked_at IS NULL GROUP BY 1 HAVING count(*) > 3;", "Manual verification by staff (organisation_document) when registry down > 48 h"],
    ["Gig fulfilment system", "Handoff not acknowledged within 5 min; no inbound events for active assignments for 30 min", "gig_reconcile_state 'pending' then 'drift_detected'; UI shows 'confirmation pending'; earnings shown with earnings_as_of_at", "gig_assignment, gig_fulfilment_event, gig_fulfilment_event_attempt", "Outbound retry with handoff_idempotency_key: 30 s, 2 min, 10 min, 1 h; reconciliation poll every 15 min for assignments in reconcile state pending/drift via idx_gig_assignment_gig_reconcile_state_id_status_reconciled_at",
     "Replay external events by cursor; duplicates dropped by uq_gig_fulfilment_event_source_system_idempotency_key; reapply unprocessed events ordered by occurred_at", "SELECT id FROM gig_assignment WHERE NOT is_completed AND gig_reconcile_state_id IN (SELECT id FROM gig_reconcile_state WHERE code IN ('pending','drift_detected')) AND status_reconciled_at < now()-interval '1 hour';", "Ops reviews 'manual_review' state; no payouts decided locally"],
    ["Legacy job and candidate systems", "Sync run fails or source unreachable", "Platform unaffected; run recorded failed; next scheduled run resumes by cursor/payload hash", "legacy_sync_run, legacy_record_map, legacy_sync_conflict, profile_import", "Retry next schedule (nightly); no deletes inferred from absence",
     "Re-run is idempotent: uq_legacy_record_map_legacy_source_system_id_legacy_entity_kind_id_source_record_ref, uq_profile_import_candidate_profile_id_import_source_id_pa_a579 and uq_candidate_profile_legacy_candidate_ref; watermark advances only from a completed legacy_sync_run", "SELECT import_status_id, count(*) FROM profile_import WHERE finished_at IS NULL AND created_at < now()-interval '2 hours' GROUP BY 1;", "Data team reviews partially_applied runs"],
    ["Messaging and notification providers", "Provider error rate > 5% or webhook silence > 10 min", "Failover to notification_provider.failover_notification_provider_id; in-app inbox unaffected; OTP escalates channel", "notification_delivery, notification_delivery_event, provider_webhook_event, notification_suppression", "Dispatcher retry attempt_no+1 (max 10) with backoff 30 s * 2^n; deferrals bounded by notification.expires_at",
     "Provider event replay lands in provider_webhook_event ON CONFLICT DO NOTHING; matcher resolves via (provider, provider_message_ref); daily rollup must run before TTL (monitor: missing notification_delivery_daily_summary day)", "SELECT notification_provider_id, count(*) FROM notification_delivery WHERE delivery_status_id IN (SELECT id FROM delivery_status WHERE code IN ('failed','deferred')) AND dispatched_at > now()-interval '1 hour' GROUP BY 1;", "Alert when failures > 5% for 10 min; complaint/bounce spikes pause sending per provider"],
    ["Social sharing", "N/A (no server dependency)", "Share intent opens on the client; if the network blocks it nothing is posted", "job_share_event, community_share", "None", "None", "SELECT count(*) FROM job_share_event WHERE share_token IS NULL;  -- must be 0", "None"],
    ["Mapping and geolocation service", "Geocoder quota/timeouts", "Store address with NULL coordinates or city centroid (geo_source 'city_centroid'); radius search degrades to city filter", "organisation_location, job_requirement_location, gig_request", "Backfill job selects rows WHERE latitude IS NULL AND address present, in batches of 500, rate-limited",
     "Backfill is idempotent (overwrite)", "SELECT count(*) FROM organisation_location WHERE latitude IS NULL AND deleted_at IS NULL;", "None (optional boundary)"],
    ["Company review and salary data", "Refresh job fails or source contract ends", "Show last good snapshot until max_staleness_days, then hide; never show zero", "reference_data_refresh, company_rating_snapshot, salary_benchmark", "Retry daily; alert after 2 failures",
     "Re-run with a new run_key; snapshot uniqueness by uq_company_rating_snapshot_grain", "SELECT source_code FROM reference_data_source WHERE is_active AND (last_refreshed_at IS NULL OR last_refreshed_at < now() - make_interval(days => max_staleness_days));", "Data owner renews licence or disables source"],
]

# ===================================================================================================================
def main():
    assert not BAD, "\n".join(sorted(set(BAD)))
    wb = new_wb()
    hdr = ["Boundary", "Data Ownership", "Stored vs. Referenced", "Failure Behaviour", "Idempotency", "Audit"]
    rows = [[b["name"], b["owner"], "STORED: " + b["stored"] + "\nREFERENCED / NOT STORED: " + b["referenced"], b["failure"], b["idem"], b["audit"]] for b in B]
    add_sheet(wb, "Boundaries", hdr, rows, widths=[26, 50, 80, 80, 80, 60], wrap_cols=tuple(hdr[1:]))
    add_sheet(wb, "Legacy sync", ["Entity", "Platform field(s)", "Sync direction", "Conflict rule", "Idempotency / natural key", "Note"], LS, widths=[12, 60, 36, 70, 52, 20], wrap_cols=("Platform field(s)", "Sync direction", "Conflict rule", "Idempotency / natural key"))
    ws = wb["Legacy sync"]
    r0 = ws.max_row + 3
    ws.cell(row=r0, column=1, value="GAPS in the current model")
    for i, g in enumerate(GAPS):
        for j, v in enumerate(g):
            ws.cell(row=r0 + 1 + i, column=1 + j, value=v)
    r1 = r0 + len(GAPS) + 3
    ws.cell(row=r1, column=1, value="IMPLEMENTED MODEL (source system = legacy_source_system, source record = source_record_ref)")
    ws.cell(row=r1 + 1, column=1, value=DDL)
    add_sheet(wb, "Boundary tables", ["Boundary", "Table", "Module", "Columns", "Unique idempotency constraint / index name", "TTL", "Delete policy"], BT_ROWS, widths=[34, 34, 22, 56, 90, 26, 12], wrap_cols=("Columns", "Unique idempotency constraint / index name"))
    add_sheet(wb, "Outage playbook", ["Boundary", "Detection signal", "Automatic behaviour", "Tables involved", "Retry policy", "Recovery / replay", "Reconciliation query", "Escalation"], OP,
              widths=[26, 40, 52, 36, 52, 52, 70, 36], wrap_cols=("Detection signal", "Automatic behaviour", "Tables involved", "Retry policy", "Recovery / replay", "Reconciliation query", "Escalation"))
    out = os.path.join(ROOT, "docs", "09_integration_boundaries.xlsx")
    wb.save(out)
    print("wrote", out, len(B), "boundaries", len(BT_ROWS), "boundary table rows")


if __name__ == "__main__":
    main()

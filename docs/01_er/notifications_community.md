# ER diagram: Notifications, preferences and community

Module key `notifications_community`, owner Team, 93 tables (28 lookups). Generated from `05_data_dictionary.xlsx` (do not edit by hand). Columns shown: primary key, foreign keys, unique/natural keys and a few business columns; see the dictionary for all columns. Edges to lookup (enumeration) tables are listed under *Lookup references* rather than drawn.

## Design notes

<!-- notifications_community.yaml -->
## Scope and entity list (B48)
Module owns 4 registry tables (notification, notification_delivery, community_post, support_ticket) plus ~60 supporting tables and ~34 lookups:
catalogue (notification_type/template/template_translation/provider), inbox (notification), B57 delivery path (notification_delivery,
notification_delivery_event, provider_webhook_event, notification_delivery_daily_summary), hygiene (notification_suppression,
unsubscribe_token, push_subscription), preferences (notification_preference, quiet_period, digest_batch(+item), language_preference,
privacy_setting(+hidden organisation)), job alerts (preference, skill, location, run, match), community (groups, posts, comments, reactions,
polls, follows, hashtags, mentions, shares, reports, moderation, feed_item), messaging (thread, participant, message, attachment, read
receipt), support (ticket, message, status history, attachment, SLA policy, CSAT), FAQ (category, item, translation, feedback), referral_event,
announcement (+translation, dismissal) and in-app surveys.

## B57 - high-volume write path for deliveries (decision and justification)
Decision: delivery state is EVENT-SOURCED and INSERT-ONLY. A status column updated in place (queued->sent->delivered->opened) costs 3-5 UPDATEs
per message, each rewriting an MVCC version and contending on one row under SERIALIZABLE; at the design target (50M deliveries/day, about 600/s
average and 5,000/s peak) that is 3-5x the write amplification for no benefit.
1. Source of work: a CockroachDB CHANGEFEED on `notification` (insert events) feeds the dispatcher workers. There is no queue table and no polling of
   a status column, so no UPDATE ever marks a notification as sent.
2. Dispatch: a worker renders, sends, then writes `notification_delivery` rows (one per notification x channel x attempt) in batches of 500-1000 rows
   per transaction using one multi-row `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`. Rows are written AFTER the provider answered, so the
   row already carries the final dispatch outcome (sent/failed/suppressed/deferred) and `provider_message_ref`. 'queued' is a valid status but is only used by
   the scheduler for future-dated sends. Rows are never updated.
3. Receipts: provider callbacks land in `provider_webhook_event` (INSERT ... ON CONFLICT (provider, provider_event_ref) DO NOTHING; SendGrid dedupe id is
   sg_event_id, Twilio sends no event id so the key is MessageSid:MessageStatus; Twilio documents that callbacks may arrive out of order). A matcher batch
   resolves the delivery through the unique `(provider, provider_message_ref)` index and inserts `notification_delivery_event` rows
   (unique (delivery, status): a status is recorded once, duplicates and replays are no-ops). Latest status = max(delivery_status.precedence) via
   `vw_notification_delivery_latest`; terminal bounce/complaint events also insert `notification_suppression` rows.
4. No hot ranges: primary keys are random UUIDv4; every time-ordered index (dispatched_at, occurred_at, received_at, expires_at, created_at) is
   HASH-SHARDED (bucket_count 16) so sequential timestamps spread across ranges; per-user access paths lead with the random user id.
   No SERIAL/sequence anywhere. Unique idempotency indexes are over hashes/UUIDs (uniform).
5. Retention: row-level TTL (`ttl_expiration_expression = 'expires_at'`, `ttl_job_cron = '@daily'`, `ttl_select_batch_size = 500`,
   `ttl_delete_batch_size = 500`, `ttl_delete_rate_limit` tuned per cluster) - 30 days for delivery and delivery_event, 7 days for raw webhook payloads,
   180 days for inbox rows, 90-120 days for alert run/match logs, 14 days for feed_item. Secondary indexes slow the TTL job (CockroachDB docs), so
   each TTL table carries only the indexes its read paths need.
6. Audit survives TTL: before expiry the daily rollup (01:00 UTC, idempotent, unique key per slice-day) writes `notification_delivery_daily_summary`:
   counts per day x channel x provider x kind plus `rows_digest` and a hash chain; kept 7 years. Rollup lag is bounded (< 2 days) far below the
   30-day TTL; a monitor alerts if a day is missing before its rows expire.
7. Bodies: Section 6 boundary. Only `body_digest` (SHA-256 of the rendered body), template id and the minimal payload are kept; the recipient is
   stored only as a keyed HMAC (`recipient_hash`), the address is resolved from contact tables at send time. Provider keeps the body.
8. Provider unavailable: the dispatcher fails over along notification_provider.failover_notification_provider_id and records the failed attempt;
   retries insert attempt_no+1 (max 10). Idempotency key = sha256(notification|channel|attempt_no).

## Preferences and privacy
notification_preference rows are lazily created (absence = kind default). Transactional kinds (OTP, receipts, security) are not configurable
(CHECK on notification_type). quiet_period defers non-critical sends. privacy_setting is one row per user and is consumed by the recruiter-search
and profile-download paths through `vw_recruiter_searchable_candidate` (also applies privacy_hidden_organisation anti-join and contact_visibility).
Consent artefacts stay in administration.user_consent; privacy_setting holds the operational switches only.

## Community, feed and moderation
fan-out: hybrid. Authors with <= 5,000 followers fan out on write into feed_item (TTL 14 days, rebuildable cache); large authors and company pages
are merged at read time from community_post (fan-out-on-read). Counters (reactions/comments/shares) are kept off the post row in community_post_stat,
refreshed in batches, to avoid hot-row contention. Search over posts/groups: trigram GIN indexes (D-16); ranking is the search service's job via changefeed.
Moderation: community_report -> moderation_case (one open case per target by partial unique) -> moderation_action_log (append-only hash chain).
Social sharing is outbound link only: community_share stores who shared what object to which network with a tracked link/token; no external content.

## Messaging
Bodies are application-layer encrypted (AES-GCM, KMS key id stored) on top of storage encryption; body is NULLable to model retention purge and
delete-for-everyone with a tombstone row. Blocking reuses administration.user_block, checked in the same transaction as thread/message creation (no
duplicate block table). message_permission (privacy_setting) gates who can start a thread (verified recruiters only etc.).
Read state: participant.last_read_at watermark (cheap); message_read_receipt only when the reader allows receipts.

## Support and FAQ
support_ticket models Zendesk concepts: category, priority, status, channel, SLA policy with due timestamps, first response/resolution times,
status history, CSAT. Ticket numbers are random base32 (no sequence). FAQ text per language lives in faq_item_translation (fallback chain in
language_preference notes); category names via ui_label keys.

## referral_event
Docket lists referral events in this module while the profile module owns referral tracking. profile.referral is not in REGISTRY, so referral_event
holds referrer/referred users directly and a `referral_code` string; no FK to a referral table. If a referral table is added later,
add referral_id (nullable) via migration.

## Competitor coverage observations
* LinkedIn: notification inbox with per-type settings, email digests, feed with reactions (6 types) / comments / reshares / polls / hashtags / mentions,
  groups, follows, InMail and messaging with read receipts and 'who can message me' settings, Open-to-Work visibility. We match all and add durable suppression,
  delivery-event audit and moderation case tooling that LinkedIn does not expose publicly.
* Naukri: job alerts (keywords, location, salary, experience, frequency), 'hide profile from companies', profile visibility, messenger, FAQs/help centre
  and support tickets. We match and exceed by modelling alert runs/matches (no duplicate alerts), per-channel preferences and quiet hours.
* Monster: job 'search agents' with email frequency, privacy 'confidential' profile and 'hide from employers'; community/forums; covered by
  job_alert_preference and privacy_hidden_organisation.
* Twilio/SendGrid delivery models: processed/delivered/deferred/bounce/dropped/open/click/spamreport and queued/sent/delivered/undelivered/failed/read;
  all mapped by delivery_status with precedence for out-of-order callbacks.
* Gaps by choice: no payment/voice-call logs, no full-text search engine, no ad targeting.

## Deviations and extension points
* D-04 applies: created_by/updated_by are not FKs. Polymorphic *_ref columns (source_object_ref, target_ref, shared_object_ref, related_object_ref)
  are soft references by design (several target tables); each is paired with a type code.
* enum FK columns on large tables intentionally have no leading index (index_exempt) to save write cost.
* Assumptions about master_data (to be reconciled at integration): notification_channel, visibility_level, salary_period, employment_type, work_type are
  lookups (enum_ref FKs); country, state_region, city, language, currency, skill, industry, job_category are entities (identifier FKs).
* Extension points: WhatsApp template approval workflow, per-organisation notification settings, sub-roles inside employer org for ticket access.

Language of user-entered text (finding F-06): free-text tables of this module carry a nullable `language_id` (FK `language`), NULL = unknown.

## Diagram

```mermaid
erDiagram
  announcement {
    UUID id PK
    STRING_60 announcement_code
    UUID announcement_placement_id FK
    UUID audience_type_id FK
    UUID country_id FK
    TIMESTAMPTZ starts_at
    TIMESTAMPTZ ends_at
  }
  announcement_dismissal {
    UUID id PK
    UUID announcement_id FK
    UUID app_user_id FK
  }
  announcement_translation {
    UUID id PK
    UUID announcement_id FK
    UUID language_id FK
    STRING_200 title_text
    STRING_2000 body_text
    STRING_60 cta_label
  }
  community_comment {
    UUID id PK
    UUID community_post_id FK
    UUID author_app_user_id FK
    UUID parent_community_comment_id FK
    STRING_4000 body_text
    UUID language_id FK
    UUID moderation_state_id FK
    TIMESTAMPTZ edited_at
  }
  community_follow {
    UUID id PK
    UUID follower_app_user_id FK
    UUID followee_app_user_id FK
    UUID followee_organisation_id FK
  }
  community_group {
    UUID id PK
    STRING_80 slug
    STRING_150 group_name
    STRING_2000 description_text
    UUID owner_app_user_id FK
    UUID group_join_policy_id FK
    UUID visibility_level_id FK
    UUID language_id FK
    UUID industry_id FK
    UUID job_category_id FK
  }
  community_group_member {
    UUID id PK
    UUID community_group_id FK
    UUID app_user_id FK
    UUID community_group_role_id FK
    UUID group_membership_status_id FK
    TIMESTAMPTZ joined_at
  }
  community_mention {
    UUID id PK
    UUID community_post_id FK
    UUID community_comment_id FK
    UUID mentioned_app_user_id FK
  }
  community_poll {
    UUID id PK
    UUID community_post_id FK
    TIMESTAMPTZ closes_at
    BOOL allows_multiple
    BOOL is_anonymous
  }
  community_poll_option {
    UUID id PK
    UUID community_poll_id FK
    STRING_200 option_label
    INT2 sort_order UK
  }
  community_poll_vote {
    UUID id PK
    UUID community_poll_id FK
    UUID community_poll_option_id FK
    UUID app_user_id FK
    UUID choice_slot_ref UK
  }
  community_post {
    UUID id PK
    UUID author_app_user_id FK
    UUID community_group_id FK
    UUID organisation_id FK
    UUID post_type_id FK
    STRING body_text
    UUID language_id FK
    UUID visibility_level_id FK
    UUID moderation_state_id FK
    UUID original_community_post_id FK
    STRING_1000 link_url
    TIMESTAMPTZ published_at
  }
  community_post_hashtag {
    UUID id PK
    UUID community_post_id FK
    UUID hashtag_id FK
  }
  community_post_media {
    UUID id PK
    UUID community_post_id FK
    UUID media_type_id FK
    STRING_300 storage_ref
    STRING_100 mime_type
    INT8 byte_size
    INT2 sort_order UK
    UUID scan_status_id FK
  }
  community_post_stat {
    UUID id PK
    UUID community_post_id FK
    INT4 reaction_count
    INT4 comment_count
    INT4 share_count
  }
  community_reaction {
    UUID id PK
    UUID community_post_id FK
    UUID app_user_id FK
    UUID reaction_type_id FK
  }
  community_report {
    UUID id PK
    UUID reporter_app_user_id FK
    UUID report_target_type_id FK
    UUID target_ref
    UUID report_reason_id FK
    STRING_1000 details_text
    UUID moderation_case_id FK
  }
  community_share {
    UUID id PK
    UUID app_user_id FK
    UUID share_channel_id FK
    UUID share_object_type_id FK
    UUID shared_object_ref
    UUID community_post_id FK
    STRING_500 share_link_url
    STRING_32 share_token UK
  }
  digest_batch {
    UUID id PK
    UUID app_user_id FK
    UUID notification_channel_id FK
    UUID alert_frequency_id FK
    TIMESTAMPTZ period_start_at UK
    TIMESTAMPTZ period_end_at
    UUID digest_batch_status_id FK
    TIMESTAMPTZ scheduled_for_at
    TIMESTAMPTZ built_at
    UUID digest_notification_id FK
  }
  digest_batch_item {
    UUID id PK
    UUID digest_batch_id FK
    UUID notification_id FK
  }
  faq_category {
    UUID id PK
    STRING_60 category_code
    UUID parent_faq_category_id FK
    STRING_150 name_label_key
    UUID audience_type_id FK
    INT4 sort_order
  }
  faq_feedback {
    UUID id PK
    UUID faq_item_id FK
    UUID app_user_id FK
    BOOL is_helpful
    UUID language_id FK
    STRING_500 comment_text
  }
  faq_item {
    UUID id PK
    UUID faq_category_id FK
    STRING_80 item_code
    UUID audience_type_id FK
    INT4 sort_order
    BOOL is_published
  }
  faq_item_translation {
    UUID id PK
    UUID faq_item_id FK
    UUID language_id FK
    STRING_500 question_text
    STRING answer_text
    BOOL is_machine_translated
  }
  feed_item {
    UUID id PK
    UUID recipient_app_user_id FK
    UUID community_post_id FK
    UUID feed_item_source_id FK
    DECIMAL_7_4 rank_score
    TIMESTAMPTZ expires_at
  }
  hashtag {
    UUID id PK
    STRING_80 tag_text UK
  }
  in_app_survey {
    UUID id PK
    STRING_60 survey_code
    STRING_150 title_label_key
    UUID audience_type_id FK
    STRING_60 trigger_event_code
  }
  in_app_survey_answer {
    UUID id PK
    UUID in_app_survey_response_id FK
    UUID in_app_survey_id FK
    UUID in_app_survey_question_id FK
    INT2 answer_number
    STRING_60 answer_code
    STRING_2000 answer_text
  }
  in_app_survey_question {
    UUID id PK
    UUID in_app_survey_id FK
    STRING_150 question_label_key
    UUID survey_question_type_id FK
    BOOL is_required
    INT2 sort_order UK
    JSONB choice_options
  }
  in_app_survey_response {
    UUID id PK
    UUID in_app_survey_id FK
    UUID app_user_id FK
    UUID language_id FK
  }
  job_alert_location {
    UUID id PK
    UUID job_alert_preference_id FK
    UUID country_id FK
    UUID state_region_id FK
    UUID city_id FK
    BOOL is_remote
  }
  job_alert_match {
    UUID id PK
    UUID job_alert_run_id FK
    UUID job_alert_preference_id FK
    UUID job_requirement_id FK
    DECIMAL_7_4 match_score
    INT2 rank_position
    BOOL included_in_notification
  }
  job_alert_preference {
    UUID id PK
    UUID app_user_id FK
    STRING_100 alert_name
    JSONB keyword_list
    UUID job_category_id FK
    UUID industry_id FK
    UUID employment_type_id FK
    UUID work_type_id FK
    DECIMAL_18_4 min_salary
    UUID currency_id FK
    UUID salary_period_id FK
    UUID alert_frequency_id FK
    UUID notification_channel_id FK
  }
  job_alert_run {
    UUID id PK
    UUID job_alert_preference_id FK
    TIMESTAMPTZ scheduled_for_at UK
    TIMESTAMPTZ started_at
    TIMESTAMPTZ finished_at
    UUID alert_run_status_id FK
    INT4 candidate_job_count
    UUID notification_id FK
  }
  job_alert_skill {
    UUID id PK
    UUID job_alert_preference_id FK
    UUID skill_id FK
    BOOL is_required
  }
  language_preference {
    UUID id PK
    UUID app_user_id FK
    UUID ui_language_id FK
    UUID content_language_id FK
    UUID notification_language_id FK
    UUID fallback_language_id FK
    STRING_64 time_zone
    BOOL is_auto_detected
  }
  message {
    UUID id PK
    UUID message_thread_id FK
    UUID sender_app_user_id FK
    UUID reply_to_message_id FK
    BYTES body_encrypted
    STRING_100 body_key_ref
    STRING_64 client_message_ref UK
    BOOL is_system
    UUID moderation_state_id FK
  }
  message_attachment {
    UUID id PK
    UUID message_id FK
    UUID media_type_id FK
    STRING_300 storage_ref
    STRING_255 file_name
    STRING_100 mime_type
    UUID scan_status_id FK
  }
  message_read_receipt {
    UUID id PK
    UUID message_id FK
    UUID app_user_id FK
  }
  message_thread {
    UUID id PK
    UUID message_thread_type_id FK
    UUID started_by_app_user_id FK
    UUID job_requirement_id FK
    UUID job_application_id FK
    UUID organisation_id FK
    STRING_200 subject_text
    TIMESTAMPTZ last_message_at
  }
  message_thread_participant {
    UUID id PK
    UUID message_thread_id FK
    UUID app_user_id FK
    UUID organisation_id FK
    TIMESTAMPTZ joined_at
    TIMESTAMPTZ left_at
    TIMESTAMPTZ last_read_at
  }
  moderation_action_log {
    UUID id PK
    UUID moderation_case_id FK
    UUID moderation_action_id FK
    STRING_500 note
    STRING_64 prev_hash
    STRING_64 row_hash
  }
  notification {
    UUID id PK
    UUID recipient_app_user_id FK
    UUID notification_type_id FK
    UUID notification_priority_id FK
    JSONB payload
    STRING_40 source_object_type
    UUID source_object_ref
    STRING_100 dedupe_key UK
    UUID language_id FK
  }
  notification_delivery {
    UUID id PK
    UUID notification_id FK
    UUID notification_channel_id FK
    UUID notification_provider_id FK
    UUID notification_template_id FK
    INT2 attempt_no UK
    UUID delivery_status_id FK
    STRING_64 idempotency_key UK
    STRING_200 provider_message_ref
    STRING_64 recipient_hash
    STRING_60 error_code
  }
  notification_delivery_daily_summary {
    UUID id PK
    DATE summary_date UK
    UUID notification_channel_id FK
    UUID notification_provider_id FK
    UUID notification_type_id FK
    INT8 attempted_count
    INT8 sent_count
    INT8 delivered_count
  }
  notification_delivery_event {
    UUID id PK
    UUID notification_delivery_id FK
    UUID delivery_status_id FK
    TIMESTAMPTZ occurred_at
    STRING_128 provider_event_ref
    TIMESTAMPTZ expires_at
  }
  notification_preference {
    UUID id PK
    UUID app_user_id FK
    UUID notification_type_id FK
    UUID notification_channel_id FK
    BOOL is_enabled
    UUID alert_frequency_id FK
  }
  notification_provider {
    UUID id PK
    UUID notification_channel_id FK
    STRING_40 provider_code UK
    STRING_100 display_name
    INT2 routing_weight
    INT4 rate_limit_per_second
    UUID failover_notification_provider_id FK
  }
  notification_suppression {
    UUID id PK
    UUID notification_channel_id FK
    STRING_64 recipient_hash
    UUID suppression_reason_id FK
    UUID notification_provider_id FK
    UUID app_user_id FK
    TIMESTAMPTZ suppressed_at
    TIMESTAMPTZ expires_at
  }
  notification_template {
    UUID id PK
    UUID notification_type_id FK
    UUID notification_channel_id FK
    UUID notification_provider_id FK
    STRING_80 template_code
    INT4 version_no
    STRING_120 provider_template_ref
  }
  notification_template_translation {
    UUID id PK
    UUID notification_template_id FK
    UUID language_id FK
    STRING_200 title_text
    STRING_300 subject_text
    STRING body_text
  }
  notification_type {
    UUID id PK
    STRING_60 type_code UK
    UUID notification_category_id FK
    UUID default_notification_priority_id FK
    STRING_150 title_label_key
    STRING_150 description_label_key
    BOOL is_transactional
  }
  privacy_hidden_organisation {
    UUID id PK
    UUID app_user_id FK
    UUID organisation_id FK
  }
  privacy_setting {
    UUID id PK
    UUID app_user_id FK
    UUID profile_visibility_level_id FK
    UUID contact_visibility_id FK
    UUID message_permission_id FK
    BOOL is_searchable_by_recruiters
    BOOL hide_from_company_list
    BOOL show_last_active
  }
  provider_webhook_event {
    UUID id PK
    UUID notification_provider_id FK
    STRING_160 provider_event_ref UK
    STRING_60 event_type_code
    UUID delivery_status_id FK
    STRING_200 provider_message_ref
    UUID notification_delivery_id FK
    TIMESTAMPTZ occurred_at
  }
  push_subscription {
    UUID id PK
    UUID app_user_id FK
    UUID device_id FK
    UUID push_platform_id FK
    UUID notification_provider_id FK
    BYTES endpoint_encrypted
    STRING_64 endpoint_hash UK
    STRING_30 app_version
    TIMESTAMPTZ last_seen_at
  }
  quiet_period {
    UUID id PK
    UUID app_user_id FK
    STRING_64 time_zone
    TIME quiet_from_time
    TIME quiet_to_time
  }
  referral_event {
    UUID id PK
    UUID referrer_app_user_id FK
    UUID referred_app_user_id FK
    UUID referral_event_type_id FK
    STRING_24 referral_code
    STRING_64 invitee_contact_hash
    UUID notification_channel_id FK
    UUID job_requirement_id FK
    TIMESTAMPTZ occurred_at
    STRING_64 idempotency_key UK
  }
  support_csat_response {
    UUID id PK
    UUID support_ticket_id FK
    UUID app_user_id FK
    INT2 rating
    STRING_1000 comment_text
  }
  support_sla_policy {
    UUID id PK
    STRING_60 policy_code
    UUID ticket_priority_id FK
    UUID support_ticket_category_id FK
    INT4 first_response_minutes
    INT4 resolution_minutes
  }
  support_ticket {
    UUID id PK
    STRING_20 ticket_number
    UUID requester_app_user_id FK
    UUID organisation_id FK
    UUID support_ticket_category_id FK
    UUID ticket_priority_id FK
    UUID ticket_status_id FK
    UUID ticket_channel_id FK
    UUID support_sla_policy_id FK
    UUID assigned_app_user_id FK
    UUID language_id FK
    STRING_200 subject_text
    STRING_40 related_object_type
  }
  support_ticket_attachment {
    UUID id PK
    UUID support_ticket_id FK
    UUID support_ticket_message_id FK
    UUID media_type_id FK
    STRING_300 storage_ref
    STRING_255 file_name
    STRING_100 mime_type
    UUID scan_status_id FK
  }
  support_ticket_message {
    UUID id PK
    UUID support_ticket_id FK
    UUID author_app_user_id FK
    BOOL is_internal_note
    STRING body_text
    STRING_255 email_message_ref
    UUID language_id FK
  }
  support_ticket_status_history {
    UUID id PK
    UUID support_ticket_id FK
    UUID from_ticket_status_id FK
    UUID to_ticket_status_id FK
    STRING_300 change_reason
  }
  unsubscribe_token {
    UUID id PK
    UUID app_user_id FK
    UUID notification_category_id FK
    UUID notification_channel_id FK
    STRING_64 token_hash UK
    TIMESTAMPTZ expires_at
    TIMESTAMPTZ used_at
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
  industry {
    UUID id PK
  }
  job_application {
    UUID id PK
  }
  job_category {
    UUID id PK
  }
  job_requirement {
    UUID id PK
  }
  language {
    UUID id PK
  }
  moderation_case {
    UUID id PK
  }
  organisation {
    UUID id PK
  }
  skill {
    UUID id PK
  }
  state_region {
    UUID id PK
  }
  announcement ||--o{ announcement_dismissal : "announcement_id"
  announcement ||--o{ announcement_translation : "announcement_id"
  app_user |o--o{ community_follow : "followee_app_user_id"
  app_user |o--o{ faq_feedback : "app_user_id"
  app_user |o--o{ in_app_survey_response : "app_user_id"
  app_user |o--o{ notification_suppression : "app_user_id"
  app_user |o--o{ referral_event : "referred_app_user_id"
  app_user |o--o{ support_ticket : "assigned_app_user_id"
  app_user ||--o{ announcement_dismissal : "app_user_id"
  app_user ||--o{ community_comment : "author_app_user_id"
  app_user ||--o{ community_follow : "follower_app_user_id"
  app_user ||--o{ community_group : "owner_app_user_id"
  app_user ||--o{ community_group_member : "app_user_id"
  app_user ||--o{ community_mention : "mentioned_app_user_id"
  app_user ||--o{ community_poll_vote : "app_user_id"
  app_user ||--o{ community_post : "author_app_user_id"
  app_user ||--o{ community_reaction : "app_user_id"
  app_user ||--o{ community_report : "reporter_app_user_id"
  app_user ||--o{ community_share : "app_user_id"
  app_user ||--o{ digest_batch : "app_user_id"
  app_user ||--o{ feed_item : "recipient_app_user_id"
  app_user ||--o{ job_alert_preference : "app_user_id"
  app_user ||--o{ message : "sender_app_user_id"
  app_user ||--o{ message_read_receipt : "app_user_id"
  app_user ||--o{ message_thread : "started_by_app_user_id"
  app_user ||--o{ message_thread_participant : "app_user_id"
  app_user ||--o{ notification : "recipient_app_user_id"
  app_user ||--o{ notification_preference : "app_user_id"
  app_user ||--o{ privacy_hidden_organisation : "app_user_id"
  app_user ||--o{ push_subscription : "app_user_id"
  app_user ||--o{ referral_event : "referrer_app_user_id"
  app_user ||--o{ support_csat_response : "app_user_id"
  app_user ||--o{ support_ticket : "requester_app_user_id"
  app_user ||--o{ support_ticket_message : "author_app_user_id"
  app_user ||--o{ unsubscribe_token : "app_user_id"
  app_user ||--o| language_preference : "app_user_id"
  app_user ||--o| privacy_setting : "app_user_id"
  app_user ||--o| quiet_period : "app_user_id"
  city |o--o{ job_alert_location : "city_id"
  community_comment |o--o{ community_comment : "parent_community_comment_id"
  community_comment |o--o{ community_mention : "community_comment_id"
  community_group |o--o{ community_post : "community_group_id"
  community_group ||--o{ community_group_member : "community_group_id"
  community_poll ||--o{ community_poll_option : "community_poll_id"
  community_poll ||--o{ community_poll_vote : "community_poll_id"
  community_poll_option ||--o{ community_poll_vote : "community_poll_id"
  community_poll_option ||--o{ community_poll_vote : "community_poll_option_id"
  community_post |o--o{ community_mention : "community_post_id"
  community_post |o--o{ community_post : "original_community_post_id"
  community_post |o--o{ community_share : "community_post_id"
  community_post ||--o{ community_comment : "community_post_id"
  community_post ||--o{ community_poll : "community_post_id"
  community_post ||--o{ community_post_hashtag : "community_post_id"
  community_post ||--o{ community_post_media : "community_post_id"
  community_post ||--o{ community_reaction : "community_post_id"
  community_post ||--o{ feed_item : "community_post_id"
  community_post ||--o| community_post_stat : "community_post_id"
  country |o--o{ announcement : "country_id"
  country |o--o{ job_alert_location : "country_id"
  currency |o--o{ job_alert_preference : "currency_id"
  device |o--o{ push_subscription : "device_id"
  digest_batch ||--o{ digest_batch_item : "digest_batch_id"
  faq_category |o--o{ faq_category : "parent_faq_category_id"
  faq_category ||--o{ faq_item : "faq_category_id"
  faq_item ||--o{ faq_feedback : "faq_item_id"
  faq_item ||--o{ faq_item_translation : "faq_item_id"
  hashtag ||--o{ community_post_hashtag : "hashtag_id"
  in_app_survey ||--o{ in_app_survey_answer : "in_app_survey_id"
  in_app_survey ||--o{ in_app_survey_question : "in_app_survey_id"
  in_app_survey ||--o{ in_app_survey_response : "in_app_survey_id"
  in_app_survey_question ||--o{ in_app_survey_answer : "in_app_survey_id"
  in_app_survey_question ||--o{ in_app_survey_answer : "in_app_survey_question_id"
  in_app_survey_response ||--o{ in_app_survey_answer : "in_app_survey_response_id"
  industry |o--o{ community_group : "industry_id"
  industry |o--o{ job_alert_preference : "industry_id"
  job_alert_preference ||--o{ job_alert_location : "job_alert_preference_id"
  job_alert_preference ||--o{ job_alert_match : "job_alert_preference_id"
  job_alert_preference ||--o{ job_alert_run : "job_alert_preference_id"
  job_alert_preference ||--o{ job_alert_skill : "job_alert_preference_id"
  job_alert_run ||--o{ job_alert_match : "job_alert_run_id"
  job_application |o--o{ message_thread : "job_application_id"
  job_category |o--o{ community_group : "job_category_id"
  job_category |o--o{ job_alert_preference : "job_category_id"
  job_requirement |o--o{ message_thread : "job_requirement_id"
  job_requirement |o--o{ referral_event : "job_requirement_id"
  job_requirement ||--o{ job_alert_match : "job_requirement_id"
  language |o--o{ community_comment : "language_id"
  language |o--o{ community_group : "language_id"
  language |o--o{ community_post : "language_id"
  language |o--o{ faq_feedback : "language_id"
  language |o--o{ in_app_survey_response : "language_id"
  language |o--o{ language_preference : "content_language_id"
  language |o--o{ language_preference : "fallback_language_id"
  language |o--o{ language_preference : "notification_language_id"
  language |o--o{ notification : "language_id"
  language |o--o{ support_ticket : "language_id"
  language |o--o{ support_ticket_message : "language_id"
  language ||--o{ announcement_translation : "language_id"
  language ||--o{ faq_item_translation : "language_id"
  language ||--o{ language_preference : "ui_language_id"
  language ||--o{ notification_template_translation : "language_id"
  message |o--o{ message : "reply_to_message_id"
  message ||--o{ message_attachment : "message_id"
  message ||--o{ message_read_receipt : "message_id"
  message_thread ||--o{ message : "message_thread_id"
  message_thread ||--o{ message_thread_participant : "message_thread_id"
  moderation_case |o--o{ community_report : "moderation_case_id"
  moderation_case ||--o{ moderation_action_log : "moderation_case_id"
  notification |o--o{ digest_batch : "digest_notification_id"
  notification |o--o{ job_alert_run : "notification_id"
  notification ||--o{ digest_batch_item : "notification_id"
  notification ||--o{ notification_delivery : "notification_id"
  notification_delivery |o--o{ provider_webhook_event : "notification_delivery_id"
  notification_delivery ||--o{ notification_delivery_event : "notification_delivery_id"
  notification_provider |o--o{ notification_provider : "failover_notification_provider_id"
  notification_provider |o--o{ notification_suppression : "notification_provider_id"
  notification_provider |o--o{ notification_template : "notification_provider_id"
  notification_provider ||--o{ notification_delivery : "notification_provider_id"
  notification_provider ||--o{ notification_delivery_daily_summary : "notification_provider_id"
  notification_provider ||--o{ provider_webhook_event : "notification_provider_id"
  notification_provider ||--o{ push_subscription : "notification_provider_id"
  notification_template ||--o{ notification_delivery : "notification_template_id"
  notification_template ||--o{ notification_template_translation : "notification_template_id"
  notification_type ||--o{ notification : "notification_type_id"
  notification_type ||--o{ notification_delivery_daily_summary : "notification_type_id"
  notification_type ||--o{ notification_preference : "notification_type_id"
  notification_type ||--o{ notification_template : "notification_type_id"
  organisation |o--o{ community_follow : "followee_organisation_id"
  organisation |o--o{ community_post : "organisation_id"
  organisation |o--o{ message_thread : "organisation_id"
  organisation |o--o{ message_thread_participant : "organisation_id"
  organisation |o--o{ support_ticket : "organisation_id"
  organisation ||--o{ privacy_hidden_organisation : "organisation_id"
  skill ||--o{ job_alert_skill : "skill_id"
  state_region |o--o{ job_alert_location : "state_region_id"
  support_sla_policy |o--o{ support_ticket : "support_sla_policy_id"
  support_ticket ||--o{ support_ticket_attachment : "support_ticket_id"
  support_ticket ||--o{ support_ticket_message : "support_ticket_id"
  support_ticket ||--o{ support_ticket_status_history : "support_ticket_id"
  support_ticket ||--o| support_csat_response : "support_ticket_id"
  support_ticket_message |o--o{ support_ticket_attachment : "support_ticket_message_id"
```

## Lookup tables in this module

| Lookup | Values | Description |
|---|---|---|
| `alert_frequency` | 4 | How often a job alert or digest is produced. |
| `alert_run_status` | 5 | Outcome of one scheduled job-alert matching run. |
| `announcement_placement` | 4 | Where an announcement is displayed. |
| `audience_type` | 7 | Audience targeted by an announcement, FAQ entry or survey. |
| `community_group_role` | 3 | Role of a member inside a community group. |
| `contact_visibility` | 5 | Who may see a candidate's contact details. |
| `delivery_status` | 11 | Status of one delivery attempt or provider receipt. precedence resolves out-of-order callbacks (higher wins among non-terminal). |
| `digest_batch_status` | 4 | Build/send state of a digest batch. |
| `feed_item_source` | 5 | Why an item appears in a user's feed. |
| `group_join_policy` | 3 | How new members join a community group. |
| `group_membership_status` | 5 | State of a group membership. |
| `message_permission` | 4 | Who may start a direct message with the user. |
| `message_thread_type` | 5 | Kind of direct-messaging thread. |
| `moderation_state` | 6 | Moderation state of user-generated content. |
| `notification_category` | 11 | Top-level grouping of notification types, used for preference screens and unsubscribe groups. |
| `notification_priority` | 4 | Dispatch priority of a notification; drives queue selection and quiet-hours override. |
| `post_type` | 6 | Kind of community post. |
| `push_platform` | 3 | Push delivery platform of a registered push subscription. |
| `reaction_type` | 6 | Reaction a user can leave on a community post. |
| `referral_event_type` | 7 | Stage of a referral funnel emitted as an event. |
| `report_target_type` | 6 | Kind of object a report or moderation case points at. |
| `share_object_type` | 6 | Kind of platform object that was shared. |
| `support_ticket_category` | 11 | Support issue category (first level of the help taxonomy). |
| `suppression_reason` | 7 | Why an address is on the suppression list. |
| `survey_question_type` | 5 | Answer format of an in-app survey question. |
| `ticket_channel` | 5 | Channel through which a support ticket was raised. |
| `ticket_priority` | 4 | Support ticket priority; selects the SLA policy. |
| `ticket_status` | 6 | Lifecycle state of a support ticket. |

## Lookup references (enumeration FKs)

| Table | Lookup tables referenced |
|---|---|
| `announcement` | `announcement_placement`, `audience_type` |
| `community_comment` | `moderation_state` |
| `community_group` | `group_join_policy`, `visibility_level` |
| `community_group_member` | `community_group_role`, `group_membership_status` |
| `community_post` | `moderation_state`, `post_type`, `visibility_level` |
| `community_post_media` | `media_type`, `scan_status` |
| `community_reaction` | `reaction_type` |
| `community_report` | `report_reason`, `report_target_type` |
| `community_share` | `share_channel`, `share_object_type` |
| `digest_batch` | `alert_frequency`, `digest_batch_status`, `notification_channel` |
| `faq_category` | `audience_type` |
| `faq_item` | `audience_type` |
| `feed_item` | `feed_item_source` |
| `in_app_survey` | `audience_type` |
| `in_app_survey_question` | `survey_question_type` |
| `job_alert_preference` | `alert_frequency`, `employment_type`, `notification_channel`, `salary_period`, `work_type` |
| `job_alert_run` | `alert_run_status` |
| `message` | `moderation_state` |
| `message_attachment` | `media_type`, `scan_status` |
| `message_thread` | `message_thread_type` |
| `moderation_action_log` | `moderation_action` |
| `notification` | `notification_priority` |
| `notification_delivery` | `delivery_status`, `notification_channel` |
| `notification_delivery_daily_summary` | `notification_channel` |
| `notification_delivery_event` | `delivery_status` |
| `notification_preference` | `alert_frequency`, `notification_channel` |
| `notification_provider` | `notification_channel` |
| `notification_suppression` | `notification_channel`, `suppression_reason` |
| `notification_template` | `notification_channel` |
| `notification_type` | `notification_category`, `notification_priority` |
| `privacy_setting` | `contact_visibility`, `message_permission`, `visibility_level` |
| `provider_webhook_event` | `delivery_status` |
| `push_subscription` | `push_platform` |
| `referral_event` | `notification_channel`, `referral_event_type` |
| `support_sla_policy` | `support_ticket_category`, `ticket_priority` |
| `support_ticket` | `support_ticket_category`, `ticket_channel`, `ticket_priority`, `ticket_status` |
| `support_ticket_attachment` | `media_type`, `scan_status` |
| `support_ticket_status_history` | `ticket_status` |
| `unsubscribe_token` | `notification_category`, `notification_channel` |

## Tables

| Table | Kind | Delete | Volume | Description |
|---|---|---|---|---|
| `announcement` | entity | soft | low | System announcement/banner shown in the app within a window to an audience. |
| `announcement_dismissal` | event | append_only | high | A user dismissing an announcement. |
| `announcement_translation` | entity | hard | low | Announcement text per language. |
| `community_comment` | entity | soft | high | Comment on a post, one level of threading via parent. |
| `community_follow` | link | hard | very_high | Follow of a person or company page; exactly one target is set. |
| `community_group` | entity | soft | medium | Topic or professional community group (LinkedIn Groups style): public/private, join policy, language and industry focus. |
| `community_group_member` | link | hard | high | Membership of a user in a group with role and status. |
| `community_mention` | link | hard | high | @mention of a user in a post or comment; drives mention notifications. |
| `community_poll` | entity | soft | medium | Poll attached to a post (question is the post body). |
| `community_poll_option` | entity | hard | medium | Answer option of a poll. |
| `community_poll_vote` | link | hard | high | Vote of a user on an option. choice_slot_ref = poll id for single-choice polls and option id for multi-choice, so a unique key enforces the one-vote rule declaratively. |
| `community_post` | entity | soft | high | Community feed post (text, article, media, poll, shared job). Body stored once in the author's language. Public content, but message-style PII in free text is classified Personal. |
| `community_post_hashtag` | link | hard | high | Post to hashtag link. |
| `community_post_media` | entity | hard | high | Media items attached to a post (object-store references only). |
| `community_post_stat` | snapshot | hard | high | Denormalised counters per post, updated by a batch job from reactions/comments/shares. Keeps hot counter updates off community_post. |
| `community_reaction` | link | hard | very_high | A user's single reaction to a post (changing it updates the row). |
| `community_report` | event | soft | medium | User report of a post/comment/message/group/user/ticket. Reports on one target are grouped into a moderation_case. |
| `community_share` | event | append_only | high | Outbound social-sharing event: who shared which platform object to which external network. Only the generated outbound link is stored, never the external post. |
| `digest_batch` | entity | hard | high | One digest per user x channel x frequency x period, collecting notifications that opted into digest mode. |
| `digest_batch_item` | link | hard | high | Notification included in a digest. |
| `faq_category` | entity | soft | low | FAQ/help-centre category tree; name through ui_label. |
| `faq_feedback` | event | append_only | medium | Helpful/not-helpful vote on an FAQ item. |
| `faq_item` | entity | soft | low | FAQ entry; question and answer text per language are in faq_item_translation. |
| `faq_item_translation` | entity | hard | low | Question and answer of an FAQ item in one language (fallback: language.fallback, then English). |
| `feed_item` | snapshot | hard | very_high | Materialised feed entry. Design: hybrid fan-out. Authors with <= 5,000 followers fan out on write into this table; celebrities/company pages are merged at read time (fan-out-on-read) from community_post, so no author write explodes into millions of rows. |
| `hashtag` | entity | hard | high | Normalised hashtag. |
| `in_app_survey` | entity | soft | low | Short in-app survey (NPS/CSAT/feature feedback) shown to an audience on a trigger. |
| `in_app_survey_answer` | event | append_only | medium | Answer to one question. Composite FKs guarantee the question and the response belong to the same survey. |
| `in_app_survey_question` | entity | hard | low | Question of a survey; text via ui_label. |
| `in_app_survey_response` | event | append_only | medium | One submission of a survey. |
| `job_alert_location` | link | hard | high | Locations of an alert at city, state or country granularity, or remote. |
| `job_alert_match` | event | append_only | very_high | Matching log: which job matched which alert in which run, with score and whether it made it into the notification. Unique per alert and job so a job is never alerted twice. |
| `job_alert_preference` | entity | soft | high | Saved job-alert criteria (Naukri 'Job alerts', LinkedIn 'job alert', Monster 'search agent'): keywords, locations, skills, salary, experience, frequency and channel. Skills and locations are child rows so matching can use FK indexes. |
| `job_alert_run` | event | append_only | very_high | One row per executed matching run of an alert (written at run completion, never updated). Idempotent per alert and scheduled slot. |
| `job_alert_skill` | link | hard | high | Skills an alert requires or prefers. |
| `language_preference` | config | hard | high | One row per user: UI language, content language, notification language and explicit fallback. Feeds ui_label fallback: requested -> fallback_language -> language.fallback -> English. |
| `message` | entity | soft | very_high | Direct message. Body is envelope-encrypted at the application layer (AES-GCM, key id in body_key_ref) in addition to storage-layer encryption; DB never sees plaintext (D-17, B77). Idempotent on client_message_ref. |
| `message_attachment` | entity | hard | high | File attached to a direct message (object-store reference). |
| `message_read_receipt` | event | append_only | high | Per-message read receipt, written only when the reader's privacy_setting.allow_read_receipts is true. created_at is the read time. |
| `message_thread` | entity | soft | high | Direct-message conversation (1:1, group, recruiter outreach, application conversation). Blocking uses administration.user_block, checked before a thread/message is created. |
| `message_thread_participant` | link | hard | high | User in a thread with read watermark, mute and archive state. last_read_at is the cheap read marker; per-message receipts exist only for users who allow read receipts. |
| `moderation_action_log` | audit | append_only | medium | Immutable record of each moderator action on a case with hash chain. |
| `notification` | event | hard | very_high | Logical notification per recipient (the inbox item). One row per recipient and business event; physical sends per channel live in notification_delivery. Payload is minimal ids/params only; rendered text is never stored. |
| `notification_delivery` | event | append_only | very_high | B57 HIGH-VOLUME WRITE PATH. One immutable row per dispatch attempt (notification x channel x attempt_no), inserted by the dispatcher in batches of ~500-1000 AFTER the provider accepted/rejected the send. Never updated: later receipts (delivered/bounced/opened) are inserted into notification_delivery_event. No message body is stored, only a SHA-256 digest of the rendered body for audit. |
| `notification_delivery_daily_summary` | snapshot | append_only | medium | Per day x channel x provider x notification kind counts of deliveries by outcome, with a digest of the underlying delivery ids. This is the durable audit record once row-level TTL has removed the detail. |
| `notification_delivery_event` | event | append_only | very_high | One row per state change reported for a delivery (delivered, opened, clicked, bounced, complained...). Replaces UPDATE-in-place of a status column: duplicates and out-of-order callbacks collapse via the unique key; latest/best status = max(delivery_status.precedence). |
| `notification_preference` | config | hard | high | User x notification kind x channel opt-in/opt-out and frequency. The send path joins this to decide whether to produce a delivery. |
| `notification_provider` | config | hard | low | External messaging provider account per channel (e.g. email ESP, SMS gateway, WhatsApp Business API, FCM/APNs). Platform stores delivery status only; provider owns message bodies after hand-off (docket Section 6). |
| `notification_suppression` | entity | hard | high | Do-not-send list per channel: hard bounces, spam complaints, unsubscribes. Checked before every send. Keyed on the recipient hash, never the clear address. |
| `notification_template` | config | soft | low | Per notification kind x channel template version. Bodies live in notification_template_translation (per language); short UI strings use ui_label keys. |
| `notification_template_translation` | config | hard | low | Subject/title/body of a template in one language. Fallback: requested language, language.fallback, then English. |
| `notification_type` | config | hard | low | Catalogue of notification kinds (e.g. job_alert_match, application_status_changed, interview_scheduled, message_received). Preferences, templates and notifications all key on it. |
| `privacy_hidden_organisation` | link | hard | medium | Organisations from which a candidate hides the profile (Naukri 'hide profile from companies', LinkedIn 'block current employer'). |
| `privacy_setting` | config | hard | high | One row per user: who sees the profile and contact details, whether recruiters can find the user, who may message. Search and download paths MUST filter on this row. |
| `provider_webhook_event` | event | append_only | very_high | Inbound delivery receipts from messaging providers, verified and stored raw for replay/forensics. Idempotency: unique (provider, provider_event_ref); SendGrid supplies sg_event_id, Twilio does not, so the key is MessageSid:MessageStatus. |
| `push_subscription` | entity | hard | high | Registered push endpoint (FCM/APNs token or Web Push subscription) per user and device. |
| `quiet_period` | config | hard | high | Do-not-disturb window per user in the user's own time zone; the dispatcher defers non-critical sends into the window's end. |
| `referral_event` | event | append_only | high | Referral funnel event (invite sent/clicked/signed up/applied/hired/reward). Emitted by the product and consumed by notifications and the profile module's referral tracking. Not FK-linked to profile.referral (not in registry): referrer and referred user are held directly, referral_code joins loosely. |
| `support_csat_response` | event | append_only | low | Customer satisfaction rating after resolution (one per ticket). |
| `support_sla_policy` | config | soft | low | Response/resolution targets per priority (and optionally category), Zendesk-style SLA policy. |
| `support_ticket` | entity | soft | medium | Customer support ticket (Zendesk-like). Conversation in support_ticket_message, transitions in ticket_status_history. |
| `support_ticket_attachment` | entity | hard | medium | File attached to a ticket or one of its messages. |
| `support_ticket_message` | event | append_only | medium | Public replies and internal notes of a ticket; corrections are new messages. |
| `support_ticket_status_history` | audit | append_only | medium | State transition log of a ticket (basis for SLA and reopen metrics). |
| `unsubscribe_token` | entity | hard | high | One-click unsubscribe tokens embedded in email/SMS footers (RFC 8058 List-Unsubscribe-Post). Token hash only; opaque to the DB. |
| `alert_frequency` | lookup | hard | low | How often a job alert or digest is produced. |
| `alert_run_status` | lookup | hard | low | Outcome of one scheduled job-alert matching run. |
| `announcement_placement` | lookup | hard | low | Where an announcement is displayed. |
| `audience_type` | lookup | hard | low | Audience targeted by an announcement, FAQ entry or survey. |
| `community_group_role` | lookup | hard | low | Role of a member inside a community group. |
| `contact_visibility` | lookup | hard | low | Who may see a candidate's contact details. |
| `delivery_status` | lookup | hard | low | Status of one delivery attempt or provider receipt. precedence resolves out-of-order callbacks (higher wins among non-terminal). |
| `digest_batch_status` | lookup | hard | low | Build/send state of a digest batch. |
| `feed_item_source` | lookup | hard | low | Why an item appears in a user's feed. |
| `group_join_policy` | lookup | hard | low | How new members join a community group. |
| `group_membership_status` | lookup | hard | low | State of a group membership. |
| `message_permission` | lookup | hard | low | Who may start a direct message with the user. |
| `message_thread_type` | lookup | hard | low | Kind of direct-messaging thread. |
| `moderation_state` | lookup | hard | low | Moderation state of user-generated content. |
| `notification_category` | lookup | hard | low | Top-level grouping of notification types, used for preference screens and unsubscribe groups. |
| `notification_priority` | lookup | hard | low | Dispatch priority of a notification; drives queue selection and quiet-hours override. |
| `post_type` | lookup | hard | low | Kind of community post. |
| `push_platform` | lookup | hard | low | Push delivery platform of a registered push subscription. |
| `reaction_type` | lookup | hard | low | Reaction a user can leave on a community post. |
| `referral_event_type` | lookup | hard | low | Stage of a referral funnel emitted as an event. |
| `report_target_type` | lookup | hard | low | Kind of object a report or moderation case points at. |
| `share_object_type` | lookup | hard | low | Kind of platform object that was shared. |
| `support_ticket_category` | lookup | hard | low | Support issue category (first level of the help taxonomy). |
| `suppression_reason` | lookup | hard | low | Why an address is on the suppression list. |
| `survey_question_type` | lookup | hard | low | Answer format of an in-app survey question. |
| `ticket_channel` | lookup | hard | low | Channel through which a support ticket was raised. |
| `ticket_priority` | lookup | hard | low | Support ticket priority; selects the SLA policy. |
| `ticket_status` | lookup | hard | low | Lifecycle state of a support ticket. |

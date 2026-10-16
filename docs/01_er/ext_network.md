# ER diagram: Extension: Network, connections and graph

Module key `ext_network`, owner Team, 88 tables (36 lookups). Generated from `05_data_dictionary.xlsx` (do not edit by hand). Columns shown: primary key, foreign keys, unique/natural keys and a few business columns; see the dictionary for all columns. Edges to lookup (enumeration) tables are listed under *Lookup references* rather than drawn.

## Design notes

<!-- ext_network.yaml -->
## ext_network - professional network, creator and events extension (tier X)
Extension beyond the docket's 11 modules; stays inside Section 5 rules (no UI, no live integrations, no ETL).

**Graph model (scale 10M users x 500 = 5B endpoints = 2.5B rows).**
* `connection` stores each undirected edge ONCE: columns `lo_app_user_id < hi_app_user_id` (CHECK `ck_connection_canonical_order`) with `UNIQUE (lo,hi)`.
  Mirrored rows would double storage and make accept/disconnect two-row transactions. Adjacency of user U is
  `SELECT hi FROM connection WHERE lo=U UNION ALL SELECT lo FROM connection WHERE hi=U`, served by two covering indexes
  `(lo, connected_at DESC) STORING (hi)` and `(hi, connected_at DESC) STORING (lo)` (see `vw_connection_adjacency`). Four index entries per edge
  (PK, unique, two adjacency) is the price of both-direction reads without duplicate rows; roughly 100 B row + 3 x ~60 B index entries, i.e. about 0.7 TB for 2.5B edges before replication.
* Every index key leads with a random user UUID, so writes spread over the keyspace; no global time-ordered index exists on `connection`, hence `no_hash_why`.
  Time-ordered scans (expiry, retention) live on tables where a hash-sharded index on the time column is declared (bucket_count 16-32, CRDB `USING HASH`).
* `connection_request` keeps one open request per unordered pair through computed STORED `pair_low_key/pair_high_key` plus a partial unique `WHERE is_pending`; this also closes the A->B / B->A race.
  `connection_request_quota` implements the weekly invite cap with a conditional UPDATE under SERIALIZABLE.
* Blocks: `user_block` (administration) is authoritative. Declarative FK cannot express "no edge between blocked users", so the write path checks `user_block` in the same transaction
  (BR-NET-06) and block creation deletes the edge, pending requests and cache rows (connection_event records `removed_by_block`).
* Degree-of-separation: a full 2nd-degree join is 500 x 500 per read; instead `network_second_degree` materialises a CAPPED top-N per ACTIVE member with row-level TTL
  (cap/TTL/activity gate in `network_cache_config`), `mutual_connection_cache` holds pair counts plus <=5 sample ids, `network_graph_stat` holds recomputed counters (no hot counter row).
  All caches are disposable: correctness never depends on them; exact answers come from `connection`.
* PYMK: `pymk_candidate` (+ `pymk_candidate_reason`) are offline-model output with TTL; `pymk_dismissal` is permanent suppression. The model/ranker is outside the DB (integration boundary, D-16).
* Address book: only HMAC-SHA256 hashes (pepper in KMS) of contact emails/phones are stored (`contact_import_entry`, TTL 30 days); matching joins `contact_match_key`, populated only for
  members who allow discoverability. Upload requires a `user_consent` row (FK RESTRICT, BR-NET-09).

**Reuse, no duplication.** Person/company follows = `community_follow`, `organisation_follow`, `candidate_follow` (existing); this module adds only `user_topic_follow`
(hashtag/skill/industry). Endorsement and recommendation RECORDS stay in profile (`candidate_skill_endorsement`, `candidate_recommendation`); here only the request side
(`endorsement_request`, `recommendation_request`). Groups = `community_group`. Profile view audit with credit deduction = `candidate_profile_view`; `profile_visitor_daily` is the member-facing
rollup that honours viewing modes. `referral` keeps referral rewards; `network_invitation` is the join-and-connect invite and links to it optionally. Open-to-work job preferences remain in
`candidate_open_to_work_signal`; `user_signal` is the network-visible frame (hiring, services, mentor). Lookups reused: visibility_level, verification_status, reaction_type,
moderation_state, share_channel, notification_channel, view_source, relationship_type.

**Creator and events.** `newsletter`/`newsletter_issue`/`newsletter_subscription`; events: `event`, `event_session`, `event_speaker`, `event_ticket_type` (free, invite-only, paid),
`event_rsvp`, `event_ticket`, `event_attendance`, `event_reminder`, `event_invite`; optional audio rooms `live_room`, `live_room_participant`. Paid tickets settle through the existing
`payment_order` (no payment logic here). Event capacity is enforced under `SELECT ... FOR UPDATE` on the event row (BR-NET-17), not by a counter column.
**Company page and alumni.** `organisation_update` (draft/schedule/approve workflow; published copy linked to `community_post`) + `organisation_update_reaction`;
`institution_alumni_page`, `alumni_membership` (optionally backed by `candidate_education`).
**Trust and discovery.** `badge_type` lookup + `user_badge`; `user_signal`; `profile_share_link/visit` (QR); `public_profile_slug` (current + redirect), `public_profile_slug_history`,
`reserved_slug`; `public_profile_setting`; `sitemap_entry`.

**Deviations / caveats.** (1) Row-level TTL uses `ttl_expiration_expression`-style semantics on `expires_at` with a 1-hour/1-day grace in `ttl.expire_after`; the TTL job cron must be set >= gc.ttlseconds
(crdb_capabilities row 12). (2) `LEAST/GREATEST` on UUID in computed columns and `UUID::STRING` casts must be smoke-tested on the target image. (3) Hash-sharded + partial combos are used only where listed
in the lint-clean DDL. (4) No claim is made about exact LinkedIn internals; coverage mirrors public product features (invitations, PYMK, tags/notes, newsletters, events, audio events, verification badges, open-to-work frame, public profile URL and visibility settings, who-viewed-your-profile).
**Extension points.** Graph export to a dedicated graph store via changefeed on `connection`; job-title/company affinity signals feed `pymk_signal_type`; paid event ticketing refunds via existing `refund`.

Language of user-entered text (finding F-06): free-text tables of this module carry a nullable `language_id` (FK `language`), NULL = unknown.

## Diagram

```mermaid
erDiagram
  alumni_membership {
    UUID id PK
    UUID institution_id FK
    UUID alumnus_app_user_id FK
    UUID candidate_education_id FK
    UUID alumni_link_status_id FK
    UUID qualification_id FK
    INT2 graduation_year
    TIMESTAMPTZ verified_at
    UUID verified_by_app_user_id FK
  }
  connection {
    UUID id PK
    UUID lo_app_user_id FK
    UUID hi_app_user_id FK
    UUID connection_origin_id FK
    UUID initiator_app_user_id FK
    UUID connection_request_id FK
    TIMESTAMPTZ connected_at
  }
  connection_event {
    UUID id PK
    UUID lo_app_user_id FK
    UUID hi_app_user_id FK
    UUID connection_event_type_id FK
    UUID actor_app_user_id FK
    TIMESTAMPTZ occurred_at
  }
  connection_note {
    UUID id PK
    UUID owner_app_user_id FK
    UUID subject_app_user_id FK
    STRING_2000 note_text
    DATE met_on_date
    UUID met_event_id FK
    TIMESTAMPTZ remind_at
  }
  connection_request {
    UUID id PK
    UUID requester_app_user_id FK
    UUID recipient_app_user_id FK
    UUID connection_request_status_id FK
    UUID connection_origin_id FK
    STRING_300 note_text
    BOOL is_pending
    UUID pair_low_key
  }
  connection_request_quota {
    UUID id PK
    UUID app_user_id FK
    DATE week_start_date UK
    INT4 sent_count
    INT4 limit_count
  }
  connection_tag {
    UUID id PK
    UUID owner_app_user_id FK
    STRING_60 tag_label
    STRING_60 label_key UK
    STRING_16 colour_code
  }
  connection_tag_assignment {
    UUID id PK
    UUID connection_tag_id FK
    UUID subject_app_user_id FK
    TIMESTAMPTZ assigned_at
  }
  contact_import {
    UUID id PK
    UUID app_user_id FK
    UUID contact_import_source_id FK
    UUID contact_import_status_id FK
    UUID user_consent_id FK
    STRING_64 file_digest
    INT4 total_contacts
    INT4 matched_contacts
  }
  contact_import_entry {
    UUID id PK
    UUID contact_import_id FK
    BYTES email_hash
    BYTES phone_hash
    UUID matched_app_user_id FK
    BOOL is_invited
  }
  contact_match_key {
    UUID id PK
    UUID app_user_id FK
    UUID contact_match_kind_id FK
    BYTES match_hash UK
    TIMESTAMPTZ verified_at
  }
  endorsement_request {
    UUID id PK
    UUID requester_app_user_id FK
    UUID requested_app_user_id FK
    UUID candidate_skill_id FK
    UUID assist_request_status_id FK
    STRING_500 message_text
    UUID candidate_skill_endorsement_id FK
    BOOL is_pending
    TIMESTAMPTZ requested_at
  }
  event {
    UUID id PK
    UUID organiser_app_user_id FK
    UUID organiser_organisation_id FK
    UUID community_group_id FK
    UUID event_type_id FK
    UUID event_format_id FK
    UUID event_status_id FK
    UUID visibility_level_id FK
    UUID language_id FK
    STRING_100 slug
    STRING_200 title
    STRING description_text
    UUID city_id FK
    UUID country_id FK
  }
  event_attendance {
    UUID id PK
    UUID event_id FK
    UUID event_session_id FK
    UUID attendee_app_user_id FK
    UUID event_ticket_id FK
    UUID event_attendance_source_id FK
    TIMESTAMPTZ checked_in_at
    INT4 attended_seconds
  }
  event_invite {
    UUID id PK
    UUID event_id FK
    UUID inviter_app_user_id FK
    UUID invitee_app_user_id FK
    TIMESTAMPTZ invited_at
    TIMESTAMPTZ responded_at
  }
  event_reminder {
    UUID id PK
    UUID event_id FK
    UUID attendee_app_user_id FK
    UUID notification_channel_id FK
    UUID notification_id FK
    TIMESTAMPTZ remind_at UK
    TIMESTAMPTZ sent_at
    TIMESTAMPTZ expires_at
  }
  event_rsvp {
    UUID id PK
    UUID event_id FK
    UUID attendee_app_user_id FK
    UUID event_rsvp_status_id FK
    UUID event_ticket_type_id FK
    UUID connection_origin_id FK
    INT4 guest_count
    TIMESTAMPTZ responded_at
  }
  event_session {
    UUID id PK
    UUID event_id FK
    INT4 session_no UK
    STRING_200 title
    STRING_80 room_label
    TIMESTAMPTZ starts_at
  }
  event_speaker {
    UUID id PK
    UUID event_id FK
    UUID event_session_id FK
    UUID speaker_app_user_id FK
    UUID event_speaker_role_id FK
    STRING_150 display_name
    STRING_200 headline
    INT4 sort_order
  }
  event_ticket {
    UUID id PK
    UUID event_rsvp_id FK
    UUID event_ticket_type_id FK
    UUID payment_order_id FK
    STRING_32 ticket_code UK
    TIMESTAMPTZ issued_at
    TIMESTAMPTZ voided_at
  }
  event_ticket_type {
    UUID id PK
    UUID event_id FK
    UUID event_ticket_kind_id FK
    STRING_80 ticket_name UK
    DECIMAL_12_2 price_amount
    UUID currency_id FK
    INT4 quantity_total
    TIMESTAMPTZ sales_start_at
  }
  institution_alumni_page {
    UUID id PK
    UUID institution_id FK
    STRING_200 tagline
    UUID admin_app_user_id FK
    BOOL requires_verification
    BOOL is_active
  }
  invite_link {
    UUID id PK
    UUID app_user_id FK
    STRING_24 link_code UK
    STRING_40 campaign_code
    INT4 max_uses
    BOOL is_active
  }
  invite_link_redemption {
    UUID id PK
    UUID invite_link_id FK
    UUID redeemed_by_app_user_id FK
    INET visitor_ip
    TIMESTAMPTZ redeemed_at
  }
  live_room {
    UUID id PK
    UUID host_app_user_id FK
    UUID event_id FK
    UUID live_room_status_id FK
    STRING_200 title
    TIMESTAMPTZ scheduled_start_at
    TIMESTAMPTZ scheduled_end_at
  }
  live_room_participant {
    UUID id PK
    UUID live_room_id FK
    UUID participant_app_user_id FK
    UUID live_room_role_id FK
    TIMESTAMPTZ joined_at
    TIMESTAMPTZ left_at
  }
  mutual_connection_cache {
    UUID id PK
    UUID lo_app_user_id FK
    UUID hi_app_user_id FK
    INT4 mutual_count
    JSONB sample_ids
    TIMESTAMPTZ computed_at
  }
  network_cache_config {
    UUID id PK
    UUID cache_kind_id FK
    INT4 ttl_hours
    INT4 max_rows_per_user
    INT4 min_active_days
  }
  network_graph_stat {
    UUID id PK
    UUID app_user_id FK
    INT4 connection_count
    INT4 pending_sent_count
    INT4 pending_received_count
  }
  network_invitation {
    UUID id PK
    UUID inviter_app_user_id FK
    UUID network_invitation_channel_id FK
    UUID network_invitation_status_id FK
    STRING_254 invitee_email
    STRING_16 invitee_phone
    UUID invitee_phone_country_id FK
    UUID invite_link_id FK
    UUID referral_id FK
    UUID joined_app_user_id FK
    BOOL is_open
  }
  network_second_degree {
    UUID id PK
    UUID app_user_id FK
    UUID reach_app_user_id FK
    UUID via_app_user_id FK
    INT2 degree_level
    INT4 mutual_count
    TIMESTAMPTZ computed_at
  }
  newsletter {
    UUID id PK
    UUID owner_app_user_id FK
    UUID owner_organisation_id FK
    UUID newsletter_status_id FK
    UUID newsletter_frequency_id FK
    UUID language_id FK
    UUID industry_id FK
    STRING_80 slug
    STRING_120 title
    STRING_1000 description_text
  }
  newsletter_issue {
    UUID id PK
    UUID newsletter_id FK
    INT4 issue_no
    UUID newsletter_issue_status_id FK
    STRING_200 title
    STRING body_text
    UUID community_post_id FK
    UUID language_id FK
  }
  newsletter_subscription {
    UUID id PK
    UUID newsletter_id FK
    UUID subscriber_app_user_id FK
    UUID notification_channel_id FK
    BOOL is_muted
    TIMESTAMPTZ subscribed_at
  }
  organisation_update {
    UUID id PK
    UUID organisation_id FK
    UUID author_app_user_id FK
    UUID approver_app_user_id FK
    UUID organisation_update_type_id FK
    UUID visibility_level_id FK
    UUID moderation_state_id FK
    UUID language_id FK
    UUID community_post_id FK
    STRING_200 title
    STRING body_text
    STRING_500 media_url
  }
  organisation_update_reaction {
    UUID id PK
    UUID organisation_update_id FK
    UUID reactor_app_user_id FK
    UUID reaction_type_id FK
    TIMESTAMPTZ reacted_at
  }
  profile_share_link {
    UUID id PK
    UUID app_user_id FK
    UUID share_link_purpose_id FK
    STRING_16 short_code UK
    UUID event_id FK
    BOOL is_active
    TIMESTAMPTZ expires_at
  }
  profile_share_visit {
    UUID id PK
    UUID profile_share_link_id FK
    UUID share_channel_id FK
    UUID visitor_app_user_id FK
    UUID country_id FK
    TIMESTAMPTZ visited_at
  }
  profile_visitor_daily {
    UUID id PK
    UUID owner_app_user_id FK
    UUID visitor_app_user_id FK
    UUID profile_visit_mode_id FK
    UUID view_source_id FK
    UUID visitor_organisation_id FK
    UUID visitor_job_role_id FK
    DATE visit_date
    INT4 visit_count
    TIMESTAMPTZ last_visited_at
  }
  profile_visitor_insight_weekly {
    UUID id PK
    UUID owner_app_user_id FK
    DATE week_start_date UK
    INT4 total_views
    INT4 unique_visitors
    INT4 search_appearances
  }
  public_profile_setting {
    UUID id PK
    UUID app_user_id FK
    BOOL is_public_page_enabled
    BOOL show_photo
    BOOL show_headline
  }
  public_profile_slug {
    UUID id PK
    UUID app_user_id FK
    STRING_100 slug UK
    UUID slug_status_id FK
    BOOL is_current
    TIMESTAMPTZ claimed_at
    TIMESTAMPTZ redirect_expires_at
  }
  public_profile_slug_history {
    UUID id PK
    UUID app_user_id FK
    STRING_100 slug
    UUID slug_status_id FK
    TIMESTAMPTZ changed_at
  }
  pymk_candidate {
    UUID id PK
    UUID app_user_id FK
    UUID suggested_app_user_id FK
    DECIMAL_7_4 score
    INT4 mutual_count
    UUID primary_pymk_signal_type_id FK
    STRING_32 model_version
  }
  pymk_candidate_reason {
    UUID id PK
    UUID pymk_candidate_id FK
    UUID pymk_signal_type_id FK
    DECIMAL_6_4 weight
    UUID shared_organisation_id FK
    UUID shared_institution_id FK
    UUID shared_community_group_id FK
    TIMESTAMPTZ expires_at
  }
  pymk_dismissal {
    UUID id PK
    UUID app_user_id FK
    UUID dismissed_app_user_id FK
    UUID pymk_dismiss_reason_id FK
    TIMESTAMPTZ dismissed_at
  }
  recommendation_request {
    UUID id PK
    UUID requester_app_user_id FK
    UUID requested_app_user_id FK
    UUID relationship_type_id FK
    UUID candidate_experience_id FK
    UUID assist_request_status_id FK
    STRING_1000 message_text
    UUID candidate_recommendation_id FK
    BOOL is_pending
    TIMESTAMPTZ requested_at
  }
  reserved_slug {
    UUID id PK
    STRING_100 slug UK
    UUID reserved_slug_reason_id FK
    UUID organisation_id FK
  }
  sitemap_entry {
    UUID id PK
    UUID sitemap_entity_kind_id FK
    UUID sitemap_change_frequency_id FK
    UUID app_user_id FK
    UUID organisation_id FK
    UUID newsletter_id FK
    UUID event_id FK
    STRING_300 url_path UK
    DECIMAL_2_1 priority
    INT4 shard_no
    TIMESTAMPTZ last_modified_at
  }
  user_badge {
    UUID id PK
    UUID app_user_id FK
    UUID badge_type_id FK
    UUID badge_issuer_kind_id FK
    UUID issuer_organisation_id FK
    UUID issuer_institution_id FK
    UUID verification_status_id FK
    STRING_80 issuer_key
    STRING_120 evidence_ref
    BOOL is_displayed
  }
  user_signal {
    UUID id PK
    UUID app_user_id FK
    UUID network_signal_type_id FK
    UUID visibility_level_id FK
    UUID candidate_open_to_work_signal_id FK
    STRING_120 headline_text
    UUID job_role_id FK
    UUID city_id FK
    UUID job_requirement_id FK
    BOOL show_frame
    TIMESTAMPTZ starts_at
  }
  user_topic_follow {
    UUID id PK
    UUID follower_app_user_id FK
    UUID topic_kind_id FK
    UUID hashtag_id FK
    UUID skill_id FK
    UUID industry_id FK
    TIMESTAMPTZ followed_at
  }
  app_user {
    UUID id PK
  }
  candidate_education {
    UUID id PK
  }
  candidate_experience {
    UUID id PK
  }
  candidate_open_to_work_signal {
    UUID id PK
  }
  candidate_recommendation {
    UUID id PK
  }
  candidate_skill {
    UUID id PK
  }
  candidate_skill_endorsement {
    UUID id PK
  }
  city {
    UUID id PK
  }
  community_group {
    UUID id PK
  }
  community_post {
    UUID id PK
  }
  country {
    UUID id PK
  }
  currency {
    UUID id PK
  }
  hashtag {
    UUID id PK
  }
  industry {
    UUID id PK
  }
  institution {
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
  notification {
    UUID id PK
  }
  organisation {
    UUID id PK
  }
  payment_order {
    UUID id PK
  }
  qualification {
    UUID id PK
  }
  referral {
    UUID id PK
  }
  skill {
    UUID id PK
  }
  user_consent {
    UUID id PK
  }
  app_user |o--o{ alumni_membership : "verified_by_app_user_id"
  app_user |o--o{ connection_event : "actor_app_user_id"
  app_user |o--o{ contact_import_entry : "matched_app_user_id"
  app_user |o--o{ event_speaker : "speaker_app_user_id"
  app_user |o--o{ invite_link_redemption : "redeemed_by_app_user_id"
  app_user |o--o{ network_invitation : "joined_app_user_id"
  app_user |o--o{ newsletter : "owner_app_user_id"
  app_user |o--o{ organisation_update : "approver_app_user_id"
  app_user |o--o{ profile_share_visit : "visitor_app_user_id"
  app_user |o--o{ profile_visitor_daily : "visitor_app_user_id"
  app_user |o--o{ public_profile_slug_history : "app_user_id"
  app_user |o--o{ sitemap_entry : "app_user_id"
  app_user ||--o{ alumni_membership : "alumnus_app_user_id"
  app_user ||--o{ connection : "hi_app_user_id"
  app_user ||--o{ connection : "initiator_app_user_id"
  app_user ||--o{ connection : "lo_app_user_id"
  app_user ||--o{ connection_event : "hi_app_user_id"
  app_user ||--o{ connection_event : "lo_app_user_id"
  app_user ||--o{ connection_note : "owner_app_user_id"
  app_user ||--o{ connection_note : "subject_app_user_id"
  app_user ||--o{ connection_request : "recipient_app_user_id"
  app_user ||--o{ connection_request : "requester_app_user_id"
  app_user ||--o{ connection_request_quota : "app_user_id"
  app_user ||--o{ connection_tag : "owner_app_user_id"
  app_user ||--o{ connection_tag_assignment : "subject_app_user_id"
  app_user ||--o{ contact_import : "app_user_id"
  app_user ||--o{ contact_match_key : "app_user_id"
  app_user ||--o{ endorsement_request : "requested_app_user_id"
  app_user ||--o{ endorsement_request : "requester_app_user_id"
  app_user ||--o{ event : "organiser_app_user_id"
  app_user ||--o{ event_attendance : "attendee_app_user_id"
  app_user ||--o{ event_invite : "invitee_app_user_id"
  app_user ||--o{ event_invite : "inviter_app_user_id"
  app_user ||--o{ event_reminder : "attendee_app_user_id"
  app_user ||--o{ event_rsvp : "attendee_app_user_id"
  app_user ||--o{ institution_alumni_page : "admin_app_user_id"
  app_user ||--o{ invite_link : "app_user_id"
  app_user ||--o{ live_room : "host_app_user_id"
  app_user ||--o{ live_room_participant : "participant_app_user_id"
  app_user ||--o{ mutual_connection_cache : "hi_app_user_id"
  app_user ||--o{ mutual_connection_cache : "lo_app_user_id"
  app_user ||--o{ network_invitation : "inviter_app_user_id"
  app_user ||--o{ network_second_degree : "app_user_id"
  app_user ||--o{ network_second_degree : "reach_app_user_id"
  app_user ||--o{ network_second_degree : "via_app_user_id"
  app_user ||--o{ newsletter_subscription : "subscriber_app_user_id"
  app_user ||--o{ organisation_update : "author_app_user_id"
  app_user ||--o{ organisation_update_reaction : "reactor_app_user_id"
  app_user ||--o{ profile_share_link : "app_user_id"
  app_user ||--o{ profile_visitor_daily : "owner_app_user_id"
  app_user ||--o{ profile_visitor_insight_weekly : "owner_app_user_id"
  app_user ||--o{ public_profile_slug : "app_user_id"
  app_user ||--o{ pymk_candidate : "app_user_id"
  app_user ||--o{ pymk_candidate : "suggested_app_user_id"
  app_user ||--o{ pymk_dismissal : "app_user_id"
  app_user ||--o{ pymk_dismissal : "dismissed_app_user_id"
  app_user ||--o{ recommendation_request : "requested_app_user_id"
  app_user ||--o{ recommendation_request : "requester_app_user_id"
  app_user ||--o{ user_badge : "app_user_id"
  app_user ||--o{ user_signal : "app_user_id"
  app_user ||--o{ user_topic_follow : "follower_app_user_id"
  app_user ||--o| network_graph_stat : "app_user_id"
  app_user ||--o| public_profile_setting : "app_user_id"
  candidate_education |o--o{ alumni_membership : "candidate_education_id"
  candidate_experience |o--o{ recommendation_request : "candidate_experience_id"
  candidate_open_to_work_signal |o--o{ user_signal : "candidate_open_to_work_signal_id"
  candidate_recommendation |o--o{ recommendation_request : "candidate_recommendation_id"
  candidate_skill ||--o{ endorsement_request : "candidate_skill_id"
  candidate_skill_endorsement |o--o{ endorsement_request : "candidate_skill_endorsement_id"
  city |o--o{ event : "city_id"
  city |o--o{ user_signal : "city_id"
  community_group |o--o{ event : "community_group_id"
  community_group |o--o{ pymk_candidate_reason : "shared_community_group_id"
  community_post |o--o{ newsletter_issue : "community_post_id"
  community_post |o--o{ organisation_update : "community_post_id"
  connection_request |o--o{ connection : "connection_request_id"
  connection_tag ||--o{ connection_tag_assignment : "connection_tag_id"
  contact_import ||--o{ contact_import_entry : "contact_import_id"
  country |o--o{ event : "country_id"
  country |o--o{ network_invitation : "invitee_phone_country_id"
  country |o--o{ profile_share_visit : "country_id"
  currency |o--o{ event_ticket_type : "currency_id"
  event |o--o{ connection_note : "met_event_id"
  event |o--o{ live_room : "event_id"
  event |o--o{ profile_share_link : "event_id"
  event |o--o{ sitemap_entry : "event_id"
  event ||--o{ event_attendance : "event_id"
  event ||--o{ event_invite : "event_id"
  event ||--o{ event_reminder : "event_id"
  event ||--o{ event_rsvp : "event_id"
  event ||--o{ event_session : "event_id"
  event ||--o{ event_speaker : "event_id"
  event ||--o{ event_ticket_type : "event_id"
  event_rsvp ||--o{ event_ticket : "event_rsvp_id"
  event_session |o--o{ event_attendance : "event_session_id"
  event_session |o--o{ event_speaker : "event_session_id"
  event_ticket |o--o{ event_attendance : "event_ticket_id"
  event_ticket_type |o--o{ event_rsvp : "event_ticket_type_id"
  event_ticket_type ||--o{ event_ticket : "event_ticket_type_id"
  hashtag |o--o{ user_topic_follow : "hashtag_id"
  industry |o--o{ newsletter : "industry_id"
  industry |o--o{ user_topic_follow : "industry_id"
  institution |o--o{ pymk_candidate_reason : "shared_institution_id"
  institution |o--o{ user_badge : "issuer_institution_id"
  institution ||--o{ alumni_membership : "institution_id"
  institution ||--o| institution_alumni_page : "institution_id"
  invite_link |o--o{ network_invitation : "invite_link_id"
  invite_link ||--o{ invite_link_redemption : "invite_link_id"
  job_requirement |o--o{ user_signal : "job_requirement_id"
  job_role |o--o{ profile_visitor_daily : "visitor_job_role_id"
  job_role |o--o{ user_signal : "job_role_id"
  language |o--o{ newsletter_issue : "language_id"
  language ||--o{ event : "language_id"
  language ||--o{ newsletter : "language_id"
  language ||--o{ organisation_update : "language_id"
  live_room ||--o{ live_room_participant : "live_room_id"
  newsletter |o--o{ sitemap_entry : "newsletter_id"
  newsletter ||--o{ newsletter_issue : "newsletter_id"
  newsletter ||--o{ newsletter_subscription : "newsletter_id"
  notification |o--o{ event_reminder : "notification_id"
  organisation |o--o{ event : "organiser_organisation_id"
  organisation |o--o{ newsletter : "owner_organisation_id"
  organisation |o--o{ profile_visitor_daily : "visitor_organisation_id"
  organisation |o--o{ pymk_candidate_reason : "shared_organisation_id"
  organisation |o--o{ reserved_slug : "organisation_id"
  organisation |o--o{ sitemap_entry : "organisation_id"
  organisation |o--o{ user_badge : "issuer_organisation_id"
  organisation ||--o{ organisation_update : "organisation_id"
  organisation_update ||--o{ organisation_update_reaction : "organisation_update_id"
  payment_order |o--o{ event_ticket : "payment_order_id"
  profile_share_link ||--o{ profile_share_visit : "profile_share_link_id"
  pymk_candidate ||--o{ pymk_candidate_reason : "pymk_candidate_id"
  qualification |o--o{ alumni_membership : "qualification_id"
  referral |o--o{ network_invitation : "referral_id"
  skill |o--o{ user_topic_follow : "skill_id"
  user_consent ||--o{ contact_import : "user_consent_id"
```

## Lookup tables in this module

| Lookup | Values | Description |
|---|---|---|
| `alumni_link_status` | 4 | Verification state of an alumni claim. |
| `assist_request_status` | 6 | State of a request asking someone to endorse or recommend. |
| `badge_issuer_kind` | 4 | Who awards a badge. |
| `badge_type` | 11 | Professional and verification badges (LinkedIn verification badges, Naukri verified labels). |
| `cache_kind` | 4 | Kinds of graph cache with their own TTL and cap (network_cache_config). |
| `connection_event_type` | 5 | Edge lifecycle event kinds written to connection_event. |
| `connection_origin` | 12 | How two people came to be connected or how a request was initiated. |
| `connection_request_status` | 6 | Lifecycle of a connection (invitation) request. |
| `contact_import_source` | 5 | Origin of an uploaded address book. |
| `contact_import_status` | 5 | Processing state of an address-book upload. |
| `contact_match_kind` | 2 | Kind of identifier a contact_match_key hashes. |
| `event_attendance_source` | 4 | How attendance was captured. |
| `event_format` | 3 | Delivery format of an event. |
| `event_rsvp_status` | 5 | Response of an invitee to an event. |
| `event_speaker_role` | 6 | Role of a person in an event programme. |
| `event_status` | 6 | Lifecycle of an event. |
| `event_ticket_kind` | 5 | Ticket categories; free and invite-only need no payment, paid ones reference payment_order. |
| `event_type` | 9 | Kind of event. |
| `live_room_role` | 3 | Participant role in a live room. |
| `live_room_status` | 4 | State of a live/audio room. |
| `network_invitation_channel` | 5 | Channel through which a non-member is invited to join and connect. |
| `network_invitation_status` | 6 | State of an invitation to join the network. |
| `network_signal_type` | 6 | Profile frame signals (open to work, hiring, ...). |
| `newsletter_frequency` | 5 | Publishing cadence declared by a newsletter author. |
| `newsletter_issue_status` | 4 | State of one newsletter issue. |
| `newsletter_status` | 5 | Publication state of a newsletter. |
| `organisation_update_type` | 7 | Kind of company-page update. |
| `profile_visit_mode` | 4 | How a visit is shown to the profile owner (LinkedIn viewing modes). |
| `pymk_dismiss_reason` | 4 | Why a suggested person was dismissed. |
| `pymk_signal_type` | 9 | Signals that justify a People-you-may-know suggestion. |
| `reserved_slug_reason` | 6 | Why a slug cannot be claimed. |
| `share_link_purpose` | 4 | Purpose of a shareable profile link or QR code. |
| `sitemap_change_frequency` | 7 | Sitemap changefreq hint. |
| `sitemap_entity_kind` | 4 | Entity families published to search-engine sitemaps. |
| `slug_status` | 3 | State of a vanity-URL slug. |
| `topic_kind` | 3 | Kind of topic a person can follow. |

## Lookup references (enumeration FKs)

| Table | Lookup tables referenced |
|---|---|
| `alumni_membership` | `alumni_link_status` |
| `connection` | `connection_origin` |
| `connection_event` | `connection_event_type` |
| `connection_request` | `connection_origin`, `connection_request_status` |
| `contact_import` | `contact_import_source`, `contact_import_status` |
| `contact_match_key` | `contact_match_kind` |
| `endorsement_request` | `assist_request_status` |
| `event` | `event_format`, `event_status`, `event_type`, `visibility_level` |
| `event_attendance` | `event_attendance_source` |
| `event_reminder` | `notification_channel` |
| `event_rsvp` | `connection_origin`, `event_rsvp_status` |
| `event_speaker` | `event_speaker_role` |
| `event_ticket_type` | `event_ticket_kind` |
| `live_room` | `live_room_status` |
| `live_room_participant` | `live_room_role` |
| `network_cache_config` | `cache_kind` |
| `network_invitation` | `network_invitation_channel`, `network_invitation_status` |
| `newsletter` | `newsletter_frequency`, `newsletter_status` |
| `newsletter_issue` | `newsletter_issue_status` |
| `newsletter_subscription` | `notification_channel` |
| `organisation_update` | `moderation_state`, `organisation_update_type`, `visibility_level` |
| `organisation_update_reaction` | `reaction_type` |
| `profile_share_link` | `share_link_purpose` |
| `profile_share_visit` | `share_channel` |
| `profile_visitor_daily` | `profile_visit_mode`, `view_source` |
| `public_profile_slug` | `slug_status` |
| `public_profile_slug_history` | `slug_status` |
| `pymk_candidate` | `pymk_signal_type` |
| `pymk_candidate_reason` | `pymk_signal_type` |
| `pymk_dismissal` | `pymk_dismiss_reason` |
| `recommendation_request` | `assist_request_status`, `relationship_type` |
| `reserved_slug` | `reserved_slug_reason` |
| `sitemap_entry` | `sitemap_change_frequency`, `sitemap_entity_kind` |
| `user_badge` | `badge_issuer_kind`, `badge_type`, `verification_status` |
| `user_signal` | `network_signal_type`, `visibility_level` |
| `user_topic_follow` | `topic_kind` |

## Tables

| Table | Kind | Delete | Volume | Description |
|---|---|---|---|---|
| `alumni_membership` | link | hard | high | A member's claimed/verified alumni relationship to an institution, optionally tied to a profile education row. |
| `connection` | link | hard | very_high | Undirected first-degree edge stored ONCE with canonical ordering (lo < hi). Adjacency of user U = rows with lo=U union rows with hi=U, served by two covering indexes, never by mirrored rows. Scale target 10M users x 500 = 2.5B rows. |
| `connection_event` | event | append_only | very_high | Append-only history of edge creation and removal so the live edge table can stay hard-delete and small. |
| `connection_note` | entity | hard | very_high | Private note a member keeps about a connection (LinkedIn relationship notes); never visible to the other person. |
| `connection_request` | entity | hard | very_high | Directed invitation to connect (LinkedIn invitation with a note of up to 300 characters). At most one open request per unordered pair; cooldown after decline/ignore. |
| `connection_request_quota` | entity | hard | high | Weekly invitation allowance per member (LinkedIn caps weekly invites); enforced by a conditional UPDATE inside the send transaction. |
| `connection_tag` | entity | hard | high | Owner-defined label for organising connections (LinkedIn tags such as 'Recruiter', 'Client'). |
| `connection_tag_assignment` | link | hard | very_high | Tag applied to a connection by its owner. |
| `contact_import` | entity | hard | high | One address-book upload batch (Google/Outlook/CSV). Raw hashed entries are short-lived; the batch row keeps counts for funnel analysis. |
| `contact_import_entry` | staging | hard | very_high | One uploaded contact reduced to identifier hashes (no names, no plaintext). |
| `contact_match_key` | entity | hard | very_high | HMAC of a member's verified email or phone so uploaded address-book hashes can be matched without storing plaintext contacts. Present only for members who allow discovery. |
| `endorsement_request` | entity | hard | high | Request side of a skill endorsement ('Ask for endorsements'). The endorsement itself is profile.candidate_skill_endorsement. |
| `event` | entity | soft | high | Professional event (webinar, meetup, hiring drive, career fair) hosted by a member, company page or group. |
| `event_attendance` | event | append_only | very_high | Check-in/join record for an event or session. |
| `event_invite` | link | hard | high | Member invites a connection to an event. |
| `event_reminder` | entity | hard | very_high | Scheduled reminder for an RSVP; dispatch creates a notification. |
| `event_rsvp` | link | hard | very_high | A member's response to an event (going/maybe/waitlisted). |
| `event_session` | entity | hard | high | Programme item (talk, panel, break) within an event. |
| `event_speaker` | link | hard | high | Speaker/host on an event or session; may be a member or an external guest. |
| `event_ticket` | entity | hard | high | Issued ticket (free or paid) bound to an RSVP; paid tickets point to the payment order. |
| `event_ticket_type` | entity | hard | medium | Ticket category of an event; free tiers need no payment, paid tiers settle through payment_order. |
| `institution_alumni_page` | entity | hard | medium | Alumni network landing configuration of an institution (LinkedIn Alumni tool). |
| `invite_link` | entity | hard | medium | Shareable referral/invite link of a member (also feeds QR codes). |
| `invite_link_redemption` | event | append_only | high | Each visit/sign-up through an invite link. |
| `live_room` | entity | hard | medium | Optional live/audio room (LinkedIn Audio Events style), standalone or attached to an event. |
| `live_room_participant` | event | append_only | high | Join/leave record of a room participant. |
| `mutual_connection_cache` | snapshot | hard | very_high | Cached mutual connection count and a small sample for a canonical pair (shown on profiles and invitations). |
| `network_cache_config` | config | hard | low | TTL, cap and activity gate per graph cache kind; changing a value takes effect at the next refresh. |
| `network_graph_stat` | snapshot | hard | high | Per-member counters (connections, pending, followers) recomputed periodically; avoids a hot counter row updated on every edge write. |
| `network_invitation` | entity | hard | high | Invitation to a non-member to join and connect (email/SMS/WhatsApp). Distinct from profile.referral which owns referral rewards; optional link via referral_id. |
| `network_second_degree` | snapshot | hard | very_high | Materialised capped second-degree reach per ACTIVE member (top-N by mutual count) with TTL; avoids 500x500 fan-out joins at read time. |
| `newsletter` | entity | soft | medium | Creator newsletter (LinkedIn newsletters) owned by a member or by a company page. |
| `newsletter_issue` | entity | soft | high | One edition of a newsletter; optionally also published as a community article. |
| `newsletter_subscription` | link | hard | very_high | Member subscribed to a newsletter (subscribe = follow with delivery preference). |
| `organisation_update` | entity | soft | high | Company-page update drafted/scheduled/approved by page admins (LinkedIn page posts). Published form is mirrored into community_post for the feed via community_post_id. |
| `organisation_update_reaction` | link | hard | very_high | Reaction of a member to a company-page update. |
| `profile_share_link` | entity | hard | high | Shareable profile link or QR code (profile QR, digital business card, event badge). |
| `profile_share_visit` | event | append_only | very_high | Visit of a shared link/QR (scan counts, referrer, country). |
| `profile_visitor_daily` | snapshot | hard | very_high | Per-day rollup of who visited a member's profile, shown in 'Who viewed your profile'. Distinct from profile.candidate_profile_view (append-only audit of recruiter views with credit deduction); this is the member-facing aggregate honouring viewing modes. |
| `profile_visitor_insight_weekly` | snapshot | hard | high | Weekly analytics shown to the owner: total views, unique viewers, search appearances and top viewer breakdowns (industry, company, title). |
| `public_profile_setting` | config | hard | high | Section-level SEO/public-page controls of a member. Master indexing consent stays in privacy_setting.allow_search_engine_indexing; effective exposure = both (vw_public_profile_indexable). |
| `public_profile_slug` | entity | hard | high | Vanity URL (e.g. /in/jane-doe-4f2a) of a member. Current slug plus old slugs kept as redirects; a slug is globally unique across current and redirect rows. |
| `public_profile_slug_history` | audit | append_only | high | History of slug changes and releases (audit, abuse investigation, anti-squatting limits). |
| `pymk_candidate` | snapshot | hard | very_high | Materialised People-you-may-know candidate edge (viewer -> suggested), refreshed by an offline job and expired by TTL. Directed (scores are asymmetric). |
| `pymk_candidate_reason` | snapshot | hard | very_high | Explanation rows behind a suggestion ('2 mutual connections', 'worked at Infosys'). |
| `pymk_dismissal` | link | hard | high | Permanent suppression of a suggested person for one viewer. |
| `recommendation_request` | entity | hard | high | Request side of a written recommendation; the recommendation text is profile.candidate_recommendation. |
| `reserved_slug` | config | hard | low | Slugs that cannot be claimed (system routes, brands, offensive terms). |
| `sitemap_entry` | entity | hard | very_high | Sitemap hint for one indexable public URL; the sitemap writer pages by shard_no (<=50,000 URLs per file). |
| `user_badge` | entity | hard | very_high | Badge awarded to a member (verification or achievement), with issuer and validity. |
| `user_signal` | entity | hard | high | Network-visible profile frame/signal (Open to work, Hiring, Providing services, Mentor). Complements profile.candidate_open_to_work_signal, which holds job-search preferences; link via that id when the signal is open_to_work. |
| `user_topic_follow` | link | hard | very_high | Follow of a topic (hashtag, skill or industry). Person and company follows live in community_follow / organisation_follow and are NOT duplicated here. |
| `alumni_link_status` | lookup | hard | low | Verification state of an alumni claim. |
| `assist_request_status` | lookup | hard | low | State of a request asking someone to endorse or recommend. |
| `badge_issuer_kind` | lookup | hard | low | Who awards a badge. |
| `badge_type` | lookup | hard | low | Professional and verification badges (LinkedIn verification badges, Naukri verified labels). |
| `cache_kind` | lookup | hard | low | Kinds of graph cache with their own TTL and cap (network_cache_config). |
| `connection_event_type` | lookup | hard | low | Edge lifecycle event kinds written to connection_event. |
| `connection_origin` | lookup | hard | low | How two people came to be connected or how a request was initiated. |
| `connection_request_status` | lookup | hard | low | Lifecycle of a connection (invitation) request. |
| `contact_import_source` | lookup | hard | low | Origin of an uploaded address book. |
| `contact_import_status` | lookup | hard | low | Processing state of an address-book upload. |
| `contact_match_kind` | lookup | hard | low | Kind of identifier a contact_match_key hashes. |
| `event_attendance_source` | lookup | hard | low | How attendance was captured. |
| `event_format` | lookup | hard | low | Delivery format of an event. |
| `event_rsvp_status` | lookup | hard | low | Response of an invitee to an event. |
| `event_speaker_role` | lookup | hard | low | Role of a person in an event programme. |
| `event_status` | lookup | hard | low | Lifecycle of an event. |
| `event_ticket_kind` | lookup | hard | low | Ticket categories; free and invite-only need no payment, paid ones reference payment_order. |
| `event_type` | lookup | hard | low | Kind of event. |
| `live_room_role` | lookup | hard | low | Participant role in a live room. |
| `live_room_status` | lookup | hard | low | State of a live/audio room. |
| `network_invitation_channel` | lookup | hard | low | Channel through which a non-member is invited to join and connect. |
| `network_invitation_status` | lookup | hard | low | State of an invitation to join the network. |
| `network_signal_type` | lookup | hard | low | Profile frame signals (open to work, hiring, ...). |
| `newsletter_frequency` | lookup | hard | low | Publishing cadence declared by a newsletter author. |
| `newsletter_issue_status` | lookup | hard | low | State of one newsletter issue. |
| `newsletter_status` | lookup | hard | low | Publication state of a newsletter. |
| `organisation_update_type` | lookup | hard | low | Kind of company-page update. |
| `profile_visit_mode` | lookup | hard | low | How a visit is shown to the profile owner (LinkedIn viewing modes). |
| `pymk_dismiss_reason` | lookup | hard | low | Why a suggested person was dismissed. |
| `pymk_signal_type` | lookup | hard | low | Signals that justify a People-you-may-know suggestion. |
| `reserved_slug_reason` | lookup | hard | low | Why a slug cannot be claimed. |
| `share_link_purpose` | lookup | hard | low | Purpose of a shareable profile link or QR code. |
| `sitemap_change_frequency` | lookup | hard | low | Sitemap changefreq hint. |
| `sitemap_entity_kind` | lookup | hard | low | Entity families published to search-engine sitemaps. |
| `slug_status` | lookup | hard | low | State of a vanity-URL slug. |
| `topic_kind` | lookup | hard | low | Kind of topic a person can follow. |

"""Size / row-count model for the Talentra data dictionary (used by docs 11 and 12).

Everything is computed from the dictionary (docs/_staging via dictlib.load_model()):
  * row_bytes(t)        : estimated stored bytes of one table row (primary-index entry) from column types.
  * index_bytes(t, ix)  : estimated stored bytes of ONE entry of a secondary index (incl. PK suffix, MVCC, STORING).
  * rows(t)             : laptop / full-scale row counts from ANCHORS (explicit) or the long-tail rule (by volume class).
All figures are LOGICAL bytes of ONE replica, uncompressed. Cluster disk = x3 (replication factor 3). Calibrate with
`python docs/tools/perf_model.py --calibrate <measured_bytes_per_estimated_byte>` after the real seed (SHOW RANGES ... WITH DETAILS).
"""
from __future__ import annotations
import math, os, re, sys

sys.path.insert(0, os.path.dirname(__file__))
from dictlib import load_model  # noqa: E402

# ---- scale drivers -----------------------------------------------------------------------------------------------
LAPTOP = dict(profiles=1_000_000, jobs=100_000, applications=5_000_000)
FULL = dict(profiles=10_000_000, jobs=1_000_000, applications=100_000_000)
# full = laptop x factor by driver
FACTOR = {"P": 10, "J": 10, "A": 20, "N": 10, "G": 10, "F": 1, "O": 10}
# driver legend: P profile-driven, J job-driven, A application-driven, N notification/event stream, G gig, F fixed (reference), O organisation

# ---- ANCHORS: table -> (laptop rows, driver). Everything else uses the long-tail rule (tail_rows) ------------------
ANCHORS: dict[str, tuple[int, str]] = {}


def _a(driver, **kw):
    for k, v in kw.items():
        ANCHORS[k] = (v, driver)


# identity / administration (users = 1M candidates + 0.1M recruiters/employer users/consultants/staff)
_a("P", app_user=1_150_000, user_credential=1_150_000, user_credential_history=1_500_000, password_reset_token=150_000,
   email_verification_token=1_200_000, identity_session=3_000_000, user_consent=3_000_000, audit_event=8_000_000,
   user_block=40_000, duplicate_candidate_match=45_000, error_log=2_000_000, security_event=300_000,
   user_credential_history_dummy=0)
ANCHORS.pop("user_credential_history_dummy")
# profile
_a("P", candidate_profile=1_000_000, candidate_contact_phone=1_100_000, candidate_contact_email=1_300_000, candidate_address=800_000,
   candidate_social_link=1_200_000, candidate_language=1_800_000, candidate_education=1_800_000, candidate_experience=2_500_000,
   candidate_skill=8_000_000, candidate_skill_endorsement=2_000_000, candidate_project=1_000_000, candidate_project_skill=3_000_000,
   candidate_certification=600_000, candidate_follow=1_000_000, candidate_profile_section=5_000_000,
   candidate_preferred_location=2_000_000, candidate_preferred_role=1_500_000, candidate_preferred_industry=1_200_000,
   candidate_preferred_employment_type=1_500_000, candidate_preferred_work_type=1_500_000, candidate_media_file=1_600_000,
   resume=1_400_000, resume_parse_result=1_400_000, profile_import=120_000, candidate_profile_completion_snapshot=2_000_000,
   candidate_profile_view=10_000_000, candidate_activity_log=12_000_000, candidate_shortlist_item=1_500_000,
   candidate_shortlist_history=3_000_000, course_enrolment=500_000, assessment_attempt=1_000_000, candidate_self_assessment=600_000,
   referral=100_000, candidate_identity_verification=300_000, candidate_diversity_declaration=150_000)
# jobs and applications
_a("J", job_requirement=100_000, job_position=150_000, job_requirement_skill=600_000, job_requirement_location=120_000,
   questionnaire_question=50_000, questionnaire_question_option=200_000, job_requirement_stat=100_000, job_syndication=200_000,
   job_share_event=500_000, job_bookmark=2_000_000, job_shortlist_item=1_000_000, talent_pool_member=500_000)
_a("A", job_application=5_000_000, job_application_status_history=20_000_000, application_note=1_000_000, application_scorecard=400_000,
   application_scorecard_score=1_600_000, application_view_event=6_000_000, job_application_document=3_000_000,
   questionnaire_answer=10_000_000, questionnaire_answer_option=12_000_000, job_application_screening_result=5_000_000,
   interview=800_000, interview_interviewer=1_200_000, interview_slot=1_600_000, interview_feedback=1_000_000, offer=200_000,
   offer_revision=260_000, offer_component=1_000_000, rejection=1_500_000, candidate_job_search_history=20_000_000,
   job_view_event=30_000_000)
# sourcing
_a("A", outreach_recipient=4_000_000, outreach_exclusion=2_000_000, outreach_message=4_000_000, outreach_reply=400_000,
   outreach_credit_usage=500_000, candidate_search_history=6_000_000, candidate_contact_attempt=2_000_000, saved_search_run=500_000,
   sourcing_shortlist_item=300_000)
_a("J", sourcing_request=30_000, outreach_batch=20_000)
# subscriptions / payments
_a("O", wallet=25_000, wallet_expiry_bucket=100_000, subscription_period=60_000, invoice=250_000, invoice_line=500_000, invoice_tax_line=500_000,
   payment_order=300_000, payment_attempt=360_000, payment_event=1_000_000, payment_event_outcome=1_000_000, reconciliation_item=300_000)
_a("A", wallet_ledger_entry=6_000_000, wallet_bucket_consumption=4_000_000, profile_download=3_000_000)
# licences / devices
_a("P", device=1_800_000, device_session=3_000_000, login_attempt=8_000_000, session_activity_sample=6_000_000,
   device_trust_challenge=200_000, push_registration=1_000_000, licence_seat=60_000, device_slot=180_000)
_a("O", licence=20_000, licence_seat_slot=60_000)
# notifications / community (10M-notification laptop window; full x10 = 100M-row delivery table)
_a("N", notification=10_000_000, notification_delivery=12_000_000, notification_delivery_event=20_000_000, provider_webhook_event=15_000_000,
   notification_suppression=300_000, unsubscribe_token=1_000_000, notification_preference=3_000_000, quiet_period=300_000,
   push_registration_dummy=0, push_subscription=1_000_000, digest_batch=500_000, digest_batch_item=2_000_000,
   job_alert_preference=500_000, job_alert_skill=1_500_000, job_alert_location=700_000, job_alert_run=8_000_000, job_alert_match=10_000_000,
   language_preference=1_000_000, privacy_setting=1_000_000, feed_item=10_000_000, message_thread=300_000, message_thread_participant=600_000,
   message=3_000_000, message_attachment=300_000, message_read_receipt=2_000_000, referral_event=100_000)
ANCHORS.pop("push_registration_dummy")
_a("P", community_group_member=500_000, community_post=300_000, community_post_stat=300_000, community_post_media=150_000,
   community_comment=900_000, community_reaction=3_000_000, community_share=200_000, community_follow=2_000_000,
   community_poll_vote=200_000, hashtag=20_000, community_post_hashtag=400_000, community_mention=200_000, announcement_dismissal=500_000)
_a("P", support_ticket=100_000, support_ticket_message=300_000)
# organisation
_a("O", organisation=20_000, organisation_hierarchy_closure=50_000, organisation_follow=400_000, organisation_review=60_000,
   organisation_review_vote=300_000, organisation_review_moderation=20_000, organisation_location=40_000, organisation_identifier=25_000,
   organisation_verification=40_000, recruiter_organisation_mapping=60_000, job_requirement_assignment=120_000)
# gig
_a("G", gig_worker_profile=60_000, gig_worker_skill=240_000, gig_request=50_000, gig_request_status_history=150_000, gig_application=300_000,
   gig_assignment=40_000, gig_assignment_status_history=120_000, gig_slot=2_000_000, gig_timesheet_entry=400_000,
   gig_earnings_snapshot=500_000, gig_fulfilment_event=1_000_000, gig_fulfilment_event_attempt=1_500_000, gig_rating=30_000,
   gig_payout_reference=35_000, gig_availability_window=300_000, gig_time_off=40_000)
# master data entities (reference sizes; full = laptop)
_a("F", country=250, state_region=5_000, city=150_000, language=200, currency=170, skill=40_000, qualification=5_000, job_role=15_000,
   industry=600)
# analytics (facts mirror OLTP volume at ~1 row per source event)
_a("A", fact_application_event=20_000_000, fact_application_pipeline=5_000_000, fact_job_posting_daily=3_000_000, fact_profile_view=10_000_000,
   fact_profile_download=3_000_000, fact_search_event=26_000_000, fact_notification_delivery=12_000_000, fact_wallet_transaction=6_000_000,
   fact_payment=300_000, fact_session_activity=3_000_000, fact_outreach_recipient=4_000_000, fact_user_signup=1_150_000,
   fact_candidate_activity_daily=15_000_000, bridge_candidate_skill=8_000_000, fact_gig_assignment=40_000, fact_gig_request_daily=500_000,
   fact_subscription_daily_snapshot=1_500_000)
_a("P", dim_candidate=1_100_000, dim_user=1_150_000)
_a("J", dim_job_requirement=110_000)
_a("F", dim_date=11_000)


# ---- laptop disk budget trims --------------------------------------------------------------------------------------
# A 3-node RF=3 docker cluster on ONE 512 GB SSD stores every range 3x. Budget: <= ~105 GB logical per replica (x3 = ~315 GB),
# leaving ~190 GB for WAL, compaction headroom and a backup (docket: 512 GB free SSD). The docket-fixed anchors
# (1M profiles, 100k jobs, 5M applications) are NOT trimmed; only TTL'd event streams, derived analytics facts and
# child/answer tables are seeded as a sample of the steady-state size (full scale is unchanged: full = laptop x factor
# after trimming is re-multiplied by SAMPLE_UP so the full-scale target stays at the stated steady-state volume).
TRIM = dict(
    provider_webhook_event=3_000_000, job_view_event=10_000_000, candidate_job_search_history=6_000_000, candidate_activity_log=6_000_000,
    candidate_profile_view=5_000_000, notification=5_000_000, notification_delivery=6_000_000, notification_delivery_event=10_000_000,
    job_alert_match=3_000_000, job_alert_run=3_000_000, feed_item=3_000_000, job_application_status_history=15_000_000,
    questionnaire_answer=4_000_000, questionnaire_answer_option=5_000_000, job_application_screening_result=2_500_000,
    audit_event=4_000_000, login_attempt=3_000_000, session_activity_sample=1_500_000, identity_session=2_000_000,
    outreach_message=1_500_000, candidate_search_history=2_000_000, application_view_event=3_000_000,
    fact_search_event=4_000_000, fact_application_event=5_000_000, fact_profile_view=5_000_000, fact_notification_delivery=6_000_000,
    fact_candidate_activity_daily=3_000_000, fact_application_pipeline=5_000_000, bridge_candidate_skill=8_000_000,
    fact_wallet_transaction=3_000_000, fact_outreach_recipient=1_500_000, fact_job_posting_daily=1_000_000,
    wallet_bucket_consumption=2_000_000, outreach_recipient=2_000_000, outreach_exclusion=1_000_000, candidate_skill_endorsement=1_000_000,
    candidate_profile_section=3_000_000, candidate_profile_completion_snapshot=1_000_000, user_consent=2_000_000, error_log=500_000,
    candidate_media_file=1_200_000, email_verification_token=600_000, device=1_300_000, device_session=1_500_000,
    candidate_contact_attempt=1_000_000, job_application_document=2_000_000, message=1_000_000, message_read_receipt=600_000,
    community_reaction=1_000_000, community_follow=1_000_000, notification_preference=2_000_000, unsubscribe_token=300_000,
)
for _k, _v in TRIM.items():
    ANCHORS[_k] = (_v, ANCHORS[_k][1])

# ---- long-tail rule ---------------------------------------------------------------------------------------------
# rows for tables NOT in ANCHORS: by dictionary volume class. Extension modules (ext_*) are seeded at 10% (sparse).
TAIL = {"low": 1_000, "medium": 20_000, "high": 150_000, "very_high": 500_000}
TAIL_ANALYTICS = {"low": 1_000, "medium": 20_000, "high": 100_000, "very_high": 500_000}
EXT_FACTOR = 0.05
TAIL_FULL_FACTOR = 10


def tail_rows(t) -> tuple[int, str]:
    base = (TAIL_ANALYTICS if t["module"] == "analytics" else TAIL)[t["volume"]]
    if t["module"].startswith("ext_"):
        base = max(100, int(base * EXT_FACTOR))
    if t["module"] == "tier3_reference":
        base = {"low": 500, "medium": 20_000}.get(t["volume"], base)
    return base, "T"


FACTOR["T"] = TAIL_FULL_FACTOR

# ---- type widths --------------------------------------------------------------------------------------------------
NULL_FILL = 0.6          # fraction of rows where a nullable column is populated (NULL columns cost 0 bytes in CRDB value encoding)
COL_OVERHEAD = 1.5       # column id delta + type tag per stored column
ROW_KEY = 22             # /Table/<id>/<index>/<uuid pk 17>/<family>
MVCC = 12                # MVCC timestamp suffix
VALUE_HDR = 5            # checksum + tag
INDEX_KEY_PREFIX = 6     # /Table/<id>/<index id>


def _str_len(c) -> float:
    m = re.match(r"STRING\((\d+)\)", c["type"])
    n = int(m.group(1)) if m else None
    nm = c["name"]
    dom = c.get("domain", "")
    if n is None:  # unbounded text
        if "description" in nm or nm == "body_text" or nm == "body":
            return 1500
        return 1200
    if dom == "person_name" or nm.endswith("_name") and n <= 200:
        return min(24, n)
    if dom in ("code", "ui_label_key"):
        return min(14, n)
    if dom == "email":
        return 26
    if dom == "phone":
        return 14
    if dom == "currency_code":
        return 3
    if dom == "url" or nm.endswith("_url"):
        return min(60, 0.5 * n)
    if "hash" in nm or "digest" in nm:
        return min(64, n)
    if "idempotency" in nm or nm.endswith("_ref") or nm.endswith("_key") or "token" in nm:
        return min(40, n)
    if n >= 1000:
        return 0.2 * n
    if n >= 200:
        return min(0.35 * n, 120)
    return min(0.5 * n, 40)


def col_bytes(c) -> float:
    t = c["type"].upper()
    nm = c["name"]
    if t.startswith("UUID"):
        w = 17
    elif t.startswith("TIMESTAMPTZ"):
        w = 9
    elif t.startswith("DATE"):
        w = 5
    elif t.startswith("BOOL"):
        w = 1
    elif t.startswith("INT2"):
        w = 2
    elif t.startswith("INT4"):
        w = 4
    elif t.startswith("INT8"):
        w = 7
    elif t.startswith("DECIMAL"):
        m = re.match(r"DECIMAL\((\d+),(\d+)\)", t)
        w = 5 + (int(m.group(1)) // 4 if m else 4)
    elif t.startswith("STRING"):
        w = _str_len(c) + 1
    elif t.startswith("JSONB"):
        w = 60 if c.get("domain") == "json" and nm == "name_i18n" else (400 if nm in ("payload", "raw_payload", "parsed_json", "before_state", "after_state") else 150)
    elif t.startswith("BYTES"):
        w = 34 if ("hash" in nm or "digest" in nm) else (90 if "encrypted" in nm or "ciphertext" in nm else 64)
    elif t.startswith("INET"):
        w = 9
    elif t.startswith("TIME"):
        w = 6
    elif t.startswith("INTERVAL"):
        w = 9
    else:
        w = 8
    w += COL_OVERHEAD
    if c.get("null"):
        w *= NULL_FILL
    return w


def row_bytes(t) -> float:
    """Primary index entry: key + MVCC + value header + all non-PK column values (single column family)."""
    s = ROW_KEY + MVCC + VALUE_HDR
    for c in t["columns"]:
        if c["name"] == "id":
            continue
        s += col_bytes(c)
    return s


def _cmap(t):
    return {c["name"]: c for c in t["columns"]}


def _key_width(c) -> float:
    """Encoded width of a column as an index key component (key encoding, no column id)."""
    ty = c["type"].upper()
    if ty.startswith("UUID"):
        return 17
    if ty.startswith("TIMESTAMPTZ"):
        return 9
    if ty.startswith("DATE"):
        return 5
    if ty.startswith("BOOL"):
        return 2
    if ty.startswith("INT"):
        return 5
    if ty.startswith("DECIMAL"):
        return 9
    if ty.startswith("STRING"):
        return _str_len(c) + 2
    if ty.startswith("BYTES"):
        return 34
    return 9


def where_fraction(where: str | None, cmap: dict) -> float:
    if not where:
        return 1.0
    f = 1.0
    for atom in re.split(r"\s+AND\s+", where.strip(), flags=re.I):
        a = atom.strip().strip("()")
        m = re.match(r"(\w+)\s+IS\s+NOT\s+NULL", a, re.I)
        if m:
            c = cmap.get(m.group(1))
            f *= (NULL_FILL if c and c.get("null") else 1.0)
            continue
        m = re.match(r"(\w+)\s+IS\s+NULL", a, re.I)
        if m:
            nm = m.group(1)
            f *= 0.97 if nm == "deleted_at" else (0.03 if nm in ("ended_at",) else (0.6 if nm in ("effective_to", "withdrawn_at", "revoked_at", "lifted_at", "closed_at") else 0.5))
            continue
        m = re.match(r"NOT\s+(\w+)$", a, re.I)
        if m:
            f *= 0.4
            continue
        m = re.match(r"(\w+)$", a)
        if m:  # boolean column
            nm = m.group(1)
            f *= {"is_live": 0.3, "is_active": 0.8, "is_searchable": 0.8, "is_displayable": 0.9, "is_primary": 0.2,
                  "is_default": 0.1, "is_open_to_consultants": 0.1, "is_active_binding": 0.9, "is_completed": 0.4}.get(nm, 0.5)
            continue
        f *= 0.5
    return max(min(f, 1.0), 0.0001)


def index_entry_bytes(t, ix) -> float:
    cm = _cmap(t)
    cols = []
    for c in ix["columns"]:
        nm = re.split(r"\s+", c.strip())[0]
        if "(" in nm:  # expression
            nm = nm.split("(")[1].rstrip(")")
        cols.append(cm.get(nm))
    kw = sum(_key_width(c) if c else 10 for c in cols)
    if ix.get("hash"):
        kw += 2  # shard column
    pk = 17
    if ix.get("inverted") or any(ix.get("inverted") for _ in [0]):
        pass
    sto = sum(col_bytes(cm[s]) for s in (ix.get("storing") or []) if s in cm)
    unique_all_not_null = ix.get("unique") and all(c and not c.get("null") for c in cols)
    # non-unique (or nullable unique) index: PK appended to key; unique: PK in value (similar width)
    return INDEX_KEY_PREFIX + kw + pk + MVCC + VALUE_HDR + sto


def inverted_entries_per_row(t, ix) -> float:
    cm = _cmap(t)
    nm = re.split(r"\s+", ix["columns"][-1].strip())[0]
    c = cm.get(nm)
    if c is None:
        return 10
    if "gin_trgm" in ix["columns"][-1] or ix.get("opclass") == "gin_trgm_ops" or ("title" in nm or "name" in nm or "search" in nm) and c["type"].upper().startswith("STRING"):
        L = _str_len(c)
        return max(1.0, (L - 2) * 0.85)
    if c["type"].upper().startswith("JSONB"):
        return 8.0
    return 10.0


def index_bytes_per_row(t, ix) -> float:
    """bytes contributed per TABLE row (accounts for partial fraction and inverted fan-out)."""
    frac = where_fraction(ix.get("where"), _cmap(t))
    if ix.get("inverted"):
        # per entry: prefix + term (~12) + pk 17 + mvcc 12 + hdr 5
        per_entry = INDEX_KEY_PREFIX + 12 + 17 + MVCC + VALUE_HDR
        return frac * inverted_entries_per_row(t, ix) * per_entry
    return frac * index_entry_bytes(t, ix)


def unique_constraint_bytes_per_row(t, uc) -> float:
    cm = _cmap(t)
    kw = sum(_key_width(cm[c]) for c in uc["columns"] if c in cm)
    return INDEX_KEY_PREFIX + kw + 17 + MVCC + VALUE_HDR


# ---- the model ------------------------------------------------------------------------------------------------------
class Model:
    def __init__(self, calib: float = 1.0):
        self.m = load_model()
        self.T = {t["name"]: t for t in self.m["tables"]}
        self.calib = calib
        self.rows_cache = {}

    def rows(self, t) -> tuple[int, int, str]:
        n = t["name"]
        if t.get("is_lookup"):
            k = max(len(t.get("values") or []), 1)
            return k, k, "L"
        if n in ANCHORS:
            lap, drv = ANCHORS[n]
        else:
            lap, drv = tail_rows(t)
        full = int(lap * FACTOR[drv])
        return lap, full, drv

    def table_stats(self, t):
        lap, full, drv = self.rows(t)
        rb = row_bytes(t)
        # secondary structures
        ix_list = []
        for uc in t["constraints"]:
            if uc["type"] == "unique":
                ix_list.append((uc["name"], "unique constraint", unique_constraint_bytes_per_row(t, uc)))
        for ix in t["indexes"]:
            kind = "inverted/trigram" if ix.get("inverted") else ("hash-sharded" if ix.get("hash") else ("partial" if ix.get("where") else "btree"))
            if ix.get("unique") and not ix.get("inverted"):
                kind = "unique " + kind
            ix_list.append((ix["name"], kind, index_bytes_per_row(t, ix)))
        return dict(name=t["name"], module=t["module"], kind=t["kind"], volume=t["volume"], laptop_rows=lap, full_rows=full, driver=drv,
                    row_bytes=rb * self.calib, indexes=[(a, b, c * self.calib) for a, b, c in ix_list],
                    idx_bytes_row=sum(c for _, _, c in ix_list) * self.calib)

    def all_stats(self):
        return [self.table_stats(t) for t in self.m["tables"]]


GB = 1024 ** 3

if __name__ == "__main__":
    calib = 1.0
    if "--calibrate" in sys.argv:
        calib = float(sys.argv[sys.argv.index("--calibrate") + 1])
    M = Model(calib)
    st = M.all_stats()
    lap = sum(s["laptop_rows"] * (s["row_bytes"] + s["idx_bytes_row"]) for s in st) / GB
    full = sum(s["full_rows"] * (s["row_bytes"] + s["idx_bytes_row"]) for s in st) / GB
    print(f"tables={len(st)} laptop logical GB (1 replica, data+indexes)={lap:.1f}  full={full:.1f}")
    st.sort(key=lambda s: -s["laptop_rows"] * (s["row_bytes"] + s["idx_bytes_row"]))
    for s in st[:40]:
        print(f"{s['name']:45s} {s['laptop_rows']:>11,} {s['row_bytes']:7.0f}B idx/row {s['idx_bytes_row']:7.0f}B  lap {s['laptop_rows']*(s['row_bytes']+s['idx_bytes_row'])/GB:6.1f}GB full {s['full_rows']*(s['row_bytes']+s['idx_bytes_row'])/GB:7.1f}GB")

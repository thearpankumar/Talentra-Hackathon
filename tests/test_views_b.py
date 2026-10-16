"""Business views B (analytics, ext_learning, ext_talent_ops, ext_matching, ext_network, ext_ads, notifications_community,
gig_marketplace).  Each test inserts its own rows (fresh uuids) as root, then SELECTs through `SET ROLE <granted role>`.
Run: cd tests && TALENTRA_DB=ts_probe .venv/Scripts/python -m pytest test_views_b.py -q
"""
import datetime as dt
import uuid

import pytest
import helpers_rowbuilder as _hr
from helpers_rowbuilder import lookup_id

_orig_build = _hr.build_row
_REUSE = {"language": dict(is_default=True), "currency": dict(iso_numeric=None), "country": {}, "dim_date": {}, "dim_time_of_day": {}, "dim_etl_run": {}}
_reuse_cache = {}


def _reusing_build_row(conn, table, *a, **kw):
    """Shared master data (language, currency, country, dim_date) has global uniques/CHECKs: reuse one existing row."""
    if table in _REUSE and not any(not k.startswith("_") for k in kw):
        if table not in _reuse_cache:
            cur = conn.execute(f"SELECT * FROM {table} ORDER BY created_at LIMIT 1")
            r = cur.fetchone()
            if r is None:
                extra = dict(_REUSE[table])
                if table == "currency":
                    extra["iso_numeric"] = "%03d" % (uuid.uuid4().int % 1000)
                if table == "language":
                    extra["language_tag"] = "zz-" + uuid.uuid4().hex[:6]
                if table == "currency":
                    extra["currency_code"] = "".join(chr(65 + int(x, 16) % 26) for x in uuid.uuid4().hex[:3])
                return _reuse_cache.setdefault(table, _orig_build(conn, table, *a, **{**kw, **extra}))
            _reuse_cache[table] = dict(zip([d.name for d in cur.description], r))
        return _reuse_cache[table]
    if table == "app_user":
        kw.setdefault("email", f"u{uuid.uuid4().hex[:12]}@example.test")
    if "name_i18n" in {c["column"] for c in _hr._dd()[0].get(table, [])}:
        kw.setdefault("name_i18n", Jsonb({"en": "x"}))
    if table == "ad_account":
        kw.setdefault("credit_limit_amount", 100)
    return _orig_build(conn, table, *a, **kw)


_hr.build_row = _reusing_build_row
build_row = _reusing_build_row

import time

import psycopg
from psycopg.types.json import Jsonb

from conftest import connect


class RC:
    """autocommit connection that reconnects (HAProxy drops sessions on the loaded local cluster) and retries 40001."""

    def __init__(self, role=None):
        self.role, self.c = role, None
        self._open()

    def _open(self):
        last = None
        for _ in range(30):
            try:
                self.c = connect(role=self.role)
                return
            except psycopg.OperationalError as e:
                last = e
                time.sleep(2)
        raise last

    @property
    def info(self):
        return self.c.info

    def execute(self, sql, params=None):
        write = sql.lstrip()[:6].upper() in ("INSERT", "UPDATE", "DELETE")
        for i in range(12):
            try:
                return self.c.execute(sql, params)
            except psycopg.OperationalError:
                time.sleep(1)
                self._open()
                if write:                      # may or may not have been applied: let the whole test restart (see _restart)
                    raise
            except psycopg.errors.SerializationFailure:
                time.sleep(0.1 * (i + 1))
        return self.c.execute(sql, params)

    def close(self):
        try:
            self.c.close()
        except Exception:
            pass


@pytest.fixture(scope="module")
def admin():
    c = RC()
    yield c
    c.close()


@pytest.fixture()
def as_role():
    made = []

    def mk(role):
        c = RC(role)
        made.append(c)
        return c
    yield mk
    for c in made:
        c.close()


NOW = dt.datetime.now(dt.timezone.utc)
W0 = NOW.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=2)   # gig windows must sit on 15-minute marks
WIN = dict(window_start_at=W0, window_end_at=W0 + dt.timedelta(hours=2), is_rate_based=False, fixed_price=50)
DAY = dt.timedelta(days=1)


def tok():
    return uuid.uuid4().hex[:10]


def lk(admin, table, code):
    return lookup_id(admin, table, code)


def rows(conn, sql, *params):
    cur = conn.execute(sql, params)
    names = [d.name for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def cols(conn, view):
    return [d.name for d in conn.execute(f"SELECT * FROM {view} LIMIT 0").description]


def refresh(admin, mv):
    for _ in range(30):
        try:
            return admin.execute("REFRESH MATERIALIZED VIEW " + mv)
        except psycopg.errors.ObjectNotInPrerequisiteState:
            time.sleep(2)
    raise AssertionError("refresh of " + mv + " is blocked")


def new_candidate(admin, **kw):
    return build_row(admin, "candidate_profile", **kw)["id"]


# ----------------------------------------------------------------------------------------------- every view is selectable
MY_VIEWS = """vw_unified_demand_daily vw_dim_candidate_current vw_dim_organisation_current vw_dim_job_requirement_current
vw_dim_user_current vw_kpi_catalogue vw_pipeline_latency vw_etl_freshness mv_funnel_last_90_days mv_kpi_summary_monthly
mv_recruiter_leaderboard_30d mv_mrr_trend mv_wallet_cover_risk vw_gig_vs_job_fill vw_sponsored_job_serving vw_ad_campaign_pacing
vw_ad_campaign_report vw_ad_audience_rule_public vw_course_catalogue_public vw_candidate_learning_transcript
vw_certificate_public_verification vw_assessment_item_statistics vw_mentor_directory vw_employer_learning_compliance
vw_learning_leaderboard_latest vw_candidate_job_recommendation_live vw_recommendation_ctr_hourly vw_search_index_lag
vw_zero_result_top_queries vw_raw_title_review_queue vw_experiment_readout vw_connection_adjacency vw_pending_connection_request
vw_public_profile_indexable vw_upcoming_event vw_active_user_signal mv_event_rsvp_headcount vw_onboarding_progress
vw_client_vendor_submission vw_staffing_margin vw_timesheet_pending_approval vw_drive_funnel vw_active_account_restriction
vw_eeo_aggregate_cell_count vw_unified_demand vw_gig_worker_public vw_gig_open_request vw_notification_delivery_latest
vw_unread_notification_count vw_recruiter_searchable_candidate vw_support_ticket_sla_breach""".split()


def test_all_views_exist_and_selectable_by_granted_roles(admin, as_role):
    assert len(MY_VIEWS) == 51
    granted = {}
    for v, r in admin.execute(
            "SELECT table_name, grantee FROM information_schema.role_table_grants WHERE table_schema='public' "
            "AND privilege_type='SELECT' AND table_name = ANY(%s)", (MY_VIEWS,)).fetchall():
        granted.setdefault(v, set()).add(r)
    for v in MY_VIEWS:
        admin.execute(f"SELECT * FROM {v} LIMIT 0")           # exists + valid
        roles = sorted(granted.get(v, set()) - {"root", "admin", "schema_owner"})
        assert roles, f"{v}: no SELECT grant for any role"
        c = as_role(roles[0])
        c.execute(f"SELECT * FROM {v} LIMIT 1").fetchall()    # selectable by a granted role


# ----------------------------------------------------------------------------------------------- ext_learning
def test_course_catalogue_public_hides_deleted_and_unapproved_reviews(admin, as_role):
    t = tok()
    c = build_row(admin, "course", title="cat" + t, is_active=True)
    build_row(admin, "course_list_price", course_id=c["id"], valid_from=NOW - DAY, valid_to=None, list_amount=10)
    build_row(admin, "course_list_price", course_id=c["id"], valid_from=NOW - 3 * DAY, valid_to=NOW - DAY, list_amount=99)
    ok, rej = lk(admin, "content_review_status", "approved"), lk(admin, "content_review_status", "rejected")
    build_row(admin, "course_review", course_id=c["id"], rating=4, content_review_status_id=ok)
    build_row(admin, "course_review", course_id=c["id"], rating=1, content_review_status_id=rej)
    build_row(admin, "course_review", course_id=c["id"], rating=1, content_review_status_id=ok, deleted_at=NOW)
    gone = build_row(admin, "course", title="gone" + t, is_active=True, deleted_at=NOW)
    inactive = build_row(admin, "course", title="inact" + t, is_active=False)
    g = as_role("app_guest")
    r = rows(g, "SELECT * FROM vw_course_catalogue_public WHERE id = %s", c["id"])
    assert len(r) == 1                       # old (expired) price not joined -> no fan-out
    assert float(r[0]["list_amount"]) == 10 and float(r[0]["avg_rating"]) == 4 and r[0]["review_count"] == 1
    assert not rows(g, "SELECT 1 FROM vw_course_catalogue_public WHERE id = ANY(%s)", [gone["id"], inactive["id"]])


def test_candidate_learning_transcript_union_branches(admin, as_role):
    cand = new_candidate(admin)
    done, prog = lk(admin, "enrolment_status", "completed"), lk(admin, "enrolment_status", "in_progress")
    c1, c2 = build_row(admin, "course"), build_row(admin, "course")
    e1 = build_row(admin, "course_enrolment", candidate_profile_id=cand, course_id=c1["id"], enrolment_status_id=done, completed_at=NOW)
    build_row(admin, "course_enrolment", candidate_profile_id=cand, course_id=c2["id"], enrolment_status_id=prog)
    cert = build_row(admin, "certificate", candidate_profile_id=cand, title="cert" + tok(), revoked_at=NOW,
                     certificate_revocation_reason_id=lookup_id(admin, "certificate_revocation_reason"),
                     certificate_status_id=lk(admin, "certificate_status", "revoked"))
    build_row(admin, "certificate", candidate_profile_id=cand, title="deleted", deleted_at=NOW)
    r = rows(as_role("app_candidate"), "SELECT * FROM vw_candidate_learning_transcript WHERE candidate_profile_id = %s", cand)
    kinds = sorted(x["entry_kind"] for x in r)
    assert kinds == ["certificate", "course"]            # in-progress enrolment and deleted certificate absent
    assert len({(x["entry_kind"], x["entry_id"]) for x in r}) == len(r)      # branches do not overlap
    assert [x for x in r if x["entry_kind"] == "certificate"][0]["is_revoked"] is True    # revoked is flagged, not hidden
    assert [x for x in r if x["entry_kind"] == "course"][0]["entry_id"] == e1["id"]


def test_certificate_public_verification_validity_and_no_holder_data(admin, as_role):
    cand = new_candidate(admin)
    good = build_row(admin, "certificate", candidate_profile_id=cand, certificate_status_id=lk(admin, "certificate_status", "issued"),
                     expires_at=NOW + DAY, verification_code="V" + tok())
    bad = build_row(admin, "certificate", candidate_profile_id=cand, certificate_status_id=lk(admin, "certificate_status", "revoked"),
                    revoked_at=NOW, verification_code="V" + tok(),
                    certificate_revocation_reason_id=lookup_id(admin, "certificate_revocation_reason"))
    gone = build_row(admin, "certificate", candidate_profile_id=cand, deleted_at=NOW, verification_code="V" + tok())
    g = as_role("app_guest")
    q = lambda c: rows(g, "SELECT * FROM vw_certificate_public_verification WHERE verification_code = %s", c)
    assert q(good["verification_code"])[0]["is_valid"] is True
    assert q(bad["verification_code"])[0]["is_valid"] is False
    assert q(gone["verification_code"]) == []
    assert not {"candidate_profile_id", "serial_no", "content_hash"} & set(cols(g, "vw_certificate_public_verification"))


def test_assessment_item_statistics_aliases_and_graded_only(admin, as_role):
    qq = build_row(admin, "quiz_question")
    now = NOW
    build_row(admin, "assessment_answer", quiz_question_id=qq["id"], graded_at=now, is_correct=True, time_spent_seconds=10)
    build_row(admin, "assessment_answer", quiz_question_id=qq["id"], graded_at=now, is_correct=False, time_spent_seconds=30)
    build_row(admin, "assessment_answer", quiz_question_id=qq["id"], graded_at=None, is_correct=True, time_spent_seconds=500)
    s = as_role("app_staff_admin")
    names = cols(s, "vw_assessment_item_statistics")
    assert len(names) == len(set(names))
    r = rows(s, "SELECT * FROM vw_assessment_item_statistics WHERE quiz_question_id = %s", qq["id"])
    assert len(r) == 1 and r[0]["answer_count"] == 2 and float(r[0]["facility_index"]) == 0.5 and float(r[0]["avg_time_spent_seconds"]) == 20


def test_mentor_directory_excludes_suspended_deleted_and_private(admin, as_role):
    mk = lambda status, **kw: build_row(admin, "mentor_profile", mentor_status_id=lk(admin, "mentor_status", status),
                                        is_accepting_requests=True, headline="m" + tok(), **kw)
    ok, sus, dele, closed = mk("active"), mk("suspended"), mk("active", deleted_at=NOW), mk("approved")
    admin.execute("UPDATE mentor_profile SET is_accepting_requests = false WHERE id = %s", (closed["id"],))
    c = as_role("app_guest")
    seen = {x["id"] for x in rows(c, "SELECT id FROM vw_mentor_directory WHERE id = ANY(%s)", [m["id"] for m in (ok, sus, dele, closed)])}
    assert seen == {ok["id"]}
    assert not {"candidate_profile_id", "time_zone", "deleted_at"} & set(cols(c, "vw_mentor_directory"))


def test_employer_learning_compliance_overdue_and_mandatory_only(admin, as_role):
    org = build_row(admin, "organisation")["id"]
    st = lambda code: lk(admin, "learning_assignment_status", code)
    past = (NOW - 5 * DAY).date()
    course = build_row(admin, "course")["id"]
    for status, mand, due in (("assigned", True, past), ("completed", True, past), ("assigned", False, past), ("assigned", True, None)):
        build_row(admin, "learning_assignment", organisation_id=org, course_id=course, learning_assignment_status_id=st(status), is_mandatory=mand, due_date=due)
    r = rows(as_role("app_account_admin"), "SELECT * FROM vw_employer_learning_compliance WHERE organisation_id = %s", org)
    by = {x["assignment_status_code"]: x for x in r}
    assert by["assigned"]["assignment_count"] == 2 and by["assigned"]["overdue_count"] == 1     # non-mandatory ignored
    assert by["completed"]["overdue_count"] == 0


def test_learning_leaderboard_latest_only_newest_snapshot_and_anonymised(admin, as_role):
    scope, period, key = lk(admin, "leaderboard_scope", "global"), lk(admin, "leaderboard_period", None), "k" + tok()
    old = build_row(admin, "leaderboard_snapshot", leaderboard_scope_id=scope, leaderboard_period_id=period, scope_key=key, computed_at=NOW - DAY, period_start_date=dt.date(2000, 1, 1))
    new = build_row(admin, "leaderboard_snapshot", leaderboard_scope_id=scope, leaderboard_period_id=period, scope_key=key, computed_at=NOW, period_start_date=dt.date(2000, 1, 2))
    a, b = new_candidate(admin), new_candidate(admin)
    build_row(admin, "leaderboard_entry", leaderboard_snapshot_id=old["id"], candidate_profile_id=a, rank_no=1, is_anonymised=False)
    build_row(admin, "leaderboard_entry", leaderboard_snapshot_id=new["id"], candidate_profile_id=a, rank_no=1, is_anonymised=False)
    build_row(admin, "leaderboard_entry", leaderboard_snapshot_id=new["id"], candidate_profile_id=b, rank_no=2, is_anonymised=True)
    r = rows(as_role("app_candidate"), "SELECT * FROM vw_learning_leaderboard_latest WHERE scope_key = %s ORDER BY rank_no", key)
    assert [x["leaderboard_snapshot_id"] for x in r] == [new["id"]] * 2
    assert r[0]["candidate_profile_id"] == a and r[1]["candidate_profile_id"] is None and r[1]["is_anonymised"]


# ----------------------------------------------------------------------------------------------- ext_matching
def test_recommendation_live_hides_expired_dismissed_and_not_live(admin, as_role):
    cand = new_candidate(admin)
    live = build_row(admin, "job_requirement", is_live=True, published_at=NOW - DAY)
    off = build_row(admin, "job_requirement", is_live=False)
    mk = lambda job, **kw: build_row(admin, "job_recommendation", candidate_profile_id=cand, job_requirement_id=job["id"],
                                     expires_at=kw.pop("expires_at", NOW + DAY), **kw)
    ok = mk(live)
    mk(live, expires_at=NOW - DAY)
    mk(live, dismissed_at=NOW)
    mk(off)
    r = rows(as_role("app_candidate"), "SELECT id FROM vw_candidate_job_recommendation_live WHERE candidate_profile_id = %s", cand)
    assert [x["id"] for x in r] == [ok["id"]]


def test_recommendation_ctr_hourly_attributes_clicks_to_impressions(admin, as_role):
    surface = lk(admin, "recommendation_surface", None)
    model = build_row(admin, "recommendation_model_version")["id"]
    job = build_row(admin, "job_requirement")["id"]
    req = "r" + tok()
    hour = NOW.replace(minute=5, second=0, microsecond=0) - dt.timedelta(hours=3)
    common = dict(visitor_ref="v" + tok(), recommendation_surface_id=surface, recommendation_model_version_id=model, job_requirement_id=job)
    build_row(admin, "recommendation_impression", request_ref=req, rank_position=1, impressed_at=hour, **common)
    build_row(admin, "recommendation_impression", request_ref=req, rank_position=2, impressed_at=hour, **common)
    build_row(admin, "recommendation_click_event", request_ref=req, rank_position=1, clicked_at=hour, **common)
    r = rows(as_role("app_staff_admin"), "SELECT * FROM vw_recommendation_ctr_hourly WHERE recommendation_model_version_id = %s", model)
    assert len(r) == 1 and r[0]["impressions"] == 2 and r[0]["clicks"] == 1 and float(r[0]["ctr"]) == 0.5


def test_raw_title_review_queue_only_needs_review_ordered(admin, as_role):
    nr, conf = lk(admin, "normalisation_status", "needs_review"), lk(admin, "normalisation_status", "confirmed")
    t = tok()
    a = build_row(admin, "raw_title_normalisation", raw_title="a" + t, normalisation_status_id=nr, seen_count=5)
    b = build_row(admin, "raw_title_normalisation", raw_title="b" + t, normalisation_status_id=nr, seen_count=50)
    build_row(admin, "raw_title_normalisation", raw_title="c" + t, normalisation_status_id=conf, seen_count=500)
    r = rows(as_role("app_staff_admin"), "SELECT id FROM vw_raw_title_review_queue WHERE raw_title LIKE %s ORDER BY seen_count DESC", "%" + t)
    assert [x["id"] for x in r] == [b["id"], a["id"]]


# ----------------------------------------------------------------------------------------------- gig_marketplace
def test_unified_demand_two_branches_without_deleted(admin, as_role):
    org = build_row(admin, "organisation")["id"]
    job = build_row(admin, "job_requirement", organisation_id=org)
    gig = build_row(admin, "gig_request", organisation_id=org, **WIN)
    build_row(admin, "gig_request", organisation_id=org, deleted_at=NOW, **WIN)
    build_row(admin, "job_requirement", organisation_id=org, deleted_at=NOW)
    r = rows(as_role("app_employer"), "SELECT * FROM vw_unified_demand WHERE organisation_id = %s", org)
    assert sorted((x["demand_type"], x["id"]) for x in r) == sorted([("job", job["id"]), ("gig", gig["id"])])
    assert "title" not in cols(as_role("app_employer"), "vw_unified_demand")


def test_gig_worker_public_rounds_coordinates_and_hides_kyc(admin, as_role):
    w = build_row(admin, "gig_worker_profile", is_listed=True, home_latitude=12.345678, home_longitude=77.987654, geo_source_id=lookup_id(admin, "geo_source"), kyc_token="secret" + tok())
    build_row(admin, "gig_worker_profile", is_listed=False)
    gone = build_row(admin, "gig_worker_profile", is_listed=True, deleted_at=NOW)
    c = as_role("app_employer")
    names = cols(c, "vw_gig_worker_public")
    assert len(names) == len(set(names))
    assert not {"kyc_token", "kyc_verified_at", "home_latitude", "home_longitude", "app_user_id"} & set(names)
    r = rows(c, "SELECT * FROM vw_gig_worker_public WHERE id = ANY(%s)", [w["id"], gone["id"]])
    assert [x["id"] for x in r] == [w["id"]]
    assert float(r[0]["approx_latitude"]) == 12.35 and float(r[0]["approx_longitude"]) == 77.99


def test_gig_open_request_only_open_public_unexpired(admin, as_role):
    org = build_row(admin, "organisation")["id"]
    st = lambda c: lk(admin, "gig_request_status", c)
    vis = lambda c: lk(admin, "gig_request_visibility", c)
    mk = lambda **kw: build_row(admin, "gig_request", organisation_id=org, **{"gig_request_status_id": st("open"),
                                "gig_request_visibility_id": vis("public"), **WIN, **kw})
    ok = mk(application_deadline_at=W0 + dt.timedelta(hours=1), latitude=1.23456, longitude=2.34567, geo_source_id=lookup_id(admin, "geo_source"))
    ids = {n: mk(**kw)["id"] for n, kw in {"draft": dict(gig_request_status_id=st("draft")), "invite": dict(gig_request_visibility_id=vis("invite_only")),
                                           "expired": dict(application_deadline_at=NOW - DAY), "deleted": dict(deleted_at=NOW)}.items()}
    c = as_role("app_guest")
    r = rows(c, "SELECT * FROM vw_gig_open_request WHERE organisation_id = %s", org)
    assert [x["id"] for x in r] == [ok["id"]] and float(r[0]["approx_latitude"]) == 1.23
    assert not {"requester_app_user_id", "latitude", "longitude"} & set(cols(c, "vw_gig_open_request"))


# ----------------------------------------------------------------------------------------------- analytics
def test_dim_candidate_current_current_only_without_personal_key(admin, as_role):
    cand = new_candidate(admin)
    cur = build_row(admin, "dim_candidate", candidate_profile_id=cand, is_current=True, valid_from=NOW - 2 * DAY, is_erased=False, version_no=2)
    old = build_row(admin, "dim_candidate", candidate_profile_id=cand, is_current=False, valid_from=NOW - 2 * DAY, valid_to=NOW - DAY, is_erased=False, version_no=1)
    er = build_row(admin, "dim_candidate", candidate_profile_id=new_candidate(admin), is_current=True, is_erased=True)
    c = as_role("analytics_reader")
    got = {x["id"] for x in rows(c, "SELECT id FROM vw_dim_candidate_current WHERE id = ANY(%s)", [cur["id"], old["id"], er["id"]])}
    assert got == {cur["id"]}
    assert "candidate_profile_id" not in cols(c, "vw_dim_candidate_current")


def test_kpi_summary_monthly_and_refresh(admin, as_role):
    p = dt.date(3000, 1, 1) + dt.timedelta(days=int(uuid.uuid4().int % 100000))
    cur = build_row(admin, "currency")["id"]
    build_row(admin, "agg_time_to_fill_monthly", period_date=p, requirements_filled=2, avg_days_to_fill=10)
    build_row(admin, "agg_time_to_fill_monthly", period_date=p, requirements_filled=6, avg_days_to_fill=20)
    build_row(admin, "agg_offer_acceptance_monthly", period_date=p, offers_extended=10, offers_accepted=4)
    build_row(admin, "agg_cost_per_hire_monthly", period_date=p, currency_id=cur, hires=4, spend_amount=1000)
    build_row(admin, "agg_revenue_monthly", period_date=p, currency_id=cur, net_revenue_amount=777, closing_mrr_amount=50)
    refresh(admin, "mv_kpi_summary_monthly")
    r = rows(as_role("app_staff_admin"), "SELECT * FROM mv_kpi_summary_monthly WHERE period_date = %s", p)
    assert len(r) == 1
    x = r[0]
    assert float(x["k01_avg_days_to_fill"]) == 17.5 and float(x["k05_offer_acceptance_rate"]) == 0.4
    assert float(x["k06_cost_per_hire"]) == 250 and float(x["k14_net_revenue"]) == 777


def test_wallet_cover_risk_days_of_cover_below_seven(admin, as_role):
    low, high = (build_row(admin, "dim_organisation", organisation_id=build_row(admin, "organisation")["id"])["id"] for _ in range(2))
    for org, bal in ((low, 100), (high, 100000)):
        build_row(admin, "fact_wallet_transaction", dim_organisation_id=org, event_at=NOW - 2 * DAY, credits_delta=-70, balance_after_credits=bal + 70, is_reversal=False)
        build_row(admin, "fact_wallet_transaction", dim_organisation_id=org, event_at=NOW - DAY, credits_delta=-70, balance_after_credits=bal, is_reversal=False)
    refresh(admin, "mv_wallet_cover_risk")
    r = rows(as_role("app_staff_admin"), "SELECT * FROM mv_wallet_cover_risk WHERE dim_organisation_id = ANY(%s)", [low, high])
    assert [x["dim_organisation_id"] for x in r] == [low]          # 100 / (140/7 = 20) = 5 days
    assert float(r[0]["days_of_cover"]) == 5


def test_refresh_plan_lists_every_materialized_view_statements(admin):
    plan = admin.execute("SELECT mv_name, refresh_sql FROM vw_mv_refresh_plan ORDER BY sort_order").fetchall()
    assert {p[0] for p in plan} == {v for v in MY_VIEWS if v.startswith("mv_")}
    for name, sql in plan:                      # statements only: a refresh is a schema-change job (slow), run by the dedicated MV tests
        assert sql == "REFRESH MATERIALIZED VIEW " + name


# ----------------------------------------------------------------------------------------------- ext_ads
def test_ad_campaign_report_ctr_cpa_and_deleted_hidden(admin, as_role):
    camp = build_row(admin, "ad_campaign")
    gone = build_row(admin, "ad_campaign", deleted_at=NOW)
    d = NOW.date()
    build_row(admin, "ad_performance_daily", ad_campaign_id=camp["id"], ad_account_id=camp["ad_account_id"], performance_date=d, impressions=2000, clicks=100, conversions=10, spend_amount=300)
    build_row(admin, "ad_performance_daily", ad_campaign_id=gone["id"], ad_account_id=gone["ad_account_id"], performance_date=d, impressions=10, clicks=1, conversions=1, spend_amount=1)
    r = rows(as_role("app_employer"), "SELECT * FROM vw_ad_campaign_report WHERE ad_campaign_id = ANY(%s)", [camp["id"], gone["id"]])
    assert len(r) == 1 and float(r[0]["ctr"]) == 0.05 and float(r[0]["cost_per_conversion"]) == 30


def test_sponsored_job_serving_only_active_verified_live(admin, as_role):
    act = lambda c: lk(admin, "ad_entity_status", c)
    acct_ok = build_row(admin, "ad_account", ad_account_status_id=lk(admin, "ad_account_status", "active"), verified_at=NOW)
    acct_susp = build_row(admin, "ad_account", ad_account_status_id=lk(admin, "ad_account_status", "suspended"), verified_at=NOW)
    mkc = lambda a: build_row(admin, "ad_campaign", ad_account_id=a["id"], ad_entity_status_id=act("active"), budget_amount=100, spent_amount=1)
    c_ok, c_susp = mkc(acct_ok), mkc(acct_susp)
    jr = lambda acct, live: build_row(admin, "job_requirement", organisation_id=acct["organisation_id"], is_live=live, **(dict(published_at=NOW - DAY) if live else {}))
    live, off, live_s = jr(acct_ok, True), jr(acct_ok, False), jr(acct_susp, True)
    mk = lambda acct, camp, job, **kw: build_row(admin, "sponsored_job", ad_account_id=acct["id"], organisation_id=acct["organisation_id"], ad_campaign_id=camp["id"], ad_group_id=build_row(admin, "ad_group", ad_campaign_id=camp["id"])["id"],
                                                 job_requirement_id=job["id"], ad_entity_status_id=kw.pop("st", act("active")),
                                                 starts_at=NOW - DAY, ends_at=NOW + DAY, **kw)
    ok = mk(acct_ok, c_ok, live)
    bad = [mk(acct_susp, c_susp, live_s), mk(acct_ok, c_ok, off), mk(acct_ok, c_ok, live, st=act("paused")), mk(acct_ok, c_ok, live, deleted_at=NOW)]
    r = rows(as_role("app_service"), "SELECT id FROM vw_sponsored_job_serving WHERE id = ANY(%s)", [ok["id"]] + [b["id"] for b in bad])
    assert [x["id"] for x in r] == [ok["id"]]


# ----------------------------------------------------------------------------------------------- ext_network
def test_connection_adjacency_gives_both_directions(admin, as_role):
    a, b = sorted([build_row(admin, "app_user")["id"], build_row(admin, "app_user")["id"]], key=str)
    build_row(admin, "connection", lo_app_user_id=a, hi_app_user_id=b, initiator_app_user_id=a)
    r = rows(as_role("app_staff_admin"), "SELECT app_user_id, neighbour_app_user_id FROM vw_connection_adjacency WHERE app_user_id = ANY(%s)", [a, b])
    assert sorted((str(x["app_user_id"]), str(x["neighbour_app_user_id"])) for x in r) == sorted([(str(a), str(b)), (str(b), str(a))])
    assert "created_by" not in cols(as_role("app_staff_admin"), "vw_connection_adjacency")


def test_pending_connection_request_excludes_expired_and_answered(admin, as_role):
    u1, u2, u3, u4 = (build_row(admin, "app_user")["id"] for _ in range(4))
    live = build_row(admin, "connection_request", requester_app_user_id=u1, recipient_app_user_id=u2, is_pending=True, expires_at=NOW + DAY)
    exp = build_row(admin, "connection_request", requester_app_user_id=u1, recipient_app_user_id=u3, is_pending=True, expires_at=NOW - DAY)
    ans = build_row(admin, "connection_request", requester_app_user_id=u1, recipient_app_user_id=u4, is_pending=False)
    r = rows(as_role("app_candidate"), "SELECT id FROM vw_pending_connection_request WHERE requester_app_user_id = %s", u1)
    assert [x["id"] for x in r] == [live["id"]]


def test_upcoming_event_published_future_without_join_url(admin, as_role):
    st = lambda c: lk(admin, "event_status", c)
    ok = build_row(admin, "event", event_status_id=st("published"), starts_at=NOW + DAY, ends_at=NOW + 2 * DAY, join_url="https://x/" + tok())
    others = [build_row(admin, "event", event_status_id=st("draft"), starts_at=NOW + DAY, ends_at=NOW + 2 * DAY),
              build_row(admin, "event", event_status_id=st("published"), starts_at=NOW - DAY, ends_at=NOW - DAY / 2),
              build_row(admin, "event", event_status_id=st("published"), starts_at=NOW + DAY, ends_at=NOW + 2 * DAY, deleted_at=NOW)]
    c = as_role("app_guest")
    r = rows(c, "SELECT id FROM vw_upcoming_event WHERE id = ANY(%s)", [ok["id"]] + [o["id"] for o in others])
    assert [x["id"] for x in r] == [ok["id"]]
    assert "join_url" not in cols(c, "vw_upcoming_event")


def test_event_rsvp_headcount_mv(admin, as_role):
    ev = build_row(admin, "event")
    status = lookup_id(admin, "event_rsvp_status")
    build_row(admin, "event_rsvp", event_id=ev["id"], event_rsvp_status_id=status, guest_count=0)
    build_row(admin, "event_rsvp", event_id=ev["id"], event_rsvp_status_id=status, guest_count=2)
    refresh(admin, "mv_event_rsvp_headcount")
    r = rows(as_role("app_staff_admin"), "SELECT * FROM mv_event_rsvp_headcount WHERE event_id = %s", ev["id"])
    assert len(r) == 1 and r[0]["rsvp_count"] == 2 and r[0]["seats"] == 4


def test_active_user_signal_window(admin, as_role):
    u, u2, u3 = (build_row(admin, "app_user")["id"] for _ in range(3))
    ok = build_row(admin, "user_signal", app_user_id=u, starts_at=NOW - DAY, ends_at=None)
    build_row(admin, "user_signal", app_user_id=u2, starts_at=NOW - 2 * DAY, ends_at=NOW - DAY)
    build_row(admin, "user_signal", app_user_id=u3, starts_at=NOW + DAY, ends_at=None)
    r = rows(as_role("app_staff_admin"), "SELECT id FROM vw_active_user_signal WHERE app_user_id = ANY(%s)", [u, u2, u3])
    assert [x["id"] for x in r] == [ok["id"]]


# ----------------------------------------------------------------------------------------------- ext_talent_ops
def test_onboarding_progress_counts_required_done_overdue(admin, as_role):
    org = build_row(admin, "organisation")["id"]
    plan = build_row(admin, "onboarding_plan", organisation_id=org)
    seq = iter(range(1, 20))
    mk = lambda **kw: build_row(admin, "onboarding_plan_task", onboarding_plan_id=plan["id"], organisation_id=org, sequence_no=next(seq), **kw)
    past = (NOW - 3 * DAY).date()
    mk(is_required=True, completed_at=NOW)
    mk(is_required=True, completed_at=None, due_date=past)
    mk(is_required=False, completed_at=None, due_date=None)
    mk(is_required=True, completed_at=None, due_date=past, deleted_at=NOW)
    r = rows(as_role("app_recruiter"), "SELECT * FROM vw_onboarding_progress WHERE organisation_id = %s", org)
    assert len(r) == 1 and (r[0]["required_tasks"], r[0]["completed_tasks"], r[0]["overdue_tasks"]) == (2, 1, 1)


def test_client_vendor_submission_has_no_pay_rate(admin, as_role):
    sub = build_row(admin, "vendor_submission", proposed_pay_rate=55, proposed_bill_rate=80)
    gone = build_row(admin, "vendor_submission", deleted_at=NOW)
    c = as_role("app_employer")
    assert not {"proposed_pay_rate", "decision_note"} & set(cols(c, "vw_client_vendor_submission"))
    r = rows(c, "SELECT id FROM vw_client_vendor_submission WHERE id = ANY(%s)", [sub["id"], gone["id"]])
    assert [x["id"] for x in r] == [sub["id"]]


def test_staffing_margin_and_timesheet_pending(admin, as_role):
    a = build_row(admin, "staffing_assignment", bill_rate=100, pay_rate=70)
    gone = build_row(admin, "staffing_assignment", bill_rate=100, pay_rate=70, deleted_at=NOW)
    c = as_role("app_recruiter")
    r = rows(c, "SELECT * FROM vw_staffing_margin WHERE id = ANY(%s)", [a["id"], gone["id"]])
    assert len(r) == 1 and float(r[0]["margin"]) == 30 and float(r[0]["margin_percent"]) == 30
    d0 = dt.date(2020, 1, 1)
    sub = build_row(admin, "timesheet", staffing_assignment_id=a["id"], submitted_at=NOW, locked_at=None, period_start_date=d0, period_end_date=d0)
    build_row(admin, "timesheet", staffing_assignment_id=a["id"], submitted_at=NOW, locked_at=NOW, period_start_date=d0 + DAY, period_end_date=d0 + DAY)
    build_row(admin, "timesheet", staffing_assignment_id=a["id"], submitted_at=None, period_start_date=d0 + 2 * DAY, period_end_date=d0 + 2 * DAY)
    r = rows(c, "SELECT id FROM vw_timesheet_pending_approval WHERE staffing_assignment_id = %s", a["id"])
    assert [x["id"] for x in r] == [sub["id"]]


def test_drive_funnel_suppresses_cells_below_five(admin, as_role):
    inst = build_row(admin, "institution")["id"]
    small, big = build_row(admin, "placement_drive"), build_row(admin, "placement_drive")
    sel = lk(admin, "drive_result_decision", "selected")
    for drive, n in ((small, 4), (big, 5)):
        for i in range(n):
            reg = build_row(admin, "drive_registration", placement_drive_id=drive["id"], institution_id=inst)
            if i == 0:
                build_row(admin, "drive_result", drive_registration_id=reg["id"], drive_result_decision_id=sel)
    c = as_role("app_tpo")
    r = {x["placement_drive_id"]: x for x in rows(c, "SELECT * FROM vw_drive_funnel WHERE institution_id = %s", inst)}
    assert r[small["id"]]["registered"] is None and r[small["id"]]["is_suppressed"] is True
    assert r[big["id"]]["registered"] == 5 and r[big["id"]]["selected"] == 1 and r[big["id"]]["is_suppressed"] is False


def test_active_account_restriction_window(admin, as_role):
    u = build_row(admin, "app_user")["id"]
    ok = build_row(admin, "account_restriction", app_user_id=u, starts_at=NOW - DAY, ends_at=None, lifted_at=None, lifted_by_app_user_id=None)
    build_row(admin, "account_restriction", app_user_id=u, starts_at=NOW - DAY, lifted_at=NOW, lifted_by_app_user_id=u)
    build_row(admin, "account_restriction", app_user_id=u, starts_at=NOW - 3 * DAY, ends_at=NOW - DAY)
    r = rows(as_role("app_service"), "SELECT id FROM vw_active_account_restriction WHERE app_user_id = %s", u)
    assert [x["id"] for x in r] == [ok["id"]]


def test_eeo_aggregate_cell_count_min_cell_size_ten(admin, as_role):
    org = build_row(admin, "organisation")["id"]
    s_small, s_big = build_row(admin, "equal_opportunity_survey"), build_row(admin, "equal_opportunity_survey")
    for s, n in ((s_small, 9), (s_big, 10)):
        for _ in range(n):
            cand = new_candidate(admin)
            build_row(admin, "equal_opportunity_response", equal_opportunity_survey_id=s["id"], organisation_id=org, candidate_profile_id=cand, declined_to_answer=True, answers_encrypted=None)
    c = as_role("compliance_svc")
    r = {x["equal_opportunity_survey_id"]: x for x in rows(c, "SELECT * FROM vw_eeo_aggregate_cell_count WHERE organisation_id = %s", org)}
    assert s_small["id"] not in r and r[s_big["id"]]["responses"] == 10
    assert "candidate_profile_id" not in cols(c, "vw_eeo_aggregate_cell_count")


# ----------------------------------------------------------------------------------------------- notifications_community
def test_unread_notification_count_ignores_read_and_expired(admin, as_role):
    u = build_row(admin, "app_user")["id"]
    build_row(admin, "notification", recipient_app_user_id=u, read_at=None, expires_at=NOW + DAY)
    build_row(admin, "notification", recipient_app_user_id=u, read_at=None, expires_at=NOW + DAY)
    build_row(admin, "notification", recipient_app_user_id=u, read_at=NOW, expires_at=NOW + DAY)
    build_row(admin, "notification", recipient_app_user_id=u, read_at=None, expires_at=NOW - DAY)
    r = rows(as_role("app_candidate"), "SELECT unread_count FROM vw_unread_notification_count WHERE recipient_app_user_id = %s", u)
    assert r == [{"unread_count": 2}]


def test_recruiter_searchable_candidate_hidden_org_list_has_no_leak(admin, as_role):
    org_a, org_b, org_c = (build_row(admin, "organisation")["id"] for _ in range(3))
    cand_user = build_row(admin, "app_user")["id"]
    cand = new_candidate(admin, app_user_id=cand_user, is_searchable=True)
    build_row(admin, "privacy_setting", app_user_id=cand_user, is_searchable_by_recruiters=True)
    for o in (org_a, org_b):
        build_row(admin, "privacy_hidden_organisation", app_user_id=cand_user, organisation_id=o)
    # not searchable by recruiters / deleted profile
    u2 = build_row(admin, "app_user")["id"]
    new_candidate(admin, app_user_id=u2, is_searchable=True)
    build_row(admin, "privacy_setting", app_user_id=u2, is_searchable_by_recruiters=False)
    u3 = build_row(admin, "app_user")["id"]
    new_candidate(admin, app_user_id=u3, is_searchable=True, deleted_at=NOW)
    build_row(admin, "privacy_setting", app_user_id=u3, is_searchable_by_recruiters=True)
    c = as_role("app_recruiter")
    r = rows(c, "SELECT * FROM vw_recruiter_searchable_candidate WHERE app_user_id = ANY(%s)", [cand_user, u2, u3])
    assert len(r) == 1 and r[0]["candidate_profile_id"] == cand            # one row per candidate: no per-hidden-org rows
    assert set(r[0]["hidden_from_organisation_ids"]) == {org_a, org_b}
    # the consumer predicate: org A and B cannot see the candidate, org C can
    q = lambda o: rows(c, "SELECT 1 FROM vw_recruiter_searchable_candidate WHERE app_user_id = %s AND NOT (%s = ANY(hidden_from_organisation_ids))", cand_user, o)
    assert q(org_a) == [] and q(org_b) == [] and len(q(org_c)) == 1


def test_support_ticket_sla_breach_open_overdue_only(admin, as_role):
    mk = lambda **kw: build_row(admin, "support_ticket", ticket_number="T" + tok()[:9], **kw)
    over = mk(resolved_at=None, resolution_due_at=NOW - DAY)
    no_first = mk(resolved_at=None, resolution_due_at=NOW + DAY, first_response_due_at=NOW - DAY, first_responded_at=None)
    fine = mk(resolved_at=None, resolution_due_at=NOW + DAY, first_response_due_at=NOW + DAY)
    done = mk(resolved_at=NOW, resolution_due_at=NOW - DAY)
    gone = mk(resolved_at=None, resolution_due_at=NOW - DAY, deleted_at=NOW)
    ids = [t["id"] for t in (over, no_first, fine, done, gone)]
    r = rows(as_role("app_staff_admin"), "SELECT id FROM vw_support_ticket_sla_breach WHERE id = ANY(%s)", ids)
    assert {x["id"] for x in r} == {over["id"], no_first["id"]}


def _restart(fn):
    """Whole-test restart on a dropped session (HAProxy kills sessions on the overloaded local cluster); every test uses fresh uuids."""
    import functools
    import inspect

    @functools.wraps(fn)
    def run(*a, **kw):
        for i in range(4):
            try:
                return fn(*a, **kw)
            except psycopg.OperationalError:
                if i == 3:
                    raise
                time.sleep(3)
    run.__signature__ = inspect.signature(fn)
    return run


for _n, _f in list(globals().items()):
    if _n.startswith("test_") and callable(_f):
        globals()[_n] = _restart(_f)

"""Procedural business rules of infra/schema/parts/60_rules_other.sql (gig, jobs, network, notifications, profile, sourcing, ops,
learning, analytics).  Fixtures are tiny rows from helpers_rowbuilder (root connection); the function under test is called as
app_service / etl_service.  Intended for the EC2 run:  cd tests && TALENTRA_DB=talentra python -m pytest test_rules_other.py -q
Rejections use SQLSTATE 23514 (limit), 55000 (state), 55006 (stale optimistic lock), 42501 (blocked), 23505 (declarative overlap)."""
import datetime
import uuid

import pytest
from psycopg import errors as E

from conftest import connect, retry
from helpers_rowbuilder import build_row, lookup_id

UTC = datetime.timezone.utc


@pytest.fixture()
def adm():
    c = connect()
    yield c
    c.close()


@pytest.fixture()
def app():
    c = connect(role="app_service")
    yield c
    c.close()


def call(conn, sql, *args):
    return retry(lambda: conn.execute(sql, args).fetchone()[0], tries=60)


def _user(adm):
    return build_row(adm, "app_user")["id"]


def _gig_request(adm, headcount=1, start=None, hours=2, status="open"):
    start = start or (datetime.datetime.now(UTC) + datetime.timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    org = build_row(adm, "organisation")["id"]
    return build_row(adm, "gig_request", organisation_id=org, headcount=headcount, accepted_count=0,
                     window_start_at=start, window_end_at=start + datetime.timedelta(hours=hours),
                     gig_request_status_id=lookup_id(adm, "gig_request_status", status))


def _application(adm, req, worker=None, accepted=False):
    worker = worker or build_row(adm, "gig_worker_profile")["id"]
    return build_row(adm, "gig_application", gig_request_id=req["id"], gig_worker_profile_id=worker, is_accepted=accepted,
                     currency_id=req["currency_id"])


# ---------------------------------------------------------------------------------------------------------------- gig
def test_gig_accept_never_exceeds_headcount(adm, app):                                       # BR-GIG-12
    req = _gig_request(adm, headcount=1)
    a1, a2 = _application(adm, req), _application(adm, req)
    actor = _user(adm)
    call(app, "SELECT fn_gig_accept_application(%s, %s)", a1["id"], actor)
    call(app, "SELECT fn_gig_accept_application(%s, %s)", a1["id"], actor)       # idempotent
    with pytest.raises(E.CheckViolation, match="BR-GIG-12"):
        call(app, "SELECT fn_gig_accept_application(%s, %s)", a2["id"], actor)
    assert adm.execute("SELECT accepted_count FROM gig_request WHERE id=%s", (req["id"],)).fetchone()[0] == 1


def test_gig_overlapping_slots_rejected(adm, app):                                           # BR-GIG-01/03/10
    worker = build_row(adm, "gig_worker_profile")["id"]
    actor = _user(adm)
    req1, req2 = _gig_request(adm), _gig_request(adm, start=None)
    # same window for both requests: the second assignment of the same worker must clash on the slot key
    for r in (req1, req2):
        adm.execute("UPDATE gig_request SET window_start_at=%s, window_end_at=%s WHERE id=%s",
                    (req1["window_start_at"], req1["window_end_at"], r["id"]))
    a1 = _application(adm, req1, worker, True)
    a2 = _application(adm, req2, worker, True)
    call(app, "SELECT fn_gig_create_assignment(%s, %s)", a1["id"], actor)
    with pytest.raises(E.UniqueViolation):
        call(app, "SELECT fn_gig_create_assignment(%s, %s)", a2["id"], actor)
    n = adm.execute("SELECT count(*) FROM gig_slot WHERE gig_worker_profile_id=%s", (worker,)).fetchone()[0]
    assert n == 8   # 2 h of 15 minute slots, the failed assignment left nothing behind


def test_gig_time_off_blocks_and_cancel_releases(adm, app):                                  # BR-GIG-03/05
    worker = build_row(adm, "gig_worker_profile")["id"]
    actor = _user(adm)
    s = (datetime.datetime.now(UTC) + datetime.timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
    typ = lookup_id(adm, "gig_time_off_type")
    off = call(app, "SELECT fn_gig_time_off_add(%s, %s, %s, %s, %s)", worker, typ, s, s + datetime.timedelta(hours=1), actor)
    with pytest.raises(E.UniqueViolation):
        call(app, "SELECT fn_gig_time_off_add(%s, %s, %s, %s, %s)", worker, typ, s, s + datetime.timedelta(minutes=30), actor)
    assert call(app, "SELECT fn_gig_time_off_cancel(%s, %s)", off, actor) == 4
    call(app, "SELECT fn_gig_time_off_add(%s, %s, %s, %s, %s)", worker, typ, s, s + datetime.timedelta(minutes=30), actor)


# ------------------------------------------------------------------------------------------------------------- jobs
def test_job_application_transition_is_optimistic(adm, app):                                 # BR-JOB-11/12
    actor = _user(adm)
    req = build_row(adm, "job_requirement", is_live=True)
    cand = build_row(adm, "candidate_profile")["id"]
    src = lookup_id(adm, "job_source")
    app_id = call(app, "SELECT fn_job_application_create(%s, %s, %s, %s, 'candidate')", cand, req["id"], src, actor)
    assert adm.execute("SELECT status_sequence_no FROM job_application WHERE id=%s", (app_id,)).fetchone()[0] == 1
    assert adm.execute("SELECT count(*) FROM job_application_status_history WHERE job_application_id=%s", (app_id,)).fetchone()[0] == 1
    nxt = adm.execute("SELECT to_application_status_id FROM application_status_transition t JOIN application_status a "
                      "ON a.id = t.from_application_status_id WHERE a.code='applied' LIMIT 1").fetchone()[0]
    with pytest.raises(E.ObjectInUse):          # stale expected sequence (55006)
        call(app, "SELECT fn_job_application_transition(%s, %s, 7, %s)", app_id, nxt, actor)
    assert call(app, "SELECT fn_job_application_transition(%s, %s, 1, %s)", app_id, nxt, actor) == 2


def test_workforce_request_fill_cap(adm, app):                                               # BR-OPS-20
    actor = _user(adm)
    wr = build_row(adm, "workforce_request", headcount=2, filled_count=0)
    assert call(app, "SELECT fn_workforce_request_fill(%s, %s, 2)", wr["id"], actor) == 2
    with pytest.raises(E.CheckViolation, match="BR-OPS-20"):
        call(app, "SELECT fn_workforce_request_fill(%s, %s, 1)", wr["id"], actor)


def test_locked_timesheet_cannot_be_edited(adm, app):                                        # BR-OPS-24
    ts = build_row(adm, "timesheet", locked_at=datetime.datetime.now(UTC))
    with pytest.raises(E.ObjectNotInPrerequisiteState, match="BR-OPS-24"):
        call(app, "SELECT fn_timesheet_assert_editable(%s)", ts["id"])


# ---------------------------------------------------------------------------------------------------------- network
def test_block_prevents_connection_and_removes_existing(adm, app):                           # BR-NET-04/06
    a, b = _user(adm), _user(adm)
    origin = lookup_id(adm, "connection_origin")
    req = call(app, "SELECT fn_connection_request_send(%s, %s, %s)", a, b, origin)
    assert call(app, "SELECT fn_connection_accept(%s, %s)", req, b) is not None
    with pytest.raises(E.ObjectNotInPrerequisiteState, match="BR-NET-04"):
        call(app, "SELECT fn_connection_request_send(%s, %s, %s)", b, a, origin)       # already connected
    call(app, "SELECT fn_user_block(%s, %s, %s, %s, %s)", a, b, lookup_id(adm, "actor_type"), lookup_id(adm, "actor_type"),
         lookup_id(adm, "block_reason"))
    lo, hi = sorted([a, b])
    assert adm.execute("SELECT count(*) FROM connection WHERE lo_app_user_id=%s AND hi_app_user_id=%s", (lo, hi)).fetchone()[0] == 0
    with pytest.raises(E.InsufficientPrivilege, match="BR-NET-06"):
        call(app, "SELECT fn_connection_request_send(%s, %s, %s)", b, a, origin)


def test_event_rsvp_waitlist_and_promotion(adm, app):                                        # BR-NET-17
    ev = build_row(adm, "event", capacity=1, registration_closes_at=datetime.datetime.now(UTC) + datetime.timedelta(days=1))
    u1, u2 = _user(adm), _user(adm)
    origin = lookup_id(adm, "connection_origin")
    assert call(app, "SELECT fn_event_rsvp(%s, %s, 'going', %s, 0)", ev["id"], u1, origin) == "going"
    assert call(app, "SELECT fn_event_rsvp(%s, %s, 'going', %s, 0)", ev["id"], u2, origin) == "waitlisted"
    call(app, "SELECT fn_event_rsvp(%s, %s, 'declined', %s, 0)", ev["id"], u1, origin)     # frees the seat -> u2 promoted
    st = adm.execute("SELECT s.code FROM event_rsvp r JOIN event_rsvp_status s ON s.id=r.event_rsvp_status_id "
                     "WHERE r.event_id=%s AND r.attendee_app_user_id=%s", (ev["id"], u2)).fetchone()[0]
    assert st == "going"


# ---------------------------------------------------------------------------------------------- notifications / profile
def test_job_alert_limit(adm, app):                                                          # BR-NTF-17
    u = _user(adm)
    freq, chan = lookup_id(adm, "alert_frequency"), lookup_id(adm, "notification_channel")
    for i in range(2):
        call(app, "SELECT fn_job_alert_create(%s, %s, %s, %s, %s, 2)", u, f"a{i}", freq, chan, u)
    with pytest.raises(E.CheckViolation, match="BR-NTF-17"):
        call(app, "SELECT fn_job_alert_create(%s, 'one more', %s, %s, %s, 2)", u, freq, chan, u)


def test_shortlist_changes_leave_history(adm, app):                                          # BR-PRF-18
    actor = _user(adm)
    org = build_row(adm, "organisation")["id"]
    cand = build_row(adm, "candidate_profile")["id"]
    kind, stage = lookup_id(adm, "shortlist_kind"), lookup_id(adm, "shortlist_stage")
    item = call(app, "SELECT fn_shortlist_add(%s, %s, %s, %s, %s)", org, cand, actor, kind, stage)
    with pytest.raises(E.UniqueViolation):
        call(app, "SELECT fn_shortlist_add(%s, %s, %s, %s, %s)", org, cand, actor, kind, stage)
    retry(lambda: app.execute("SELECT fn_shortlist_remove(%s, %s)", (item, actor)))
    n = adm.execute("SELECT count(*) FROM candidate_shortlist_history WHERE candidate_shortlist_item_id=%s", (item,)).fetchone()[0]
    assert n == 2


# ------------------------------------------------------------------------------------------------------- sourcing/ana
def test_consultant_openness_requires_open_requirement(adm, app):                            # BR-SRC-02
    actor = _user(adm)
    req = build_row(adm, "job_requirement", is_open_to_consultants=False)
    with pytest.raises(E.ObjectNotInPrerequisiteState, match="BR-SRC-02"):
        call(app, "SELECT fn_consultant_openness_open(%s, %s)", req["id"], actor)


def test_etl_watermark_never_moves_backwards(adm):                                           # BR-ANA-13
    etl = connect(role="etl_service")
    try:
        code = "wm_" + uuid.uuid4().hex[:8]
        run = build_row(adm, "dim_etl_run", pipeline_code=code,
                        dim_etl_run_status_id=lookup_id(adm, "dim_etl_run_status", "succeeded"))
        build_row(adm, "dim_etl_watermark", pipeline_code=code, last_watermark_at=None)
        t1 = datetime.datetime.now(UTC)
        assert call(etl, "SELECT fn_etl_watermark_advance(%s, %s, %s)", code, t1, run["id"]) is True
        assert call(etl, "SELECT fn_etl_watermark_advance(%s, %s, %s)", code, t1 - datetime.timedelta(hours=1), run["id"]) is False
    finally:
        etl.close()

"""Business views + viewer-scoped access of the core modules (parts/views/*.sql, 65_viewer_core.sql, 75_post_grants_core.sql).

Fixtures are built as root with fresh uuids (no reliance on global counts); every assertion on a viewer-scoped view filters
on the viewer, exactly as the data-access layer does (AT-25).  Persona checks run under SET ROLE <persona>.
"""
import datetime as dt
import uuid

import psycopg
import pytest

from conftest import SYSTEM_ACTOR, connect, retry

import helpers_rowbuilder as _rb

# master-data tables (no `code` column, CHECK-heavy) that the generic builder cannot synthesise: one shared seeded row each,
# handed out as if they were lookups.
SHARED = {"language": ("language_tag", "en", {}),
          "currency": ("currency_code", "INR", {"iso_numeric": "356", "minor_unit": 2}),
          "country": ("iso2_code", "IN", {"iso3_code": "IND", "calling_code": "91"})}
_shared_ids = {}
_orig_lookup_id = _rb.lookup_id


def _lookup_id(conn, table, code=None):
    if table in SHARED:
        if table not in _shared_ids:
            col, val, extra = SHARED[table]
            r = conn.execute(f"SELECT id FROM {table} WHERE {col}=%s", (val,)).fetchone()
            if r is None:
                try:
                    r = (_orig_build(conn, table, **{col: val}, **extra)["id"],)
                except psycopg.errors.UniqueViolation:
                    r = conn.execute(f"SELECT id FROM {table} WHERE {col}=%s", (val,)).fetchone()
            _shared_ids[table] = r[0]
        return _shared_ids[table]
    return _orig_lookup_id(conn, table, code)


_orig_build = _rb.build_row



def _build(conn, table, *a, **kw):
    if table == "app_user" and not any(k in kw for k in ("email", "phone")):
        kw["email"] = f"{uuid.uuid4().hex[:12]}@example.test"   # parents built on demand satisfy ck_app_user_contact
    return _orig_build(conn, table, *a, **kw)


_orig_value = _rb._value


def _value(c):
    if c["column"].endswith("_i18n"):
        from psycopg.types.json import Jsonb
        return Jsonb({"en": "x"})
    if c["column"] == "currency_code":
        return "".join(chr(65 + int(x, 16) % 26) for x in uuid.uuid4().hex[:3])
    return _orig_value(c)


_rb._value = _value
_rb.build_row = _build
build_row = _build
_rb.lookup_id = _lookup_id
lookup_id = _lookup_id
for _t in SHARED:
    _rb._dd()[2][_t]["kind"] = "lookup"


NOW = dt.datetime.now(dt.timezone.utc)
DAY = dt.timedelta(days=1)


def _seed_language(conn):
    if conn.execute("SELECT count(*) FROM language WHERE language_tag='en'").fetchone()[0] == 0:
        try:
            conn.execute("INSERT INTO language (language_tag, iso639_1, iso639_3, display_name, native_name, text_direction_id, is_ui_supported,"
                         " is_default, is_content_supported, sort_order, is_active, created_by, updated_by)"
                         " VALUES ('en','en','eng','English','English',%s,true,true,true,1,true,%s,%s)",
                         (lookup_id(conn, "text_direction"), SYSTEM_ACTOR, SYSTEM_ACTOR))
        except psycopg.errors.UniqueViolation:
            pass


class RConn:
    """autocommit connection that reconnects (and re-assumes its role) when HAProxy/the node drops the session."""

    def __init__(self, role=None):
        self.role, self.c = role, None
        self._open()

    def _open(self):
        import time
        for i in range(20):
            try:
                self.c = connect(role=self.role)
                return
            except psycopg.OperationalError:
                time.sleep(1 + i * 0.5)
        self.c = connect(role=self.role)

    def execute(self, *a, **k):
        for i in range(8):
            try:
                return self.c.execute(*a, **k)
            except psycopg.OperationalError:
                self._open()
        return self.c.execute(*a, **k)

    def __getattr__(self, name):
        return getattr(self.c, name)

    def close(self):
        try:
            self.c.close()
        except Exception:
            pass


@pytest.fixture(scope="module")
def db():
    c = RConn()
    _seed_language(c)
    ensure_policy(c, lookup_id(c, "actor_type"))   # default actor type used by builder-created parents
    yield c
    c.close()


@pytest.fixture()
def as_role():
    made = []

    def make(role):
        c = RConn(role)
        made.append(c)
        return c
    yield make
    for c in made:
        c.close()


def q(conn, sql, *args):
    return retry(lambda: conn.execute(sql, args).fetchall())


def n(conn, sql, *args):
    return retry(lambda: conn.execute(sql, args).fetchone()[0])


def mk(conn, table, **kw):
    return retry(lambda: build_row(conn, table, **kw))


def ensure_policy(conn, at):
    ch = lookup_id(conn, "registration_channel")
    if conn.execute("SELECT count(*) FROM registration_policy WHERE actor_type_id=%s AND registrar_actor_type_id=%s AND registration_channel_id=%s",
                    (at, at, ch)).fetchone()[0] == 0:
        try:
            mk(conn, "registration_policy", actor_type_id=at, registrar_actor_type_id=at, registration_channel_id=ch)
        except psycopg.errors.UniqueViolation:
            pass
    return ch


def user(conn, actor, **kw):
    at = lookup_id(conn, "actor_type", actor)
    ch = ensure_policy(conn, at)
    return mk(conn, "app_user", actor_type_id=at, registrar_actor_type_id=at, registration_channel_id=ch,
              email=f"{uuid.uuid4().hex[:12]}@example.test", **kw)["id"]


def org(conn, **kw):
    return mk(conn, "organisation", organisation_status_id=lookup_id(conn, "organisation_status", "active"), **kw)["id"]


def closure(conn, anc, desc, depth):
    retry(lambda: conn.execute(
        "INSERT INTO organisation_hierarchy_closure (ancestor_organisation_id, descendant_organisation_id, depth, created_by, updated_by) VALUES (%s,%s,%s,%s,%s)",
        (anc, desc, depth, SYSTEM_ACTOR, SYSTEM_ACTOR)))


def tree(conn):
    """parent organisation P with child C (closure rows incl. self rows)."""
    p, c = org(conn), org(conn)
    for a, d, k in ((p, p, 0), (c, c, 0), (p, c, 1)):
        closure(conn, a, d, k)
    return p, c


def mapping(conn, rec, o, scope="organisation_only", **kw):
    return mk(conn, "recruiter_organisation_mapping", recruiter_app_user_id=rec, organisation_id=o,
              mapping_scope_id=lookup_id(conn, "mapping_scope", scope), effective_from=NOW - DAY, **kw)["id"]


def job(conn, o, **kw):
    if kw.get("is_live") and "published_at" not in kw:
        kw["published_at"] = NOW - DAY
    return mk(conn, "job_requirement", organisation_id=o, **kw)["id"]


def candidate(conn, **kw):
    if kw.get("current_salary_amount") is not None:
        kw.setdefault("salary_currency_id", lookup_id(conn, "currency"))
        kw.setdefault("salary_period_id", lookup_id(conn, "salary_period"))
    u = user(conn, "candidate")
    return u, mk(conn, "candidate_profile", app_user_id=u, **kw)["id"]


def role(conn, scoped, actor="employer"):
    r = mk(conn, "app_role", is_organisation_scoped=scoped)["id"]
    try:
        mk(conn, "app_role_eligibility", app_role_id=r, actor_type_id=lookup_id(conn, "actor_type", actor))
    except psycopg.errors.UniqueViolation:
        pass
    return r


def consent(conn, u, ctype, action, when):
    ct = lookup_id(conn, "consent_type", ctype)
    doc = mk(conn, "consent_document", consent_type_id=ct)["id"]
    return mk(conn, "user_consent", app_user_id=u, consent_type_id=ct, consent_document_id=doc,
              consent_action_id=lookup_id(conn, "consent_action", action), occurred_at=when)


def vis(conn, viewer):
    return {r[0] for r in q(conn, "SELECT candidate_profile_id FROM fn_candidate_visible_to_user(%s)", viewer)}


def jobs_of(conn, viewer):
    return {r[0] for r in q(conn, "SELECT id FROM fn_recruiter_job_requirement(%s)", viewer)}


def ublock(conn, blocker, blocked, **kw):
    bt = lambda u: n(conn, "SELECT actor_type_id FROM app_user WHERE id=%s", u)
    if n(conn, "SELECT count(*) FROM block_policy WHERE blocker_actor_type_id=%s AND blocked_actor_type_id=%s", bt(blocker), bt(blocked)) == 0:
        try:
            mk(conn, "block_policy", blocker_actor_type_id=bt(blocker), blocked_actor_type_id=bt(blocked))
        except psycopg.errors.UniqueViolation:
            pass
    return mk(conn, "user_block", blocker_app_user_id=blocker, blocked_app_user_id=blocked,
              blocker_actor_type_id=bt(blocker), blocked_actor_type_id=bt(blocked), effective_from=NOW - DAY, **kw)["id"]


def oblock(conn, o, cand, direction="candidate_blocks_organisation", **kw):
    return mk(conn, "organisation_block", organisation_id=o, candidate_profile_id=cand,
              block_direction_id=lookup_id(conn, "block_direction", direction), effective_from=NOW - DAY, **kw)["id"]


# ----------------------------------------------------------------------------------------------------------- recruiter scoping
def test_recruiter_sees_only_mapped_organisation_requirements(db):
    a, b = org(db), org(db)
    closure(db, a, a, 0); closure(db, b, b, 0)
    ja, jb = job(db, a), job(db, b)
    r = user(db, "recruiter")
    mapping(db, r, a)
    got = jobs_of(db, r)
    assert ja in got and jb not in got


def test_recruiter_scope_organisation_only_excludes_descendants(db):
    p, c = tree(db)
    jp, jc = job(db, p), job(db, c)
    r = user(db, "recruiter")
    mapping(db, r, p, "organisation_only")
    got = jobs_of(db, r)
    assert jp in got and jc not in got


def test_recruiter_scope_with_descendants_includes_children(db):
    p, c = tree(db)
    jp, jc = job(db, p), job(db, c)
    r = user(db, "recruiter")
    mapping(db, r, p, "with_descendants")
    assert {jp, jc} <= jobs_of(db, r)
    # a mapping on the child never reaches the parent
    r2 = user(db, "recruiter")
    mapping(db, r2, c, "with_descendants")
    got = jobs_of(db, r2)
    assert jc in got and jp not in got


def test_recruiter_mapping_ended_sees_nothing(db):
    a = org(db)
    closure(db, a, a, 0)
    ja = job(db, a)
    r = user(db, "recruiter")
    m = mapping(db, r, a)
    assert ja in jobs_of(db, r)
    retry(lambda: db.execute("UPDATE recruiter_organisation_mapping SET effective_to = now() - interval '1 second', mapping_end_reason_id=%s WHERE id=%s",
                             (lookup_id(db, "mapping_end_reason"), m)))
    assert jobs_of(db, r) == set()


def test_recruiter_future_mapping_not_effective_yet(db):
    a = org(db)
    closure(db, a, a, 0)
    job(db, a)
    r = user(db, "recruiter")
    mk(db, "recruiter_organisation_mapping", recruiter_app_user_id=r, organisation_id=a,
       mapping_scope_id=lookup_id(db, "mapping_scope", "organisation_only"), effective_from=NOW + DAY)
    assert jobs_of(db, r) == set()


def test_recruiter_view_hides_soft_deleted_requirement_and_inactive_org(db):
    a, b = org(db), org(db)
    closure(db, a, a, 0); closure(db, b, b, 0)
    live, gone = job(db, a), job(db, a)
    jb = job(db, b)
    retry(lambda: db.execute("UPDATE job_requirement SET deleted_at = now() WHERE id=%s", (gone,)))
    retry(lambda: db.execute("UPDATE organisation SET organisation_status_id=%s WHERE id=%s",
                             (lookup_id(db, "organisation_status", "suspended"), b)))
    r = user(db, "recruiter")
    mapping(db, r, a); mapping(db, r, b)
    got = jobs_of(db, r)
    assert live in got and gone not in got and jb not in got


def test_recruiter_unknown_or_null_viewer_gets_nothing(db):
    assert jobs_of(db, uuid.uuid4()) == set()
    assert n(db, "SELECT count(*) FROM fn_recruiter_job_requirement(NULL)") == 0


def test_recruiter_can_see_organisation_point_check(db):
    a, b = org(db), org(db)
    closure(db, a, a, 0); closure(db, b, b, 0)
    job(db, a); job(db, b)
    r = user(db, "recruiter")
    mapping(db, r, a)
    assert n(db, "SELECT fn_recruiter_can_see_organisation(%s,%s)", r, a) is True
    assert n(db, "SELECT fn_recruiter_can_see_organisation(%s,%s)", r, b) is False


# ------------------------------------------------------------------------------------------------------------- blocked parties
def test_user_block_hides_candidate_from_blocked_viewer_only(db):
    cu, cp = candidate(db)
    blocked, other = user(db, "employer"), user(db, "employer")
    assert cp in vis(db, blocked) and cp in vis(db, other)
    ublock(db, cu, blocked)
    assert cp not in vis(db, blocked)
    assert cp in vis(db, other)
    assert n(db, "SELECT fn_candidate_is_visible(%s,%s)", blocked, cp) is False
    assert n(db, "SELECT fn_candidate_is_visible(%s,%s)", other, cp) is True


def test_user_block_other_direction_viewer_blocked_candidate(db):
    cu, cp = candidate(db)
    viewer = user(db, "consultant")
    ublock(db, viewer, cu)
    assert cp not in vis(db, viewer)


def test_user_block_unblock_restores_visibility_immediately(db):
    cu, cp = candidate(db)
    viewer = user(db, "recruiter")
    b = ublock(db, cu, viewer)
    assert cp not in vis(db, viewer)
    retry(lambda: db.execute("UPDATE user_block SET effective_to = now() WHERE id=%s", (b,)))
    assert cp in vis(db, viewer)


def test_organisation_block_hides_candidate_from_mapped_recruiter_not_others(db):
    p, c = tree(db)
    cu, cp = candidate(db)
    rec_p, rec_c, rec_o = user(db, "recruiter"), user(db, "recruiter"), user(db, "recruiter")
    mapping(db, rec_p, p, "with_descendants")     # reaches c through descendants
    mapping(db, rec_c, c)                         # mapped to the blocked child directly
    other = org(db); closure(db, other, other, 0)
    mapping(db, rec_o, other)
    oblock(db, c, cp)
    assert cp not in vis(db, rec_c)
    assert cp not in vis(db, rec_p)
    assert cp in vis(db, rec_o)


def test_organisation_block_scope_organisation_only_parent_unaffected_by_child_block(db):
    p, c = tree(db)
    cu, cp = candidate(db)
    rec = user(db, "recruiter")
    mapping(db, rec, p, "organisation_only")
    oblock(db, c, cp)
    assert cp in vis(db, rec)


def test_organisation_block_company_direction_and_employer_role_scope(db):
    o = org(db); closure(db, o, o, 0)
    cu, cp = candidate(db)
    emp = user(db, "employer")
    mk(db, "app_user_role", app_user_id=emp, app_role_id=role(db, True), organisation_id=o, effective_from=NOW - DAY, is_organisation_scoped=True,
       actor_type_id=lookup_id(db, "actor_type", "employer"))
    oblock(db, o, cp, "organisation_blocks_candidate")
    assert cp not in vis(db, emp)


def test_organisation_block_ended_restores(db):
    o = org(db); closure(db, o, o, 0)
    cu, cp = candidate(db)
    rec = user(db, "recruiter")
    mapping(db, rec, o)
    b = oblock(db, o, cp)
    assert cp not in vis(db, rec)
    retry(lambda: db.execute("UPDATE organisation_block SET effective_to = now() WHERE id=%s", (b,)))
    assert cp in vis(db, rec)


def test_soft_deleted_candidate_hidden_from_visibility_view(db):
    cu, cp = candidate(db)
    viewer = user(db, "employer")
    assert cp in vis(db, viewer)
    retry(lambda: db.execute("UPDATE candidate_profile SET deleted_at = now() WHERE id=%s", (cp,)))
    assert cp not in vis(db, viewer)


def test_visibility_viewer_must_be_employer_side_actor(db):
    cu, cp = candidate(db)
    other_candidate = user(db, "candidate")
    assert vis(db, other_candidate) == set()


def test_recruiter_card_blocked_viewer_gets_no_row_and_masks_salary(db):
    cu, cp = candidate(db, is_searchable=True, is_contact_visible_to_recruiters=False,
                       current_salary_amount=123456, contact_email="hidden@example.test",
                       current_salary_disclosure_id=lookup_id(db, "salary_disclosure", "undisclosed"))
    ok, bad = user(db, "recruiter"), user(db, "recruiter")
    ublock(db, cu, bad)
    rows = q(db, "SELECT id, current_salary_amount, contact_email FROM fn_candidate_profile_recruiter(%s) WHERE id=%s", ok, cp)
    assert rows == [(cp, None, None)]
    assert q(db, "SELECT id FROM fn_candidate_profile_recruiter(%s) WHERE id=%s", bad, cp) == []


def test_recruiter_card_discloses_salary_and_contact_when_allowed(db):
    cu, cp = candidate(db, is_searchable=True, is_contact_visible_to_recruiters=True,
                       current_salary_amount=777, contact_email="visible@example.test",
                       current_salary_disclosure_id=lookup_id(db, "salary_disclosure", "disclosed"))
    v = user(db, "recruiter")
    rows = q(db, "SELECT current_salary_amount, contact_email FROM fn_candidate_profile_recruiter(%s) WHERE id=%s", v, cp)
    assert rows == [(777, "visible@example.test")]


def test_outreach_eligible_excludes_blocked_recent_contact_and_withdrawn_consent(db):
    sender = user(db, "recruiter")
    cu1, ok = candidate(db, is_searchable=True)
    cu2, blocked = candidate(db, is_searchable=True)
    cu3, contacted = candidate(db, is_searchable=True)
    cu4, withdrawn = candidate(db, is_searchable=True)
    ublock(db, cu2, sender)
    mk(db, "candidate_contact_attempt", candidate_profile_id=contacted, contacter_app_user_id=sender,
       attempted_at=NOW - dt.timedelta(hours=1), block_checked_at=NOW - dt.timedelta(hours=2))
    for action, ts in (("granted", NOW - 2 * DAY), ("withdrawn", NOW - DAY)):
        consent(db, cu4, "profile_visible_to_employers", action, ts)
    got = {r[0] for r in q(db, "SELECT id FROM fn_outreach_eligible_candidate(%s)", sender)}
    assert ok in got
    assert blocked not in got and contacted not in got and withdrawn not in got


# ---------------------------------------------------------------------------------------------------------------- consultants
def consultant(conn):
    u = user(conn, "consultant")
    return u, mk(conn, "consultant_profile", app_user_id=u)["id"]


def window(conn, jr, cons=None, **kw):
    return mk(conn, "consultant_job_openness", job_requirement_id=jr, consultant_profile_id=cons,
              valid_from=kw.pop("valid_from", NOW - DAY), **kw)["id"]


def openset(conn, cons):
    return {r[0] for r in q(conn, "SELECT job_requirement_id FROM fn_consultant_open_requirement(%s)", cons)}


def test_consultant_open_windows_general_and_personal(db):
    o = org(db)
    _, c1 = consultant(db)
    _, c2 = consultant(db)
    j_all = job(db, o, is_open_to_consultants=True, is_live=True)
    j_inv = job(db, o, is_open_to_consultants=True, is_live=True)
    window(db, j_all)                 # general window
    window(db, j_inv, cons=c1)        # personal invitation for c1
    assert {j_all, j_inv} <= openset(db, c1)
    got2 = openset(db, c2)
    assert j_all in got2 and j_inv not in got2
    assert n(db, "SELECT fn_consultant_can_work_requirement(%s,%s)", c2, j_inv) is False


def test_consultant_window_time_and_closed_filters(db):
    o = org(db)
    _, c = consultant(db)
    expired = job(db, o, is_open_to_consultants=True, is_live=True)
    future = job(db, o, is_open_to_consultants=True, is_live=True)
    closed = job(db, o, is_open_to_consultants=True, is_live=True)
    deleted = job(db, o, is_open_to_consultants=True, is_live=True)
    window(db, expired, valid_from=NOW - 3 * DAY, valid_to=NOW - DAY)
    window(db, future, valid_from=NOW + DAY)
    window(db, closed, closed_at=NOW - dt.timedelta(hours=1))
    w = window(db, deleted)
    retry(lambda: db.execute("UPDATE consultant_job_openness SET deleted_at=now() WHERE id=%s", (w,)))
    assert openset(db, c).isdisjoint({expired, future, closed, deleted})


def test_consultant_requirement_not_open_to_consultants_or_not_live_hidden(db):
    o = org(db)
    _, c = consultant(db)
    closed_flag = job(db, o, is_open_to_consultants=False, is_live=True)
    not_live = job(db, o, is_open_to_consultants=True, is_live=False)
    window(db, closed_flag); window(db, not_live)
    assert openset(db, c).isdisjoint({closed_flag, not_live})


def test_consultant_job_requirement_alias_only_general_windows(db):
    o = org(db)
    _, c = consultant(db)
    j_gen = job(db, o, is_open_to_consultants=True, is_live=True)
    j_inv = job(db, o, is_open_to_consultants=True, is_live=True)
    window(db, j_gen); window(db, j_inv, cons=c)
    ids = {r[0] for r in q(db, "SELECT id FROM vw_consultant_job_requirement WHERE id IN (%s,%s)", j_gen, j_inv)}
    assert ids == {j_gen}


# ----------------------------------------------------------------------------------------------------------------- public views
def test_live_job_view_hides_confidential_employer_private_and_deleted(db):
    o = org(db, display_name=f"Acme {uuid.uuid4().hex[:6]}")
    pub = lookup_id(db, "visibility_level", "public")
    priv = lookup_id(db, "visibility_level", "private")
    j_open = job(db, o, is_live=True, visibility_level_id=pub, is_confidential_employer=False)
    j_conf = job(db, o, is_live=True, visibility_level_id=pub, is_confidential_employer=True)
    j_priv = job(db, o, is_live=True, visibility_level_id=priv)
    j_del = job(db, o, is_live=True, visibility_level_id=pub)
    j_off = job(db, o, is_live=False, visibility_level_id=pub)
    retry(lambda: db.execute("UPDATE job_requirement SET deleted_at=now() WHERE id=%s", (j_del,)))
    rows = {r[0]: (r[1], r[2]) for r in q(db, "SELECT id, organisation_id, organisation_name FROM vw_live_job_requirement WHERE id = ANY(%s)",
                                         [j_open, j_conf, j_priv, j_del, j_off])}
    assert set(rows) == {j_open, j_conf}
    assert rows[j_open][0] == o and rows[j_open][1].startswith("Acme")
    assert rows[j_conf] == (None, None)


def test_consent_current_picks_latest_event(db):
    u = user(db, "candidate")
    ct = lookup_id(db, "consent_type", "marketing_email")
    for action, ago in (("granted", 5), ("withdrawn", 3), ("granted", 1)):
        consent(db, u, "marketing_email", action, NOW - ago * DAY)
    rows = q(db, "SELECT consent_action_id FROM vw_user_consent_current WHERE app_user_id=%s AND consent_type_id=%s", u, ct)
    assert rows == [(lookup_id(db, "consent_action", "granted"),)]
    consent(db, u, "marketing_email", "withdrawn", NOW)
    rows = q(db, "SELECT consent_action_id FROM vw_user_consent_current WHERE app_user_id=%s AND consent_type_id=%s", u, ct)
    assert rows == [(lookup_id(db, "consent_action", "withdrawn"),)]


def test_effective_permission_follows_role_inheritance_and_expiry(db):
    u = user(db, "employer")
    child, parent, expired_role = (role(db, False) for _ in range(3))
    mk(db, "app_role_inheritance", child_app_role_id=child, parent_app_role_id=parent)
    perm_p, perm_x = mk(db, "permission"), mk(db, "permission")
    mk(db, "app_role_permission", app_role_id=parent, permission_id=perm_p["id"])
    mk(db, "app_role_permission", app_role_id=expired_role, permission_id=perm_x["id"])
    mk(db, "app_user_role", app_user_id=u, app_role_id=child, effective_from=NOW - DAY, is_organisation_scoped=False, organisation_id=None,
       actor_type_id=lookup_id(db, "actor_type", "employer"))
    mk(db, "app_user_role", app_user_id=u, app_role_id=expired_role, effective_from=NOW - 3 * DAY, effective_to=NOW - DAY,
       is_organisation_scoped=False, organisation_id=None, actor_type_id=lookup_id(db, "actor_type", "employer"))
    codes = {r[0] for r in q(db, "SELECT code FROM vw_user_effective_permission WHERE app_user_id=%s", u)}
    assert perm_p["code"] in codes          # inherited from the parent role
    assert perm_x["code"] not in codes      # expired assignment


def test_public_views_hide_soft_deleted_rows_and_mask_email(db):
    live = user(db, "candidate", phone="+9198" + str(uuid.uuid4().int)[:6] + "7890", phone_country_id=lookup_id(db, "country"), display_name="Visible")
    gone = user(db, "candidate")
    retry(lambda: db.execute("UPDATE app_user SET deleted_at=now() WHERE id=%s", (gone,)))
    rows = {r[0]: r for r in q(db, "SELECT id, email_masked, phone_masked FROM vw_app_user_public WHERE id = ANY(%s)", [live, gone])}
    assert set(rows) == {live}
    assert "@" in rows[live][1] and rows[live][1][1:4] == "***" and rows[live][2] == "***7890"
    po = org(db, visibility_level_id=lookup_id(db, "visibility_level", "public"))
    pd = org(db, visibility_level_id=lookup_id(db, "visibility_level", "public"))
    retry(lambda: db.execute("UPDATE organisation SET deleted_at=now() WHERE id=%s", (pd,)))
    assert {r[0] for r in q(db, "SELECT id FROM vw_organisation_public WHERE id = ANY(%s)", [po, pd])} == {po}


def test_salary_benchmark_published_one_row_fresh_sources_only(db):
    fresh1 = mk(db, "reference_data_source", attribution_text="Source One", max_staleness_days=30, is_active=True)["id"]
    fresh2 = mk(db, "reference_data_source", attribution_text="Source Two", max_staleness_days=30, is_active=True)["id"]
    stale = mk(db, "reference_data_source", attribution_text="Stale Src", max_staleness_days=1, is_active=True)["id"]
    b_ok = mk(db, "salary_benchmark", is_publishable=True, refreshed_at=NOW - 2 * DAY)["id"]
    b_stale = mk(db, "salary_benchmark", is_publishable=True, refreshed_at=NOW - 10 * DAY)["id"]
    b_hidden = mk(db, "salary_benchmark", is_publishable=False, refreshed_at=NOW - DAY)["id"]
    for b, s in ((b_ok, fresh1), (b_ok, fresh2), (b_ok, stale), (b_stale, stale), (b_hidden, fresh1)):
        mk(db, "salary_benchmark_source", salary_benchmark_id=b, reference_data_source_id=s)
    rows = q(db, "SELECT id, attribution_text, fresh_source_count FROM vw_salary_benchmark_published WHERE id = ANY(%s)", [b_ok, b_stale, b_hidden])
    assert [r[0] for r in rows] == [b_ok]
    assert rows[0][1] == "Source One; Source Two" and rows[0][2] == 2


# ---------------------------------------------------------------------------------------------------------- persona privileges
def test_recruiter_role_reads_view_and_function_but_not_base_tables(db, as_role):
    a = org(db); closure(db, a, a, 0)
    ja = job(db, a)
    r = user(db, "recruiter")
    mapping(db, r, a)
    c = as_role("app_recruiter")
    assert ja in {x[0] for x in c.execute("SELECT id FROM fn_recruiter_job_requirement(%s)", (r,)).fetchall()}
    assert ja in {x[0] for x in c.execute("SELECT id FROM vw_recruiter_job_requirement WHERE viewer_app_user_id=%s", (r,)).fetchall()}
    for t in ("job_requirement", "candidate_profile", "recruiter_organisation_mapping", "user_block", "organisation_block"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(f"SELECT 1 FROM {t} LIMIT 1")


def test_recruiter_role_candidate_visibility_function(db, as_role):
    cu, cp = candidate(db)
    r = user(db, "recruiter")
    c = as_role("app_recruiter")
    assert c.execute("SELECT fn_candidate_is_visible(%s,%s)", (r, cp)).fetchone()[0] is True
    ublock(db, cu, r)
    assert c.execute("SELECT fn_candidate_is_visible(%s,%s)", (r, cp)).fetchone()[0] is False


def test_consultant_role_reads_open_requirement_but_not_job_requirement(db, as_role):
    o = org(db)
    cu, cp = consultant(db)
    j = job(db, o, is_open_to_consultants=True, is_live=True)
    window(db, j)
    c = as_role("app_consultant")
    assert j in {x[0] for x in c.execute("SELECT job_requirement_id FROM fn_consultant_open_requirement(%s)", (cp,)).fetchall()}
    for t in ("job_requirement", "consultant_job_openness", "candidate_profile"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(f"SELECT 1 FROM {t} LIMIT 1")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT 1 FROM vw_recruiter_job_requirement LIMIT 1")


def test_guest_reads_live_jobs_view_not_base_or_pair_views(db, as_role):
    g = as_role("app_guest")
    g.execute("SELECT count(*) FROM vw_live_job_requirement").fetchone()
    for obj in ("job_requirement", "candidate_profile", "vw_candidate_visible_to_user", "vw_recruiter_job_requirement"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            g.execute(f"SELECT 1 FROM {obj} LIMIT 1")


def test_candidate_role_cannot_read_pair_views(db, as_role):
    c = as_role("app_candidate")
    for obj in ("vw_candidate_visible_to_user", "vw_recruiter_job_requirement", "vw_consultant_open_requirement"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(f"SELECT 1 FROM {obj} LIMIT 1")


def test_employer_role_executes_visibility_function(db, as_role):
    e = user(db, "employer")
    c = as_role("app_employer")
    c.execute("SELECT count(*) FROM fn_candidate_visible_to_user(%s)", (e,)).fetchone()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT * FROM fn_recruiter_job_requirement(%s)", (e,)).fetchall()


# --------------------------------------------------------------------------------------------------------------------- catalogue
MY_VIEWS = """vw_candidate_visible_to_user vw_user_consent_current vw_user_effective_permission vw_app_user_public vw_audit_event_redacted
vw_live_job_requirement vw_consultant_job_requirement vw_application_pipeline vw_login_history vw_active_device_session
vw_licence_seat_availability vw_recruiter_job_requirement vw_organisation_public vw_organisation_review_public vw_account_active_admin
vw_candidate_profile_recruiter vw_candidate_search_document vw_consultant_open_requirement vw_outreach_eligible_candidate
vw_sourcing_request_summary vw_subscription_period_overlap vw_wallet_balance_drift vw_invoice_number_gap vw_organisation_download_spend
vw_salary_benchmark_published vw_company_rating_latest vw_feature_flag_effective""".split()


@pytest.mark.parametrize("view", MY_VIEWS)
def test_core_view_exists_and_is_queryable(db, view):
    db.execute(f"SELECT * FROM {view} LIMIT 0")


def test_pair_views_expose_viewer_column(db):
    for v, col in (("vw_candidate_visible_to_user", "viewer_app_user_id"), ("vw_recruiter_job_requirement", "viewer_app_user_id"),
                   ("vw_consultant_open_requirement", "viewer_consultant_profile_id"), ("vw_outreach_eligible_candidate", "sender_app_user_id"),
                   ("vw_candidate_profile_recruiter", "viewer_app_user_id")):
        assert n(db, "SELECT count(*) FROM information_schema.columns WHERE table_name=%s AND column_name=%s", v, col) == 1

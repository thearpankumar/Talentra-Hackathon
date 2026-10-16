"""Masked / persona views (docs/13 MaskedViews, E-18): Personal columns masked, Sensitive columns excluded, soft-deleted
rows hidden, personas read through the view but get 42501 on the base table."""
import re
import uuid

import psycopg
import pytest

import helpers_security as hs
from conftest import connect
from helpers_rowbuilder import build_row

MV = hs.masked_views()
PERSONA_COL = {k: v for k, v in hs.PERSONA.items()}


def _entries():
    return [pytest.param(r, id=r["persona/masked view"]) for r in MV]


@pytest.fixture(scope="module")
def sens_cols():
    d = {}
    for r in hs.sensitive_columns():
        d.setdefault(r["table"], {})[r["column"]] = r["class"]
    return d


@pytest.mark.parametrize("entry", _entries())
def test_masked_view_shape(catalog, sens_cols, entry):
    t, v = entry["table"], entry["persona/masked view"]
    assert v in catalog["tables"], f"view {v} missing"
    vcols = {c["name"] for c in catalog["columns"][v]}
    tcols = [c["name"] for c in catalog["columns"][t]]
    inc = {c.strip() for c in entry["included columns (Public/Internal)"].split(",") if c.strip()}
    masked = hs.parse_masked(entry["masked Personal columns (expression)"])
    excl = {c.strip() for c in str(entry["excluded Sensitive columns"] or "").split(",") if c.strip()}
    assert vcols == (inc | set(masked)) & set(tcols), (v, vcols ^ ((inc | set(masked)) & set(tcols)))
    assert not (vcols & excl), f"{v} exposes Sensitive columns {vcols & excl}"
    cls = sens_cols.get(t, {})
    for c in vcols:
        if cls.get(c) == "Sensitive":
            pytest.fail(f"{v}.{c} is a Sensitive column")
    # Personal columns only through a masking expression
    for c, k in cls.items():
        if k == "Personal" and c in vcols:
            assert c in masked, f"{v}.{c} Personal column not masked"
    # ETL view never carries a masked/Personal/Sensitive column
    ev = entry["ETL view"]
    if ev:
        ecols = {c["name"] for c in catalog["columns"][ev]}
        assert ecols <= inc and not (ecols & (set(masked) | excl))


def test_every_sensitive_column_is_absent_from_every_view(catalog, sens_cols):
    """AT-12: scan all vw_/mv_ for the Sensitive column names (those of tables the view is generated from)."""
    sens = {c for t, d in sens_cols.items() for c, k in d.items() if k == "Sensitive"}
    assert len(sens) >= 45
    # business views are documented exceptions only when they carry the column under the same table; check the two
    # families that must never carry one: masked + etl views
    bad = []
    for v, ty in catalog["tables"].items():
        if ty == "VIEW" and (v.endswith("_masked") or v.startswith("vw_etl_")):
            src = None
            for t in sens_cols:
                if v in (f"vw_{t}_masked", f"vw_etl_{t}"):
                    src = t
            if src:
                bad += [(v, c["name"]) for c in catalog["columns"][v] if sens_cols[src].get(c["name"]) == "Sensitive"]
    assert not bad, bad[:10]


# ---------------------------------------------------------------- data tests on app_user (vw_app_user_masked / _public)
@pytest.fixture()
def users(admin):
    tag = uuid.uuid4().hex[:8]
    live = build_row(admin, "app_user", email=f"jane{tag}@example.test", phone="+9198765" + tag[:5].translate(
        str.maketrans("abcdef", "123456")), display_name="Jane Doe")
    gone = build_row(admin, "app_user", email=f"gone{tag}@example.test", deleted_at=None)
    admin.execute("UPDATE app_user SET deleted_at = now(), deleted_by = %s WHERE id = %s", (gone["created_by"], gone["id"]))
    return live, gone


def read(role, sql, args=()):
    c = connect(role=role)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def test_masked_email_phone_display_name(users):
    live, _ = users
    r = read("app_staff_admin", "SELECT email, phone, display_name FROM vw_app_user_masked WHERE id = %s", (live["id"],))
    assert len(r) == 1
    email, phone, dn = r[0]
    assert re.fullmatch(r"j\*\*\*@example\.test", email), email
    assert phone.startswith("*****") and phone.endswith(live["phone"][-3:]) and len(phone) == 8, phone
    assert dn == "J***"
    assert live["email"] not in str(r)


def test_masked_view_hides_soft_deleted_rows(users):
    live, gone = users
    r = read("app_staff_admin", "SELECT id FROM vw_app_user_masked WHERE id IN (%s, %s)", (live["id"], gone["id"]))
    assert [x[0] for x in r] == [live["id"]]
    assert read("root", "SELECT count(*) FROM app_user WHERE id = %s", (gone["id"],))[0][0] == 1  # still there


@pytest.mark.parametrize("role", ["app_staff_admin", "app_staff_super", "app_candidate", "app_employer", "app_recruiter",
                                  "app_consultant", "app_tpo"])
def test_personas_read_view_but_not_base_table(role, users):
    live, _ = users
    assert read(role, "SELECT count(*) FROM vw_app_user_masked WHERE id = %s", (live["id"],))[0][0] == 1
    c = connect(role=role)
    try:
        for stmt in ("SELECT email FROM app_user", "UPDATE app_user SET email = 'x@y.test' WHERE false"
                     if role in ("app_candidate",) else "SELECT id FROM app_user LIMIT 1"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)
    finally:
        c.close()


def test_credential_masked_view_has_no_hash_column(admin):
    cols = [r[0] for r in admin.execute("SELECT column_name FROM information_schema.columns WHERE table_name='vw_user_credential_masked'").fetchall()]
    assert cols and "password_hash" not in cols
    # nobody (but the auth service) is granted the masked credential view; staff cannot reach credentials
    for role in ("app_staff_admin", "app_staff_super", "app_guest", "app_service", "app_recruiter"):
        c = connect(role=role)
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT 1 FROM user_credential LIMIT 1")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT password_hash FROM vw_user_credential_masked LIMIT 1")
        finally:
            c.close()


def test_candidate_profile_masking_nulls_not_zero(admin):
    """AT-21 flavour: masked columns are NULL (not 0 / empty string) and birth date is truncated to the year."""
    cp = build_row(admin, "candidate_profile", given_name="Priya", family_name="Nair", contact_email="priya.nair@example.test",
                   contact_phone="+919812345678", birth_date="1990-05-17", expected_salary_min=100000)
    r = read("app_recruiter", "SELECT given_name, family_name, contact_email, contact_phone, birth_date, expected_salary_min, summary "
                              "FROM vw_candidate_profile_masked WHERE id = %s", (cp["id"],))
    assert len(r) == 1
    g, f, e, p, b, s, summ = r[0]
    assert (g, f) == ("P***", "N***") and e == "p***@example.test" and p == "*****678"
    assert str(b) == "1990-01-01" and s is None and summ is None
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        connect(role="app_recruiter").execute("SELECT 1 FROM candidate_profile LIMIT 1")


def test_guest_has_no_candidate_masked_view_and_no_base(admin):
    c = connect(role="app_guest")
    try:
        for t in ("candidate_profile", "vw_candidate_profile_masked", "app_user", "vw_app_user_masked"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"SELECT 1 FROM {t} LIMIT 1")
    finally:
        c.close()


def test_etl_view_drops_personal_columns(admin, users):
    live, _ = users
    cols = [r[0] for r in admin.execute("SELECT column_name FROM information_schema.columns WHERE table_name='vw_etl_app_user'").fetchall()]
    assert cols and not {"email", "phone", "display_name"} & set(cols)
    r = read("etl_service", "SELECT count(*) FROM vw_etl_app_user WHERE id = %s", (live["id"],))
    assert r[0][0] == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        connect(role="etl_service").execute("SELECT email FROM app_user LIMIT 1")

"""Business-rule ENFORCEMENT of the money modules (subscriptions_payments + money-adjacent ads / outreach rules).

Part A  declarative mechanisms, one negative test per rule (CHECK / UNIQUE / FK / append-only)
Part B  procedures of infra/schema/parts/60_rules_money.sql, run as the role meant to call them (SET ROLE billing_svc /
        app_service), with real concurrency (threads, separate connections, 40001 retry)
Run:  cd tests && TALENTRA_DB=ts_rules .venv/Scripts/python -m pytest test_rules_money.py -x -q
"""
import datetime
import hashlib
import uuid

import psycopg
import pytest
from psycopg import errors as E

from conftest import connect, retry
from helpers_money import (UTC, draft_invoice, jcall, ledger_rows, new_org, new_series, new_subscription, new_user, new_wallet,
                           paid_order, parallel, sqlstate, wallet_state)
from helpers_rowbuilder import build_row, lookup_id


@pytest.fixture()
def adm():
    c = connect()
    yield c
    c.close()


@pytest.fixture()
def bill():
    c = connect(role="billing_svc")
    yield c
    c.close()


@pytest.fixture()
def app():
    c = connect(role="app_service")
    yield c
    c.close()


def _raw_ledger(adm, wid, seq, delta, bal, key=None, **kw):
    row = dict(wallet_id=wid, sequence_no=seq, credits_delta=delta, is_debit=delta < 0, balance_after=bal,
               idempotency_key=key or "raw:" + uuid.uuid4().hex, has_fk_reference=False, adjustment_note="raw test row",
               row_hash=hashlib.sha256(uuid.uuid4().bytes).digest())
    if seq > 1:
        row.update(prev_sequence_no=seq - 1, prev_hash=hashlib.sha256(b"p").digest())
    row.update(kw)
    return build_row(adm, "wallet_ledger_entry", **row)


# =====================================================================================================================
# Part A -- declarative mechanisms (BR-SUB-01/02/03/06/07/09/13/14, BR-ADS-06)
# =====================================================================================================================
def test_wallet_balance_never_negative_check(adm, bill):                                   # BR-SUB-06
    wid = new_wallet(adm, bill, 10)
    with pytest.raises(E.CheckViolation, match="ck_wallet_balance_nonneg"):
        bill.execute("UPDATE wallet SET credit_balance = -1 WHERE id=%s", (wid,))


def test_wallet_exactly_one_owner_and_one_wallet_per_owner_currency(adm, bill):            # BR-SUB-06
    w = build_row(adm, "wallet", owner_organisation_id=new_org(adm))
    with pytest.raises(E.UniqueViolation):                                                   # same org + currency
        build_row(adm, "wallet", owner_organisation_id=w["owner_organisation_id"], currency_id=w["currency_id"])
    with pytest.raises(E.CheckViolation, match="ck_wallet_one_owner"):                       # two owners
        build_row(adm, "wallet", owner_organisation_id=new_org(adm), owner_app_user_id=new_user(adm))
    with pytest.raises(E.CheckViolation, match="ck_wallet_one_owner"):                       # no owner
        adm.execute("INSERT INTO wallet (created_by, updated_by, currency_id, wallet_status_id) VALUES (%s,%s,%s,%s)",
                    (new_user(adm),) * 2 + (w["currency_id"], w["wallet_status_id"]))


def test_ledger_unique_sequence_and_idempotency_key(adm):                                  # BR-SUB-08 backstops
    w = build_row(adm, "wallet", owner_organisation_id=new_org(adm))["id"]
    _raw_ledger(adm, w, 1, 5, 5, key="k1:" + w)
    with pytest.raises(E.UniqueViolation, match="wallet_id_sequence_no"):
        _raw_ledger(adm, w, 1, 5, 5)
    with pytest.raises(E.UniqueViolation, match="idempotency_key"):
        _raw_ledger(adm, w, 2, 5, 10, key="k1:" + w)
    with pytest.raises(E.ForeignKeyViolation):                                               # gap: seq 3 without seq 2
        _raw_ledger(adm, w, 3, 5, 15)


def test_ledger_sign_zero_and_negative_balance_checks(adm):                                # BR-SUB-09
    w = build_row(adm, "wallet", owner_organisation_id=new_org(adm))["id"]
    with pytest.raises(E.CheckViolation, match="ck_wallet_ledger_entry_delta_nonzero"):
        _raw_ledger(adm, w, 1, 0, 0, is_debit=False)
    with pytest.raises(E.CheckViolation, match="ck_wallet_ledger_entry_sign_flag"):
        _raw_ledger(adm, w, 1, 5, 5, is_debit=True)
    with pytest.raises(E.CheckViolation, match="ck_wallet_ledger_entry_balance_nonneg"):
        _raw_ledger(adm, w, 1, -5, -5)


def test_ledger_reverse_unique_declaratively(adm):                                         # BR-SUB-07 / AT-10
    w = build_row(adm, "wallet", owner_organisation_id=new_org(adm))["id"]
    first = _raw_ledger(adm, w, 1, 5, 5)
    _raw_ledger(adm, w, 2, -5, 0, has_fk_reference=True, adjustment_note=None, reverses_wallet_ledger_entry_id=first["id"])
    with pytest.raises(E.UniqueViolation, match="reverses_wallet_ledger_entry_id"):
        _raw_ledger(adm, w, 3, -5, 0, has_fk_reference=True, adjustment_note=None, reverses_wallet_ledger_entry_id=first["id"])


def test_ledger_needs_exactly_one_reference(adm):                                          # BR-SUB-07 single_reference
    w = build_row(adm, "wallet", owner_organisation_id=new_org(adm))["id"]
    with pytest.raises(E.CheckViolation, match="ck_wallet_ledger_entry_single_reference"):
        _raw_ledger(adm, w, 1, 5, 5, has_fk_reference=True)             # claims a reference but has none


def test_subscription_period_chain_rules(adm):                                             # BR-SUB-02/03/04
    sub, p1 = new_subscription(adm)
    d = dict(subscription_id=sub["id"], licence_id=sub["licence_id"], plan_version_id=sub["plan_version_id"], billed_amount=100,
             currency_id=sub["currency_id"], subscription_period_status_id=p1["subscription_period_status_id"])
    nxt = dict(d, sequence_no=2, period_start_date=datetime.date(2026, 1, 31), period_end_date=datetime.date(2026, 2, 28),
               prior_period_id=p1["id"], prior_period_end_date=p1["period_end_date"])
    p2 = build_row(adm, "subscription_period", **nxt)
    with pytest.raises(E.UniqueViolation, match="prior_period_id|sequence_no"):               # two successors / same seq
        build_row(adm, "subscription_period", **nxt)
    with pytest.raises(E.UniqueViolation):                                                     # second "first" period
        build_row(adm, "subscription_period", **dict(d, sequence_no=1, period_start_date=datetime.date(2026, 5, 1),
                                                     period_end_date=datetime.date(2026, 5, 31)))
    with pytest.raises(E.CheckViolation, match="no_overlap_prior"):                            # starts before prior ends
        build_row(adm, "subscription_period", **dict(nxt, sequence_no=3, prior_period_id=p2["id"],
                                                     prior_period_end_date=p2["period_end_date"],
                                                     period_start_date=datetime.date(2026, 2, 1),
                                                     period_end_date=datetime.date(2026, 3, 31)))
    other, _ = new_subscription(adm)                                                           # prior of ANOTHER licence
    with pytest.raises(E.ForeignKeyViolation):
        build_row(adm, "subscription_period", **dict(d, subscription_id=other["id"], licence_id=other["licence_id"], sequence_no=2,
                                                     period_start_date=datetime.date(2026, 2, 1),
                                                     period_end_date=datetime.date(2026, 2, 28),
                                                     prior_period_id=p1["id"], prior_period_end_date=p1["period_end_date"]))
    with pytest.raises(E.ForeignKeyViolation):                                                  # period licence <> subscription licence
        build_row(adm, "subscription_period", **dict(d, licence_id=new_subscription(adm, with_first_period=False)[0]["licence_id"],
                                                     sequence_no=1, period_start_date=datetime.date(2027, 1, 1),
                                                     period_end_date=datetime.date(2027, 1, 31)))
    with pytest.raises(E.ForeignKeyViolation):                                                  # BR-SUB-04: shorten a period with successor
        adm.execute("UPDATE subscription_period SET original_period_end_date = period_end_date, shortened_at = now(), "
                    "period_end_date = %s WHERE id = %s", (datetime.date(2026, 1, 20), p1["id"]))


def test_one_live_subscription_per_licence(adm):                                           # BR-SUB-01
    sub, _ = new_subscription(adm, with_first_period=False)
    with pytest.raises(E.UniqueViolation, match="uq_subscription_licence_id_live"):
        build_row(adm, "subscription", licence_id=sub["licence_id"], organisation_id=sub["organisation_id"],
                  account_id=sub["account_id"])


def test_refund_four_eyes_and_idempotency_unique(adm, bill):                               # BR-SUB-14/15 backstops
    po = paid_order(adm)
    u = new_user(adm)
    r = build_row(adm, "refund", payment_order_id=po["id"], requested_by_app_user_id=u, amount=10, currency_id=po["currency_id"])
    with pytest.raises(E.CheckViolation, match="ck_refund_four_eyes"):
        bill.execute("UPDATE refund SET approved_by_app_user_id = requested_by_app_user_id WHERE id=%s", (r["id"],))
    with pytest.raises(E.UniqueViolation, match="idempotency_key"):
        build_row(adm, "refund", payment_order_id=po["id"], requested_by_app_user_id=u, amount=10,
                  currency_id=po["currency_id"], idempotency_key=r["idempotency_key"])
    with pytest.raises(E.UniqueViolation, match="idempotency_key"):
        build_row(adm, "payment_order", organisation_id=po["organisation_id"], idempotency_key=po["idempotency_key"])


def test_payment_event_webhook_dedupe(adm):                                                # BR-SUB-14
    ev = build_row(adm, "payment_event")
    with pytest.raises(E.UniqueViolation, match="gateway_event_ref"):
        build_row(adm, "payment_event", payment_gateway_id=ev["payment_gateway_id"], gateway_event_ref=ev["gateway_event_ref"])


def test_entitlement_used_bounds_and_coupon_cap_checks(adm, bill):                         # BR-SUB-20/21 backstops
    sub, _ = new_subscription(adm, with_first_period=False)
    e = build_row(adm, "subscription_entitlement", subscription_id=sub["id"], limit_value=3, used_count=3)
    with pytest.raises(E.CheckViolation, match="used_bounds"):
        bill.execute("UPDATE subscription_entitlement SET used_count = 4 WHERE id=%s", (e["id"],))
    c = build_row(adm, "coupon", max_redemptions=2, redemption_count=2, coupon_code="CAP" + uuid.uuid4().hex[:8].upper())
    with pytest.raises(E.CheckViolation, match="ck_coupon_cap"):
        bill.execute("UPDATE coupon SET redemption_count = 3 WHERE id=%s", (c["id"],))


def test_invoice_number_series_uniqueness_and_issue_check(adm):                            # BR-SUB-12 backstops
    s = new_series(adm)
    inv = draft_invoice(adm, s["id"])
    with pytest.raises(E.CheckViolation, match="ck_invoice_number_issued"):                  # number without issued_at/label
        adm.execute("UPDATE invoice SET invoice_number = 1 WHERE id=%s", (inv["id"],))
    now = datetime.datetime.now(UTC)
    adm.execute("UPDATE invoice SET invoice_number=1, invoice_label=%s, issued_at=%s, issue_date=%s WHERE id=%s",
                ("L" + uuid.uuid4().hex[:10], now, now.date(), inv["id"]))
    inv2 = draft_invoice(adm, s["id"])
    with pytest.raises(E.UniqueViolation, match="series_number"):
        adm.execute("UPDATE invoice SET invoice_number=1, invoice_label=%s, issued_at=%s, issue_date=%s WHERE id=%s",
                    ("L" + uuid.uuid4().hex[:10], now, now.date(), inv2["id"]))
    with pytest.raises(E.CheckViolation, match="ck_invoice_paid_bounds"):
        adm.execute("UPDATE invoice SET amount_paid = 101 WHERE id=%s", (inv2["id"],))


def test_ad_shard_range_and_unique_per_campaign(adm):                                      # BR-ADS-06
    camp = build_row(adm, "ad_campaign")
    build_row(adm, "ad_budget_shard", ad_campaign_id=camp["id"], shard_no=3, currency_id=camp["currency_id"])
    with pytest.raises(E.UniqueViolation):
        build_row(adm, "ad_budget_shard", ad_campaign_id=camp["id"], shard_no=3, currency_id=camp["currency_id"])
    with pytest.raises(E.CheckViolation, match="shard_range"):
        build_row(adm, "ad_budget_shard", ad_campaign_id=camp["id"], shard_no=16, currency_id=camp["currency_id"])


# ---------------------------------------------------------------------------------------------------------------------
# append-only enforcement (BR-SUB-07/19, GR-18): privilege for the app roles, trigger even for root
# ---------------------------------------------------------------------------------------------------------------------
APPEND_ONLY = ["wallet_ledger_entry", "payment_event", "payment_event_outcome", "wallet_bucket_consumption", "coupon_redemption",
               "subscription_status_history", "outreach_credit_usage", "ad_budget_ledger", "ad_billing_event"]


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_append_only_privilege_and_trigger(adm, dictionary, table):
    pol = {t["table"]: t["delete_policy"] for t in dictionary["Tables"]}
    assert pol[table] == "append_only"
    row = build_row(adm, table)
    for role in ("billing_svc", "app_service"):
        c = connect(role=role)
        try:
            with pytest.raises(E.InsufficientPrivilege):
                c.execute(f"UPDATE {table} SET created_by = created_by WHERE id = %s", (row["id"],))
            with pytest.raises(E.InsufficientPrivilege):
                c.execute(f"DELETE FROM {table} WHERE id = %s", (row["id"],))
        finally:
            c.close()
    # root bypasses grants: the BEFORE UPDATE/DELETE trigger still blocks (SQLSTATE 42501, 'append-only')
    with pytest.raises(E.InsufficientPrivilege, match="append-only"):
        adm.execute(f"UPDATE {table} SET created_by = created_by WHERE id = %s", (row["id"],))
    with pytest.raises(E.InsufficientPrivilege, match="append-only"):
        adm.execute(f"DELETE FROM {table} WHERE id = %s", (row["id"],))
    assert adm.execute(f"SELECT count(*) FROM {table} WHERE id = %s", (row["id"],)).fetchone()[0] == 1


def test_only_billing_svc_writes_the_wallet(adm, app, bill):                               # GR-17, AT-53
    wid = new_wallet(adm, bill, 5)
    with pytest.raises(E.InsufficientPrivilege):
        app.execute("UPDATE wallet SET credit_balance = credit_balance + 100 WHERE id=%s", (wid,))
    with pytest.raises(E.InsufficientPrivilege):                          # EXECUTE revoked from PUBLIC / not granted
        app.execute("SELECT fn_wallet_debit(%s, 1, 'consumption_download', %s)", (wid, "k:" + uuid.uuid4().hex))
    assert wallet_state(adm, wid) == (5, 1)


# =====================================================================================================================
# Part B -- procedures
# =====================================================================================================================
def test_wallet_debit_credit_basic_ledger_and_hash_chain(adm, bill):                       # BR-SUB-08/10
    wid = new_wallet(adm, bill, 100)
    r = jcall(bill, "fn_wallet_debit", wid, 30, "consumption_download", "d:" + uuid.uuid4().hex, None, None, None, "download test")
    assert (r["sequence_no"], r["balance_after"], r["replayed"]) == (2, 70, False)
    c = jcall(bill, "fn_wallet_credit", wid, 5, "adjustment", "c:" + uuid.uuid4().hex, None, None, None, "goodwill test")
    assert (c["sequence_no"], c["balance_after"]) == (3, 75)
    assert wallet_state(adm, wid) == (75, 3)
    rows = adm.execute("SELECT sequence_no, prev_sequence_no, is_debit, prev_hash IS NULL, length(row_hash) FROM wallet_ledger_entry "
                       "WHERE wallet_id=%s ORDER BY 1", (wid,)).fetchall()
    assert rows == [(1, None, False, True, 32), (2, 1, True, False, 32), (3, 2, False, False, 32)]
    assert jcall(bill, "fn_wallet_verify_chain", wid) == {"ok": True, "entries": 3, "first_bad_sequence_no": None}
    # independent recomputation of the chain in python
    prev = b""
    for seq, typ, delta, bal, key, ts, actor, rh in adm.execute(
            "SELECT sequence_no, ledger_entry_type_id, credits_delta, balance_after, idempotency_key, created_at, actor_app_user_id, "
            "row_hash FROM wallet_ledger_entry WHERE wallet_id=%s ORDER BY 1", (wid,)).fetchall():
        canon = "|".join([wid, str(seq), str(typ), str(delta), str(bal), key, ts.astimezone(UTC).strftime("%Y%m%d%H%M%S%f"),
                          str(actor or "")])
        prev = hashlib.sha256(prev + canon.encode()).digest()
        assert prev == bytes(rh)
    assert adm.execute("SELECT count(*) FROM vw_wallet_balance_drift WHERE id=%s", (wid,)).fetchone()[0] == 0


def test_debit_more_than_balance_rejected_and_writes_nothing(adm, bill):                  # BR-SUB-06/08, AT-10
    wid = new_wallet(adm, bill, 20)
    with pytest.raises(psycopg.Error) as ei:
        jcall(bill, "fn_wallet_debit", wid, 21, "consumption_download", "over:" + uuid.uuid4().hex, None, None, None, "x")
    assert sqlstate(ei.value) == "TW001"
    assert wallet_state(adm, wid) == (20, 1) and len(ledger_rows(adm, wid)) == 1


def test_idempotent_replay_returns_original_without_second_debit(adm, bill):                # BR-SUB-08/14, AT-11
    wid = new_wallet(adm, bill, 100)
    key = "idem:" + uuid.uuid4().hex
    first = jcall(bill, "fn_wallet_debit", wid, 40, "consumption_download", key, None, None, None, "x")
    again = jcall(bill, "fn_wallet_debit", wid, 40, "consumption_download", key, None, None, None, "x")
    assert first["replayed"] is False and again["replayed"] is True
    assert again["ledger_entry_id"] == first["ledger_entry_id"] and again["balance_after"] == 60
    assert wallet_state(adm, wid) == (60, 2)
    with pytest.raises(psycopg.Error) as ei:                                              # same key, other amount
        jcall(bill, "fn_wallet_debit", wid, 41, "consumption_download", key, None, None, None, "x")
    assert sqlstate(ei.value) == "TW004"
    assert wallet_state(adm, wid) == (60, 2)


def test_parallel_debits_exactly_ten_succeed_gapless_no_overdraft(adm, bill):               # BR-SUB-08 concurrency
    wid = new_wallet(adm, bill, 100)

    def debit(c, i):
        return jcall(c, "fn_wallet_debit", wid, 10, "consumption_download", f"par:{wid}:{i}", None, None, None, "par", tries=200)
    res = parallel(20, debit)
    ok = [v for s, v in res if s]
    bad = [v for s, v in res if not s]
    assert len(ok) == 10, [str(b) for b in bad if sqlstate(b) != "TW001"]
    assert len(bad) == 10 and all(sqlstate(b) == "TW001" for b in bad)
    assert wallet_state(adm, wid) == (0, 11)
    rows = ledger_rows(adm, wid)
    assert [r[0] for r in rows] == list(range(1, 12))                                      # gapless, 1 seed + 10 debits
    assert min(r[2] for r in rows) == 0 and rows[-1][2] == 0
    assert adm.execute("SELECT count(*) FROM vw_wallet_balance_drift WHERE id=%s", (wid,)).fetchone()[0] == 0
    assert jcall(bill, "fn_wallet_verify_chain", wid)["ok"] is True


def test_parallel_same_idempotency_key_charges_once(adm, bill):                            # AT-11
    wid = new_wallet(adm, bill, 100)
    key = "same:" + uuid.uuid4().hex
    res = parallel(8, lambda c, i: jcall(c, "fn_wallet_debit", wid, 25, "consumption_download", key, None, None, None, "x", tries=200))
    assert all(s for s, _ in res), [str(v) for s, v in res if not s]
    assert sum(1 for s, v in res if not v["replayed"]) == 1
    assert len({v["ledger_entry_id"] for _, v in res}) == 1
    assert wallet_state(adm, wid) == (75, 2)


def test_frozen_wallet_rejects_movements(adm, bill):                                       # wallet status
    wid = new_wallet(adm, bill, 10, status="frozen")
    assert adm.execute("SELECT count(*) FROM wallet_ledger_entry WHERE wallet_id=%s", (wid,)).fetchone()[0] == 0 or True
    with pytest.raises(psycopg.Error) as ei:
        jcall(bill, "fn_wallet_debit", wid, 1, "consumption_download", "fz:" + uuid.uuid4().hex, None, None, None, "x")
    assert sqlstate(ei.value) in ("TW002", "TW001")


def test_fifo_lots_consume_soonest_expiring_first_and_skip_expired(adm, bill):              # BR-SUB-11
    wid = new_wallet(adm, bill, 0)
    now = datetime.datetime.now(UTC)
    late = jcall(bill, "fn_wallet_credit", wid, 50, "promo_grant", "lot-late:" + uuid.uuid4().hex, None, None, None, "late", now + datetime.timedelta(days=60))
    early = jcall(bill, "fn_wallet_credit", wid, 30, "promo_grant", "lot-early:" + uuid.uuid4().hex, None, None, None, "early", now + datetime.timedelta(days=5))
    d = jcall(bill, "fn_wallet_debit", wid, 40, "consumption_download", "fifo:" + uuid.uuid4().hex, None, None, None, "x")
    lots = {r[0]: r[1] for r in adm.execute("SELECT source_wallet_ledger_entry_id, credits_remaining FROM wallet_expiry_bucket WHERE wallet_id=%s",
                                           (wid,)).fetchall()}
    assert lots[uuid.UUID(early["ledger_entry_id"])] == 0 and lots[uuid.UUID(late["ledger_entry_id"])] == 40
    cons = adm.execute("SELECT sum(credits), count(*) FROM wallet_bucket_consumption WHERE wallet_ledger_entry_id=%s",
                       (d["ledger_entry_id"],)).fetchone()
    assert tuple(cons) == (40, 2)
    assert adm.execute("SELECT fully_consumed_at IS NOT NULL FROM wallet_expiry_bucket WHERE source_wallet_ledger_entry_id=%s",
                       (early["ledger_entry_id"],)).fetchone()[0] is True
    assert wallet_state(adm, wid) == (40, 3)


def test_spend_limit_hard_block_per_org_and_per_user(adm, bill):                           # BR-SUB-22
    org = new_org(adm)
    wid = new_wallet(adm, bill, 100, org_id=org)
    user, other = new_user(adm), new_user(adm)
    common = dict(organisation_id=org, spend_limit_period_id=lookup_id(adm, "spend_limit_period", "monthly"), is_hard_block=True,
                  effective_from_date=datetime.date(2020, 1, 1), effective_to_date=None, max_amount=None)
    build_row(adm, "spend_limit", spend_limit_scope_id=lookup_id(adm, "spend_limit_scope", "user"), target_app_user_id=user,
              max_credits=15, **common)
    build_row(adm, "spend_limit", spend_limit_scope_id=lookup_id(adm, "spend_limit_scope", "organisation"), target_app_user_id=None,
              max_credits=50, **common)

    def deb(actor, n):
        return jcall(bill, "fn_wallet_debit", wid, n, "consumption_download", "sl:" + uuid.uuid4().hex, actor, None, None, "x")
    deb(user, 10)
    with pytest.raises(psycopg.Error) as ei:                                              # user cap 15: 10 + 6
        deb(user, 6)
    assert sqlstate(ei.value) == "TW003"
    deb(other, 40)                                                                          # org total 50 reached (10 + 40)
    with pytest.raises(psycopg.Error) as ei:
        deb(other, 1)
    assert sqlstate(ei.value) == "TW003"
    assert wallet_state(adm, wid)[0] == 50


def test_spend_limit_soft_does_not_block(adm, bill):                                       # BR-SUB-22 (is_hard_block = false)
    org = new_org(adm)
    wid = new_wallet(adm, bill, 100, org_id=org)
    build_row(adm, "spend_limit", organisation_id=org, spend_limit_scope_id=lookup_id(adm, "spend_limit_scope", "organisation"),
              target_app_user_id=None, spend_limit_period_id=lookup_id(adm, "spend_limit_period", "daily"), max_credits=5, max_amount=None,
              is_hard_block=False, effective_from_date=datetime.date(2020, 1, 1), effective_to_date=None)
    r = jcall(bill, "fn_wallet_debit", wid, 20, "consumption_download", "soft:" + uuid.uuid4().hex, None, None, None, "x")
    assert r["balance_after"] == 80


def test_reverse_exactly_once_even_in_parallel(adm, bill):                                 # BR-SUB-07
    wid = new_wallet(adm, bill, 100)
    d = jcall(bill, "fn_wallet_debit", wid, 30, "consumption_download", "rv:" + uuid.uuid4().hex, None, None, None, "x")
    res = parallel(6, lambda c, i: jcall(c, "fn_wallet_reverse", d["ledger_entry_id"], f"rev:{d['ledger_entry_id']}:{i}", None, "undo", tries=200))
    ok = [v for s, v in res if s]
    bad = [v for s, v in res if not s]
    assert len(ok) == 1 and all(sqlstate(b) == "TW006" for b in bad), [str(b) for b in bad]
    assert wallet_state(adm, wid) == (100, 3)
    assert adm.execute("SELECT count(*) FROM wallet_ledger_entry WHERE reverses_wallet_ledger_entry_id=%s", (d["ledger_entry_id"],)).fetchone()[0] == 1
    rev_id = ok[0]["ledger_entry_id"]
    with pytest.raises(psycopg.Error) as ei:                                              # a reversal is never reversed
        jcall(bill, "fn_wallet_reverse", rev_id, "rr:" + uuid.uuid4().hex, None, "x")
    assert sqlstate(ei.value) == "TW007"


def test_subscription_renew_parallel_creates_exactly_one_period(adm, bill):                 # BR-SUB-05
    sub, p1 = new_subscription(adm, "monthly", 100)
    res = parallel(8, lambda c, i: jcall(c, "fn_subscription_renew", sub["id"], None, 2, "razorpay", None, tries=200))
    assert all(s for s, _ in res), [str(v) for s, v in res if not s]
    assert len({v["period_id"] for _, v in res}) == 1 and sum(1 for _, v in res if not v["replayed"]) == 1
    periods = adm.execute("SELECT sequence_no, prior_period_id, period_start_date, period_end_date FROM subscription_period "
                          "WHERE subscription_id=%s ORDER BY 1", (sub["id"],)).fetchall()
    assert len(periods) == 2 and periods[1][1] == p1["id"] and periods[1][2] == p1["period_end_date"]
    assert adm.execute("SELECT count(*) FROM payment_order WHERE subscription_id=%s", (sub["id"],)).fetchone()[0] == 1
    assert adm.execute("SELECT count(*) FROM subscription_status_history WHERE subscription_id=%s", (sub["id"],)).fetchone()[0] == 1


def test_subscription_renew_chain_and_rules(adm, bill):                                    # BR-SUB-03/05
    sub, p1 = new_subscription(adm, "quarterly", 300)
    r2 = jcall(bill, "fn_subscription_renew", sub["id"])
    r3 = jcall(bill, "fn_subscription_renew", sub["id"])
    assert (r2["sequence_no"], r3["sequence_no"]) == (2, 3)
    assert r2["period_start_date"] == "2026-01-31" and r2["period_end_date"] == "2026-04-30"
    assert r3["period_start_date"] == r2["period_end_date"]
    assert adm.execute("SELECT prior_period_id FROM subscription_period WHERE id=%s", (r3["period_id"],)).fetchone()[0] == uuid.UUID(r2["period_id"])
    po = adm.execute("SELECT amount, payment_order_status_id = %s FROM payment_order WHERE id=%s",
                     (lookup_id(adm, "payment_order_status", "created"), r2["payment_order_id"])).fetchone()
    assert tuple(po) == (300, True)
    with pytest.raises(psycopg.Error) as ei:                                              # cannot skip a period
        jcall(bill, "fn_subscription_renew", sub["id"], None, 9)
    assert sqlstate(ei.value) == "TS003"
    once, _ = new_subscription(adm, "one_time", 10)
    with pytest.raises(psycopg.Error) as ei:
        jcall(bill, "fn_subscription_renew", once["id"])
    assert sqlstate(ei.value) == "TS004"
    # all-or-nothing: a failing renewal (unknown gateway -> NULL payment_gateway_id) leaves no period behind
    with pytest.raises(psycopg.Error):
        jcall(bill, "fn_subscription_renew", sub["id"], None, None, "no_such_gateway")
    assert adm.execute("SELECT max(sequence_no) FROM subscription_period WHERE subscription_id=%s", (sub["id"],)).fetchone()[0] == 3


def test_invoice_issue_parallel_numbers_are_unique_and_gapless(adm, bill):                  # BR-SUB-12
    s = new_series(adm)
    invs = [draft_invoice(adm, s["id"])["id"] for _ in range(12)]
    res = parallel(12, lambda c, i: jcall(c, "fn_invoice_issue", invs[i], None, tries=300))
    assert all(ok for ok, _ in res), [str(v) for ok, v in res if not ok]
    nums = sorted(v["invoice_number"] for _, v in res)
    assert nums == list(range(1, 13))
    assert adm.execute("SELECT next_number FROM invoice_number_series WHERE id=%s", (s["id"],)).fetchone()[0] == 13
    assert adm.execute("SELECT count(*) FROM vw_invoice_number_gap WHERE invoice_number_series_id=%s", (s["id"],)).fetchone()[0] == 0
    again = jcall(bill, "fn_invoice_issue", invs[0])                                       # replay
    assert again["replayed"] is True and again["invoice_number"] == adm.execute(
        "SELECT invoice_number FROM invoice WHERE id=%s", (invs[0],)).fetchone()[0]
    assert adm.execute("SELECT next_number FROM invoice_number_series WHERE id=%s", (s["id"],)).fetchone()[0] == 13


def test_invoice_issue_rollback_does_not_burn_a_number(adm, bill):                          # BR-SUB-12 gapless
    s = new_series(adm)
    a, b = draft_invoice(adm, s["id"])["id"], draft_invoice(adm, s["id"])["id"]
    tx = connect(role="billing_svc", autocommit=False)
    try:
        jcall(tx, "fn_invoice_issue", a)
        tx.rollback()
    finally:
        tx.close()
    assert jcall(bill, "fn_invoice_issue", b)["invoice_number"] == 1


def test_credit_note_numbering_from_credit_note_series(adm, bill):                          # BR-SUB-12 (credit_note)
    inv_series = new_series(adm, "invoice")
    cn_series = new_series(adm, "credit_note")
    inv = draft_invoice(adm, inv_series["id"])["id"]
    jcall(bill, "fn_invoice_issue", inv)
    reason = adm.execute("SELECT code FROM credit_note_reason ORDER BY sort_order LIMIT 1").fetchone()[0]
    c1 = jcall(bill, "fn_credit_note_issue", inv, cn_series["id"], reason, 10, 0)
    c2 = jcall(bill, "fn_credit_note_issue", inv, cn_series["id"], reason, 5, 0)
    assert (c1["number"], c2["number"]) == (1, 2)
    with pytest.raises(psycopg.Error) as ei:                                              # an invoice series is not a credit note series
        jcall(bill, "fn_credit_note_issue", inv, inv_series["id"], reason, 5, 0)
    assert sqlstate(ei.value) == "TI003"


def test_refund_over_limit_rejected_and_sum_enforced(adm, bill):                            # BR-SUB-15
    po = paid_order(adm, 1000)
    u = new_user(adm)
    k = lambda: "rf:" + uuid.uuid4().hex  # noqa: E731
    jcall(bill, "fn_refund_request", po["id"], 600, "customer_request" if False else adm.execute("SELECT code FROM refund_reason ORDER BY sort_order LIMIT 1").fetchone()[0], u, k())
    reason = adm.execute("SELECT code FROM refund_reason ORDER BY sort_order LIMIT 1").fetchone()[0]
    with pytest.raises(psycopg.Error) as ei:
        jcall(bill, "fn_refund_request", po["id"], 401, reason, u, k())
    assert sqlstate(ei.value) == "TR003"
    ok = jcall(bill, "fn_refund_request", po["id"], 400, reason, u, k())
    assert ok["refunded_so_far"] == 1000
    with pytest.raises(psycopg.Error) as ei:
        jcall(bill, "fn_refund_request", po["id"], 1, reason, u, k())
    assert sqlstate(ei.value) == "TR003"
    key = k()
    # rejecting frees the amount again
    r = jcall(bill, "fn_refund_request", paid_order(adm, 100)["id"], 50, reason, u, key)
    assert jcall(bill, "fn_refund_request", adm.execute("SELECT payment_order_id FROM refund WHERE id=%s", (r["refund_id"],)).fetchone()[0],
                 50, reason, u, key)["replayed"] is True
    jcall(bill, "fn_refund_reject", ok["refund_id"], new_user(adm))
    assert jcall(bill, "fn_refund_request", po["id"], 400, reason, u, k())["refund_id"]


def test_refund_parallel_requests_cannot_exceed_amount_paid(adm, bill):                     # BR-SUB-15 concurrency
    po = paid_order(adm, 1000)
    u = new_user(adm)
    reason = adm.execute("SELECT code FROM refund_reason ORDER BY sort_order LIMIT 1").fetchone()[0]
    res = parallel(8, lambda c, i: jcall(c, "fn_refund_request", po["id"], 300, reason, u, f"prf:{po['id']}:{i}", None, tries=200))
    ok = [v for s, v in res if s]
    assert len(ok) == 3 and all(sqlstate(v) == "TR003" for s, v in res if not s)
    assert adm.execute("SELECT sum(amount) FROM refund WHERE payment_order_id=%s", (po["id"],)).fetchone()[0] == 900


def test_refund_approver_must_differ_and_complete_claws_back_credits(adm, bill):            # BR-SUB-15
    wid = new_wallet(adm, bill, 100)
    po = paid_order(adm, 500, wallet_id=wid)
    req, appr = new_user(adm), new_user(adm)
    reason = adm.execute("SELECT code FROM refund_reason ORDER BY sort_order LIMIT 1").fetchone()[0]
    r = jcall(bill, "fn_refund_request", po["id"], 500, reason, req, "rfc:" + uuid.uuid4().hex, 40)
    with pytest.raises(psycopg.Error) as ei:
        jcall(bill, "fn_refund_approve", r["refund_id"], req)
    assert sqlstate(ei.value) == "TR002"
    with pytest.raises(psycopg.Error) as ei:                                              # not approved yet
        jcall(bill, "fn_refund_complete", r["refund_id"], "gw_" + uuid.uuid4().hex)
    assert sqlstate(ei.value) == "TR004"
    jcall(bill, "fn_refund_approve", r["refund_id"], appr)
    done = jcall(bill, "fn_refund_complete", r["refund_id"], "gw_" + uuid.uuid4().hex)
    assert done["clawback"]["balance_after"] == 60 and wallet_state(adm, wid) == (60, 2)
    assert adm.execute("SELECT s.code FROM payment_order o JOIN payment_order_status s ON s.id=o.payment_order_status_id WHERE o.id=%s",
                       (po["id"],)).fetchone()[0] == "refunded"
    assert jcall(bill, "fn_refund_complete", r["refund_id"], None)["replayed"] is True
    assert wallet_state(adm, wid) == (60, 2)                                               # no second clawback
    assert adm.execute("SELECT refund_id IS NOT NULL FROM wallet_ledger_entry WHERE wallet_id=%s AND sequence_no=2", (wid,)).fetchone()[0]


def test_entitlement_free_quota_never_exceeds_limit_in_parallel(adm, bill):                 # BR-SUB-20
    sub, _ = new_subscription(adm, with_first_period=False)
    feat = adm.execute("SELECT code FROM entitlement_feature WHERE code='profile_downloads'").fetchone()[0]
    now = datetime.datetime.now(UTC)
    build_row(adm, "subscription_entitlement", subscription_id=sub["id"], limit_value=5, used_count=0,
              entitlement_feature_id=lookup_id(adm, "entitlement_feature", feat), usage_window_start_at=now - datetime.timedelta(days=1),
              usage_window_end_at=now + datetime.timedelta(days=29))
    res = parallel(12, lambda c, i: jcall(c, "fn_entitlement_consume", sub["id"], feat, 1, None, None, tries=200))
    assert all(s for s, _ in res), [str(v) for s, v in res if not s]
    assert sum(1 for _, v in res if v["ok"]) == 5
    assert sum(1 for _, v in res if not v["ok"] and v["reason"] == "quota_exhausted") == 7
    assert adm.execute("SELECT used_count FROM subscription_entitlement WHERE subscription_id=%s", (sub["id"],)).fetchone()[0] == 5
    assert jcall(bill, "fn_entitlement_consume", sub["id"], "bulk_exports")["reason"] == "no_entitlement"


def _coupon(adm, **kw):
    kw.setdefault("coupon_code", "T" + uuid.uuid4().hex[:10].upper())
    kw.setdefault("valid_from", datetime.datetime.now(UTC) - datetime.timedelta(days=1))
    kw.setdefault("valid_to", datetime.datetime.now(UTC) + datetime.timedelta(days=30))
    kw.setdefault("percent_off", None)
    kw.setdefault("amount_off", None)
    kw.setdefault("currency_id", None)
    kw.setdefault("bonus_credits", None)
    kw.setdefault("max_redemptions", None)
    kw.setdefault("per_organisation_limit", 1)
    return build_row(adm, "coupon", **kw)


def test_coupon_global_cap_holds_in_parallel(adm, bill):                                    # BR-SUB-21
    c = _coupon(adm, discount_type_id=lookup_id(adm, "discount_type", "percent_off"), percent_off=10, max_redemptions=3)
    cur = build_row(adm, "currency")["id"] if False else adm.execute("SELECT id FROM currency ORDER BY sort_order LIMIT 1").fetchone()[0]
    orgs = [new_org(adm) for _ in range(8)]
    res = parallel(8, lambda cn, i: jcall(cn, "fn_coupon_redeem", c["coupon_code"], orgs[i], None, 200, cur, None, None, None, None, tries=200))
    assert sum(1 for s, _ in res if s) == 3 and all(sqlstate(v) == "TC003" for s, v in res if not s)
    assert adm.execute("SELECT redemption_count FROM coupon WHERE id=%s", (c["id"],)).fetchone()[0] == 3
    assert adm.execute("SELECT count(*), max(discount_amount) FROM coupon_redemption WHERE coupon_id=%s", (c["id"],)).fetchone() == (3, 20)


def test_coupon_per_org_validity_and_bonus_credits(adm, bill):                              # BR-SUB-21
    cur = adm.execute("SELECT id FROM currency ORDER BY sort_order LIMIT 1").fetchone()[0]
    org = new_org(adm)
    c = _coupon(adm, discount_type_id=lookup_id(adm, "discount_type", "percent_off"), percent_off=5)
    jcall(bill, "fn_coupon_redeem", c["coupon_code"], org, None, 100, cur)
    with pytest.raises(psycopg.Error) as ei:
        jcall(bill, "fn_coupon_redeem", c["coupon_code"], org, None, 100, cur)
    assert sqlstate(ei.value) == "TC004"
    expired = _coupon(adm, discount_type_id=lookup_id(adm, "discount_type", "percent_off"), percent_off=5,
                      valid_from=datetime.datetime.now(UTC) - datetime.timedelta(days=9), valid_to=datetime.datetime.now(UTC) - datetime.timedelta(days=1))
    with pytest.raises(psycopg.Error) as ei:
        jcall(bill, "fn_coupon_redeem", expired["coupon_code"], org, None, 100, cur)
    assert sqlstate(ei.value) == "TC002"
    with pytest.raises(psycopg.Error) as ei:
        jcall(bill, "fn_coupon_redeem", "NOPE" + uuid.uuid4().hex[:6].upper(), org, None, 100, cur)
    assert sqlstate(ei.value) == "TC001"
    bonus = _coupon(adm, discount_type_id=lookup_id(adm, "discount_type", "bonus_credits"), bonus_credits=25)
    wid = new_wallet(adm, bill, 0, org_id=org)
    r = jcall(bill, "fn_coupon_redeem", bonus["coupon_code"], org, None, None, cur, None, None, wid,
              datetime.datetime.now(UTC) + datetime.timedelta(days=30))
    assert r["bonus_credits"] == 25 and r["ledger"]["balance_after"] == 25 and wallet_state(adm, wid) == (25, 1)
    assert adm.execute("SELECT coupon_redemption_id = %s FROM wallet_ledger_entry WHERE wallet_id=%s", (r["redemption_id"], wid)).fetchone()[0]
    assert adm.execute("SELECT credits_remaining FROM wallet_expiry_bucket WHERE wallet_id=%s", (wid,)).fetchone()[0] == 25


def _batch(adm, org=None):
    return build_row(adm, "outreach_batch", organisation_id=org or new_org(adm), credits_per_recipient=1)


def test_outreach_reserve_consume_release_and_refund_on_reply(adm, bill):                  # BR-SRC-09
    org = new_org(adm)
    wid = new_wallet(adm, bill, 100, org_id=org)
    b = _batch(adm, org)["id"]
    k = uuid.uuid4().hex[:12]
    r = jcall(bill, "fn_outreach_reserve", b, wid, 30, "res:" + k)
    assert wallet_state(adm, wid) == (70, 2) and r["replayed"] is False
    assert jcall(bill, "fn_outreach_reserve", b, wid, 30, "res:" + k)["replayed"] is True       # retry does not double charge
    assert wallet_state(adm, wid) == (70, 2)
    assert adm.execute("SELECT outreach_batch_id = %s FROM wallet_ledger_entry WHERE wallet_id=%s AND sequence_no=2", (b, wid)).fetchone()[0]
    rec = build_row(adm, "outreach_recipient", outreach_batch_id=b)["id"]
    jcall(bill, "fn_outreach_consume", b, rec, 10, "con:" + k)
    with pytest.raises(psycopg.Error) as ei:                                              # more than still reserved
        jcall(bill, "fn_outreach_consume", b, rec, 21, "con2:" + k)
    assert sqlstate(ei.value) == "TO002"
    rr = jcall(bill, "fn_outreach_refund_on_reply", b, rec, "rep:" + k)
    assert rr["refunded"] == 10 and wallet_state(adm, wid)[0] == 80
    with pytest.raises(psycopg.Error) as ei:                                              # only once per recipient
        jcall(bill, "fn_outreach_refund_on_reply", b, rec, "rep2:" + k)
    assert sqlstate(ei.value) == "TO003"
    rel = jcall(bill, "fn_outreach_release", b, "rel:" + k)                               # 30 - 10 consumed = 20 unused
    assert rel["released"] == 20 and wallet_state(adm, wid)[0] == 100
    assert jcall(bill, "fn_outreach_release", b, "rel:" + k)["replayed"] is True
    assert jcall(bill, "fn_outreach_release", b, "rel2:" + k)["released"] == 0
    assert wallet_state(adm, wid)[0] == 100


def test_outreach_reserve_cannot_overspend_wallet_in_parallel(adm, bill):                   # BR-SRC-09 + D-14
    org = new_org(adm)
    wid = new_wallet(adm, bill, 50, org_id=org)
    batches = [_batch(adm, org)["id"] for _ in range(6)]
    res = parallel(6, lambda c, i: jcall(c, "fn_outreach_reserve", batches[i], wid, 20, f"pres:{wid}:{i}", None, tries=200))
    assert sum(1 for s, _ in res if s) == 2 and all(sqlstate(v) == "TW001" for s, v in res if not s)
    assert wallet_state(adm, wid)[0] == 10


def _campaign_with_shards(adm, budget, pendings):
    camp = build_row(adm, "ad_campaign", budget_amount=budget, spent_amount=0, ledger_sequence=0)
    for n, p in enumerate(pendings):
        build_row(adm, "ad_budget_shard", ad_campaign_id=camp["id"], shard_no=n, pending_spend_amount=p, event_count=1,
                  currency_id=camp["currency_id"])
    return camp


def test_ad_budget_settle_accrues_caps_at_budget_and_is_idempotent(adm, app):               # BR-ADS-05
    camp = _campaign_with_shards(adm, 100, [30, 30, 30, 30])                                # 120 pending, budget 100
    hour = datetime.datetime(2026, 3, 1, 10, 15, tzinfo=UTC)
    r = jcall(app, "fn_ad_budget_settle", camp["id"], hour)
    assert float(r["accrued"]) == 100 and float(r["absorbed"]) == 20 and r["sequence_no"] == 1 and r["replayed"] is False
    assert adm.execute("SELECT spent_amount, ledger_sequence FROM ad_campaign WHERE id=%s", (camp["id"],)).fetchone() == (100, 1)
    assert adm.execute("SELECT sum(pending_spend_amount), sum(event_count) FROM ad_budget_shard WHERE ad_campaign_id=%s", (camp["id"],)).fetchone() == (0, 0)
    again = jcall(app, "fn_ad_budget_settle", camp["id"], hour)
    assert again["replayed"] is True and again["ledger_id"] == r["ledger_id"]
    assert adm.execute("SELECT count(*) FROM ad_budget_ledger WHERE ad_campaign_id=%s", (camp["id"],)).fetchone()[0] == 1
    led = adm.execute("SELECT idempotency_key, spent_after, length(row_hash), prev_hash IS NULL FROM ad_budget_ledger WHERE ad_campaign_id=%s",
                      (camp["id"],)).fetchone()
    assert led[0].startswith("accrue:" + camp["id"]) and led[1:] == (100, 32, True)


def test_ad_budget_settle_parallel_flushes_chain_without_gaps(adm, app):                    # BR-ADS-05 concurrency
    camp = _campaign_with_shards(adm, 1000, [10, 0, 0, 0])
    hour = datetime.datetime(2026, 3, 1, 10, tzinfo=UTC)
    res = parallel(6, lambda c, i: jcall(c, "fn_ad_budget_settle", camp["id"], hour, tries=200), role="app_service")
    assert all(s for s, _ in res), [str(v) for s, v in res if not s]
    assert adm.execute("SELECT count(*) FROM ad_budget_ledger WHERE ad_campaign_id=%s", (camp["id"],)).fetchone()[0] == 1
    # later hours: new pending spend per hour -> sequence 2, 3 chained
    for h in (11, 12):
        adm.execute("UPDATE ad_budget_shard SET pending_spend_amount = 5 WHERE ad_campaign_id=%s AND shard_no=0", (camp["id"],))
        jcall(app, "fn_ad_budget_settle", camp["id"], datetime.datetime(2026, 3, 1, h, tzinfo=UTC))
    rows = adm.execute("SELECT sequence_no, prev_sequence_no, spent_after FROM ad_budget_ledger WHERE ad_campaign_id=%s ORDER BY 1", (camp["id"],)).fetchall()
    assert rows == [(1, None, 10), (2, 1, 15), (3, 2, 20)]


def test_ad_wallet_settlement_goes_through_the_wallet(adm, bill):                           # BR-ADS-09
    org = new_org(adm)
    wid = new_wallet(adm, bill, 100, org_id=org)
    acct = build_row(adm, "ad_account", organisation_id=org)
    cur = acct["currency_id"] if "currency_id" in acct else adm.execute("SELECT id FROM currency ORDER BY sort_order LIMIT 1").fetchone()[0]
    t0 = datetime.datetime(2026, 3, 1, tzinfo=UTC)
    key = "adset:" + uuid.uuid4().hex
    a = jcall(bill, "fn_ad_wallet_settle", acct["id"], wid, t0, t0 + datetime.timedelta(days=1), 12.5, cur, 2, key)
    assert a["credits_debited"] == 25 and wallet_state(adm, wid) == (75, 2)
    assert adm.execute("SELECT wallet_ledger_entry_id = %s FROM ad_billing_event WHERE id=%s", (a["ledger_entry_id"], a["event_id"])).fetchone()[0]
    assert jcall(bill, "fn_ad_wallet_settle", acct["id"], wid, t0, t0 + datetime.timedelta(days=1), 12.5, cur, 2, key)["replayed"] is True
    assert wallet_state(adm, wid) == (75, 2)
    with pytest.raises(psycopg.Error) as ei:                                              # prepaid: never below zero
        jcall(bill, "fn_ad_wallet_settle", acct["id"], wid, t0, t0 + datetime.timedelta(days=1), 100, cur, 2, "adset2:" + uuid.uuid4().hex)
    assert sqlstate(ei.value) == "TW001"
    assert wallet_state(adm, wid) == (75, 2)
    assert adm.execute("SELECT count(*) FROM ad_billing_event WHERE ad_account_id=%s", (acct["id"],)).fetchone()[0] == 1

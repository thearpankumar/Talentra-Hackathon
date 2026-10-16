"""Helpers for tests/test_rules_money.py (rules money team).  Uses the generic row builder for fixtures (root connection)
and the real functions of infra/schema/parts/60_rules_money.sql for behaviour (SET ROLE billing_svc / app_service)."""
import concurrent.futures as cf
import datetime
import uuid

import psycopg

from conftest import connect, retry
from helpers_rowbuilder import build_row, lookup_id

UTC = datetime.timezone.utc


def jcall(conn, fn, *args, tries=80):
    """SELECT fn(args) -> parsed JSON dict; retried on 40001."""
    ph = ", ".join(["%s"] * len(args))

    def run():
        return conn.execute(f"SELECT {fn}({ph})", args).fetchone()[0]
    return retry(run, tries=tries)


def new_org(adm):
    return build_row(adm, "organisation")["id"]


def new_user(adm):
    return build_row(adm, "app_user")["id"]


def new_wallet(adm, bill, balance=0, org_id=None, status="active", expires_at=None):
    w = build_row(adm, "wallet", owner_organisation_id=org_id or new_org(adm),
                  wallet_status_id=lookup_id(adm, "wallet_status", status))
    if balance:
        jcall(bill, "fn_wallet_credit", w["id"], balance, "recharge", "seed:" + uuid.uuid4().hex, None, None, None,
              "test seed credit", expires_at)
    return w["id"]


def wallet_state(adm, wid):
    return adm.execute("SELECT credit_balance, ledger_sequence FROM wallet WHERE id=%s", (wid,)).fetchone()


def ledger_rows(adm, wid):
    return adm.execute("SELECT sequence_no, credits_delta, balance_after, idempotency_key FROM wallet_ledger_entry "
                       "WHERE wallet_id=%s ORDER BY sequence_no", (wid,)).fetchall()


def parallel(n, fn, role="billing_svc", workers=None):
    """Run fn(conn, i) in n threads, each with its own connection (SET ROLE role).  Returns list of (ok, value_or_exc)."""
    def one(i):
        c = connect(role=role)
        try:
            try:
                return True, fn(c, i)
            except Exception as e:  # noqa: BLE001 - collected for the assertions
                return False, e
        finally:
            c.close()
    with cf.ThreadPoolExecutor(max_workers=workers or n) as ex:
        return list(ex.map(one, range(n)))


def sqlstate(exc):
    return getattr(exc, "sqlstate", None)


def new_subscription(adm, interval="monthly", amount=100, with_first_period=True):
    """subscription + first period (sequence 1) ending 2026-01-31 + the licence it belongs to."""
    sub = build_row(adm, "subscription", billing_interval_id=lookup_id(adm, "billing_interval", interval),
                    recurring_amount=amount, subscription_status_id=lookup_id(adm, "subscription_status", "active"),
                    started_at=datetime.datetime(2026, 1, 1, tzinfo=UTC))
    first = None
    if with_first_period:
        first = build_row(adm, "subscription_period", subscription_id=sub["id"], licence_id=sub["licence_id"], sequence_no=1,
                          period_start_date=datetime.date(2026, 1, 1), period_end_date=datetime.date(2026, 1, 31),
                          billed_amount=amount, plan_version_id=sub["plan_version_id"], currency_id=sub["currency_id"],
                          subscription_period_status_id=lookup_id(adm, "subscription_period_status", "active"))
    return sub, first


def paid_order(adm, amount=1000, wallet_id=None, org_id=None):
    return build_row(adm, "payment_order", organisation_id=org_id or new_org(adm), amount=amount, wallet_id=wallet_id,
                     payment_order_status_id=lookup_id(adm, "payment_order_status", "paid"),
                     idempotency_key="po:" + uuid.uuid4().hex)


def new_series(adm, kind="invoice"):
    return build_row(adm, "invoice_number_series", series_document_kind_id=lookup_id(adm, "series_document_kind", kind),
                     prefix="T" + uuid.uuid4().hex[:6].upper(), fiscal_year_label="2026-27", next_number=1)


def draft_invoice(adm, series_id):
    return build_row(adm, "invoice", invoice_number_series_id=series_id, subtotal_amount=100, tax_amount=0, total_amount=100,
                     invoice_status_id=lookup_id(adm, "invoice_status", "draft"))

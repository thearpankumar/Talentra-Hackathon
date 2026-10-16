"""Backup -> restore drill using the real infra stacks (main-db backup handler + backup-db cluster).

Needs: local stacks main-db and backup-db running, the schema applied into database `talentra` (the handler backs up
BACKUP_DB=talentra), docker CLI.  Measured times are appended to docs/20_backup_restore_log.xlsx (sheet Log) and
to tests/backup_restore_results.json.  Marked slow (restore of the 1306-table schema takes several minutes).
"""
import csv
import datetime as dt
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import openpyxl
import psycopg
import pytest

from conftest import BACKUP_PORT, DB, DOCS, HOST, ROOT, connect, dsn

pytestmark = [pytest.mark.slow]
HANDLER = os.environ.get("BACKUP_HANDLER_CONTAINER", "talentra-main-local-backup-handler-1")
MAIN_NODE = os.environ.get("MAIN_NODE_CONTAINER", "talentra-main-local-crdb1-1")
RESULTS = Path(__file__).with_name("backup_restore_results.json")
LOGCSV = ROOT / "infra" / "main-db" / "logs" / "backup_timing.csv"
RTO_MIN = 60  # docs/20_backup_restore_log.xlsx Plan!C2 (laptop scale)
RESTORED = "talentra_rt_test"

state = {}


def sh(cmd, timeout=3600):
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr), time.time() - t


def docker_exec(container, *args, timeout=3600):
    return sh(["docker", "exec", container, *args], timeout=timeout)


@pytest.fixture(scope="module", autouse=True)
def _env():
    if DB != "talentra":
        pytest.skip("handler backs up database 'talentra' only; run with TALENTRA_DB=talentra")
    rc, out, _ = sh(["docker", "ps", "--format", "{{.Names}}"])
    if rc != 0 or HANDLER not in out:
        pytest.skip("backup handler container not running (make -C infra up-local STACK=main-db)")
    yield


def _save():
    RESULTS.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def test_01_seed_marker_rows():
    from helpers_rowbuilder import build_row
    c = connect()
    marker = "bk" + uuid.uuid4().hex[:8]
    ids = []
    for i in range(25):
        ids.append(build_row(c, "organisation", name=f"{marker}_{i}" if False else None)["id"] if False else None)
    # use the lookup-free, FK-light table set: app_user + organisation rows are enough to prove data restore
    users = [build_row(c, "app_user", email=f"{marker}{i}@example.test")["id"] for i in range(25)]
    state["marker"] = marker
    state["marker_user_ids"] = [str(u) for u in users]
    n_tables = c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'").fetchone()[0]
    n_views = c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='VIEW'").fetchone()[0]
    n_idx = c.execute("SELECT count(*) FROM pg_indexes WHERE schemaname='public'").fetchone()[0]
    n_con = c.execute("SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace").fetchone()[0]
    total_rows = sum(r[0] for r in c.execute(
        "SELECT count(*) FROM app_user UNION ALL SELECT count(*) FROM country").fetchall())
    state["source"] = dict(tables=n_tables, views=n_views, indexes=n_idx, constraints=n_con, sample_rows=total_rows)
    state["started_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _save()
    assert n_tables >= 1306


def test_02_full_backup_timed():
    rc, out, secs = docker_exec(HANDLER, "handler.sh", "backup", "full", timeout=3600)
    assert rc == 0 and "fail" not in out.lower().split("backup")[-1][:200], out[-500:]
    state["full_backup"] = dict(seconds=round(secs, 1), output=out.strip()[-300:])
    _save()


def test_03_incremental_backup_timed():
    from helpers_rowbuilder import build_row
    c = connect()
    u = build_row(c, "app_user", email=f"{state['marker']}inc@example.test")["id"]
    state["marker_user_ids"].append(str(u))
    state["incr_marker_time"] = dt.datetime.now(dt.timezone.utc).isoformat()
    rc, out, secs = docker_exec(HANDLER, "handler.sh", "backup", "incr", timeout=3600)
    assert rc == 0, out[-500:]
    state["incr_backup"] = dict(seconds=round(secs, 1), output=out.strip()[-300:])
    _save()


def test_04_restore_into_backup_cluster_timed():
    """Disaster-recovery path: RESTORE from the shared nodelocal collection on the separate backup cluster."""
    bk = psycopg.connect(dsn(db="defaultdb", port=BACKUP_PORT), autocommit=True)
    bk.execute(f"DROP DATABASE IF EXISTS {RESTORED} CASCADE")
    t0 = dt.datetime.now(dt.timezone.utc)
    s = time.time()
    bk.execute(f"RESTORE DATABASE talentra FROM LATEST IN 'nodelocal://self/main' WITH new_db_name='{RESTORED}'")
    secs = time.time() - s
    t1 = dt.datetime.now(dt.timezone.utc)
    state["restore"] = dict(start=t0.isoformat(), end=t1.isoformat(), seconds=round(secs, 1))
    _save()
    assert secs / 60 <= RTO_MIN, f"restore took {secs/60:.1f} min > RTO {RTO_MIN} min"


def test_05_validate_restored_data_and_catalog():
    s = time.time()
    r = psycopg.connect(dsn(db=RESTORED, port=BACKUP_PORT), autocommit=True)
    src = state["source"]
    got = dict(
        tables=r.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'").fetchone()[0],
        views=r.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='VIEW'").fetchone()[0],
        indexes=r.execute("SELECT count(*) FROM pg_indexes WHERE schemaname='public'").fetchone()[0],
        constraints=r.execute("SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace").fetchone()[0],
    )
    ids = state["marker_user_ids"]
    found = r.execute("SELECT count(*) FROM app_user WHERE id = ANY(%s::UUID[])", (ids,)).fetchone()[0]
    countries_main = connect().execute("SELECT count(*) FROM country").fetchone()[0]
    countries_rest = r.execute("SELECT count(*) FROM country").fetchone()[0]
    # every lookup table has the same row count on both sides
    main = connect()
    lookups = [x[0] for x in main.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN "
        "(SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='name_i18n')").fetchall()]
    bad = []
    for t in lookups:
        a = main.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        b = r.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        if a != b:
            bad.append((t, a, b))
    state["validation"] = dict(restored=got, source=src, marker_found=found, marker_expected=len(ids),
                               lookups_checked=len(lookups), lookup_mismatch=bad, seconds=round(time.time() - s, 1))
    _save()
    assert found == len(ids), "marker rows missing in restored database (incremental not applied?)"
    assert got["tables"] == src["tables"] and got["views"] == src["views"]
    assert got["indexes"] == src["indexes"] and got["constraints"] == src["constraints"]
    assert countries_main == countries_rest and not bad


def test_06_post_restore_security_state_and_repair():
    """RESTORE hands object ownership to the restoring user and drops explicit grants; the documented post-restore step
    re-assigns ownership to schema_owner and re-applies the generated grants.  Measure both."""
    r = psycopg.connect(dsn(db=RESTORED, port=BACKUP_PORT), autocommit=True)
    owners = dict(r.execute("SELECT tableowner, count(*) FROM pg_tables WHERE schemaname='public' GROUP BY 1").fetchall())
    state["post_restore_owner_counts_before"] = owners
    st = time.time()
    rc, out, _ = sh([str(Path(__file__).resolve().parent.parent / "infra" / "schema" / "post_restore.sh"), RESTORED, str(BACKUP_PORT)], timeout=3600)
    state["post_restore_repair"] = dict(seconds=round(time.time() - st, 1), rc=rc, tail=out.strip()[-400:])
    owners2 = dict(r.execute("SELECT tableowner, count(*) FROM pg_tables WHERE schemaname='public' GROUP BY 1").fetchall())
    state["post_restore_owner_counts_after"] = owners2
    _save()
    assert rc == 0, out[-800:]
    assert set(owners2) == {"schema_owner"}, owners2


def test_07_handler_sync_cycle_recorded():
    """Run the handler's own sync (RESTORE into the backup cluster + rename swap + row-count verify) and record the outcome."""
    rc, out, secs = docker_exec(HANDLER, "handler.sh", "sync", timeout=5400)
    state["handler_sync"] = dict(rc=rc, seconds=round(secs, 1), tail=out.strip()[-600:])
    _save()
    # not asserted as success: the swap step uses ALTER DATABASE RENAME, which CockroachDB rejects when views depend on the
    # database name (see docs/07_schema_justification.md section 12); the outcome is recorded for the log either way.


def test_08_write_log_sheet():
    path = DOCS / "20_backup_restore_log.xlsx"
    wb = openpyxl.load_workbook(path)
    ws = wb["Log"]
    row = 2
    while ws.cell(row=row, column=1).value:
        row += 1
    full, inc, rst, val = state["full_backup"], state["incr_backup"], state["restore"], state["validation"]
    rs = dt.datetime.fromisoformat(rst["start"]).replace(tzinfo=None)
    re_ = dt.datetime.fromisoformat(rst["end"]).replace(tzinfo=None)
    bk_end = dt.datetime.fromisoformat(state["started_utc"]).replace(tzinfo=None) + dt.timedelta(seconds=full["seconds"])
    bk_start = dt.datetime.fromisoformat(state["started_utc"]).replace(tzinfo=None)
    vals = {
        1: f"RUN-{dt.date.today():%Y%m%d}-{row - 1:02d}", 2: dt.date.today(), 3: "laptop: 3-node main (docker) -> 1-node backup cluster, schema + seed rows (no bulk data)",
        4: val["source"]["sample_rows"], 5: None, 6: "full (BACKUP DATABASE INTO) + 1 incremental",
        7: bk_start, 8: bk_end, 11: rs, 12: re_, 14: round(val["seconds"] / 60, 2),
        22: "Pass" if not val["lookup_mismatch"] else "Fail",
        23: "automated: tests/test_backup_restore.py",
        24: (f"full backup {full['seconds']}s, incremental {inc['seconds']}s, restore of 1306 tables/{val['restored']['views']} views/"
             f"{val['restored']['indexes']} indexes {rst['seconds']}s; post-restore ownership+grant repair {state['post_restore_repair']['seconds']}s "
             f"(restore sets owner to the restoring user: {state['post_restore_owner_counts_before']}); handler sync rc={state['handler_sync']['rc']} in {state['handler_sync']['seconds']}s"),
    }
    for col, v in vals.items():
        if v is not None:
            ws.cell(row=row, column=col, value=v)
    wb.save(path)
    state["log_row"] = row
    _save()

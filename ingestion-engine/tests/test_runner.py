import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg
import pytest

from ingestion_engine.config import parse_config
from ingestion_engine.runner import ConfigError, NeedsConfirmation, RunControl, Runner, estimate

from .conftest import BUSINESS_TABLES, make_cfg

ZERO = {t: 0 for t in BUSINESS_TABLES}


def counts(url):
    with psycopg.connect(url) as c:
        return {t: c.execute(f"select count(*) from {t}").fetchone()[0] for t in BUSINESS_TABLES}


def planned(cfg):
    out = {}
    for s in cfg.scenarios:
        for t in s.tables:
            out[t.table] = out.get(t.table, 0) + t.record_count
    return out


def test_full_run_counts_and_validation(settings):
    r = Runner(settings)
    cfg = parse_config(make_cfg(users=1500))
    rid = r.create_run(cfg)
    assert r.execute(rid) == "completed"
    assert counts(settings.db_url) == {**ZERO, **planned(cfg)}
    rep = r.validate_run(rid)
    assert rep["ok"], rep["issues"]
    assert all(t["conflict_skipped"] == 0 for t in rep["tables"])
    with psycopg.connect(settings.db_url) as c:  # unknown / not-applicable nulls and duplicates landed
        assert c.execute("select count(*) from candidate_profile where salary_disclosure='unknown'").fetchone()[0] > 0
        assert c.execute("select count(*) from candidate_profile where salary_disclosure='not_applicable'").fetchone()[0] > 0
        assert c.execute("select count(*) - count(distinct full_name) from users").fetchone()[0] > 0
        assert c.execute("select count(*) from application a join job_requirement j on j.id=a.job_requirement_id").fetchone()[0] == 1500


def test_pause_then_resume_exact_counts(settings):
    r = Runner(settings)
    cfg = parse_config(make_cfg(users=1500, batch=250))
    rid = r.create_run(cfg, options={"workers": 2, "throttle_rows_per_sec": 1200})
    ctl = RunControl()
    res = {}
    th = threading.Thread(target=lambda: res.setdefault("s", r.execute(rid, ctl)))
    th.start()
    for _ in range(120):  # pause as soon as the first batches are committed
        time.sleep(0.25)
        if r.state.one("select count(*) n from batch where run_id=?", (rid,))["n"] >= 2:
            break
    ctl.pause.set()
    th.join(90)
    assert res["s"] == "paused"
    snap = r.progress_snapshot(rid)
    assert 0 < snap["total_done"] < snap["total_planned"]
    assert sum(counts(settings.db_url).values()) == snap["total_done"]  # DB == checkpoints
    r2 = Runner(settings)  # a "new process": fresh Runner over the same state
    assert r2.execute(rid) == "completed"
    assert counts(settings.db_url) == {**ZERO, **planned(cfg)}
    assert r2.validate_run(rid)["ok"]


def test_batches_are_idempotent_when_checkpoints_are_lost(settings):
    r = Runner(settings)
    cfg = parse_config(make_cfg(users=1000, batch=250, extra_tables=False))
    rid = r.create_run(cfg)
    assert r.execute(rid) == "completed"
    before = counts(settings.db_url)
    # crash between DB commit and SQLite checkpoint: wipe local checkpoints; DB ledger must make re-execution a no-op
    r.state.x("DELETE FROM batch WHERE run_id=?", (rid,))
    r.state.x("UPDATE run_table SET rows_done=0,batches_done=0,status='pending' WHERE run_id=?", (rid,))
    r.state.x("UPDATE run SET status='interrupted', total_done=0 WHERE id=?", (rid,))
    assert r.execute(rid) == "completed"
    assert counts(settings.db_url) == before


def test_scenario_memory_and_decisions(settings):
    r = Runner(settings)
    cfg = parse_config(make_cfg(users=300, extra_tables=False))
    rid = r.create_run(cfg)
    r.execute(rid)
    base = counts(settings.db_url)["users"]
    with pytest.raises(NeedsConfirmation) as e:
        r.create_run(cfg)
    f = e.value.findings[0]
    assert f["kind"] == "exact" and f["matched_run_id"] == rid and f["rows"] == 600
    other = parse_config(make_cfg(users=450, scn_id="another", seed=99, extra_tables=False))
    assert [x["kind"] for x in r.check_memory(other)] == ["exact"]  # same shape under another id/seed/name
    # memory also lives in the target DB: a brand-new local state still sees it
    s2 = type(settings)()
    s2.state_dir = settings.state_dir / "fresh"
    assert any(x["source"] == "db" for x in Runner(s2).check_memory(cfg))
    with pytest.raises(ConfigError):  # skip -> nothing to run
        r.create_run(cfg, decisions={"t_scn": "skip"})
    rid2 = r.create_run(cfg, decisions={"t_scn": "append"})
    r.execute(rid2)
    assert counts(settings.db_url)["users"] == base * 2
    rid3 = r.create_run(cfg, decisions={"t_scn": "replace"})  # purges the previous ledger match, then loads fresh
    r.execute(rid3)
    assert counts(settings.db_url)["users"] < base * 3


def test_purge_removes_exactly_the_runs_rows(settings):
    r = Runner(settings)
    cfg = parse_config(make_cfg(users=800, batch=300))
    rid1 = r.create_run(cfg)
    r.execute(rid1)
    c1 = counts(settings.db_url)
    rid2 = r.create_run(cfg, decisions={"t_scn": "append"})
    r.execute(rid2)
    assert counts(settings.db_url)["users"] == 2 * c1["users"]
    deleted = r.purge_run(rid2)
    assert counts(settings.db_url) == c1
    assert deleted["users"] == c1["users"]
    assert r.state.one("select status from run where id=?", (rid2,))["status"] == "purged"


def test_config_errors_and_estimate(settings):
    r = Runner(settings)
    bad = make_cfg()
    bad["scenarios"][0]["tables"][0]["table"] = "nope"
    with pytest.raises(ConfigError):
        r.create_run(parse_config(bad))
    est = estimate(r, parse_config(make_cfg(users=1000)), 300)
    assert est["total_rows"] > 0 and est["tables"] and not est["issues"]


def test_kill_process_midrun_then_resume(settings, tmp_path):
    """Hard-kill a CLI process mid-run, then resume with a new process: no missing / duplicate rows."""
    cfgfile = tmp_path / "c.json"
    d = make_cfg(users=6000, batch=500, workers=2)
    cfgfile.write_text(json.dumps(d))
    env = {**os.environ, "TARGET_DB_URL": settings.db_url, "ENGINE_STATE_DIR": str(settings.state_dir), "LLM_PROVIDER": "fake"}
    root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, "-m", "ingestion_engine.cli", "run", str(cfgfile), "--non-interactive", "--throttle", "4000"]
    p = subprocess.Popen(cmd, env=env, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 90
    while time.time() < deadline:
        time.sleep(1)
        try:
            n = sum(counts(settings.db_url).values())
        except Exception:
            n = 0
        if n >= 4000:
            break
    p.kill()
    p.wait()
    time.sleep(1)
    total = sum(planned(parse_config(d)).values())
    assert 0 < sum(counts(settings.db_url).values()) < total
    r2 = Runner(settings)
    rid = r2.state.q("select id from run order by created_at desc limit 1")[0]["id"]
    r2.state.recover_orphans()
    assert r2.state.one("select status from run where id=?", (rid,))["status"] == "interrupted"
    assert r2.execute(rid) == "completed"
    assert counts(settings.db_url) == {**ZERO, **planned(parse_config(d))}
    assert r2.validate_run(rid)["ok"]

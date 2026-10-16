import json
import time

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient

from ingestion_engine.api import create_app
from ingestion_engine.config import parse_config
from ingestion_engine.llm import TokenBudget, TokenBudgetExceeded
from ingestion_engine.scenario_graph import ScenarioGenerator, extract_json_array
from ingestion_engine.services import Services

from .conftest import BUSINESS_TABLES, make_cfg


@pytest.fixture()
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def chat(client, msg, sid="t1"):
    r = client.post("/api/chat", json={"session_id": sid, "message": msg})
    assert r.status_code == 200, r.text
    return r.json()


def wait_status(client, rid, want=("completed", "failed", "paused", "cancelled"), timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = client.get(f"/api/runs/{rid}").json()
        if s["status"] in want:
            return s
        time.sleep(0.5)
    raise AssertionError(f"timeout; last={s}")


def table_total(url):
    with psycopg.connect(url) as c:
        return sum(c.execute(f"select count(*) from {t}").fetchone()[0] for t in BUSINESS_TABLES)


def test_ui_served_and_llm_status(client):
    r = client.get("/")
    assert r.status_code == 200 and "Talentra Ingestion Engine" in r.text and "EventSource" in r.text
    st = client.get("/api/llm/status").json()
    assert st["provider"] == "fake" and st["available"]


def test_config_save_version_activate_and_validation(client, settings):
    assert client.put("/api/config", json={"content": "scenarios: [{id: x}]", "fmt": "yaml"}).status_code == 422
    bad = make_cfg()
    bad["scenarios"][0]["tables"][0]["table"] = "ghost"
    r = client.put("/api/config", json={"content": json.dumps(bad), "fmt": "json"})
    assert r.status_code == 422 and "ghost" in json.dumps(r.json())
    v1 = client.put("/api/config", json={"content": yaml.safe_dump(make_cfg(users=500)), "fmt": "yaml", "note": "first"}).json()["version_id"]
    v2 = client.put("/api/config", json={"content": yaml.safe_dump(make_cfg(users=900)), "fmt": "yaml", "note": "second"}).json()["version_id"]
    assert v2 > v1
    assert client.get("/api/config").json()["version_id"] == v2
    vs = client.get("/api/config/versions").json()
    assert [v["id"] for v in vs][:2] == [v2, v1] and vs[0]["active"]
    assert client.post(f"/api/config/versions/{v1}/activate").status_code == 200
    cur = client.get("/api/config").json()
    assert cur["version_id"] == v1 and cur["summary"][0]["records"] == 500 + 500 + 1000 + 25 + 166 + 500
    assert "$defs" in client.get("/api/config/schema").json()


def test_chat_generate_edit_accept_reject_and_persistence(client, settings):
    out = chat(client, "generate 6 scenarios for gig designers in Lagos")
    assert [c["name"] for c in out["tool_calls"]] == ["generate_scenarios"] and len(out["proposals"]) == 1
    pid = out["proposals"][0]["id"]
    assert out["proposals"][0]["diff"].count(chr(10) + "+") > 50                 # a real unified diff of the added scenarios
    assert client.get("/api/config").json()["summary"] == []         # nothing applied before acceptance
    assert client.post(f"/api/proposals/{pid}/accept").status_code == 200
    summ = client.get("/api/config").json()["summary"]
    assert len(summ) == 6 and all("lagos" in s["name"].lower() for s in summ)
    # edit via chat -> proposal -> reject keeps config
    out = chat(client, "set records to 500 for all scenarios")
    assert out["tool_calls"][0]["name"] == "set_record_count"
    pid2 = out["proposals"][0]["id"]
    assert client.post(f"/api/proposals/{pid2}/reject").json() == {"ok": True}
    assert client.get("/api/config").json()["summary"][0]["records"] != 500 * 6  # rejected edit changed nothing
    v_before = client.get("/api/config").json()["version_id"]
    out = chat(client, "set records to 400 for all scenarios")
    assert client.post(f"/api/proposals/{out['proposals'][0]['id']}/accept").status_code == 200
    cfg = parse_config(client.get("/api/config").json()["content"])
    assert all(t.record_count == 400 for s in cfg.scenarios for t in s.tables)
    assert client.get("/api/config").json()["version_id"] > v_before
    # chat tools: validate, list, versions, schema
    v = chat(client, "validate the config")
    assert v["tool_calls"][0]["name"] == "validate_config" and '"valid": true' in v["tool_calls"][0]["result_summary"]
    assert chat(client, "list scenarios")["tool_calls"][0]["name"] == "list_scenarios"
    assert chat(client, "show saved config versions")["tool_calls"][0]["name"] == "list_config_versions"
    assert chat(client, "describe schema")["tool_calls"][0]["name"] == "describe_schema"
    hist = client.get("/api/chat/t1").json()
    assert [m["role"] for m in hist][:2] == ["user", "tool"] and hist[-1]["role"] == "assistant"


def test_chat_driven_ingest_pause_resume_and_duplicate_confirmation(client, settings):
    cfgd = make_cfg(users=1200, batch=300, workers=2)
    vid = client.put("/api/config", json={"content": yaml.safe_dump(cfgd), "fmt": "yaml", "note": "ingest cfg"}).json()["version_id"]
    # starting without explicit confirmation must NOT start
    out = chat(client, "start ingest", "s2")
    assert out["tool_calls"][0]["name"] == "start_ingest" and "confirm" in out["tool_calls"][0]["result_summary"].lower()
    assert client.get("/api/runs").json() == []
    out = chat(client, "yes, go ahead and start ingest now", "s2")
    rid = client.get("/api/runs").json()[0]["id"]
    s = wait_status(client, rid)
    assert s["status"] == "completed" and s["total_done"] == s["total_planned"] == 1200 + 1200 + 2400 + 60 + 400 + 1200
    assert table_total(settings.db_url) == s["total_done"]
    rep = client.get(f"/api/runs/{rid}/report").json()
    assert rep["ok"], rep["issues"]
    # scenario memory: same scenario again -> precheck reports it, start returns 409 with options
    pc = client.post("/api/runs/precheck", json={}).json()
    assert pc["findings"] and pc["findings"][0]["kind"] == "exact" and pc["findings"][0]["options"] == ["skip", "append", "replace"]
    r = client.post("/api/runs", json={})
    assert r.status_code == 409 and r.json()["detail"]["findings"][0]["matched_run_id"] == rid
    # chat flow: agent explains and asks, then proceeds with the chosen decision
    out = chat(client, "yes start ingest now", "s2")
    assert "already ingested" in out["reply"] and "skip, append, or replace" in out["reply"]
    before = table_total(settings.db_url)
    out = chat(client, "start ingest, append", "s2")
    assert "started run" in out["reply"]
    rid2 = [r for r in client.get("/api/runs").json() if r["id"] != rid][0]["id"]
    assert wait_status(client, rid2)["status"] == "completed"
    assert table_total(settings.db_url) == 2 * before
    led = client.get("/api/ledger").json()
    assert {l["run_id"] for l in led} >= {rid, rid2}
    # skip decision via API -> nothing to run
    assert client.post("/api/runs", json={"decisions": {"t_scn": "skip"}}).status_code == 422
    # purge the append run via API
    assert client.post(f"/api/runs/{rid2}/purge").json()["deleted"]["users"] == 1200
    assert table_total(settings.db_url) == before
    # SSE stream emits JSON snapshots
    with client.stream("GET", f"/api/runs/{rid}/events") as resp:
        line = next(l for l in resp.iter_lines() if l.startswith("data:"))
        assert json.loads(line[5:])["run_id"] == rid


def test_api_pause_resume(client, settings):
    cfgd = make_cfg(users=1500, batch=250, workers=2)
    client.put("/api/config", json={"content": yaml.safe_dump(cfgd), "fmt": "yaml"})
    rid = client.post("/api/runs", json={"options": {"throttle_rows_per_sec": 1500}}).json()["run_id"]
    for _ in range(120):  # pause once the first batches are committed
        time.sleep(0.25)
        if client.get(f"/api/runs/{rid}").json()["total_done"] > 0:
            break
    assert client.post(f"/api/runs/{rid}/pause").status_code == 200
    s = wait_status(client, rid, ("paused",))
    assert 0 < s["total_done"] < s["total_planned"]
    # chat can report progress while paused
    assert "paused" in chat(client, "what's the progress status?", "s3")["reply"]
    assert client.post(f"/api/runs/{rid}/resume").status_code == 200
    s = wait_status(client, rid, ("completed",), 180)
    assert s["total_done"] == s["total_planned"] and table_total(settings.db_url) == s["total_planned"]


def test_scenario_graph_interrupt_survives_new_instance(settings):
    svc = Services(settings)
    g1 = ScenarioGenerator(svc)
    r = g1.start("sess", "blue-collar staffing in Dubai", 30)
    assert r["proposal_id"] and r["generated"] == 30 and r["graph_thread"]
    assert svc.parse_current().scenarios == []
    g2 = ScenarioGenerator(Services(settings))        # simulates a restarted process
    res = g2.resume(r["graph_thread"], "accept")
    assert res["accepted"] and res["added"] == 30
    cfg = svc.parse_current()
    assert len(cfg.scenarios) == 30 and len({s.fingerprint for s in cfg.scenarios}) == 30
    r2 = g2.start("sess", "blue-collar staffing in Dubai", 30)          # dedupes against existing config
    assert r2.get("generated", 0) == 30 and not ({s.name for s in parse_config(svc.state.proposal_get(r2["proposal_id"])["new_content"]).scenarios[30:]} & {s.name for s in cfg.scenarios})


def test_generates_thousands_of_distinct_scenarios_quickly(settings):
    svc = Services(settings)
    g = ScenarioGenerator(svc)
    t0 = time.time()
    r = g.start("big", "global hiring mix", 1000)
    assert r["generated"] == 1000, r
    cfg = parse_config(svc.state.proposal_get(r["proposal_id"])["new_content"])
    assert len({s.fingerprint for s in cfg.scenarios}) == 1000 and len({s.id for s in cfg.scenarios}) == 1000
    assert time.time() - t0 < 60


def test_token_budget_guard_and_json_extraction():
    b = TokenBudget(10)
    b.check()
    b.used = 11
    with pytest.raises(TokenBudgetExceeded):
        b.check()
    assert extract_json_array("```json\n[{\"a\":1}]\n```") == [{"a": 1}]
    assert extract_json_array("{\"scenarios\":[{\"a\":2}]}") == [{"a": 2}]
    assert extract_json_array("not json") == []

"""Real-browser check of the single-page UI (Playwright + installed Chrome). Skipped when unavailable."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psycopg
import pytest
import yaml

from .conftest import BUSINESS_TABLES, make_cfg

pw = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def server(settings):
    port = _free_port()
    env = {**os.environ, "TARGET_DB_URL": settings.db_url, "ENGINE_STATE_DIR": str(settings.state_dir), "LLM_PROVIDER": "fake"}
    root = Path(__file__).resolve().parents[1]
    p = subprocess.Popen([sys.executable, "-m", "ingestion_engine.cli", "serve", "--port", str(port)], env=env, cwd=root,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.5)
    yield base
    p.kill()
    p.wait()


@pytest.fixture()
def page(server):
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # pragma: no cover
            pytest.skip(f"no Chrome for Playwright: {e}")
        pg = browser.new_page(viewport={"width": 1500, "height": 950})
        pg.goto(server)
        yield pg
        browser.close()


def test_ui_chat_proposal_accept_run_pause_resume_and_duplicate_modal(page, server, settings):
    page.wait_for_function("document.querySelector('#llmBadge').innerText.toLowerCase().includes('fake')", timeout=60000)
    # --- chat -> LLM tool call -> proposal diff -> accept
    page.fill("#chatInput", "generate 3 scenarios for Dubai blue-collar")
    page.click("#chatSend")
    page.wait_for_selector("#chatlog >> text=generate_scenarios", timeout=60000)
    page.wait_for_selector("text=Accept", timeout=30000)
    page.click("button:has-text('Accept') >> nth=0")
    page.wait_for_function("document.querySelector('#cfgText').value.includes('scn_dubai')", timeout=30000)
    assert page.locator("#summary tr").count() >= 3
    # --- the user edits the config in the editor (small run) and saves a new version
    cfg = yaml.safe_load(page.input_value("#cfgText"))
    small = make_cfg(users=2400, batch=300, workers=2)
    page.fill("#cfgText", yaml.safe_dump(small))
    page.fill("#cfgNote", "ui small config")
    page.click("#cfgSave")
    for _ in range(40):
        versions = httpx.get(server + "/api/config/versions").json()
        if any(v["note"] == "ui small config" for v in versions):
            break
        time.sleep(0.5)
    assert any(v["note"] == "ui small config" and v["active"] for v in versions) and len(cfg["scenarios"]) >= 3
    # --- start ingest from the UI, pause, resume to completion
    page.fill("#optWorkers", "2")
    page.fill("#optBatch", "300")
    page.click("#btnStart")
    page.wait_for_function("document.querySelector('#progress').innerText.match(/running|completed|planned/i)", timeout=60000)
    page.click("#btnPause")
    page.wait_for_function("document.querySelector('#progress').innerText.match(/paused|completed/i)", timeout=90000)
    state = page.inner_text("#progress").lower()
    if "paused" in state:
        page.click("#btnResume")
    page.wait_for_function("document.querySelector('#progress').innerText.toLowerCase().includes('completed')", timeout=180000)
    with psycopg.connect(settings.db_url) as c:
        total = sum(c.execute(f"select count(*) from {t}").fetchone()[0] for t in BUSINESS_TABLES)
    assert total == 2400 + 2400 + 4800 + 120 + 800 + 2400
    # --- same scenario again: confirmation modal with skip/append/replace
    page.click("#btnStart")
    page.wait_for_selector(".modal >> text=Confirm ingest decisions", timeout=30000)
    modal = page.inner_text(".modal").lower()
    assert "skip" in modal and "append" in modal and "replace" in modal and "exact" in modal
    page.click(".modal button:has-text('Cancel')")
    assert page.locator(".modal").count() == 0
    runs = httpx.get(server + "/api/runs").json()
    assert len(runs) == 1  # cancelling the modal did not start a second run

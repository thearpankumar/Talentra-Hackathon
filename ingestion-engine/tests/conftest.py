from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

HERE = Path(__file__).parent
BASE_URL = os.getenv("TEST_DB_BASE", "postgresql://root@localhost:26357")
TEST_DB = "talentra_test"
TEST_URL = f"{BASE_URL}/{TEST_DB}?sslmode=disable"

BUSINESS_TABLES = ["application", "candidate_skill", "job_requirement", "employer", "candidate_profile", "users"]


def _crdb_up() -> bool:
    try:
        with psycopg.connect(f"{BASE_URL}/defaultdb?sslmode=disable", connect_timeout=3):
            return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def db_url() -> str:
    if not _crdb_up():
        pytest.skip("local CockroachDB (docker compose up -d) not reachable")
    # Reuse the test database across sessions (CockroachDB DDL is slow); TEST_DB_RESET=1 recreates it.
    with psycopg.connect(f"{BASE_URL}/defaultdb?sslmode=disable", autocommit=True) as c:
        if os.getenv("TEST_DB_RESET"):
            c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} CASCADE")
        c.execute(f"CREATE DATABASE IF NOT EXISTS {TEST_DB}")
    with psycopg.connect(TEST_URL, autocommit=True) as c:
        have = c.execute("select count(*) from information_schema.tables where table_schema='public' and table_name='application'").fetchone()[0]
        if not have:
            for f in ("stub_schema.sql", "stub_seed.sql"):
                c.execute((HERE / f).read_text(encoding="utf-8"))
    return TEST_URL


@pytest.fixture()
def clean_db(db_url: str) -> str:
    with psycopg.connect(db_url, autocommit=True) as c:
        for t in ("talentra_ingest_batch", "talentra_ingest_ledger"):
            try:
                c.execute(f"DELETE FROM {t} WHERE true")
            except psycopg.Error:
                pass
        for t in BUSINESS_TABLES:
            c.execute(f"DELETE FROM {t} WHERE true")
    return db_url


@pytest.fixture()
def settings(clean_db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TARGET_DB_URL", clean_db)
    monkeypatch.setenv("ENGINE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    from ingestion_engine.settings import Settings
    return Settings()


def make_cfg(users: int = 1500, seed: int = 11, scn_id: str = "t_scn", dup: float = 0.03, extra_tables: bool = True,
             batch: int = 500, workers: int = 3, locale_mix: dict | None = None) -> dict:
    tables = [
        {"table": "users", "record_count": users, "constants": {"role": "candidate"}},
        {"table": "candidate_profile", "record_count": users,
         "fields": {"years_experience": {"dist": "exponential", "params": {"scale": 1.5}, "min": 0, "max": 6, "round": 1}}},
    ]
    if extra_tables:
        tables += [
            {"table": "candidate_skill", "record_count": users * 2, "fk": {"candidate_profile_id": {"scope": "scenario"}}},
            {"table": "employer", "record_count": max(10, users // 20)},
            {"table": "job_requirement", "record_count": users // 3, "fk": {"employer_id": {"scope": "scenario", "skew": "zipf", "alpha": 1.2}}},
            {"table": "application", "record_count": users, "fk": {"job_requirement_id": {"scope": "scenario", "skew": "zipf"},
                                                                    "candidate_profile_id": {"scope": "scenario", "skew": "pareto", "alpha": 1.5}}},
        ]
    return {"version": 1, "defaults": {"batch_size": batch, "workers": workers},
            "scenarios": [{"id": scn_id, "name": f"Test scenario {scn_id}", "seed": seed, "duplicate_rate": dup, "null_rate": 0.1,
                           "locale_mix": locale_mix or {"en_IN": 0.5, "hi_IN": 0.2, "ar_AE": 0.2, "zh_CN": 0.1}, "tables": tables}]}

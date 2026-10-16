"""SQLite persistence: config versions, scenarios, runs, per-batch checkpoints, chat history, ingest ledger."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS config_version (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, fmt TEXT NOT NULL, content TEXT NOT NULL,
  note TEXT, author TEXT NOT NULL DEFAULT 'user');
CREATE TABLE IF NOT EXISTS scenario (
  id TEXT NOT NULL, fingerprint TEXT NOT NULL, name TEXT, description TEXT, features TEXT, spec TEXT,
  config_version_id INTEGER, created_at REAL NOT NULL, PRIMARY KEY (id, fingerprint));
CREATE TABLE IF NOT EXISTS run (
  id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, finished_at REAL,
  config_version_id INTEGER, config_json TEXT NOT NULL, options TEXT NOT NULL DEFAULT '{}',
  total_planned INTEGER NOT NULL DEFAULT 0, total_done INTEGER NOT NULL DEFAULT 0, error TEXT, elapsed REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS run_table (
  run_id TEXT NOT NULL, scenario_id TEXT NOT NULL, table_name TEXT NOT NULL, record_count INTEGER NOT NULL,
  batch_size INTEGER NOT NULL, n_batches INTEGER NOT NULL, token_base INTEGER NOT NULL, eff_seed INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', rows_done INTEGER NOT NULL DEFAULT 0, batches_done INTEGER NOT NULL DEFAULT 0,
  decision TEXT NOT NULL DEFAULT 'new', PRIMARY KEY (run_id, scenario_id, table_name));
CREATE TABLE IF NOT EXISTS batch (
  run_id TEXT NOT NULL, scenario_id TEXT NOT NULL, table_name TEXT NOT NULL, batch_idx INTEGER NOT NULL,
  rows INTEGER NOT NULL, inserted INTEGER NOT NULL, dups INTEGER NOT NULL DEFAULT 0, nulls INTEGER NOT NULL DEFAULT 0,
  ms REAL NOT NULL DEFAULT 0, committed_at REAL NOT NULL, PRIMARY KEY (run_id, scenario_id, table_name, batch_idx));
CREATE TABLE IF NOT EXISTS run_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts REAL NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_message (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, ts REAL NOT NULL, role TEXT NOT NULL,
  content TEXT NOT NULL, meta TEXT);
CREATE TABLE IF NOT EXISTS ledger (
  run_id TEXT NOT NULL, scenario_id TEXT NOT NULL, fingerprint TEXT NOT NULL, name TEXT, features TEXT,
  status TEXT NOT NULL, rows INTEGER NOT NULL DEFAULT 0, tables TEXT, ingested_at REAL NOT NULL,
  PRIMARY KEY (run_id, scenario_id));
CREATE TABLE IF NOT EXISTS token_counter (id INTEGER PRIMARY KEY CHECK (id=1), next_token INTEGER NOT NULL);
INSERT OR IGNORE INTO token_counter VALUES (1, 1000000);
CREATE TABLE IF NOT EXISTS pending_proposal (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, created_at REAL NOT NULL, status TEXT NOT NULL,
  summary TEXT, new_content TEXT NOT NULL, diff TEXT NOT NULL, graph_thread TEXT, base_version_id INTEGER);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


class State:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(DDL)

    # ---- helpers
    def q(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        r = self.q(sql, params)
        return r[0] if r else None

    def x(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).rowcount

    def tx(self, stmts: list[tuple[str, tuple]]) -> None:
        with self._lock:
            self.conn.execute("BEGIN")
            try:
                for s, p in stmts:
                    self.conn.execute(s, p)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    # ---- config versions
    def save_config(self, content: str, fmt: str = "yaml", note: str = "", author: str = "user") -> int:
        with self._lock:
            cur = self.conn.execute("INSERT INTO config_version(created_at,fmt,content,note,author) VALUES (?,?,?,?,?)",
                                    (time.time(), fmt, content, note, author))
            return int(cur.lastrowid)

    def latest_config(self) -> dict[str, Any] | None:
        return self.one("SELECT * FROM config_version ORDER BY id DESC LIMIT 1")

    def config_versions(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.q("SELECT id, created_at, fmt, note, author, length(content) AS size FROM config_version ORDER BY id DESC LIMIT ?", (limit,))

    def get_config(self, vid: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM config_version WHERE id=?", (vid,))

    # ---- scenarios
    def upsert_scenario(self, sc: dict[str, Any], fingerprint: str, features: list[str], version_id: int | None) -> None:
        self.x("INSERT OR REPLACE INTO scenario(id,fingerprint,name,description,features,spec,config_version_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
               (sc["id"], fingerprint, sc.get("name"), sc.get("description"), json.dumps(features), json.dumps(sc), version_id, time.time()))

    # ---- tokens
    def alloc_tokens(self, count: int, floor: int = 0) -> int:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self.conn.execute("SELECT next_token FROM token_counter WHERE id=1").fetchone()[0]
                start = max(cur, floor)
                self.conn.execute("UPDATE token_counter SET next_token=? WHERE id=1", (start + count + 1000,))
                self.conn.execute("COMMIT")
                return int(start)
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    # ---- runs
    def new_run_id(self) -> str:
        return "run_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]

    def event(self, run_id: str, level: str, message: str) -> None:
        self.x("INSERT INTO run_event(run_id,ts,level,message) VALUES (?,?,?,?)", (run_id, time.time(), level, message[:2000]))

    def set_run_status(self, run_id: str, status: str, error: str | None = None) -> None:
        now = time.time()
        if status == "running":
            self.x("UPDATE run SET status=?, started_at=COALESCE(started_at,?), error=NULL WHERE id=?", (status, now, run_id))
        elif status in ("completed", "failed", "cancelled"):
            self.x("UPDATE run SET status=?, finished_at=?, error=? WHERE id=?", (status, now, error, run_id))
        else:
            self.x("UPDATE run SET status=?, error=? WHERE id=?", (status, error, run_id))

    def recover_orphans(self) -> int:
        n = self.x("UPDATE run SET status='interrupted' WHERE status IN ('running','pausing','cancelling')")
        return n

    def committed_batches(self, run_id: str, scenario_id: str, table: str) -> set[int]:
        return {r["batch_idx"] for r in self.q(
            "SELECT batch_idx FROM batch WHERE run_id=? AND scenario_id=? AND table_name=?", (run_id, scenario_id, table))}

    def record_batch(self, run_id: str, scn: str, table: str, idx: int, rows: int, inserted: int, dups: int, nulls: int, ms: float) -> bool:
        """Insert checkpoint; returns True if newly recorded (idempotent)."""
        with self._lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO batch(run_id,scenario_id,table_name,batch_idx,rows,inserted,dups,nulls,ms,committed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, scn, table, idx, rows, inserted, dups, nulls, ms, time.time()))
            if cur.rowcount:
                self.conn.execute(
                    "UPDATE run_table SET rows_done=rows_done+?, batches_done=batches_done+1 WHERE run_id=? AND scenario_id=? AND table_name=?",
                    (inserted, run_id, scn, table))
                self.conn.execute("UPDATE run SET total_done=total_done+? WHERE id=?", (inserted, run_id))
                return True
            return False

    # ---- chat
    def add_chat(self, session_id: str, role: str, content: str, meta: dict | None = None) -> None:
        self.x("INSERT INTO chat_message(session_id,ts,role,content,meta) VALUES (?,?,?,?,?)",
               (session_id, time.time(), role, content, json.dumps(meta) if meta else None))

    def chat_history(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return list(reversed(self.q("SELECT * FROM chat_message WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit))))

    # ---- ledger
    def ledger_entries(self) -> list[dict[str, Any]]:
        rows = self.q("SELECT * FROM ledger ORDER BY ingested_at DESC")
        for r in rows:
            r["features"] = json.loads(r["features"] or "[]")
            r["tables"] = json.loads(r["tables"] or "{}")
        return rows

    def ledger_put(self, run_id: str, scenario_id: str, fingerprint: str, name: str, features: list[str], status: str,
                   rows: int, tables: dict[str, int]) -> None:
        self.x("INSERT OR REPLACE INTO ledger(run_id,scenario_id,fingerprint,name,features,status,rows,tables,ingested_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (run_id, scenario_id, fingerprint, name, json.dumps(features), status, rows, json.dumps(tables), time.time()))

    # ---- kv / active config
    def kv_get(self, k: str, default: str | None = None) -> str | None:
        r = self.one("SELECT v FROM kv WHERE k=?", (k,))
        return r["v"] if r else default

    def kv_set(self, k: str, v: str) -> None:
        self.x("INSERT OR REPLACE INTO kv(k,v) VALUES (?,?)", (k, v))

    # ---- proposals
    def proposal_create(self, session_id: str, summary: str, new_content: str, diff: str,
                        graph_thread: str | None = None, base_version_id: int | None = None) -> str:
        pid = "prop_" + uuid.uuid4().hex[:10]
        self.x("INSERT INTO pending_proposal(id,session_id,created_at,status,summary,new_content,diff,graph_thread,base_version_id) VALUES (?,?,?,?,?,?,?,?,?)",
               (pid, session_id, time.time(), "pending", summary, new_content, diff, graph_thread, base_version_id))
        return pid

    def proposal_get(self, pid: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM pending_proposal WHERE id=?", (pid,))

    def proposals(self, status: str | None = "pending") -> list[dict[str, Any]]:
        if status:
            return self.q("SELECT id,session_id,created_at,status,summary,diff,graph_thread FROM pending_proposal WHERE status=? ORDER BY created_at DESC", (status,))
        return self.q("SELECT id,session_id,created_at,status,summary,diff,graph_thread FROM pending_proposal ORDER BY created_at DESC LIMIT 100")

    def proposal_set(self, pid: str, status: str) -> None:
        self.x("UPDATE pending_proposal SET status=? WHERE id=?", (status, pid))

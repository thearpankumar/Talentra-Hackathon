"""Run orchestration: planning, scenario memory, multiprocess COPY loading, pause/resume, purge, validation."""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import psycopg
from psycopg import errors as pgerr

from .config import EngineConfig, Scenario, parse_config, validate_against_schema
from .generate import RunContext, crc, generate_batch, to_copy_bytes
from .schema import Schema, introspect
from .settings import Settings, get_settings
from .state import State

log = logging.getLogger("engine.runner")

LEDGER_DDL = [
    """CREATE TABLE IF NOT EXISTS talentra_ingest_ledger (
         run_id STRING NOT NULL, scenario_id STRING NOT NULL, fingerprint STRING NOT NULL, name STRING,
         features JSONB, status STRING NOT NULL, rows_total INT8 NOT NULL DEFAULT 0, tables JSONB,
         token_end INT8 NOT NULL DEFAULT 0, ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
         PRIMARY KEY (run_id, scenario_id))""",
    """CREATE TABLE IF NOT EXISTS talentra_ingest_batch (
         run_id STRING NOT NULL, scenario_id STRING NOT NULL, table_name STRING NOT NULL, batch_idx INT8 NOT NULL,
         rows_requested INT8 NOT NULL, rows_inserted INT8 NOT NULL, committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
         PRIMARY KEY (run_id, scenario_id, table_name, batch_idx))""",
]


class ConfigError(Exception):
    def __init__(self, issues: list[str]):
        super().__init__("; ".join(issues))
        self.issues = issues


class NeedsConfirmation(Exception):
    def __init__(self, findings: list[dict[str, Any]]):
        super().__init__("scenario(s) already ingested; decisions required")
        self.findings = findings


@dataclass
class RunControl:
    pause: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if (a | b) else 0.0


# ======================================================================================================
# worker process side
# ======================================================================================================
_W: dict[str, Any] = {}


def _connect(url: str) -> psycopg.Connection:
    c = psycopg.connect(url, autocommit=True, prepare_threshold=None)
    return c


def _winit(schema: Schema, db_url: str, pool_dir: str, run_id: str, batch_size: int) -> None:
    from pathlib import Path
    _W["url"] = db_url
    _W["conn"] = _connect(db_url)
    _W["ctx"] = RunContext(schema, run_id, Path(pool_dir), batch_size, conn_factory=lambda: _connect(db_url))
    _W["scn_cache"] = {}
    # die with the parent: a hard-killed engine must not leave orphan workers holding DB connections
    import multiprocessing as mp

    def _watch() -> None:
        parent = mp.parent_process()
        while parent is not None:
            time.sleep(1.0)
            if not parent.is_alive():
                os._exit(0)

    threading.Thread(target=_watch, daemon=True, name="parent-watch").start()


def _scn(d: dict[str, Any]) -> Scenario:
    key = (d["id"], d["seed"])
    c = _W["scn_cache"]
    if key not in c:
        c[key] = Scenario.model_validate(d)
    return c[key]


def _load(conn: psycopg.Connection, table: str, cols: list[str], data: bytes, staged: bool) -> int:
    collist = ", ".join(f'"{c}"' for c in cols)
    if not staged:
        with conn.cursor() as cur:
            with cur.copy(f'COPY "{table}" ({collist}) FROM STDIN') as cp:
                cp.write(data)
            return cur.rowcount
    stg = f"talentra_ingest_stg_{table}_{os.getpid()}"
    made: set = _W.setdefault("stg_made", set())
    key = (id(conn), table)
    if key not in made:
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{stg}" AS SELECT {collist} FROM "{table}" LIMIT 0')
        made.add(key)
    conn.execute(f'DELETE FROM "{stg}"')
    with conn.cursor() as cur:
        with cur.copy(f'COPY "{stg}" ({collist}) FROM STDIN') as cp:
            cp.write(data)
        cur.execute(f'INSERT INTO "{table}" ({collist}) SELECT {collist} FROM "{stg}" ON CONFLICT DO NOTHING')
        return cur.rowcount


def _worker_batch(task: dict[str, Any]) -> dict[str, Any]:
    ctx: RunContext = _W["ctx"]
    scn = _scn(task["scn"])
    ts = next(t for t in scn.tables if t.table == task["table"])
    t0 = time.perf_counter()
    arrs, cols, stats = generate_batch(ctx, scn, ts, task["batch"], task["n"], task["token_base"])
    data = to_copy_bytes(arrs, cols)
    t_gen = time.perf_counter() - t0
    table = task["table"]
    staged = task["staged"]
    last: Exception | None = None
    for attempt in range(8):
        conn: psycopg.Connection = _W["conn"]
        try:
            if conn.closed:
                conn = _W["conn"] = _connect(_W["url"])
            with conn.transaction():
                done = conn.execute(
                    "SELECT rows_inserted FROM talentra_ingest_batch WHERE run_id=%s AND scenario_id=%s AND table_name=%s AND batch_idx=%s",
                    (task["run_id"], task["scenario_id"], table, task["batch"])).fetchone()
                if done:
                    return {**_ret(task), "inserted": done[0], "dups": 0, "nulls": 0, "ms": 0.0, "skipped": True}
                inserted = _load(conn, table, cols, data, staged)
                conn.execute(
                    "INSERT INTO talentra_ingest_batch(run_id,scenario_id,table_name,batch_idx,rows_requested,rows_inserted) VALUES (%s,%s,%s,%s,%s,%s)",
                    (task["run_id"], task["scenario_id"], table, task["batch"], task["n"], inserted))
            return {**_ret(task), "inserted": inserted, "dups": stats["dups"], "nulls": stats["nulls"],
                    "ms": (time.perf_counter() - t0) * 1000, "gen_ms": t_gen * 1000, "skipped": False, "staged": staged, "retries": attempt}
        except pgerr.UniqueViolation as e:
            if staged:
                raise
            staged = True  # fall back to ON CONFLICT DO NOTHING path
            last = e
        except (pgerr.SerializationFailure, pgerr.OperationalError, pgerr.AdminShutdown, pgerr.ConnectionException,
                pgerr.DeadlockDetected, psycopg.InterfaceError) as e:
            last = e
            if _W["conn"].closed:
                _W["conn"] = None  # type: ignore[assignment]
                _W["conn"] = _connect(_W["url"])
            time.sleep(min(5.0, 0.05 * (2 ** attempt)) * (0.5 + np.random.random()))
    raise RuntimeError(f"batch {table}#{task['batch']} failed after retries: {last}")


def _ret(task: dict[str, Any]) -> dict[str, Any]:
    return {"scenario_id": task["scenario_id"], "table": task["table"], "batch": task["batch"], "n": task["n"]}


# ======================================================================================================
# main-process side
# ======================================================================================================
class Runner:
    def __init__(self, settings: Settings | None = None, state: State | None = None):
        self.s = settings or get_settings()
        self.state = state or State(self.s.state_db)
        self.progress: dict[str, dict[str, Any]] = {}
        self.controls: dict[str, RunControl] = {}
        self._lock = threading.Lock()

    # ---- db helpers
    def connect(self, autocommit: bool = True) -> psycopg.Connection:
        return psycopg.connect(self.s.db_url, autocommit=autocommit, prepare_threshold=None)

    def ensure_db_objects(self) -> None:
        with self.connect() as c:
            for ddl in LEDGER_DDL:
                c.execute(ddl)

    def get_schema(self) -> Schema:
        with self.connect() as c:
            sch = introspect(c, self.s.schema)
        if self.s.data_dictionary:
            from .schema import load_data_dictionary
            load_data_dictionary(self.s.data_dictionary, sch)
        return sch

    # ---- scenario memory
    def check_memory(self, cfg: EngineConfig, exclude_run: str | None = None) -> list[dict[str, Any]]:
        """Compare scenario fingerprints with the local ledger and with the target DB ledger."""
        entries: list[dict[str, Any]] = []
        for e in self.state.ledger_entries():
            if e["status"] in ("purged", "failed_empty"):
                continue
            entries.append({**e, "source": "sqlite"})
        try:
            self.ensure_db_objects()
            with self.connect() as c:
                for r in c.execute("SELECT run_id, scenario_id, fingerprint, name, features, status, rows_total, tables, ingested_at "
                                   "FROM talentra_ingest_ledger WHERE status NOT IN ('purged')").fetchall():
                    entries.append({"run_id": r[0], "scenario_id": r[1], "fingerprint": r[2], "name": r[3],
                                    "features": r[4] or [], "status": r[5], "rows": r[6], "tables": r[7] or {},
                                    "ingested_at": r[8].timestamp() if r[8] else None, "source": "db"})
        except Exception as e:  # DB unreachable: memory is best-effort
            log.warning("could not read DB ledger: %s", e)
        findings: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for sc in cfg.scenarios:
            fp = sc.fingerprint or sc.compute_fingerprint()
            feats = set(sc.features())
            best: dict[str, Any] | None = None
            for e in entries:
                if e["run_id"] == exclude_run:
                    continue
                if e["fingerprint"] == fp:
                    kind, sim = "exact", 1.0
                elif e["scenario_id"] == sc.id:
                    kind, sim = "same_id", jaccard(feats, set(e["features"]))
                else:
                    sim = jaccard(feats, set(e["features"]))
                    if sim < 0.75:
                        continue
                    kind = "similar"
                rank = {"exact": 3, "same_id": 2, "similar": 1}[kind]
                cand = {"scenario_id": sc.id, "scenario_name": sc.name, "kind": kind, "similarity": round(sim, 3),
                        "matched_scenario_id": e["scenario_id"], "matched_name": e["name"], "matched_run_id": e["run_id"],
                        "rows": e["rows"], "status": e["status"], "ingested_at": e["ingested_at"], "source": e["source"],
                        "options": ["skip", "append", "replace"]}
                if best is None or (rank, sim) > (best["_rank"], best["similarity"]):
                    best = {**cand, "_rank": rank}
            if best:
                best.pop("_rank")
                key = (best["scenario_id"], best["matched_run_id"], best["source"])
                if key not in seen:
                    findings.append(best)
                    seen.add(key)
        return findings

    # ---- planning
    def create_run(self, cfg: EngineConfig, config_version_id: int | None = None,
                   decisions: dict[str, str] | None = None, options: dict[str, Any] | None = None) -> str:
        decisions = decisions or {}
        options = options or {}
        schema = self.get_schema()
        issues = validate_against_schema(cfg, schema)
        if issues:
            raise ConfigError(issues)
        self.ensure_db_objects()
        findings = self.check_memory(cfg)
        missing = [f for f in findings if f["scenario_id"] not in decisions]
        if missing and not options.get("force_new"):
            raise NeedsConfirmation(findings)
        fmap = {f["scenario_id"]: f for f in findings}
        keep: list[Scenario] = []
        replace_runs: list[tuple[str, str]] = []
        for sc in cfg.scenarios:
            d = decisions.get(sc.id, "new")
            if d == "skip":
                continue
            if d == "replace" and sc.id in fmap:
                replace_runs.append((fmap[sc.id]["matched_run_id"], fmap[sc.id]["matched_scenario_id"]))
            keep.append(sc)
            decisions.setdefault(sc.id, "new")
        if not keep:
            raise ConfigError(["every scenario was skipped; nothing to run"])
        for rid, sid in replace_runs:
            self.purge_run(rid, [sid])
        bs = int(options.get("batch_size") or cfg.defaults.batch_size)
        workers = int(options.get("workers") or cfg.defaults.workers)
        run_id = self.state.new_run_id()
        run_cfg = cfg.model_copy(update={"scenarios": keep})
        floor = 0
        with self.connect() as c:
            floor = c.execute("SELECT coalesce(max(token_end),0) FROM talentra_ingest_ledger").fetchone()[0]
        total = sum(sc.total_records for sc in keep)
        opts = {"batch_size": bs, "workers": workers, "throttle_rows_per_sec": options.get("throttle_rows_per_sec", cfg.defaults.throttle_rows_per_sec),
                "decisions": decisions}
        self.state.x("INSERT INTO run(id,status,created_at,config_version_id,config_json,options,total_planned) VALUES (?,?,?,?,?,?,?)",
                     (run_id, "planned", time.time(), config_version_id, json.dumps(run_cfg.model_dump(mode="json")), json.dumps(opts), total))
        order = schema.order()
        with self.connect() as c:
            for sc in keep:
                attempt = (self.state.one("SELECT count(DISTINCT run_id) AS n FROM run_table WHERE scenario_id=?", (sc.id,)) or {"n": 0})["n"]
                eff_seed = (sc.seed * 1_000_003 + crc(sc.id) + attempt * 7919) & 0xFFFFFFFF
                token_end = 0
                counts: dict[str, int] = {}
                for tsp in sorted(sc.tables, key=lambda x: order.index(x.table) if x.table in order else 0):
                    base = self.state.alloc_tokens(max(tsp.record_count, 1), floor)
                    token_end = max(token_end, base + tsp.record_count)
                    nb = math.ceil(tsp.record_count / bs)
                    self.state.x("INSERT INTO run_table(run_id,scenario_id,table_name,record_count,batch_size,n_batches,token_base,eff_seed,decision) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (run_id, sc.id, tsp.table, tsp.record_count, bs, nb, base, eff_seed, decisions.get(sc.id, "new")))
                    counts[tsp.table] = tsp.record_count
                fp = sc.fingerprint or sc.compute_fingerprint()
                self.state.ledger_put(run_id, sc.id, fp, sc.name, sc.features(), "running", 0, counts)
                self.state.upsert_scenario(sc.model_dump(mode="json"), fp, sc.features(), config_version_id)
                c.execute("""INSERT INTO talentra_ingest_ledger(run_id,scenario_id,fingerprint,name,features,status,rows_total,tables,token_end)
                             VALUES (%s,%s,%s,%s,%s,'running',0,%s,%s) ON CONFLICT (run_id,scenario_id) DO NOTHING""",
                          (run_id, sc.id, fp, sc.name, json.dumps(sc.features()), json.dumps(counts), token_end))
        self.state.event(run_id, "info", f"run planned: {len(keep)} scenarios, {total} rows, batch_size={bs}, workers={workers}")
        return run_id

    # ---- execution
    def get_control(self, run_id: str) -> RunControl:
        with self._lock:
            return self.controls.setdefault(run_id, RunControl())

    def execute(self, run_id: str, control: RunControl | None = None) -> str:
        """Blocking. Returns final status: completed | paused | cancelled | failed."""
        control = control or self.get_control(run_id)
        control.pause.clear()
        control.cancel.clear()
        run = self.state.one("SELECT * FROM run WHERE id=?", (run_id,))
        if not run:
            raise KeyError(run_id)
        if run["status"] in ("completed", "cancelled"):
            return run["status"]
        cfg = parse_config(run["config_json"])
        opts = json.loads(run["options"])
        bs, workers = int(opts["batch_size"]), int(opts["workers"])
        throttle = opts.get("throttle_rows_per_sec")
        schema = self.get_schema()
        self.ensure_db_objects()
        self._reconcile(run_id)
        ctx = RunContext(schema, run_id, self.s.pool_dir, bs, conn_factory=lambda: self.connect())
        scn_by_id = {s.id: s for s in cfg.scenarios}
        nodes = self.state.q("SELECT * FROM run_table WHERE run_id=? ORDER BY rowid", (run_id,))
        node_key = lambda n: (n["scenario_id"], n["table_name"])  # noqa: E731
        remaining: dict[tuple[str, str], list[int]] = {}
        for n in nodes:
            done = self.state.committed_batches(run_id, *node_key(n))
            remaining[node_key(n)] = [b for b in range(n["n_batches"]) if b not in done]
        planned_total = sum(n["record_count"] for n in nodes)
        done_rows = sum(n["rows_done"] for n in nodes)
        prog = self.progress[run_id] = {
            "run_id": run_id, "status": "running", "total_planned": planned_total, "total_done": done_rows, "rate": 0.0,
            "eta_s": None, "errors": [], "started": time.time(), "scenarios": {}, "elapsed": run["elapsed"], "workers": workers}
        self._refresh_prog(run_id, prog)
        self.state.set_run_status(run_id, "running")
        self.state.event(run_id, "info", f"execution started/resumed: {sum(len(v) for v in remaining.values())} batches remaining")
        status_final = "completed"
        err: str | None = None
        t_start = time.time()
        elapsed0 = run["elapsed"] or 0.0
        node_done: dict[tuple[str, str], bool] = {k: not v for k, v in remaining.items()}
        parents_of = lambda tbl: schema.tables[tbl].parents() if tbl in schema.tables else set()  # noqa: E731
        active: deque = deque()   # of (key, iterator)
        started_nodes: set[tuple[str, str]] = set()
        inflight: dict[Any, tuple[str, str]] = {}
        samples: deque = deque(maxlen=40)
        pool = ProcessPoolExecutor(max_workers=workers, initializer=_winit,
                                   initargs=(schema, self.s.db_url, str(self.s.pool_dir), run_id, bs))
        max_inflight = int(opts.get("max_inflight") or workers * 2)
        dispatched_rows = 0
        t_throttle = time.time()
        try:
            while True:
                stop = control.pause.is_set() or control.cancel.is_set() or err is not None
                # ---- activate ready nodes
                if not stop:
                    for n in nodes:
                        k = node_key(n)
                        if node_done[k] or k in started_nodes:
                            continue
                        blockers = [m for m in nodes if m["table_name"] in parents_of(n["table_name"]) and m["table_name"] != n["table_name"]
                                    and not node_done[node_key(m)]]
                        if blockers:
                            continue
                        try:
                            self._prepare_node(ctx, scn_by_id[k[0]], k[1])
                        except Exception as e:  # e.g. empty parent table
                            err = f"{k[0]}.{k[1]}: {e}"
                            self.state.event(run_id, "error", err)
                            break
                        started_nodes.add(k)
                        self.state.x("UPDATE run_table SET status='running' WHERE run_id=? AND scenario_id=? AND table_name=?", (run_id, *k))
                        active.append((k, iter(remaining[k])))
                # ---- dispatch
                while not stop and active and len(inflight) < max_inflight:
                    k, it = active[0]
                    b = next(it, None)
                    if b is None:
                        active.popleft()
                        continue
                    active.rotate(-1)
                    n_ = next(x for x in nodes if node_key(x) == k)
                    nrows = min(bs, n_["record_count"] - b * bs)
                    sc = scn_by_id[k[0]].model_copy(update={"seed": n_["eff_seed"]})
                    task = {"scn": sc.model_dump(mode="json"), "scenario_id": k[0], "table": k[1], "batch": b, "n": nrows,
                            "token_base": n_["token_base"], "run_id": run_id,
                            "staged": False}
                    fut = pool.submit(_worker_batch, task)
                    inflight[fut] = k
                    dispatched_rows += nrows
                    if throttle:
                        ahead = dispatched_rows / throttle - (time.time() - t_throttle)
                        if ahead > 0:
                            time.sleep(min(ahead, 2.0))
                if not inflight:
                    if stop or not active and all(node_done.values()):
                        break
                    if not active and not stop:
                        # nothing active and nodes remain blocked -> deadlock guard
                        if all(node_done.values()):
                            break
                        pend = [k for k, d in node_done.items() if not d and k not in started_nodes]
                        if not pend:
                            continue
                        err = f"dependency deadlock among {pend[:3]}"
                        break
                    continue
                done, _ = wait(list(inflight), timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in done:
                    k = inflight.pop(fut)
                    try:
                        r = fut.result()
                    except Exception as e:  # batch failed after retries
                        err = f"{k[0]}.{k[1]}: {e}"
                        self.state.event(run_id, "error", err)
                        prog["errors"].append(err)
                        continue
                    self.state.record_batch(run_id, r["scenario_id"], r["table"], r["batch"], r["n"], r["inserted"], r["dups"], r["nulls"], r["ms"])
                    if r.get("retries"):
                        self.state.event(run_id, "warn", f"{r['table']}#{r['batch']} succeeded after {r['retries']} retries")
                    samples.append((time.time(), r["inserted"]))
                    remaining[k] = [b for b in remaining[k] if b != r["batch"]]
                    if not remaining[k] and not any(x == k for x in inflight.values()):
                        node_done[k] = True
                        self.state.x("UPDATE run_table SET status='done' WHERE run_id=? AND scenario_id=? AND table_name=?", (run_id, *k))
                        started_nodes.add(k)
                        self._update_scn_ledger(run_id, k[0], scn_by_id[k[0]], all(node_done[x] for x in node_done if x[0] == k[0]))
                    prog["total_done"] += r["inserted"]
                if len(samples) >= 2:
                    dt = samples[-1][0] - samples[0][0]
                    prog["rate"] = sum(s[1] for s in list(samples)[1:]) / dt if dt > 0 else 0.0
                prog["elapsed"] = elapsed0 + (time.time() - t_start)
                left = planned_total - prog["total_done"]
                prog["eta_s"] = (left / prog["rate"]) if prog["rate"] > 0 else None
                self._refresh_prog(run_id, prog)
            # loop exited
            if err:
                status_final = "failed"
            elif control.cancel.is_set():
                status_final = "cancelled"
            elif control.pause.is_set() and not all(node_done.values()):
                status_final = "paused"
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            # harvest any futures that completed during shutdown
            for fut, k in list(inflight.items()):
                try:
                    r = fut.result()
                    self.state.record_batch(run_id, r["scenario_id"], r["table"], r["batch"], r["n"], r["inserted"], r["dups"], r["nulls"], r["ms"])
                    prog["total_done"] += r["inserted"]
                except Exception:
                    pass
        self._drop_staging()
        self.state.x("UPDATE run SET elapsed=? WHERE id=?", (elapsed0 + (time.time() - t_start), run_id))
        if status_final == "completed" and not all(node_done.values()):
            status_final = "failed"
            err = err or "run ended with unfinished nodes"
        self.state.set_run_status(run_id, status_final, err)
        prog["status"] = status_final
        prog["error"] = err
        self._refresh_prog(run_id, prog)
        self.state.event(run_id, "info" if status_final != "failed" else "error", f"run {status_final}" + (f": {err}" if err else ""))
        if status_final in ("completed", "cancelled", "failed"):
            for sid in {n["scenario_id"] for n in nodes}:
                self._update_scn_ledger(run_id, sid, scn_by_id[sid], status_final == "completed", final=status_final)
        return status_final

    def _drop_staging(self) -> None:
        try:
            with self.connect() as c:
                for (n,) in c.execute("SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'talentra_ingest_stg_%'").fetchall():
                    c.execute(f'DROP TABLE IF EXISTS "{n}"')
        except Exception as e:  # pragma: no cover
            log.warning("staging cleanup failed: %s", e)

    def _prepare_node(self, ctx: RunContext, scn: Scenario, table: str) -> None:
        t = ctx.schema.tables[table]
        ts = next(x for x in scn.tables if x.table == table)
        for fk in t.fks:
            if len(fk.columns) != 1 or fk.ref_table == table or fk.ref_table not in ctx.schema.tables:
                continue
            spec = ts.fk.get(fk.columns[0])
            scope = spec.scope if spec else ("scenario" if fk.ref_table in scn.target_tables else "any")
            if scope == "any" or fk.ref_table not in scn.target_tables:
                ctx.any_pool(fk.ref_table, spec.pool_size if spec else None)

    def _reconcile(self, run_id: str) -> None:
        """DB batch ledger is the source of truth (commit is atomic with the data): backfill SQLite checkpoints."""
        with self.connect() as c:
            rows = c.execute("SELECT scenario_id, table_name, batch_idx, rows_requested, rows_inserted FROM talentra_ingest_batch WHERE run_id=%s", (run_id,)).fetchall()
        n = 0
        for sid, tn, bi, req, ins in rows:
            if self.state.record_batch(run_id, sid, tn, bi, req, ins, 0, 0, 0.0):
                n += 1
        if n:
            self.state.event(run_id, "info", f"reconciled {n} batches committed in DB but missing from local checkpoints")

    def _update_scn_ledger(self, run_id: str, sid: str, sc: Scenario, complete: bool, final: str | None = None) -> None:
        rows = self.state.q("SELECT table_name, sum(inserted) AS n FROM batch WHERE run_id=? AND scenario_id=? GROUP BY table_name", (run_id, sid))
        tables = {r["table_name"]: int(r["n"]) for r in rows}
        total = sum(tables.values())
        status = "completed" if complete else (final if final in ("failed", "cancelled") else "running")
        if final == "failed" or final == "cancelled":
            status = "partial" if total else "failed_empty"
        self.state.ledger_put(run_id, sid, sc.fingerprint or sc.compute_fingerprint(), sc.name, sc.features(), status, total, tables)
        try:
            with self.connect() as c:
                c.execute("UPDATE talentra_ingest_ledger SET status=%s, rows_total=%s, tables=%s WHERE run_id=%s AND scenario_id=%s",
                          (status, total, json.dumps(tables), run_id, sid))
        except Exception as e:  # pragma: no cover
            log.warning("ledger update failed: %s", e)

    def _refresh_prog(self, run_id: str, prog: dict[str, Any]) -> None:
        rows = self.state.q("SELECT scenario_id, table_name, record_count, rows_done, batches_done, n_batches, status FROM run_table WHERE run_id=?", (run_id,))
        sc: dict[str, Any] = {}
        for r in rows:
            s = sc.setdefault(r["scenario_id"], {"planned": 0, "done": 0, "tables": {}})
            s["planned"] += r["record_count"]
            s["done"] += r["rows_done"]
            s["tables"][r["table_name"]] = {"planned": r["record_count"], "done": r["rows_done"], "status": r["status"],
                                             "batches": f'{r["batches_done"]}/{r["n_batches"]}'}
        prog["scenarios"] = sc

    def progress_snapshot(self, run_id: str) -> dict[str, Any]:
        live = self.progress.get(run_id)
        run = self.state.one("SELECT * FROM run WHERE id=?", (run_id,))
        if not run:
            return {"run_id": run_id, "status": "unknown"}
        if live is None or live["status"] in ("completed", "failed", "paused", "cancelled") and live.get("status") != run["status"]:
            live = {"run_id": run_id, "rate": 0.0, "eta_s": None, "errors": [], "workers": json.loads(run["options"]).get("workers")}
            self._refresh_prog(run_id, live)
        done = self.state.one("SELECT COALESCE(SUM(rows_done),0) AS n FROM run_table WHERE run_id=?", (run_id,))["n"]
        out = dict(live)
        out.update({"status": run["status"], "total_planned": run["total_planned"], "total_done": done, "elapsed": run["elapsed"] if run["status"] != "running" else live.get("elapsed", run["elapsed"]),
                    "error": run["error"]})
        self._refresh_prog(run_id, out)
        out["events"] = self.state.q("SELECT ts, level, message FROM run_event WHERE run_id=? ORDER BY id DESC LIMIT 15", (run_id,))
        return out

    # ---- purge / rollback
    def purge_run(self, run_id: str, scenario_ids: list[str] | None = None) -> dict[str, int]:
        run = self.state.one("SELECT * FROM run WHERE id=?", (run_id,))
        if not run:
            raise KeyError(run_id)
        cfg = parse_config(run["config_json"])
        opts = json.loads(run["options"])
        bs = int(opts["batch_size"])
        schema = self.get_schema()
        scn_by_id = {s.id: s for s in cfg.scenarios}
        nodes = self.state.q("SELECT * FROM run_table WHERE run_id=?", (run_id,))
        if scenario_ids:
            nodes = [n for n in nodes if n["scenario_id"] in scenario_ids]
        self._reconcile(run_id)
        ctx = RunContext(schema, run_id, self.s.pool_dir, bs, conn_factory=lambda: self.connect())
        order = schema.order()
        nodes.sort(key=lambda n: order.index(n["table_name"]) if n["table_name"] in order else 0, reverse=True)
        deleted: dict[str, int] = {}
        with self.connect() as c:
            for n in nodes:
                sc = scn_by_id[n["scenario_id"]].model_copy(update={"seed": n["eff_seed"]})
                ts = next(t for t in sc.tables if t.table == n["table_name"])
                t = schema.tables.get(n["table_name"])
                if t is None or not t.pk:
                    continue
                batches = self.state.committed_batches(run_id, n["scenario_id"], n["table_name"])
                for b in sorted(batches):
                    nrows = min(bs, n["record_count"] - b * bs)
                    arrs, cols, _ = generate_batch(ctx, sc, ts, b, nrows, n["token_base"], only_cols=t.pk)
                    deleted[n["table_name"]] = deleted.get(n["table_name"], 0) + self._delete_keys(c, t, arrs, cols)
                    c.execute("DELETE FROM talentra_ingest_batch WHERE run_id=%s AND scenario_id=%s AND table_name=%s AND batch_idx=%s",
                              (run_id, n["scenario_id"], n["table_name"], b))
                self.state.x("DELETE FROM batch WHERE run_id=? AND scenario_id=? AND table_name=?", (run_id, n["scenario_id"], n["table_name"]))
                self.state.x("UPDATE run_table SET rows_done=0,batches_done=0,status='purged' WHERE run_id=? AND scenario_id=? AND table_name=?",
                             (run_id, n["scenario_id"], n["table_name"]))
            for sid in {n["scenario_id"] for n in nodes}:
                c.execute("UPDATE talentra_ingest_ledger SET status='purged', rows_total=0 WHERE run_id=%s AND scenario_id=%s", (run_id, sid))
                led = [e for e in self.state.ledger_entries() if e["run_id"] == run_id and e["scenario_id"] == sid]
                if led:
                    self.state.ledger_put(run_id, sid, led[0]["fingerprint"], led[0]["name"], led[0]["features"], "purged", 0, {})
        left = self.state.one("SELECT count(*) AS n FROM run_table WHERE run_id=? AND status!='purged'", (run_id,))["n"]
        if not left:
            self.state.set_run_status(run_id, "purged")
        self.state.event(run_id, "info", f"purged rows: {deleted}")
        total = self.state.one("SELECT COALESCE(SUM(rows_done),0) AS n FROM run_table WHERE run_id=?", (run_id,))["n"]
        self.state.x("UPDATE run SET total_done=? WHERE id=?", (total, run_id))
        return deleted

    @staticmethod
    def _delete_keys(c: psycopg.Connection, t: Any, arrs: dict[str, np.ndarray], cols: list[str]) -> int:
        n = 0
        total = len(arrs[cols[0]])
        for i in range(0, total, 2000):
            if len(t.pk) == 1:
                ks = arrs[t.pk[0]][i:i + 2000].tolist()
                cur = c.execute(f'DELETE FROM "{t.name}" WHERE "{t.pk[0]}"::text = ANY(%s)', (ks,))
            else:
                parts = [arrs[k][i:i + 2000].tolist() for k in t.pk]
                tuples = list(zip(*parts))
                ph = ",".join("(" + ",".join(["%s"] * len(t.pk)) + ")" for _ in tuples)
                flat = [x for tp in tuples for x in tp]
                cols_sql = ",".join(f'"{k}"::text' for k in t.pk)
                cur = c.execute(f'DELETE FROM "{t.name}" WHERE ({cols_sql}) IN (VALUES {ph})', flat)
            n += cur.rowcount
        return n

    # ---- validation report
    def validate_run(self, run_id: str) -> dict[str, Any]:
        schema = self.get_schema()
        nodes = self.state.q("SELECT * FROM run_table WHERE run_id=?", (run_id,))
        report: dict[str, Any] = {"run_id": run_id, "tables": [], "ok": True, "issues": []}
        with self.connect() as c:
            led = {(r[0], r[1]): r[2:] for r in c.execute(
                "SELECT scenario_id, table_name, count(*), sum(rows_inserted), sum(rows_requested) FROM talentra_ingest_batch WHERE run_id=%s GROUP BY 1,2",
                (run_id,)).fetchall()}
            for n in nodes:
                tn = n["table_name"]
                nb, ins, req = led.get((n["scenario_id"], tn), (0, 0, 0))
                ins, req = int(ins or 0), int(req or 0)
                local = self.state.one("SELECT COUNT(*) AS nb, COALESCE(SUM(inserted),0) AS ins FROM batch WHERE run_id=? AND scenario_id=? AND table_name=?",
                                       (run_id, n["scenario_id"], tn))
                row = {"scenario_id": n["scenario_id"], "table": tn, "planned": n["record_count"], "batches_expected": n["n_batches"],
                       "batches_in_db_ledger": nb, "rows_inserted_db_ledger": ins, "rows_inserted_local": local["ins"],
                       "conflict_skipped": req - ins}
                if n["status"] != "purged":
                    if nb != n["n_batches"]:
                        report["issues"].append(f"{tn}: {nb}/{n['n_batches']} batches committed")
                    if ins != local["ins"]:
                        report["issues"].append(f"{tn}: DB ledger rows {ins} != local checkpoint rows {local['ins']}")
                    if req != n["record_count"]:
                        report["issues"].append(f"{tn}: requested rows {req} != planned {n['record_count']}")
                report["tables"].append(row)
            tables = sorted({n["table_name"] for n in nodes})
            for tn in tables:
                cnt = c.execute(f'SELECT count(*) FROM "{tn}"').fetchone()[0]
                tot_ledger = sum(r["rows_inserted_db_ledger"] for r in report["tables"] if r["table"] == tn)
                for r in report["tables"]:
                    if r["table"] == tn:
                        r["table_rowcount_now"] = cnt
                t = schema.tables[tn]
                nulls = {}
                for cn, col in t.columns.items():
                    if col.nullable and not col.has_default:
                        nulls[cn] = round(c.execute(f'SELECT avg((("{cn}" IS NULL))::int) FROM (SELECT "{cn}" FROM "{tn}" LIMIT 20000)').fetchone()[0] or 0.0, 4)
                report.setdefault("null_ratio", {})[tn] = nulls
            for tn in tables:
                for r in c.execute(f'SELECT constraint_name, validated FROM [SHOW CONSTRAINTS FROM "{tn}"]').fetchall():
                    if r[1] is False:
                        report["issues"].append(f"{tn}.{r[0]} is not validated")
            # FK orphan sanity (cheap anti-join per FK of loaded tables)
            for tn in tables:
                for fk in schema.tables[tn].fks:
                    if len(fk.columns) == 1 and fk.ref_table in schema.tables and len(fk.ref_columns) == 1:
                        o = c.execute(f'SELECT count(*) FROM (SELECT "{fk.columns[0]}" k FROM "{tn}" WHERE "{fk.columns[0]}" IS NOT NULL LIMIT 200000) a '
                                      f'LEFT JOIN "{fk.ref_table}" p ON p."{fk.ref_columns[0]}"=a.k WHERE p."{fk.ref_columns[0]}" IS NULL').fetchone()[0]
                        if o:
                            report["issues"].append(f"{tn}.{fk.columns[0]}: {o} orphan rows")
        report["ok"] = not report["issues"]
        return report


def _needs_staging(schema: Schema, table: str) -> bool:
    from .generate import needs_staging
    return needs_staging(schema.tables[table])


# ======================================================================================================
# dry-run estimator
# ======================================================================================================
def estimate(runner: Runner, cfg: EngineConfig, sample_rows: int = 5000) -> dict[str, Any]:
    """Generate sample batches in-process (no DB writes) to estimate generation throughput and run time."""
    from pathlib import Path
    import tempfile
    schema = runner.get_schema()
    issues = validate_against_schema(cfg, schema)
    ctx = RunContext(schema, "estimate", Path(tempfile.mkdtemp()), sample_rows, conn_factory=lambda: runner.connect())
    ctx.dry = True
    out: list[dict[str, Any]] = []
    total_rows = 0
    total_sec_single = 0.0
    hist = runner.state.one("SELECT sum(total_done) AS r, sum(elapsed) AS e FROM run WHERE elapsed>5 AND total_done>0")
    db_rate = (hist["r"] / hist["e"]) if hist and hist["r"] and hist["e"] else None  # measured whole-pipeline rows/s
    assumed = db_rate or 12000.0
    w = cfg.defaults.workers
    for sc in cfg.scenarios:
        for ts in sc.tables:
            if ts.record_count == 0:
                continue
            t0 = time.perf_counter()
            n = min(sample_rows, ts.record_count)
            try:
                arrs, cols, _ = generate_batch(ctx, sc, ts, 0, n, 1_000_000)
                data = to_copy_bytes(arrs, cols)
                dt = time.perf_counter() - t0
                gen_rate = n / dt
                bytes_per_row = len(data) / n
            except Exception as e:
                gen_rate, bytes_per_row, dt = 0.0, 0.0, 0.0
                issues.append(f"[{sc.id}] {ts.table}: sample generation failed: {e}")
            eff = min(gen_rate * w, assumed) if gen_rate else 0
            sec = ts.record_count / eff if eff else None
            total_rows += ts.record_count
            if sec:
                total_sec_single += sec
            out.append({"scenario_id": sc.id, "table": ts.table, "rows": ts.record_count, "gen_rows_per_sec_1proc": round(gen_rate),
                        "bytes_per_row": round(bytes_per_row), "est_gb": round(bytes_per_row * ts.record_count / 1e9, 3),
                        "est_seconds_at_workers": round(sec, 1) if sec else None})
    return {"total_rows": total_rows, "workers": w, "est_total_seconds": round(total_sec_single, 1),
            "assumed_pipeline_rows_per_sec": round(assumed), "assumption": "measured from run history" if db_rate else "default 12k rows/s (no history yet)",
            "tables": out, "issues": issues}

"""Application services shared by the CLI, the API and the LLM tools: config store, proposals, run manager."""
from __future__ import annotations

import logging
import threading
from typing import Any


from .config import EngineConfig, diff_configs, dump_config, parse_config, validate_against_schema
from .runner import ConfigError, Runner, estimate
from .settings import Settings, get_settings
from .state import State

log = logging.getLogger("engine.services")

EMPTY_CONFIG = "version: 1\ndefaults:\n  batch_size: 10000\n  workers: 4\nscenarios: []\n"


class RunManager:
    """Runs `Runner.execute` in background threads; supports pause/resume/cancel (also after process restarts)."""

    def __init__(self, runner: Runner):
        self.runner = runner
        self.threads: dict[str, threading.Thread] = {}
        self.results: dict[str, str] = {}
        self._lock = threading.Lock()

    def is_alive(self, run_id: str) -> bool:
        t = self.threads.get(run_id)
        return bool(t and t.is_alive())

    def active_run(self) -> str | None:
        for rid, t in self.threads.items():
            if t.is_alive():
                return rid
        return None

    def start(self, run_id: str) -> None:
        with self._lock:
            other = self.active_run()
            if other and other != run_id:
                raise RuntimeError(f"run {other} is still active; pause or cancel it first")
            if self.is_alive(run_id):
                return

            def target() -> None:
                try:
                    self.results[run_id] = self.runner.execute(run_id)
                except Exception as e:  # pragma: no cover
                    log.exception("run crashed")
                    self.runner.state.set_run_status(run_id, "failed", str(e))
                    self.runner.state.event(run_id, "error", f"crashed: {e}")
                    self.results[run_id] = "failed"

            ctl = self.runner.get_control(run_id)
            ctl.pause.clear()
            ctl.cancel.clear()
            th = threading.Thread(target=target, name=f"run-{run_id}", daemon=True)
            self.threads[run_id] = th
            th.start()

    def pause(self, run_id: str, wait: float = 0) -> None:
        self.runner.get_control(run_id).pause.set()
        if wait and run_id in self.threads:
            self.threads[run_id].join(wait)

    def cancel(self, run_id: str, wait: float = 0) -> None:
        self.runner.get_control(run_id).cancel.set()
        if wait and run_id in self.threads:
            self.threads[run_id].join(wait)
        elif not self.is_alive(run_id):
            self.runner.state.set_run_status(run_id, "cancelled")

    def join(self, run_id: str, timeout: float | None = None) -> None:
        t = self.threads.get(run_id)
        if t:
            t.join(timeout)


class Services:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self.state = State(self.s.state_db)
        self.runner = Runner(self.s, self.state)
        self.runs = RunManager(self.runner)
        self.state.recover_orphans()
        if not self.state.latest_config():
            vid = self.state.save_config(EMPTY_CONFIG, "yaml", "initial empty config", "system")
            self.state.kv_set("active_config_id", str(vid))

    # ---- config
    def active_version_id(self) -> int:
        v = self.state.kv_get("active_config_id")
        if v and self.state.get_config(int(v)):
            return int(v)
        return int(self.state.latest_config()["id"])  # type: ignore[index]

    def current(self) -> dict[str, Any]:
        row = self.state.get_config(self.active_version_id())
        assert row
        return row

    def parse_current(self) -> EngineConfig:
        return parse_config(self.current()["content"], self.current()["fmt"])

    def check_content(self, content: str, fmt: str = "yaml") -> tuple[EngineConfig | None, list[str]]:
        try:
            cfg = parse_config(content, fmt)
        except Exception as e:
            return None, [f"parse/validation error: {str(e)[:1500]}"]
        issues: list[str] = []
        try:
            issues = validate_against_schema(cfg, self.runner.get_schema())
        except Exception as e:
            issues = [f"(schema check skipped: {type(e).__name__}: {str(e)[:120]})"]
        return cfg, issues

    def save_config(self, content: str, fmt: str = "yaml", note: str = "", author: str = "user", activate: bool = True) -> tuple[int, list[str]]:
        cfg, issues = self.check_content(content, fmt)
        if cfg is None:
            raise ConfigError(issues)
        hard = [i for i in issues if not i.startswith("(")]
        if hard:
            raise ConfigError(hard)
        # normalise (computes fingerprints) but keep requested format
        canon = dump_config(cfg, fmt)
        vid = self.state.save_config(canon, fmt, note, author)
        if activate:
            self.state.kv_set("active_config_id", str(vid))
        return vid, issues

    def summary(self, cfg: EngineConfig) -> list[dict[str, Any]]:
        return [{"id": s.id, "name": s.name, "tables": s.target_tables, "records": s.total_records, "fingerprint": s.fingerprint,
                 "locales": list(s.locale_mix)[:4], "duplicate_rate": s.duplicate_rate} for s in cfg.scenarios]

    def select_version(self, vid: int) -> None:
        if not self.state.get_config(vid):
            raise KeyError(f"no saved config version {vid}")
        self.state.kv_set("active_config_id", str(vid))

    # ---- proposals (LLM edits -> diff -> accept/reject)
    def propose(self, session_id: str, summary: str, new_cfg: EngineConfig, graph_thread: str | None = None) -> dict[str, Any]:
        cur = self.current()
        new_text = dump_config(new_cfg, cur["fmt"])
        old_text = dump_config(parse_config(cur["content"], cur["fmt"]), cur["fmt"])
        _, issues = self.check_content(new_text, cur["fmt"])
        hard = [i for i in issues if not i.startswith("(")]
        if hard:
            return {"error": "proposed config is invalid: " + "; ".join(hard[:8])}
        diff = diff_configs(old_text, new_text)
        if not diff.strip():
            return {"message": "no change: the proposed config equals the current config"}
        pid = self.state.proposal_create(session_id, summary, new_text, diff, graph_thread, cur["id"])
        return {"proposal_id": pid, "summary": summary, "diff_preview": diff[:1500], "status": "pending user approval in the UI"}

    def accept_proposal(self, pid: str) -> dict[str, Any]:
        p = self.state.proposal_get(pid)
        if not p or p["status"] != "pending":
            raise KeyError(f"proposal {pid} not pending")
        cur = self.current()
        if p["base_version_id"] != cur["id"]:
            # config moved on: re-diff is the user's call; still allow but note it
            log.warning("proposal %s based on version %s but current is %s", pid, p["base_version_id"], cur["id"])
        vid, issues = self.save_config(p["new_content"], cur["fmt"], f"accepted proposal: {p['summary']}", "llm+user")
        self.state.proposal_set(pid, "accepted")
        return {"version_id": vid, "issues": issues}

    def reject_proposal(self, pid: str) -> None:
        self.state.proposal_set(pid, "rejected")

    # ---- runs
    def precheck(self, version_id: int | None = None) -> dict[str, Any]:
        row = self.state.get_config(version_id) if version_id else self.current()
        cfg, issues = self.check_content(row["content"], row["fmt"])  # type: ignore[index]
        out: dict[str, Any] = {"version_id": row["id"], "issues": issues, "findings": [], "estimate": None}  # type: ignore[index]
        if cfg is None or not cfg.scenarios:
            if cfg is not None:
                out["issues"] = issues + ["config has no scenarios"]
            return out
        if [i for i in issues if not i.startswith("(")]:
            return out
        out["findings"] = self.runner.check_memory(cfg)
        try:
            e = estimate(self.runner, cfg, 2000)
            out["estimate"] = {"total_rows": e["total_rows"], "est_total_seconds": e["est_total_seconds"]}
        except Exception as ex:  # pragma: no cover
            out["estimate"] = {"error": str(ex)}
        return out

    def start_run(self, version_id: int | None = None, decisions: dict[str, str] | None = None,
                  options: dict[str, Any] | None = None) -> str:
        row = self.state.get_config(version_id) if version_id else self.current()
        assert row
        cfg = parse_config(row["content"], row["fmt"])
        decisions = dict(decisions or {})
        # wildcard decision '*' applies to every flagged scenario
        if "*" in decisions:
            star = decisions.pop("*")
            for f in self.runner.check_memory(cfg):
                decisions.setdefault(f["scenario_id"], star)
        run_id = self.runner.create_run(cfg, row["id"], decisions, options)
        self.runs.start(run_id)
        return run_id

    def resume_run(self, run_id: str) -> None:
        r = self.state.one("SELECT status FROM run WHERE id=?", (run_id,))
        if not r:
            raise KeyError(run_id)
        if r["status"] in ("completed", "cancelled", "purged"):
            raise RuntimeError(f"run is {r['status']} and cannot be resumed")
        self.runs.start(run_id)

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.state.q("SELECT id,status,created_at,started_at,finished_at,total_planned,total_done,elapsed,error,config_version_id FROM run ORDER BY created_at DESC LIMIT ?", (limit,))

    def ledger(self) -> list[dict[str, Any]]:
        rows = self.state.ledger_entries()
        for r in rows:
            r.pop("features", None)
        return rows

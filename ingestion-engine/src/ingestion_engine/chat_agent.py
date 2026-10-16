"""Chat agent: a LangGraph ReAct agent whose tools view/edit the config (as accept/reject proposals), validate,
list scenarios and control ingestion. Conversation state is checkpointed in SQLite (survives restarts)."""
from __future__ import annotations

import copy
import json
import logging
import sqlite3
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, trim_messages
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import create_react_agent

from .config import parse_config
from .llm import TokenBudgetExceeded, get_llm
from .runner import ConfigError, NeedsConfirmation
from .scenario_graph import ScenarioGenerator
from .services import Services

log = logging.getLogger("engine.chat")

SYSTEM = """You are the control agent of the Talentra data Ingestion Engine. You help the user design synthetic-data scenarios,
edit the YAML config, and run/pause/resume ingestion into the target database. Rules:
- Never invent config content: call get_config / list_scenarios / describe_schema first when unsure.
- Config edits (set_record_count, update_scenario, add_scenario, remove_scenario, set_defaults, generate_scenarios) only create a
  PROPOSAL with a diff; the user accepts or rejects it in the UI. Say so; never claim an edit is applied before acceptance.
- Before start_ingest, call precheck_ingest. If it reports findings (scenarios already ingested), explain them and ask the user to
  choose skip / append / replace per scenario; only then call start_ingest with confirmed=true and decisions. Never start without the user's explicit go-ahead.
- Be concise. Report row counts, rates and ETA from get_run_status when asked about progress."""


def _j(x: Any) -> str:
    return json.dumps(x, default=str)[:6000]


def build_tools(svc: Services, gen: ScenarioGenerator, session_id: str) -> list[StructuredTool]:
    def cfg_dict() -> dict[str, Any]:
        return svc.parse_current().model_dump(mode="json")

    def propose_dict(summary: str, d: dict[str, Any]) -> str:
        try:
            new = parse_config(d)
        except Exception as e:
            return _j({"error": f"invalid config after edit: {str(e)[:800]}"})
        return _j(svc.propose(session_id, summary, new))

    def get_config() -> str:
        """Return the active config (YAML text, truncated) and its version id."""
        cur = svc.current()
        return _j({"version_id": cur["id"], "fmt": cur["fmt"], "yaml_head": cur["content"][:3500], "total_chars": len(cur["content"])})

    def validate_config() -> str:
        """Validate the active config against the config schema and the live database schema."""
        cur = svc.current()
        cfg, issues = svc.check_content(cur["content"], cur["fmt"])
        return _j({"valid": cfg is not None and not [i for i in issues if not i.startswith("(")], "issues": issues,
                   "scenarios": len(cfg.scenarios) if cfg else 0, "total_records": cfg.total_records if cfg else 0})

    def list_scenarios(limit: int = 30) -> str:
        """List scenarios (id, name, tables, records)."""
        cfg = svc.parse_current()
        return _j({"count": len(cfg.scenarios), "total_records": cfg.total_records, "scenarios": svc.summary(cfg)[:limit]})

    def get_scenario(scenario_id: str) -> str:
        """Return the full spec of one scenario."""
        for s in svc.parse_current().scenarios:
            if s.id == scenario_id:
                return _j(s.model_dump(mode="json"))
        return _j({"error": f"unknown scenario {scenario_id}"})

    def describe_schema() -> str:
        """Describe the live target-database tables (columns, kinds, FKs) available to scenarios."""
        sch = svc.runner.get_schema()
        return _j({n: {"cols": {c: col.kind + ("?" if col.nullable else "") for c, col in t.columns.items()},
                       "fks": {",".join(f.columns): f.ref_table for f in t.fks}} for n, t in sch.tables.items()})

    def set_record_count(scenario_id: str, count: int, table: str | None = None) -> str:
        """Set record_count for a table (or all tables when table is empty) of a scenario; scenario_id '*' means every scenario."""
        d = cfg_dict()
        hit = 0
        for s in d["scenarios"]:
            if scenario_id in ("*", s["id"]):
                for t in s["tables"]:
                    if table in (None, "", t["table"]):
                        t["record_count"] = int(count)
                        hit += 1
        if not hit:
            return _j({"error": "no matching scenario/table"})
        return propose_dict(f"set record_count={count} on {hit} table spec(s) of {scenario_id}", d)

    def update_scenario(scenario_id: str, patch: dict[str, Any]) -> str:
        """Deep-merge `patch` (JSON object) into a scenario, e.g. {"duplicate_rate":0.05,"locale_mix":{"en_IN":1}}. Table lists are replaced wholesale."""
        d = cfg_dict()
        for s in d["scenarios"]:
            if s["id"] == scenario_id:
                _merge(s, patch)
                return propose_dict(f"update scenario {scenario_id}: {list(patch)[:5]}", d)
        return _j({"error": f"unknown scenario {scenario_id}"})

    def add_scenario(scenario: dict[str, Any]) -> str:
        """Add a full scenario object (id,name,tables[...] with record_count, optional fields/fk/locale_mix/...)."""
        d = cfg_dict()
        d["scenarios"].append(scenario)
        return propose_dict(f"add scenario {scenario.get('id')}", d)

    def remove_scenario(scenario_id: str) -> str:
        """Remove a scenario from the config."""
        d = cfg_dict()
        n = len(d["scenarios"])
        d["scenarios"] = [s for s in d["scenarios"] if s["id"] != scenario_id]
        if len(d["scenarios"]) == n:
            return _j({"error": f"unknown scenario {scenario_id}"})
        return propose_dict(f"remove scenario {scenario_id}", d)

    def set_defaults(workers: int | None = None, batch_size: int | None = None) -> str:
        """Change default worker processes / batch size."""
        d = cfg_dict()
        if workers:
            d["defaults"]["workers"] = int(workers)
        if batch_size:
            d["defaults"]["batch_size"] = int(batch_size)
        return propose_dict(f"set defaults workers={workers} batch_size={batch_size}", d)

    def generate_scenarios(count: int, theme: str = "") -> str:
        """Generate `count` new distinct scenarios (LLM-driven LangGraph pipeline) matching `theme`; result is a proposal to review."""
        count = max(1, min(int(count), 5000))
        return _j(gen.start(session_id, theme, count))

    def list_config_versions() -> str:
        """List saved config versions stored in SQLite and which one is active."""
        return _j({"active": svc.active_version_id(), "versions": svc.state.config_versions(15)})

    def select_config_version(version_id: int) -> str:
        """Make a saved config version the active one (used by subsequent runs)."""
        try:
            svc.select_version(int(version_id))
        except KeyError as e:
            return _j({"error": str(e)})
        cfg = svc.parse_current()
        return _j({"message": f"active config is now version {version_id} ({len(cfg.scenarios)} scenarios, {cfg.total_records} records)"})

    def precheck_ingest() -> str:
        """Validate the active config, list scenarios already ingested (scenario memory) and estimate the run."""
        return _j(svc.precheck())

    def start_ingest(confirmed: bool = False, decisions: dict[str, str] | None = None, workers: int | None = None, batch_size: int | None = None) -> str:
        """Start ingesting the active config. Needs confirmed=true (user said go). If scenarios were already ingested,
        decisions {scenario_id|'*': 'skip'|'append'|'replace'} are required."""
        if not confirmed:
            pc = svc.precheck()
            return _j({"message": f"Not started. Precheck: issues={pc['issues']}, already-ingested findings={len(pc['findings'])}, estimate={pc['estimate']}. Ask the user to confirm."})
        try:
            opts = {k: v for k, v in {"workers": workers, "batch_size": batch_size}.items() if v}
            rid = svc.start_run(None, decisions or {}, opts)
            return _j({"message": f"started run {rid}", "run_id": rid})
        except NeedsConfirmation as e:
            return _j({"needs_confirmation": True, "findings": e.findings})
        except ConfigError as e:
            return _j({"error": "; ".join(e.issues)[:800]})
        except RuntimeError as e:
            return _j({"error": str(e)})

    def _run(run_id: str | None) -> str | None:
        if run_id:
            return run_id
        rs = svc.list_runs(1)
        return rs[0]["id"] if rs else None

    def pause_run(run_id: str | None = None) -> str:
        """Pause the running ingest (state is checkpointed; resume works even after restarts)."""
        rid = _run(run_id)
        if not rid:
            return _j({"error": "no runs"})
        svc.runs.pause(rid, wait=30)
        return _j({"message": f"pause requested for {rid}; status={svc.runner.progress_snapshot(rid)['status']}"})

    def resume_run(run_id: str | None = None) -> str:
        """Resume a paused / interrupted / failed run from its last committed batch."""
        rid = _run(run_id)
        if not rid:
            return _j({"error": "no runs"})
        try:
            svc.resume_run(rid)
        except Exception as e:
            return _j({"error": str(e)})
        return _j({"message": f"resumed {rid}"})

    def cancel_run(run_id: str | None = None) -> str:
        """Cancel a run (rows already loaded stay; use purge to roll back)."""
        rid = _run(run_id)
        if not rid:
            return _j({"error": "no runs"})
        svc.runs.cancel(rid, wait=30)
        return _j({"message": f"cancel requested for {rid}"})

    def get_run_status(run_id: str | None = None) -> str:
        """Progress of a run (latest if no id): status, rows done/planned, rows/s, ETA, per-scenario."""
        rid = _run(run_id)
        if not rid:
            return _j({"message": "no runs yet"})
        p = svc.runner.progress_snapshot(rid)
        return _j({"run_id": rid, "status": p["status"], "rows_done": p["total_done"], "rows_planned": p["total_planned"],
                   "rate_rows_per_s": round(p.get("rate", 0)), "eta_s": p.get("eta_s"), "error": p.get("error"),
                   "scenarios": {k: f"{v['done']}/{v['planned']}" for k, v in list(p.get("scenarios", {}).items())[:15]}})

    def list_runs() -> str:
        """Run history."""
        return _j(svc.list_runs(10))

    def get_ledger() -> str:
        """Scenarios already ingested (scenario memory ledger)."""
        return _j(svc.ledger()[:40])

    fns = [get_config, validate_config, list_scenarios, get_scenario, describe_schema, set_record_count, update_scenario, add_scenario,
           remove_scenario, set_defaults, generate_scenarios, list_config_versions, select_config_version, precheck_ingest,
           start_ingest, pause_run, resume_run, cancel_run, get_run_status, list_runs, get_ledger]
    return [StructuredTool.from_function(f, name=f.__name__, description=(f.__doc__ or f.__name__).strip()) for f in fns]


def _merge(a: dict[str, Any], b: dict[str, Any]) -> None:
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict) and k != "fields":
            _merge(a[k], v)
        else:
            a[k] = copy.deepcopy(v)


class ChatAgent:
    def __init__(self, svc: Services, gen: ScenarioGenerator):
        self.svc, self.gen = svc, gen
        conn = sqlite3.connect(str(svc.s.checkpoint_db), check_same_thread=False)
        self.saver = SqliteSaver(conn)

    def _agent(self, session_id: str):
        handle = get_llm(self.svc.s)
        tools = build_tools(self.svc, self.gen, session_id)

        def trim(state: dict[str, Any]) -> dict[str, Any]:
            msgs = trim_messages(state["messages"], strategy="last", max_tokens=40, token_counter=len, start_on="human",
                                 include_system=False, allow_partial=False)
            return {"llm_input_messages": msgs}

        return create_react_agent(handle.model, tools, prompt=SYSTEM, checkpointer=self.saver, pre_model_hook=trim), handle

    def chat(self, session_id: str, message: str) -> dict[str, Any]:
        self.svc.state.add_chat(session_id, "user", message)
        before_pending = {p["id"] for p in self.svc.state.proposals("pending")}
        try:
            agent, handle = self._agent(session_id)
            cfg = {"configurable": {"thread_id": session_id}, "recursion_limit": 14}
            n0 = len(agent.get_state(cfg).values.get("messages", [])) if agent.get_state(cfg).values else 0
            out = agent.invoke({"messages": [HumanMessage(content=message)]}, cfg)  # type: ignore[arg-type]
        except TokenBudgetExceeded as e:
            reply = f"LLM token budget exhausted: {e}"
            self.svc.state.add_chat(session_id, "assistant", reply)
            return {"reply": reply, "tool_calls": [], "proposals": []}
        except Exception as e:
            log.exception("chat failed")
            reply = f"The LLM call failed ({type(e).__name__}: {str(e)[:300]}). Check /api/llm/status."
            self.svc.state.add_chat(session_id, "assistant", reply)
            return {"reply": reply, "tool_calls": [], "proposals": []}
        new = out["messages"][n0:]
        calls: list[dict[str, Any]] = []
        results: dict[str, str] = {}
        for m in new:
            if isinstance(m, ToolMessage):
                results[m.tool_call_id] = str(m.content)[:240]
        for m in new:
            if isinstance(m, AIMessage):
                for tc in m.tool_calls:
                    calls.append({"name": tc["name"], "args": tc["args"], "result_summary": results.get(tc["id"], "")})
                    self.svc.state.add_chat(session_id, "tool", f"{tc['name']}({json.dumps(tc['args'])[:300]}) -> {results.get(tc['id'], '')}")
        reply = ""
        for m in reversed(new):
            if isinstance(m, AIMessage) and m.content and not m.tool_calls:
                reply = m.content if isinstance(m.content, str) else "".join(b.get("text", "") for b in m.content if isinstance(b, dict))
                break
        self.svc.state.add_chat(session_id, "assistant", reply, {"model": handle.model_id})
        props = [p for p in self.svc.state.proposals("pending") if p["id"] not in before_pending]
        return {"reply": reply, "tool_calls": calls, "proposals": [{"id": p["id"], "summary": p["summary"], "diff": p["diff"][:20000]} for p in props]}

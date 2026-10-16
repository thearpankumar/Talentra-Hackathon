"""LangGraph pipeline that generates scenarios with an LLM, with SQLite checkpointing and a human-review interrupt.

init -> gen_batch (loops: LLM emits compact briefs -> compile -> validate -> dedupe by fingerprint) -> propose
     -> review (interrupt: waits for the user's accept/reject, survives process restarts) -> finalize
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from types import SimpleNamespace
import zlib
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .config import parse_config, validate_against_schema
from .llm import LLMHandle, TokenBudgetExceeded, get_budget, get_llm
from .scenario_factory import compile_brief
from .services import Services

log = logging.getLogger("engine.scenario_graph")
BATCH = 20

SYSTEM_PROMPT = """SCENARIO_BATCH_JSON
You design synthetic-data scenarios for a job platform (candidates, employers, jobs, applications, gigs; global, multilingual).
Return ONLY a JSON array (no prose, no markdown fences) of `count` distinct scenario briefs. Each brief is an object:
{"name": str (unique, descriptive), "description": str, "tags": [str], "locale_mix": {locale: weight},
 "persona": one of [freshers, mid_career, senior, blue_collar, gig, staffing_demand, startup_hiring, enterprise_hiring, gig_marketplace, full_marketplace],
 "scale": "small"|"medium"|"large", "duplicate_rate": 0..0.1, "null_rate": 0..0.2, "salary_sigma": 0.3..0.8, "salary_shift": -0.5..0.5,
 "date_range": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}}
Valid locales: %(locales)s.
Vary geography, persona, skills, seasonality and scale so no two briefs are alike. Avoid these existing names: see header.
"""


class GState(TypedDict, total=False):
    session_id: str
    theme: str
    count: int
    scale_factor: float
    existing_names: list[str]
    existing_ids: list[str]
    existing_fps: list[str]
    scenarios: list[dict[str, Any]]
    batches: int
    notes: list[str]
    proposal: dict[str, Any]
    decision: str
    result: dict[str, Any]
    _thread: str


def extract_json_array(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        d = json.loads(text)
    except Exception:
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return []
        try:
            d = json.loads(m.group(0))
        except Exception:
            return []
    if isinstance(d, dict):
        d = d.get("scenarios") or d.get("briefs") or [d]
    return [x for x in d if isinstance(x, dict)]


def build_graph(svc: Services, llm: LLMHandle, checkpointer: Any):
    from .config import KNOWN_LOCALES

    def init(st: GState) -> GState:
        cfg = svc.parse_current()
        return {"existing_names": [s.name for s in cfg.scenarios], "existing_ids": [s.id for s in cfg.scenarios],
                "existing_fps": [s.fingerprint or "" for s in cfg.scenarios], "scenarios": [], "batches": 0, "notes": []}

    schema_cache: dict[str, Any] = {}

    def gen_batch(st: GState) -> GState:
        schema = schema_cache.get("s") or schema_cache.setdefault("s", svc.runner.get_schema())
        have = list(st.get("scenarios", []))
        seen_names = set(st["existing_names"]) | {s["name"] for s in have}
        seen_ids = set(st["existing_ids"]) | {s["id"] for s in have}
        seen_fps = set(st["existing_fps"]) | {s.get("fingerprint") for s in have}
        want = min(BATCH, st["count"] - len(have))
        header = {"count": want + 2, "theme": st["theme"], "seed": zlib.crc32(f'{st["theme"]}|{st["batches"]}|{len(have)}'.encode()) & 0xFFFF,
                  "existing": sorted(seen_names)[-60:]}
        notes = list(st.get("notes", []))
        try:
            get_budget().check()
            msg = llm.model.invoke([SystemMessage(content=SYSTEM_PROMPT % {"locales": ", ".join(KNOWN_LOCALES)}),
                                    HumanMessage(content=json.dumps(header))])
            briefs = extract_json_array(str(msg.content))
        except TokenBudgetExceeded as e:
            notes.append(str(e))
            return {"notes": notes, "batches": 99999}
        except Exception as e:
            notes.append(f"LLM call failed: {type(e).__name__}: {str(e)[:200]}")
            return {"notes": notes, "batches": st["batches"] + 1}
        added = 0
        for b in briefs:
            if len(have) >= st["count"]:
                break
            try:
                sc = compile_brief(b, schema, st.get("scale_factor", 1.0), seed_salt=str(len(have)))
            except Exception as e:
                notes.append(f"dropped brief '{str(b.get('name'))[:40]}': {str(e)[:100]}")
                continue
            if sc.name in seen_names or sc.id in seen_ids:
                continue
            fp = sc.compute_fingerprint()
            if fp in seen_fps:
                continue
            if validate_against_schema(SimpleNamespace(scenarios=[sc]), schema):
                notes.append(f"dropped '{sc.name}': invalid for live schema")
                continue
            seen_names.add(sc.name); seen_ids.add(sc.id); seen_fps.add(fp)
            have.append({"name": sc.name, "id": sc.id, "fingerprint": fp, "brief": b, "salt": str(len(have))})
            added += 1
        return {"scenarios": have, "batches": st["batches"] + 1, "notes": notes[-20:]}

    def more(st: GState) -> str:
        if len(st.get("scenarios", [])) >= st["count"] or st["batches"] > max(6, st["count"] // 4 + 6):
            return "propose"
        return "gen_batch"

    def propose(st: GState) -> GState:
        scs_c = st.get("scenarios", [])
        schema = schema_cache.get("s") or svc.runner.get_schema()
        scs = []
        for h in scs_c:  # re-expand compact briefs deterministically (keeps checkpoints small)
            sc = compile_brief(h["brief"], schema, st.get("scale_factor", 1.0), seed_salt=h["salt"])
            sc.fingerprint = sc.compute_fingerprint()
            scs.append(sc.model_dump(mode="json"))
        if not scs:
            return {"result": {"error": "no valid scenarios were generated", "notes": st.get("notes", [])}, "proposal": {}}
        cfg = svc.parse_current()
        new_cfg = parse_config({**cfg.model_dump(mode="json"), "scenarios": [s.model_dump(mode="json") for s in cfg.scenarios] + scs})
        p = svc.propose(st.get("session_id", "graph"), f"add {len(scs)} generated scenarios (theme: {st['theme'][:80]})", new_cfg,
                        graph_thread=st.get("_thread"))
        return {"proposal": p}

    def review(st: GState) -> GState:
        p = st.get("proposal") or {}
        if not p.get("proposal_id"):
            return {"decision": "none"}
        decision = interrupt({"proposal_id": p["proposal_id"], "summary": p.get("summary")})
        return {"decision": str(decision)}

    def finalize(st: GState) -> GState:
        p = st.get("proposal") or {}
        if not p.get("proposal_id"):
            return {"result": st.get("result") or p}
        if st.get("decision") == "accept":
            r = svc.accept_proposal(p["proposal_id"])
            return {"result": {"accepted": True, **r, "added": len(st.get("scenarios", []))}}
        svc.reject_proposal(p["proposal_id"])
        return {"result": {"accepted": False}}

    g = StateGraph(GState)
    g.add_node("init", init)
    g.add_node("gen_batch", gen_batch)
    g.add_node("propose", propose)
    g.add_node("review", review)
    g.add_node("finalize", finalize)
    g.add_edge(START, "init")
    g.add_edge("init", "gen_batch")
    g.add_conditional_edges("gen_batch", more, {"gen_batch": "gen_batch", "propose": "propose"})
    g.add_edge("propose", "review")
    g.add_edge("review", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


class ScenarioGenerator:
    def __init__(self, svc: Services):
        self.svc = svc
        conn = sqlite3.connect(str(svc.s.checkpoint_db), check_same_thread=False)
        self.saver = SqliteSaver(conn)

    def _graph(self):
        return build_graph(self.svc, get_llm(self.svc.s), self.saver)

    def start(self, session_id: str, theme: str, count: int, scale_factor: float = 1.0) -> dict[str, Any]:
        thread = f"gen-{uuid.uuid4().hex[:8]}"
        cfgd = {"configurable": {"thread_id": thread}, "recursion_limit": 400}
        out = self._graph().invoke({"session_id": session_id, "theme": theme or "global hiring mix", "count": count,
                                    "scale_factor": scale_factor, "_thread": thread}, cfgd)  # type: ignore[arg-type]
        p = out.get("proposal") or {}
        if p.get("proposal_id"):
            # remember which graph thread owns the proposal so accept/reject can resume it
            self.svc.state.x("UPDATE pending_proposal SET graph_thread=? WHERE id=?", (thread, p["proposal_id"]))
            return {**p, "graph_thread": thread, "generated": len(out.get("scenarios", [])), "notes": out.get("notes", [])[-5:]}
        return {"error": (out.get("result") or {}).get("error") or p.get("error") or "nothing generated",
                "notes": out.get("notes", [])[-5:], "message": p.get("message")}

    def resume(self, thread: str, decision: str) -> dict[str, Any]:
        cfgd = {"configurable": {"thread_id": thread}, "recursion_limit": 400}
        out = self._graph().invoke(Command(resume=decision), cfgd)  # type: ignore[arg-type]
        return out.get("result") or {}

"""FastAPI app: serves the single-page UI and the JSON/SSE API."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .chat_agent import ChatAgent
from .config import config_json_schema
from .llm import discover_models, get_budget, get_llm
from .runner import ConfigError, NeedsConfirmation
from .scenario_graph import ScenarioGenerator
from .services import Services
from .settings import Settings, get_settings

log = logging.getLogger("engine.api")
STATIC = Path(__file__).parent / "static"


class ConfigIn(BaseModel):
    content: str
    fmt: str = "yaml"
    note: str = ""


class ChatIn(BaseModel):
    session_id: str
    message: str


class RunIn(BaseModel):
    config_version_id: int | None = None
    decisions: dict[str, str] = {}
    options: dict[str, Any] = {}


class GenIn(BaseModel):
    count: int = 10
    theme: str = ""
    session_id: str = "ui"


def create_app(settings: Settings | None = None) -> FastAPI:
    svc = Services(settings or get_settings())
    gen = ScenarioGenerator(svc)
    chat = ChatAgent(svc, gen)
    app = FastAPI(title="Talentra Ingestion Engine")
    app.state.svc = svc

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    # ---------------- llm
    @app.get("/api/llm/status")
    def llm_status() -> dict[str, Any]:
        try:
            return get_llm(svc.s).status()
        except Exception as e:
            return {"provider": "none", "model_id": svc.s.bedrock_model_id, "available": False, "note": str(e)[:300]}

    @app.get("/api/llm/discover")
    def llm_discover() -> dict[str, Any]:
        return discover_models(svc.s)

    # ---------------- config
    def cfg_payload() -> dict[str, Any]:
        cur = svc.current()
        cfg, issues = svc.check_content(cur["content"], cur["fmt"])
        return {"version_id": cur["id"], "fmt": cur["fmt"], "content": cur["content"], "issues": issues,
                "summary": svc.summary(cfg) if cfg else []}

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return cfg_payload()

    @app.put("/api/config")
    def put_config(body: ConfigIn) -> dict[str, Any]:
        try:
            vid, issues = svc.save_config(body.content, body.fmt, body.note, "user")
        except ConfigError as e:
            raise HTTPException(422, detail={"issues": e.issues})
        return {"version_id": vid, "issues": issues}

    @app.get("/api/config/schema")
    def config_schema() -> dict[str, Any]:
        return config_json_schema()

    @app.get("/api/config/versions")
    def versions() -> list[dict[str, Any]]:
        act = svc.active_version_id()
        return [{**v, "active": v["id"] == act} for v in svc.state.config_versions(100)]

    @app.get("/api/config/versions/{vid}")
    def version(vid: int) -> dict[str, Any]:
        v = svc.state.get_config(vid)
        if not v:
            raise HTTPException(404, "no such version")
        return v

    @app.post("/api/config/versions/{vid}/activate")
    def activate(vid: int) -> dict[str, Any]:
        try:
            svc.select_version(vid)
        except KeyError as e:
            raise HTTPException(404, str(e))
        return {"version_id": vid}

    @app.get("/api/schema")
    def db_schema() -> dict[str, Any]:
        return svc.runner.get_schema().to_dict()

    # ---------------- chat & proposals
    @app.post("/api/chat")
    async def chat_ep(body: ChatIn) -> dict[str, Any]:
        return await asyncio.to_thread(chat.chat, body.session_id, body.message)

    @app.get("/api/chat/{session_id}")
    def chat_history(session_id: str) -> list[dict[str, Any]]:
        return [{"role": m["role"], "content": m["content"], "ts": m["ts"]} for m in svc.state.chat_history(session_id)]

    @app.get("/api/proposals")
    def proposals(status: str = "pending") -> list[dict[str, Any]]:
        return svc.state.proposals(status if status != "all" else None)

    @app.post("/api/proposals/{pid}/accept")
    async def accept(pid: str) -> dict[str, Any]:
        p = svc.state.proposal_get(pid)
        if not p or p["status"] != "pending":
            raise HTTPException(404, "proposal not pending")
        try:
            if p.get("graph_thread"):
                r = await asyncio.to_thread(gen.resume, p["graph_thread"], "accept")
                return {"version_id": r.get("version_id", svc.active_version_id()), "issues": r.get("issues", [])}
            return svc.accept_proposal(pid)
        except ConfigError as e:
            raise HTTPException(422, detail={"issues": e.issues})

    @app.post("/api/proposals/{pid}/reject")
    async def reject(pid: str) -> dict[str, Any]:
        p = svc.state.proposal_get(pid)
        if not p:
            raise HTTPException(404, "no such proposal")
        if p.get("graph_thread") and p["status"] == "pending":
            await asyncio.to_thread(gen.resume, p["graph_thread"], "reject")
        svc.reject_proposal(pid)
        return {"ok": True}

    @app.post("/api/scenarios/generate")
    async def generate(body: GenIn) -> dict[str, Any]:
        r = await asyncio.to_thread(gen.start, body.session_id, body.theme, max(1, min(body.count, 5000)))
        if r.get("proposal_id"):
            return {"proposal": {"id": r["proposal_id"], "summary": r["summary"],
                                 "diff": (svc.state.proposal_get(r["proposal_id"]) or {}).get("diff", "")[:50000]}, "note": r.get("notes")}
        return {"proposal": None, "note": r.get("error") or r.get("message")}

    # ---------------- runs
    @app.post("/api/runs/precheck")
    async def precheck(body: dict[str, Any] | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(svc.precheck, (body or {}).get("config_version_id"))

    @app.post("/api/runs")
    async def start_run(body: RunIn) -> Any:
        try:
            rid = await asyncio.to_thread(svc.start_run, body.config_version_id, dict(body.decisions), dict(body.options))
        except NeedsConfirmation as e:
            return JSONResponse(status_code=409, content={"detail": {"findings": e.findings}})
        except ConfigError as e:
            raise HTTPException(422, detail={"issues": e.issues})
        except RuntimeError as e:
            raise HTTPException(409, detail={"error": str(e), "findings": []})
        return {"run_id": rid}

    @app.get("/api/runs")
    def runs() -> list[dict[str, Any]]:
        return svc.list_runs()

    @app.get("/api/runs/{rid}")
    def run(rid: str) -> dict[str, Any]:
        return svc.runner.progress_snapshot(rid)

    @app.post("/api/runs/{rid}/pause")
    async def pause(rid: str) -> dict[str, Any]:
        svc.runs.pause(rid)
        return {"ok": True}

    @app.post("/api/runs/{rid}/resume")
    def resume(rid: str) -> dict[str, Any]:
        try:
            svc.resume_run(rid)
        except (KeyError, RuntimeError) as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.post("/api/runs/{rid}/cancel")
    async def cancel(rid: str) -> dict[str, Any]:
        svc.runs.cancel(rid)
        return {"ok": True}

    @app.post("/api/runs/{rid}/purge")
    async def purge(rid: str) -> dict[str, Any]:
        if svc.runs.is_alive(rid):
            raise HTTPException(409, "run is active; pause or cancel first")
        return {"deleted": await asyncio.to_thread(svc.runner.purge_run, rid)}

    @app.get("/api/runs/{rid}/report")
    async def report(rid: str) -> Any:
        return await asyncio.to_thread(svc.runner.validate_run, rid)

    @app.get("/api/runs/{rid}/events")
    async def events(rid: str) -> StreamingResponse:
        async def gen_events():
            last = ""
            idle = 0
            while True:
                snap = await asyncio.to_thread(svc.runner.progress_snapshot, rid)
                payload = json.dumps(snap, default=str)
                if payload != last:
                    yield f"data: {payload}\n\n"
                    last = payload
                    idle = 0
                else:
                    idle += 1
                    if idle % 15 == 0:
                        yield ": keepalive\n\n"
                if snap.get("status") in ("completed", "failed", "cancelled", "purged") and idle > 2:
                    break
                await asyncio.sleep(1.0)

        return StreamingResponse(gen_events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/ledger")
    def ledger() -> list[dict[str, Any]]:
        return svc.ledger()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "budget": get_budget().snapshot()}

    return app


def app_factory() -> FastAPI:  # for `uvicorn --factory`
    return create_app()

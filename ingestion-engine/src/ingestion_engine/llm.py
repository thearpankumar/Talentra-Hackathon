"""LLM access: Bedrock Converse (Z.ai GLM 5 by default) with token/cost guard, fallback chain and an offline fake."""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from typing import Any, Sequence

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from .settings import Settings, get_settings

log = logging.getLogger("engine.llm")


class TokenBudgetExceeded(RuntimeError):
    pass


class TokenBudget(BaseCallbackHandler):
    """Counts tokens across all calls in the process and refuses new calls once the budget is spent."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self.calls = 0
        self._lock = threading.Lock()

    def check(self) -> None:
        if self.used >= self.limit:
            raise TokenBudgetExceeded(f"LLM token budget exhausted ({self.used}/{self.limit}); raise LLM_TOKEN_BUDGET to continue")

    def on_llm_start(self, *a: Any, **k: Any) -> None:
        self.check()

    def on_chat_model_start(self, *a: Any, **k: Any) -> None:
        self.check()

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        used = 0
        for gens in response.generations:
            for g in gens:
                um = getattr(getattr(g, "message", None), "usage_metadata", None)
                if um:
                    used += int(um.get("total_tokens", 0))
        with self._lock:
            self.used += used
            self.calls += 1

    def snapshot(self) -> dict[str, int]:
        return {"used": self.used, "limit": self.limit, "calls": self.calls}


_budget: TokenBudget | None = None


def get_budget(settings: Settings | None = None) -> TokenBudget:
    global _budget
    if _budget is None:
        _budget = TokenBudget((settings or get_settings()).token_budget)
    return _budget


# --------------------------------------------------------------------------------------------------
# discovery / probing
# --------------------------------------------------------------------------------------------------
def discover_models(settings: Settings | None = None) -> dict[str, Any]:
    """List GLM / Z.ai models visible to this account+region (foundation models and inference profiles)."""
    import boto3
    s = settings or get_settings()
    out: dict[str, Any] = {"region": s.bedrock_region, "configured_model_id": s.bedrock_model_id, "foundation_models": [],
                           "inference_profiles": [], "error": None}
    try:
        b = boto3.client("bedrock", region_name=s.bedrock_region)
        for m in b.list_foundation_models()["modelSummaries"]:
            if re.search(r"glm|z\.?ai", m["modelId"] + m.get("providerName", ""), re.I):
                out["foundation_models"].append({"id": m["modelId"], "name": m["modelName"], "provider": m.get("providerName")})
        try:
            for p in b.list_inference_profiles()["inferenceProfileSummaries"]:
                if re.search(r"glm|zai", p["inferenceProfileId"], re.I):
                    out["inference_profiles"].append(p["inferenceProfileId"])
        except Exception:
            pass
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    return out


def probe_model(model_id: str, settings: Settings | None = None) -> tuple[bool, str]:
    import boto3
    s = settings or get_settings()
    try:
        c = boto3.client("bedrock-runtime", region_name=s.bedrock_region)
        r = c.converse(modelId=model_id, messages=[{"role": "user", "content": [{"text": "Reply with the single word: ok"}]}],
                       inferenceConfig={"maxTokens": 16, "temperature": 0})
        txt = "".join(p.get("text", "") for p in r["output"]["message"]["content"])
        return True, txt.strip()[:80]
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:240]}"


class LLMHandle:
    def __init__(self, model: BaseChatModel, provider: str, model_id: str, note: str = ""):
        self.model, self.provider, self.model_id, self.note = model, provider, model_id, note

    def status(self) -> dict[str, Any]:
        return {"provider": self.provider, "model_id": self.model_id, "available": True, "note": self.note,
                "budget": get_budget().snapshot()}


_handle: LLMHandle | None = None
_handle_lock = threading.Lock()


def make_bedrock(model_id: str, s: Settings) -> BaseChatModel:
    from botocore.config import Config
    from langchain_aws import ChatBedrockConverse
    return ChatBedrockConverse(model=model_id, region_name=s.bedrock_region, temperature=0.3, max_tokens=4096,
                               config=Config(retries={"max_attempts": 8, "mode": "adaptive"}, read_timeout=120, connect_timeout=10),
                               callbacks=[get_budget(s)])


def get_llm(settings: Settings | None = None, refresh: bool = False) -> LLMHandle:
    """provider=bedrock: configured model only (errors surface). auto: Bedrock primary -> fallbacks -> fake."""
    global _handle
    s = settings or get_settings()
    with _handle_lock:
        if _handle is not None and not refresh:
            return _handle
        notes: list[str] = []
        if s.llm_provider == "fake":
            _handle = LLMHandle(FakeToolLLM(), "fake", "fake-tool-llm", "offline deterministic mock (LLM_PROVIDER=fake)")
            return _handle
        for mid in [s.bedrock_model_id, *s.fallback_model_ids]:
            ok, msg = probe_model(mid, s)
            if ok:
                note = "" if mid == s.bedrock_model_id else f"primary {s.bedrock_model_id} unavailable; using fallback. " + "; ".join(notes)
                _handle = LLMHandle(make_bedrock(mid, s), "bedrock", mid, note)
                return _handle
            notes.append(f"{mid}: {msg}")
            if s.llm_provider == "bedrock" and mid == s.bedrock_model_id and not s.fallback_model_ids:
                break
        if s.llm_provider == "bedrock":
            raise RuntimeError("Bedrock unavailable: " + " | ".join(notes))
        _handle = LLMHandle(FakeToolLLM(), "fake", "fake-tool-llm", "Bedrock unreachable -> offline mock. " + " | ".join(notes)[:600])
        return _handle


def reset_llm() -> None:
    global _handle
    _handle = None


# --------------------------------------------------------------------------------------------------
# offline fake model (deterministic; supports tool calling for the chat agent and JSON for generation)
# --------------------------------------------------------------------------------------------------
def _tc(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "args": args, "id": "call_" + uuid.uuid4().hex[:10], "type": "tool_call"}


class FakeToolLLM(BaseChatModel):
    """Rule-based stand-in for an LLM. Understands the chat intents the UI offers and the scenario-batch prompt."""

    @property
    def _llm_type(self) -> str:
        return "fake-tool-llm"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "FakeToolLLM":
        return self

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        msg = self._respond(messages)
        n_in = sum(len(str(m.content)) for m in messages) // 4
        msg.usage_metadata = {"input_tokens": n_in, "output_tokens": len(str(msg.content)) // 4 + 5,
                              "total_tokens": n_in + len(str(msg.content)) // 4 + 5}
        return ChatResult(generations=[ChatGeneration(message=msg)])

    # ------------------------------------------------------------------
    def _respond(self, messages: list[BaseMessage]) -> AIMessage:
        sys_txt = " ".join(str(m.content) for m in messages if isinstance(m, SystemMessage))
        if "SCENARIO_BATCH_JSON" in sys_txt:
            from .scenario_factory import fake_llm_batch
            return AIMessage(content=fake_llm_batch(str(messages[-1].content)))
        last = messages[-1]
        if isinstance(last, ToolMessage):
            txt = str(last.content)
            try:
                d = json.loads(txt)
                if isinstance(d, dict) and d.get("error"):
                    return AIMessage(content=f"That did not work: {d['error']}")
                if isinstance(d, dict) and d.get("needs_confirmation"):
                    lines = [f"- {f['scenario_id']}: {f['kind']} match with {f['matched_scenario_id']} ({f['rows']} rows already ingested, similarity {f['similarity']})"
                             for f in d["findings"]]
                    return AIMessage(content="These scenarios look already ingested:\n" + "\n".join(lines) +
                                     "\nHow should I proceed: skip, append, or replace? (reply e.g. 'start ingest, replace')")
                if isinstance(d, dict) and d.get("proposal_id"):
                    return AIMessage(content=f"I prepared a change ({d.get('summary','')}). Review the diff in the UI and accept or reject proposal {d['proposal_id']}.")
                if isinstance(d, dict) and d.get("message"):
                    return AIMessage(content=str(d["message"]))
                return AIMessage(content="Done. Result: " + json.dumps(d)[:900])
            except Exception:
                return AIMessage(content="Done. " + txt[:900])
        text = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                text = str(m.content)
                break
        t = text.lower()
        num = re.search(r"\b(\d[\d,_]*)\b", t)
        n = int(num.group(1).replace(",", "").replace("_", "")) if num else None
        sid = re.search(r"\b(scn_[\w\-.]+|smoke_[\w\-.]+|[a-z][\w\-.]*_[\w\-.]+)\b", text)

        def call(name: str, **args: Any) -> AIMessage:
            return AIMessage(content="", tool_calls=[_tc(name, args)])

        if re.search(r"\b(start|run|begin|launch)\b.*\b(ingest|ingestion|load)\b|\bingest\b.*\b(now|please)\b", t):
            dec = next((d for d in ("skip", "append", "replace") if d in t), None)
            confirmed = bool(re.search(r"\b(confirm|yes|go ahead|proceed)\b", t)) or dec is not None
            return call("start_ingest", confirmed=confirmed, decisions={"*": dec} if dec else {})
        if re.search(r"\bpause\b", t):
            return call("pause_run")
        if re.search(r"\b(resume|continue)\b", t):
            return call("resume_run")
        if re.search(r"\b(cancel|abort)\b", t):
            return call("cancel_run")
        if re.search(r"\b(status|progress|how far|eta|rows/s|rate)\b", t):
            return call("get_run_status")
        if re.search(r"\b(select|use|activate|switch)\b.*\b(config|version)\b.*?(\d+)", t):
            return call("select_config_version", version_id=int(re.search(r"(\d+)", t).group(1)))
        if re.search(r"(saved|previous|old).*(config|version)|versions|history", t):
            return call("list_config_versions")
        if re.search(r"ledger|already ingested|memory|ingested scenarios", t):
            return call("get_ledger")
        if re.search(r"\b(generate|create|add|make)\b.*\bscenarios?\b", t) and not re.search(r"\bremove|delete\b", t):
            theme = text
            m = re.search(r"(?:about|for|themed?|like|focus(?:ed)? on|:)\s+(.+)$", text, re.I)
            if m:
                theme = m.group(1)
            return call("generate_scenarios", count=min(n or 5, 5000), theme=theme[:300])
        if re.search(r"\b(remove|delete|drop)\b.*\bscenario\b", t) and sid:
            return call("remove_scenario", scenario_id=sid.group(1))
        if re.search(r"(record|row)s?.*\b(count|to)\b|\bset\b.*\b(records?|rows?)\b|\bscale\b", t) and n is not None:
            if re.search(r"\b(all|every)\b", t) or not sid:
                return call("set_record_count", scenario_id="*", count=n)
            return call("set_record_count", scenario_id=sid.group(1), count=n)
        if re.search(r"\b(workers?|batch size)\b", t) and n is not None:
            key = "workers" if "worker" in t else "batch_size"
            return call("set_defaults", **{key: n})
        if re.search(r"\bvalidate|check\b", t):
            return call("validate_config")
        if re.search(r"\b(schema|tables?)\b", t) and re.search(r"\b(describe|show|list|what)\b", t):
            return call("describe_schema")
        if re.search(r"\b(list|which|what)\b.*scenarios?|\bscenarios\b", t):
            return call("list_scenarios")
        if re.search(r"\b(show|view|print|display|see|get)\b.*\bconfig|\bconfig\b", t):
            return call("get_config")
        return AIMessage(content=(
            "I can view/validate the config, list scenarios, generate or edit scenarios (edits arrive as diffs you accept or reject), "
            "start/pause/resume ingestion, and report progress. Try: 'generate 5 scenarios for gulf blue-collar staffing', "
            "'set records to 20000 for all scenarios', 'start ingest', 'status'."))

"""Environment-driven settings. Secrets are read from .env but never logged."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass
class Settings:
    db_url: str = field(default_factory=lambda: os.getenv(
        "TARGET_DB_URL", "postgresql://root@localhost:26357/talentra?sslmode=disable"))
    schema: str = field(default_factory=lambda: os.getenv("TARGET_SCHEMA", "public"))
    state_dir: Path = field(default_factory=lambda: Path(os.getenv("ENGINE_STATE_DIR", str(ROOT / ".engine_state"))))
    bedrock_region: str = field(default_factory=lambda: os.getenv("BEDROCK_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")))
    bedrock_model_id: str = field(default_factory=lambda: os.getenv("BEDROCK_MODEL_ID", "zai.glm-5"))
    fallback_model_ids: list[str] = field(default_factory=lambda: [
        m.strip() for m in os.getenv("BEDROCK_FALLBACK_MODEL_IDS", "us.anthropic.claude-3-5-haiku-20241022-v1:0").split(",") if m.strip()])
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "auto"))
    token_budget: int = field(default_factory=lambda: int(os.getenv("LLM_TOKEN_BUDGET", "400000")))
    data_dictionary: str | None = field(default_factory=lambda: os.getenv("DATA_DICTIONARY_PATH") or None)

    @property
    def state_db(self) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return self.state_dir / "engine.sqlite"

    @property
    def checkpoint_db(self) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return self.state_dir / "langgraph_checkpoints.sqlite"

    @property
    def pool_dir(self) -> Path:
        p = self.state_dir / "pools"
        p.mkdir(parents=True, exist_ok=True)
        return p


def get_settings() -> Settings:
    return Settings()

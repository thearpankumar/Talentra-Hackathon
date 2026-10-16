"""Scenario/run configuration model (YAML primary, JSON supported), validation and fingerprinting."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DistKind = Literal["normal", "lognormal", "zipf", "pareto", "uniform", "poisson", "bimodal",
                   "exponential", "weights", "categorical", "constant"]

DIST_PARAMS: dict[str, set[str]] = {
    "normal": {"mean", "std"},
    "lognormal": {"mu", "sigma"},
    "zipf": {"a"},
    "pareto": {"alpha", "scale"},
    "uniform": {"low", "high"},
    "poisson": {"lam"},
    "bimodal": {"mean1", "std1", "mean2", "std2", "mix"},
    "exponential": {"scale"},
    "weights": set(),
    "categorical": set(),
    "constant": set(),
}

KNOWN_LOCALES = ["en_IN", "hi_IN", "ta_IN", "bn_BD", "ur_PK", "en_NG", "yo_NG", "ar_AE", "ar_SA", "ar_EG", "en_PH",
                 "zh_CN", "ja_JP", "es_MX", "pt_BR", "fr_FR", "de_DE", "ru_RU", "en_US", "en_GB", "sw_KE"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldSpec(Strict):
    """Distribution for one column. Numeric/date: dist+params(+min/max/round). Categorical: weights."""
    dist: DistKind = "uniform"
    params: dict[str, float] = Field(default_factory=dict)
    min: float | str | None = None
    max: float | str | None = None
    round: int | None = None
    values: list[Any] | None = None  # for dist=weights (parallel to `weights` list) or categorical subset
    weights: dict[str, float] | list[float] | None = None
    value: Any = None  # for dist=constant
    null_rate: float | None = Field(default=None, ge=0, le=1)
    null_mode: Literal["unknown", "not_applicable"] | None = None

    @model_validator(mode="after")
    def _check(self) -> "FieldSpec":
        bad = set(self.params) - DIST_PARAMS[self.dist]
        if bad:
            raise ValueError(f"unknown params {sorted(bad)} for dist '{self.dist}' (allowed: {sorted(DIST_PARAMS[self.dist])})")
        if self.dist == "categorical":
            if not isinstance(self.weights, dict) or not self.weights:
                raise ValueError("categorical requires weights as a non-empty mapping value->weight")
        if self.dist == "weights":
            if not (isinstance(self.weights, list) and self.values and len(self.values) == len(self.weights)):
                raise ValueError("dist 'weights' requires equal-length `values` and list `weights`")
        w = self.weights.values() if isinstance(self.weights, dict) else (self.weights or [])
        if any(x < 0 for x in w) or (w and sum(w) <= 0):
            raise ValueError("weights must be non-negative with positive sum")
        if self.dist == "zipf" and self.params.get("a", 2.0) <= 1:
            raise ValueError("zipf param a must be > 1")
        if self.dist == "constant" and self.value is None:
            raise ValueError("constant requires `value`")
        if self.min is not None and self.max is not None and isinstance(self.min, (int, float)) \
                and isinstance(self.max, (int, float)) and self.min > self.max:
            raise ValueError("min must be <= max")
        return self


class FkSpec(Strict):
    """How a foreign-key column samples its parent ids."""
    scope: Literal["scenario", "any"] = "any"  # scenario: parents inserted by this scenario run; any: existing rows in DB
    skew: Literal["uniform", "zipf", "pareto"] = "uniform"
    alpha: float = Field(default=1.1, gt=0)
    pool_size: int | None = Field(default=None, gt=0)


class TableSpec(Strict):
    table: str
    record_count: int = Field(ge=0)
    fields: dict[str, FieldSpec] = Field(default_factory=dict)
    fk: dict[str, FkSpec] = Field(default_factory=dict)
    duplicate_rate: float | None = Field(default=None, ge=0, le=1)
    null_rate: float | None = Field(default=None, ge=0, le=1)
    constants: dict[str, Any] = Field(default_factory=dict)


class DateRange(Strict):
    start: date = date(2023, 1, 1)
    end: date = date(2025, 12, 31)

    @model_validator(mode="after")
    def _ord(self) -> "DateRange":
        if self.end < self.start:
            raise ValueError("date_range.end must not be earlier than start")
        return self


class Scenario(Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_\-\.]{1,80}$")
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    fingerprint: str | None = None
    seed: int = 42
    locale_mix: dict[str, float] = Field(default_factory=lambda: {"en_IN": 1.0})
    duplicate_rate: float = Field(default=0.0, ge=0, le=0.5)
    null_rate: float = Field(default=0.05, ge=0, le=1)  # of nullable columns; split unknown/not_applicable
    typo_rate: float = Field(default=0.01, ge=0, le=1)
    date_range: DateRange = Field(default_factory=DateRange)
    tables: list[TableSpec]

    @field_validator("locale_mix")
    @classmethod
    def _loc(cls, v: dict[str, float]) -> dict[str, float]:
        if not v or sum(v.values()) <= 0 or any(x < 0 for x in v.values()):
            raise ValueError("locale_mix must have positive weights")
        unknown = [k for k in v if k not in KNOWN_LOCALES]
        if unknown:
            raise ValueError(f"unknown locales {unknown}; known: {KNOWN_LOCALES}")
        return v

    @model_validator(mode="after")
    def _tables(self) -> "Scenario":
        names = [t.table for t in self.tables]
        if len(set(names)) != len(names):
            raise ValueError("a table may appear only once per scenario")
        if not names:
            raise ValueError("scenario needs at least one table")
        return self

    @property
    def target_tables(self) -> list[str]:
        return [t.table for t in self.tables]

    @property
    def total_records(self) -> int:
        return sum(t.record_count for t in self.tables)

    # --- scenario memory -------------------------------------------------
    def semantic(self) -> dict[str, Any]:
        """Content that defines *what kind of data* this is (excludes id/name/description/seed/record counts)."""
        d = self.model_dump(mode="json", exclude={"id", "name", "description", "tags", "fingerprint", "seed"})
        for t in d["tables"]:
            t.pop("record_count", None)
        d["tables"] = sorted(d["tables"], key=lambda t: t["table"])
        d["locale_mix"] = {k: round(v / sum(d["locale_mix"].values()), 3) for k, v in sorted(d["locale_mix"].items())}
        return d

    def compute_fingerprint(self) -> str:
        blob = json.dumps(self.semantic(), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:20]

    def features(self) -> list[str]:
        """Coarse feature tokens used for 'similar scenario' detection (Jaccard)."""
        f: set[str] = set()
        total = sum(self.locale_mix.values())
        for k, v in self.locale_mix.items():
            if v / total >= 0.15:
                f.add(f"locale:{k}")
        f.add(f"dup:{round(self.duplicate_rate * 20)}")
        f.add(f"dr:{self.date_range.start.year}-{self.date_range.end.year}")
        for t in self.tables:
            f.add(f"table:{t.table}")
            for c, fs in t.fields.items():
                f.add(f"field:{t.table}.{c}:{fs.dist}")
                if fs.dist in ("categorical",) and fs.weights:
                    top = max(fs.weights.items(), key=lambda kv: kv[1])[0]
                    f.add(f"top:{t.table}.{c}:{top}")
            for c, k in t.fk.items():
                f.add(f"fk:{t.table}.{c}:{k.scope}:{k.skew}")
        for tag in self.tags:
            f.add(f"tag:{tag.lower()}")
        return sorted(f)


class Defaults(Strict):
    batch_size: int = Field(default=10_000, ge=100, le=200_000)
    workers: int = Field(default=4, ge=1, le=64)
    max_inflight_batches: int | None = None
    throttle_rows_per_sec: int | None = Field(default=None, ge=1)  # global soft cap


class EngineConfig(Strict):
    version: int = 1
    defaults: Defaults = Field(default_factory=Defaults)
    scenarios: list[Scenario] = Field(default_factory=list)

    @model_validator(mode="after")
    def _uniq(self) -> "EngineConfig":
        ids = [s.id for s in self.scenarios]
        if len(set(ids)) != len(ids):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate scenario ids: {dup}")
        for s in self.scenarios:
            s.fingerprint = s.compute_fingerprint()
        return self

    @property
    def total_records(self) -> int:
        return sum(s.total_records for s in self.scenarios)


def config_json_schema() -> dict[str, Any]:
    return EngineConfig.model_json_schema()


def parse_config(data: dict[str, Any] | str, fmt: str | None = None) -> EngineConfig:
    if isinstance(data, str):
        data = json.loads(data) if (fmt == "json" or data.lstrip().startswith("{")) else yaml.safe_load(data)
    return EngineConfig.model_validate(data or {})


def load_config(path: str | Path) -> EngineConfig:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return parse_config(text, "json" if p.suffix.lower() == ".json" else "yaml")


def dump_config(cfg: EngineConfig, fmt: str = "yaml") -> str:
    d = cfg.model_dump(mode="json", exclude_none=True)
    if fmt == "json":
        return json.dumps(d, indent=2, ensure_ascii=False)
    return yaml.safe_dump(d, sort_keys=False, allow_unicode=True)


def save_config(cfg: EngineConfig, path: str | Path) -> None:
    p = Path(path)
    p.write_text(dump_config(cfg, "json" if p.suffix.lower() == ".json" else "yaml"), encoding="utf-8")


def validate_against_schema(cfg: EngineConfig, schema: Any) -> list[str]:
    """Check tables/columns referenced by the config exist in the live schema (and FK scopes make sense)."""
    issues: list[str] = []
    for s in cfg.scenarios:
        in_scn = set(s.target_tables)
        for t in s.tables:
            tb = schema.tables.get(t.table)
            if tb is None:
                issues.append(f"[{s.id}] table '{t.table}' does not exist in target schema")
                continue
            for c in list(t.fields) + list(t.constants):
                if c not in tb.columns:
                    issues.append(f"[{s.id}] {t.table}.{c} does not exist")
            for c, k in t.fk.items():
                fk = tb.fk_for(c)
                if fk is None:
                    issues.append(f"[{s.id}] {t.table}.{c} is not a foreign key")
                elif k.scope == "scenario" and fk.ref_table not in in_scn:
                    issues.append(f"[{s.id}] {t.table}.{c} scope=scenario but parent '{fk.ref_table}' is not in this scenario")
    return issues


def diff_configs(old: str, new: str) -> str:
    import difflib
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "current", "proposed"))

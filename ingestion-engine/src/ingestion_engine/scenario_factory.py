"""Scenario briefs -> validated Scenario objects; plus a procedural brief generator used by the offline fake LLM.

A *brief* is the compact JSON an LLM emits (cheap in tokens); `compile_brief` expands it deterministically into a full
schema-aware Scenario (tables, FK skew, per-field distributions). This keeps LLM output small enough to generate
thousands of scenarios in many cheap calls while guaranteeing the config is always valid.
"""
from __future__ import annotations

import hashlib
import json
import re
import zlib
from typing import Any

import numpy as np

from .config import KNOWN_LOCALES, FieldSpec, FkSpec, Scenario, TableSpec
from .generate import CURRENCY_SALARY_MU, COUNTRY_CURRENCY
from .locales import LOCALES
from .schema import Schema

REGIONS: list[tuple[str, dict[str, float]]] = [
    ("Bangalore", {"en_IN": 0.6, "hi_IN": 0.2, "ta_IN": 0.2}), ("Mumbai", {"en_IN": 0.55, "hi_IN": 0.45}),
    ("Delhi NCR", {"hi_IN": 0.55, "en_IN": 0.45}), ("Hyderabad", {"en_IN": 0.7, "hi_IN": 0.1, "ta_IN": 0.2}),
    ("Chennai", {"ta_IN": 0.6, "en_IN": 0.4}), ("Pune", {"en_IN": 0.75, "hi_IN": 0.25}), ("Lagos", {"en_NG": 0.6, "yo_NG": 0.4}),
    ("Abuja", {"en_NG": 0.8, "yo_NG": 0.2}), ("Dubai", {"ar_AE": 0.35, "en_IN": 0.3, "en_PH": 0.15, "ur_PK": 0.2}),
    ("Abu Dhabi", {"ar_AE": 0.4, "en_IN": 0.3, "bn_BD": 0.15, "en_PH": 0.15}), ("Riyadh", {"ar_SA": 0.45, "ur_PK": 0.2, "bn_BD": 0.15, "en_IN": 0.2}),
    ("Jeddah", {"ar_SA": 0.5, "ur_PK": 0.2, "ar_EG": 0.15, "en_PH": 0.15}), ("Manila", {"en_PH": 1.0}), ("Cairo", {"ar_EG": 1.0}),
    ("Nairobi", {"sw_KE": 0.7, "en_GB": 0.3}), ("Dhaka", {"bn_BD": 1.0}), ("Karachi", {"ur_PK": 0.8, "en_GB": 0.2}),
    ("Sao Paulo", {"pt_BR": 1.0}), ("Mexico City", {"es_MX": 1.0}), ("Berlin", {"de_DE": 0.7, "en_GB": 0.15, "ru_RU": 0.15}),
    ("Paris", {"fr_FR": 0.85, "ar_EG": 0.15}), ("London", {"en_GB": 0.7, "en_IN": 0.2, "ur_PK": 0.1}), ("New York", {"en_US": 0.8, "es_MX": 0.2}),
    ("Austin", {"en_US": 0.85, "en_IN": 0.15}), ("Shanghai", {"zh_CN": 1.0}), ("Tokyo", {"ja_JP": 1.0}), ("Moscow", {"ru_RU": 1.0}),
    ("Gulf corridor", {"ar_AE": 0.2, "ar_SA": 0.2, "en_IN": 0.2, "ur_PK": 0.15, "bn_BD": 0.1, "en_PH": 0.1, "en_NG": 0.05}),
    ("South Asia", {"en_IN": 0.3, "hi_IN": 0.2, "bn_BD": 0.2, "ur_PK": 0.2, "ta_IN": 0.1}), ("West Africa", {"en_NG": 0.6, "yo_NG": 0.3, "en_GB": 0.1}),
]

PERSONAS: dict[str, dict[str, Any]] = {
    "freshers": {"label": "freshers", "story": "candidates", "exp": ("exponential", {"scale": 0.8}, 0, 3), "edu": {"bachelor": 0.6, "master": 0.2, "diploma": 0.15, "secondary": 0.05}, "salary_shift": -0.5, "notice": {"0": 0.5, "15": 0.3, "30": 0.2}},
    "mid_career": {"label": "mid-career professionals", "story": "candidates", "exp": ("lognormal", {"mu": 1.9, "sigma": 0.3}, 4, 14), "edu": {"bachelor": 0.55, "master": 0.35, "doctorate": 0.02, "diploma": 0.08}, "salary_shift": 0.2, "notice": {"30": 0.4, "60": 0.35, "90": 0.25}},
    "senior": {"label": "senior leaders", "story": "candidates", "exp": ("normal", {"mean": 16, "std": 4}, 10, 35), "edu": {"master": 0.5, "bachelor": 0.4, "doctorate": 0.1}, "salary_shift": 0.8, "notice": {"60": 0.3, "90": 0.7}},
    "blue_collar": {"label": "blue-collar workers", "story": "candidates", "exp": ("lognormal", {"mu": 1.6, "sigma": 0.6}, 0, 30), "edu": {"secondary": 0.55, "none": 0.15, "diploma": 0.3}, "salary_shift": -1.2, "notice": {"0": 0.7, "15": 0.3}},
    "gig": {"label": "gig freelancers", "story": "candidates", "exp": ("bimodal", {"mean1": 1.5, "std1": 1, "mean2": 7, "std2": 2, "mix": 0.55}, 0, 20), "edu": {"bachelor": 0.4, "diploma": 0.25, "secondary": 0.25, "master": 0.1}, "salary_shift": -0.7, "notice": {"0": 0.9, "15": 0.1}},
    "staffing_demand": {"label": "staffing demand surge", "story": "employers", "emp_size": {"201-1000": 0.3, "1001-5000": 0.3, "51-200": 0.25, "5000+": 0.15}, "etype": {"contract": 0.5, "full_time": 0.3, "gig": 0.2}},
    "startup_hiring": {"label": "startup hiring", "story": "employers", "emp_size": {"1-10": 0.35, "11-50": 0.45, "51-200": 0.2}, "etype": {"full_time": 0.7, "internship": 0.15, "contract": 0.15}},
    "enterprise_hiring": {"label": "enterprise hiring", "story": "employers", "emp_size": {"1001-5000": 0.4, "5000+": 0.45, "201-1000": 0.15}, "etype": {"full_time": 0.8, "part_time": 0.1, "contract": 0.1}},
    "gig_marketplace": {"label": "gig marketplace", "story": "marketplace", "emp_size": {"1-10": 0.5, "11-50": 0.3, "51-200": 0.2}, "etype": {"gig": 0.7, "contract": 0.2, "part_time": 0.1}},
    "full_marketplace": {"label": "end-to-end hiring market", "story": "marketplace", "emp_size": {"11-50": 0.3, "51-200": 0.3, "201-1000": 0.25, "1001-5000": 0.15}, "etype": {"full_time": 0.6, "contract": 0.2, "part_time": 0.1, "internship": 0.1}},
}
SKILLS = ["Java", "Python", "JavaScript", "React", "SQL", "Spark", "Go", "AWS", "Figma", "Photoshop", "Electrical wiring", "Arc welding",
          "Forklift", "Heavy vehicle driving", "Cooking", "Patient care", "Excel", "Tally", "CRM", "First aid", "Customer support", "Accounting"]
MODIFIERS = ["", "seasonal surge", "post-layoff wave", "campus hiring season", "remote-first", "night shift", "walk-in drive",
             "festival season", "monsoon slowdown", "visa-linked", "women returners", "referral driven"]
SCALES = {"small": 2000, "medium": 10000, "large": 40000}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:48]


def procedural_briefs(rng: np.random.Generator, n: int, theme: str, existing: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    theme_l = theme.lower()
    regions = [r for r in REGIONS if _slug(r[0]).replace("_", " ") in theme_l or r[0].lower() in theme_l] or REGIONS
    personas = [k for k, v in PERSONAS.items() if v["label"].split()[0] in theme_l or k.replace("_", " ") in theme_l] or list(PERSONAS)
    if re.search(r"blue.?collar|staffing|labou?r", theme_l):
        personas = ["blue_collar", "staffing_demand", "gig"] + personas[:2]
    if re.search(r"gig|freelanc", theme_l):
        personas = ["gig", "gig_marketplace"] + personas[:1]
    skills = [s for s in SKILLS if re.search(r'' + re.escape(s.lower()) + r'', theme_l)] or SKILLS
    tries = 0
    while len(out) < n and tries < n * 30:
        tries += 1
        reg_name, mix = regions[int(rng.integers(len(regions)))]
        pk = personas[int(rng.integers(len(personas)))]
        p = PERSONAS[pk]
        skill = skills[int(rng.integers(len(skills)))]
        mod = MODIFIERS[int(rng.integers(len(MODIFIERS)))]
        variant = int(rng.integers(1, 10_000))
        name = f"{reg_name} {p['label']}, {skill}-heavy" + (f", {mod}" if mod else "") + f" #{variant}"
        if name in existing:
            continue
        existing.add(name)
        jitter = {k: round(float(v) * float(rng.uniform(0.7, 1.3)), 3) for k, v in mix.items()}
        out.append({
            "name": name,
            "description": f"{p['label'].capitalize()} in {reg_name} with heavy {skill} skills" + (f" ({mod})" if mod else "") + ".",
            "tags": [reg_name.lower().replace(" ", "-"), pk, skill.lower().replace(" ", "-")] + ([mod.replace(" ", "-")] if mod else []),
            "locale_mix": jitter, "persona": pk, "scale": str(rng.choice(["small", "small", "medium", "medium", "large"])),
            "duplicate_rate": round(float(rng.choice([0.0, 0.01, 0.02, 0.05, 0.08])), 2),
            "null_rate": round(float(rng.uniform(0.02, 0.15)), 2),
            "salary_sigma": round(float(rng.uniform(0.3, 0.8)), 2),
            "salary_shift": round(float(rng.uniform(-0.3, 0.3)), 2),
            "date_range": {"start": f"{int(rng.integers(2022, 2025))}-01-01", "end": f"{int(rng.integers(2025, 2027))}-{int(rng.integers(1, 13)):02d}-28"},
        })
    return out


def fake_llm_batch(prompt: str) -> str:
    """Fake-LLM entry: prompt contains a JSON header produced by scenario_graph."""
    m = re.search(r"\{.*\}", prompt, re.S)
    hdr = json.loads(m.group(0)) if m else {}
    rng = np.random.default_rng(int(hdr.get("seed", 0)))
    return json.dumps(procedural_briefs(rng, int(hdr.get("count", 5)), hdr.get("theme", ""), set(hdr.get("existing", []))))


# ---------------------------------------------------------------------------------------------------
def compile_brief(brief: dict[str, Any], schema: Schema, scale_factor: float = 1.0, seed_salt: str = "") -> Scenario:
    persona = PERSONAS.get(brief.get("persona", "full_marketplace"), PERSONAS["full_marketplace"])
    story = brief.get("story") or persona["story"]
    base = int(SCALES.get(brief.get("scale", "small"), 2000) * scale_factor)
    mix = {k: float(v) for k, v in (brief.get("locale_mix") or {"en_IN": 1.0}).items() if k in KNOWN_LOCALES and float(v) > 0} or {"en_IN": 1.0}
    primary = max(mix, key=lambda k: mix[k])
    cur = COUNTRY_CURRENCY.get(LOCALES[primary].country, "USD")
    mu = CURRENCY_SALARY_MU.get(cur, 11.0) + float(brief.get("salary_shift", 0)) + persona.get("salary_shift", 0) * 0.3
    sig = float(brief.get("salary_sigma", 0.5))
    T = schema.tables

    plan: list[tuple[str, int]] = []
    if story in ("candidates", "marketplace"):
        plan += [("users", base), ("candidate_profile", base), ("candidate_skill", int(base * 2.5))]
    if story in ("employers", "marketplace"):
        plan += [("employer", max(5, int(base * 0.06))), ("job_requirement", max(10, int(base * 0.35)))]
    if story == "marketplace":
        plan += [("application", int(base * 1.5))]
    plan = [(t, c) for t, c in plan if t in T]
    if not plan:  # unknown schema: load a few leaf-ish tables
        roots = [t for t in schema.order() if not T[t].parents()][:2]
        plan = [(t, base) for t in roots]
    names = {t for t, _ in plan}

    tables: list[TableSpec] = []
    for tname, cnt in plan:
        t = T[tname]
        fields: dict[str, FieldSpec] = {}
        fk: dict[str, FkSpec] = {}
        constants: dict[str, Any] = {}

        def has(c: str) -> bool:
            return c in t.columns

        if tname == "users" and has("role"):
            constants["role"] = "candidate"
        if tname == "candidate_profile":
            d, params, lo, hi = persona.get("exp", ("exponential", {"scale": 2}, 0, 10))
            if has("years_experience"):
                fields["years_experience"] = FieldSpec(dist=d, params=params, min=lo, max=hi, round=1)
            if has("expected_salary_min"):
                fields["expected_salary_min"] = FieldSpec(dist="lognormal", params={"mu": round(mu, 3), "sigma": sig}, round=2)
            if has("education_level") and persona.get("edu"):
                fields["education_level"] = FieldSpec(dist="categorical", weights=persona["edu"])
            if has("notice_period_days") and persona.get("notice"):
                fields["notice_period_days"] = FieldSpec(dist="categorical", weights=persona["notice"])
        if tname == "employer" and has("size_band") and persona.get("emp_size"):
            fields["size_band"] = FieldSpec(dist="categorical", weights=persona["emp_size"])
        if tname == "job_requirement":
            if has("employment_type") and persona.get("etype"):
                fields["employment_type"] = FieldSpec(dist="categorical", weights=persona["etype"])
            if has("salary_min"):
                fields["salary_min"] = FieldSpec(dist="lognormal", params={"mu": round(mu, 3), "sigma": sig}, round=2)
            if has("openings"):
                fields["openings"] = FieldSpec(dist="poisson", params={"lam": 6.0 if persona["story"] != "candidates" else 3.0}, min=1)
            fk_alpha = round(1.05 + (zlib.crc32(brief.get("name", "").encode()) % 40) / 100, 2)
            if "employer" in names:
                fk["employer_id"] = FkSpec(scope="scenario", skew="zipf", alpha=fk_alpha)
        if tname == "application":
            if "job_requirement" in names:
                fk["job_requirement_id"] = FkSpec(scope="scenario", skew="zipf", alpha=1.1)
            if "candidate_profile" in names:
                fk["candidate_profile_id"] = FkSpec(scope="scenario", skew="pareto", alpha=1.5)
        if tname == "candidate_skill" and "candidate_profile" in names:
            fk["candidate_profile_id"] = FkSpec(scope="scenario", skew="uniform")
        if tname == "candidate_profile" and "users" in names:
            fk["user_id"] = FkSpec(scope="scenario")
        tables.append(TableSpec(table=tname, record_count=max(1, cnt), fields=fields, fk=fk, constants=constants))

    h = hashlib.sha1((brief.get("name", "") + seed_salt).encode()).hexdigest()
    dr = brief.get("date_range") or {}
    return Scenario(
        id=f"scn_{_slug(brief.get('name', 'scenario'))}_{h[:6]}", name=brief.get("name", "Scenario"),
        description=brief.get("description", ""), tags=list(brief.get("tags", []))[:10], seed=int(h[:6], 16),
        locale_mix=mix, duplicate_rate=min(max(float(brief.get("duplicate_rate", 0.02)), 0.0), 0.5),
        null_rate=min(max(float(brief.get("null_rate", 0.05)), 0.0), 1.0), typo_rate=0.01,
        date_range={"start": dr.get("start", "2024-01-01"), "end": dr.get("end", "2025-12-31")}, tables=tables)

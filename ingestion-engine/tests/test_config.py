import json

import pytest
from pydantic import ValidationError

from ingestion_engine.config import (FieldSpec, config_json_schema, dump_config, parse_config, diff_configs)
from .conftest import make_cfg


def test_yaml_json_roundtrip_and_fingerprint():
    cfg = parse_config(make_cfg())
    y = dump_config(cfg, "yaml")
    j = dump_config(cfg, "json")
    c2, c3 = parse_config(y), parse_config(j, "json")
    fp = cfg.scenarios[0].fingerprint
    assert fp and fp == c2.scenarios[0].fingerprint == c3.scenarios[0].fingerprint


def test_fingerprint_ignores_counts_name_seed_but_not_distributions():
    a = make_cfg()
    b = make_cfg(users=9999, seed=5)
    b["scenarios"][0]["name"] = "other name"
    assert parse_config(a).scenarios[0].fingerprint == parse_config(b).scenarios[0].fingerprint
    c = make_cfg()
    c["scenarios"][0]["tables"][1]["fields"]["years_experience"]["params"]["scale"] = 3.0
    assert parse_config(c).scenarios[0].fingerprint != parse_config(a).scenarios[0].fingerprint


@pytest.mark.parametrize("mut,msg", [
    (lambda d: d["scenarios"][0].update(locale_mix={"xx_YY": 1}), "unknown locales"),
    (lambda d: d["scenarios"][0]["tables"][0].update(record_count=-1), "greater than or equal"),
    (lambda d: d["scenarios"][0].update(duplicate_rate=0.9), "less than or equal"),
    (lambda d: d["scenarios"].append(d["scenarios"][0]), "duplicate scenario ids"),
    (lambda d: d["scenarios"][0]["tables"][1]["fields"]["years_experience"].update(params={"bogus": 1}), "unknown params"),
    (lambda d: d["scenarios"][0].update(date_range={"start": "2025-01-01", "end": "2024-01-01"}), "must not be earlier"),
])
def test_validation_errors(mut, msg):
    d = make_cfg()
    mut(d)
    with pytest.raises(ValidationError) as e:
        parse_config(d)
    assert msg in str(e.value)


def test_field_spec_rules():
    with pytest.raises(ValidationError):
        FieldSpec(dist="categorical")
    with pytest.raises(ValidationError):
        FieldSpec(dist="weights", values=[1, 2], weights=[1.0])
    with pytest.raises(ValidationError):
        FieldSpec(dist="zipf", params={"a": 0.5})
    assert FieldSpec(dist="weights", values=[1, 2], weights=[1.0, 3.0])


def test_json_schema_lists_all_distributions():
    s = json.dumps(config_json_schema())
    for d in ["normal", "lognormal", "zipf", "pareto", "uniform", "poisson", "bimodal", "exponential", "weights", "categorical"]:
        assert d in s


def test_features_similarity_and_diff():
    a = parse_config(make_cfg()).scenarios[0]
    b = parse_config(make_cfg(scn_id="x")).scenarios[0]
    assert set(a.features()) == set(b.features())
    d = diff_configs("a: 1\n", "a: 2\n")
    assert "-a: 1" in d and "+a: 2" in d

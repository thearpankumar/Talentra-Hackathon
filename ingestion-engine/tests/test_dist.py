import numpy as np

from ingestion_engine.config import FieldSpec, FkSpec
from ingestion_engine.dist import sample_categorical, sample_numeric, sample_unit, skew_indices

R = lambda: np.random.default_rng(1)  # noqa: E731


def test_normal_lognormal_uniform_poisson_exponential():
    n = 100_000
    assert abs(sample_numeric(FieldSpec(dist="normal", params={"mean": 10, "std": 2}), R(), n).mean() - 10) < 0.05
    ln = sample_numeric(FieldSpec(dist="lognormal", params={"mu": 1.0, "sigma": 0.5}), R(), n)
    assert abs(np.median(ln) - np.e) < 0.1 and ln.mean() > np.median(ln)
    u = sample_numeric(FieldSpec(dist="uniform", params={"low": 5, "high": 7}), R(), n)
    assert u.min() >= 5 and u.max() <= 7
    assert abs(sample_numeric(FieldSpec(dist="poisson", params={"lam": 4}), R(), n).mean() - 4) < 0.05
    assert abs(sample_numeric(FieldSpec(dist="exponential", params={"scale": 3}), R(), n).mean() - 3) < 0.1


def test_bimodal_has_two_modes_and_clip_round():
    x = sample_numeric(FieldSpec(dist="bimodal", params={"mean1": 0, "std1": 1, "mean2": 20, "std2": 1, "mix": 0.5}), R(), 50_000)
    assert 0.45 < (x < 10).mean() < 0.55
    y = sample_numeric(FieldSpec(dist="normal", params={"mean": 5, "std": 5}, min=0, max=8, round=1), R(), 10_000)
    assert y.min() >= 0 and y.max() <= 8 and np.allclose(y, np.round(y, 1))


def test_pareto_zipf_heavy_tail():
    p = sample_numeric(FieldSpec(dist="pareto", params={"alpha": 1.5, "scale": 1}), R(), 50_000)
    assert p.min() >= 1 and np.quantile(p, 0.99) > 10 * np.median(p) / 2
    z = sample_numeric(FieldSpec(dist="zipf", params={"a": 2.0}), R(), 50_000)
    assert (z == 1).mean() > 0.5


def test_categorical_and_custom_weights():
    c = sample_categorical(FieldSpec(dist="categorical", weights={"a": 0.8, "b": 0.2}), R(), 20_000)
    assert 0.77 < (c == "a").mean() < 0.83
    w = sample_numeric(FieldSpec(dist="weights", values=[1, 10], weights=[0.25, 0.75]), R(), 20_000)
    assert 0.72 < (w == 10).mean() < 0.78


def test_skew_indices_zipf_concentrates_on_head():
    n, pool = 100_000, 1000
    z = skew_indices(FkSpec(skew="zipf", alpha=1.2), R(), n, pool)
    u = skew_indices(FkSpec(skew="uniform"), R(), n, pool)
    assert z.max() < pool and z.min() >= 0
    assert (z < 10).mean() > 5 * (u < 10).mean()
    p = skew_indices(FkSpec(skew="pareto", alpha=1.5), R(), n, pool)
    assert (p < 100).mean() > (u < 100).mean()


def test_unit_samples_in_range():
    for d, params in [("exponential", {"scale": 1}), ("normal", {"mean": 0, "std": 1}), ("lognormal", {"mu": 0, "sigma": 1})]:
        x = sample_unit(FieldSpec(dist=d, params=params), R(), 1000)
        assert x.min() >= 0 and x.max() <= 1

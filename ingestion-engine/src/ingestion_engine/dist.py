"""Vectorised statistical samplers driven by FieldSpec."""
from __future__ import annotations

import numpy as np

from .config import FieldSpec, FkSpec


def sample_numeric(spec: FieldSpec, rng: np.random.Generator, n: int,
                   default_lo: float = 0.0, default_hi: float = 1.0) -> np.ndarray:
    """Return float64 samples honouring distribution + [min,max] clipping + rounding."""
    p = spec.params
    lo = float(spec.min) if isinstance(spec.min, (int, float)) else default_lo
    hi = float(spec.max) if isinstance(spec.max, (int, float)) else default_hi
    d = spec.dist
    if d == "normal":
        x = rng.normal(p.get("mean", (lo + hi) / 2), p.get("std", (hi - lo) / 6 or 1.0), n)
    elif d == "lognormal":
        x = rng.lognormal(p.get("mu", 0.0), p.get("sigma", 0.5), n)
    elif d == "zipf":
        x = rng.zipf(max(p.get("a", 2.0), 1.01), n).astype(np.float64)
    elif d == "pareto":
        x = (rng.pareto(p.get("alpha", 2.0), n) + 1.0) * p.get("scale", 1.0)
    elif d == "uniform":
        x = rng.uniform(p.get("low", lo), p.get("high", hi), n)
    elif d == "poisson":
        x = rng.poisson(p.get("lam", 3.0), n).astype(np.float64)
    elif d == "exponential":
        x = rng.exponential(p.get("scale", 1.0), n)
    elif d == "bimodal":
        mix = p.get("mix", 0.5)
        pick = rng.random(n) < mix
        x = np.where(pick, rng.normal(p.get("mean1", lo), p.get("std1", 1.0), n),
                     rng.normal(p.get("mean2", hi), p.get("std2", 1.0), n))
    elif d == "constant":
        x = np.full(n, float(spec.value))
    elif d in ("weights", "categorical"):
        vals, w = _weights(spec)
        x = rng.choice(np.array(vals, dtype=np.float64), size=n, p=w)
    else:  # pragma: no cover
        raise ValueError(d)
    if spec.min is not None and isinstance(spec.min, (int, float)):
        x = np.maximum(x, lo)
    if spec.max is not None and isinstance(spec.max, (int, float)):
        x = np.minimum(x, hi)
    if spec.round is not None:
        x = np.round(x, spec.round)
    return x


def sample_unit(spec: FieldSpec | None, rng: np.random.Generator, n: int) -> np.ndarray:
    """Samples in [0,1] used for dates/timestamps: the distribution shapes *where* in the range values fall."""
    if spec is None or spec.dist == "uniform":
        return rng.random(n)
    if spec.dist in ("weights", "categorical", "constant"):
        return np.clip(sample_numeric(spec, rng, n, 0, 1), 0, 1)
    x = sample_numeric(FieldSpec(dist=spec.dist, params=spec.params), rng, n, 0, 1)
    lo, hi = np.quantile(x, 0.001), np.quantile(x, 0.999)
    if hi <= lo:
        return rng.random(n)
    return np.clip((x - lo) / (hi - lo), 0, 1)


def _weights(spec: FieldSpec) -> tuple[list, np.ndarray]:
    if isinstance(spec.weights, dict):
        vals, w = list(spec.weights.keys()), np.array(list(spec.weights.values()), dtype=np.float64)
    else:
        vals, w = list(spec.values or []), np.array(spec.weights or [], dtype=np.float64)
    return vals, w / w.sum()


def sample_categorical(spec: FieldSpec, rng: np.random.Generator, n: int) -> np.ndarray:
    if spec.dist == "constant":
        return np.full(n, str(spec.value), dtype=object)
    vals, w = _weights(spec)
    idx = rng.choice(len(vals), size=n, p=w)
    return np.array([str(v) for v in vals], dtype=object)[idx]


def choose_weighted(options: list[str], weights: list[float] | None, rng: np.random.Generator, n: int) -> np.ndarray:
    if weights is None:
        idx = rng.integers(0, len(options), n)
    else:
        w = np.array(weights, dtype=np.float64)
        idx = rng.choice(len(options), size=n, p=w / w.sum())
    return np.array(options, dtype=object)[idx]


def skew_indices(skew: FkSpec, rng: np.random.Generator, n: int, pool: int) -> np.ndarray:
    """Indices in [0,pool) with the requested popularity skew (rank 0 = most popular)."""
    if pool <= 1:
        return np.zeros(n, dtype=np.int64)
    u = rng.random(n)
    if skew.skew == "uniform":
        return np.minimum((u * pool).astype(np.int64), pool - 1)
    a = skew.alpha
    if skew.skew == "zipf":
        # inverse CDF of bounded power law p(k) ~ k^-a over [1, pool]
        if abs(a - 1.0) < 1e-9:
            k = np.exp(u * np.log(pool))
        else:
            k = ((pool ** (1 - a) - 1) * u + 1) ** (1 / (1 - a))
    else:  # pareto: heavy tail on rank
        k = np.minimum(1 + (np.power(1 - u, -1 / a) - 1) * pool / 20.0, pool)
    return np.clip(k.astype(np.int64) - 1, 0, pool - 1)

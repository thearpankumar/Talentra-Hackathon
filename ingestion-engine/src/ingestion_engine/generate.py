"""Schema-driven, deterministic batch generator.

A batch is a pure function of (scenario seed, table, batch index, token base, pool snapshots) so replays are
byte-identical: this is what makes batches idempotent and purge-by-regeneration possible.
Every column draws from its own RNG stream, so generating a subset of columns (e.g. only PKs) is reproducible.
"""
from __future__ import annotations

import math
import re
import unicodedata
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import locales as L
from .config import FieldSpec, FkSpec, Scenario, TableSpec
from .dist import (choose_weighted, sample_categorical, sample_numeric, sample_unit, skew_indices)
from .schema import Column, Schema, Table, ordered_pairs

NULL = "\\N"
STREAM_ID, STREAM_DATA = 0, 1
AUDIT_DEFAULT_COLS = {"created_by", "updated_by"}
COUNTRY_CURRENCY = {"IN": "INR", "NG": "NGN", "AE": "AED", "SA": "SAR", "PH": "PHP", "US": "USD", "GB": "GBP", "DE": "EUR",
                    "FR": "EUR", "BR": "BRL", "MX": "MXN", "CN": "CNY", "JP": "JPY", "BD": "BDT", "PK": "PKR", "KE": "KES",
                    "EG": "EGP", "RU": "RUB"}
COUNTRY_CENTROID = {"IN": (22.0, 79.0), "NG": (9.1, 8.7), "AE": (24.4, 54.4), "SA": (24.0, 45.0), "PH": (13.0, 122.0),
                    "US": (38.0, -97.0), "GB": (54.0, -2.0), "DE": (51.0, 10.0), "FR": (46.5, 2.5), "BR": (-14.0, -51.0),
                    "MX": (23.6, -102.5), "CN": (35.0, 103.0), "JP": (36.0, 138.0), "BD": (23.7, 90.4), "PK": (30.0, 70.0),
                    "KE": (0.0, 38.0), "EG": (26.8, 30.8), "RU": (60.0, 90.0)}
# salary scale per currency relative to INR-ish log-mean (annual)
CURRENCY_SALARY_MU = {"INR": 13.3, "NGN": 14.5, "AED": 10.8, "SAR": 10.9, "PHP": 12.9, "USD": 11.0, "GBP": 10.7, "EUR": 10.7,
                      "BRL": 11.2, "MXN": 12.2, "CNY": 11.6, "JPY": 15.0, "BDT": 13.0, "PKR": 13.8, "KES": 13.5, "EGP": 12.9, "RUB": 13.2}


def fold_ascii(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower()) or "user"


def crc(s: str) -> int:
    return zlib.crc32(s.encode())


def rng_for(scn_seed: int, table: str, batch: int, stream: int, col: str = "") -> np.random.Generator:
    return np.random.default_rng([scn_seed & 0xFFFFFFFF, crc(table), batch, stream, crc(col)])


# ---------------------------------------------------------------------------------------------------------------
# id generation
# ---------------------------------------------------------------------------------------------------------------
def gen_ids(kind: str, scn_seed: int, table: str, batch: int, n: int) -> np.ndarray:
    rng = rng_for(scn_seed, table, batch, STREAM_ID, "__id")
    if kind == "uuid":
        h = rng.bytes(16 * n).hex()
        out = [f"{h[a:a+8]}-{h[a+8:a+12]}-4{h[a+13:a+16]}-8{h[a+17:a+20]}-{h[a+20:a+32]}" for a in range(0, 32 * n, 32)]
        return np.array(out, dtype=object)
    if kind == "int":
        v = rng.integers(1 << 40, (1 << 62), n)
        return np.array([str(int(x)) for x in v], dtype=object)
    h = rng.bytes(8 * n).hex()
    return np.array([h[a:a + 16] for a in range(0, 16 * n, 16)], dtype=object)


# ---------------------------------------------------------------------------------------------------------------
# context & pools
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class RunContext:
    schema: Schema
    run_id: str
    pool_dir: Path
    batch_size: int
    # table -> ids (object array). populated lazily
    _any_pools: dict[str, np.ndarray] = field(default_factory=dict)
    _scn_pools: dict[tuple[str, str], np.ndarray] = field(default_factory=dict)
    _refs: dict[str, Any] = field(default_factory=dict)
    conn_factory: Callable[[], Any] | None = None
    dry: bool = False

    # ----- 'any' scope: snapshot of existing parent ids, stored per run for determinism
    def snapshot_path(self, table: str) -> Path:
        return self.pool_dir / f"{self.run_id}__{table}.npy"

    def any_pool(self, table: str, size: int | None = None) -> np.ndarray:
        if table in self._any_pools:
            return self._any_pools[table]
        p = self.snapshot_path(table)
        if p.exists():
            arr = np.load(p, allow_pickle=True)
        else:
            arr = self.build_snapshot(table, size or 500_000)
        self._any_pools[table] = arr
        return arr

    def build_snapshot(self, table: str, limit: int) -> np.ndarray:
        t = self.schema.tables[table]
        if len(t.pk) != 1:
            raise ValueError(f"cannot sample FK parents from '{table}': needs single-column PK")
        assert self.conn_factory is not None
        with self.conn_factory() as c:
            rows = c.execute(f'SELECT "{t.pk[0]}"::text FROM "{table}" LIMIT %s', (limit,)).fetchall()
        arr = np.array([r[0] for r in rows], dtype=object)
        if len(arr) == 0 and self.dry:
            return gen_ids("uuid", 1, table, 0, 1000)
        if len(arr) == 0:
            raise ValueError(f"parent table '{table}' is empty: load it first or include it in the scenario")
        np.random.default_rng(crc(self.run_id + table)).shuffle(arr)
        tmp = self.snapshot_path(table).with_suffix(".tmp.npy")
        np.save(tmp, arr, allow_pickle=True)
        tmp.replace(self.snapshot_path(table))
        return arr

    def scenario_pool(self, scn: Scenario, table: str, cap: int) -> np.ndarray:
        key = (scn.id, table)
        if key in self._scn_pools and len(self._scn_pools[key]) >= min(cap, self._count(scn, table)):
            return self._scn_pools[key]
        count = min(cap, self._count(scn, table))
        arr = self.scenario_ids(scn, table, np.arange(count))
        self._scn_pools[key] = arr
        return arr

    def _count(self, scn: Scenario, table: str) -> int:
        return next(t.record_count for t in scn.tables if t.table == table)

    def scenario_ids(self, scn: Scenario, table: str, ordinals: np.ndarray) -> np.ndarray:
        """Primary-key values of this scenario's rows at the given ordinals (regenerated, never stored)."""
        pk_kind = self.schema.tables[table].columns[self.schema.tables[table].pk[0]].kind
        bs = self.batch_size
        out = np.empty(len(ordinals), dtype=object)
        batches = ordinals // bs
        for b in np.unique(batches):
            m = batches == b
            n_in_batch = min(bs, self._count(scn, table) - int(b) * bs)
            ids = gen_ids(pk_kind, scn.seed, table, int(b), n_in_batch)
            out[m] = ids[ordinals[m] - int(b) * bs]
        return out

    # ----- reference lookups (small master tables)
    def ref(self, name: str) -> Any:
        if name in self._refs:
            return self._refs[name]
        assert self.conn_factory is not None
        val: Any
        with self.conn_factory() as c:
            if name == "country_by_code":
                tn = self._find_table("country")
                val = {r[0]: r[1] for r in c.execute(f'SELECT code, id::text FROM "{tn}"').fetchall()} if tn else {}
            elif name == "language_by_code":
                tn = self._find_table("language")
                val = {r[0]: r[1] for r in c.execute(f'SELECT code, id::text FROM "{tn}"').fetchall()} if tn else {}
            else:
                val = None
        self._refs[name] = val
        return val

    def _find_table(self, kind: str) -> str | None:
        for n, t in self.schema.tables.items():
            if n == kind and "code" in t.columns and len(t.pk) == 1:
                return n
        return None

    def grouped_by_country(self, table: str, fk_col: str) -> dict[str, np.ndarray]:
        key = f"grouped:{table}:{fk_col}"
        if key in self._refs:
            return self._refs[key]
        t = self.schema.tables[table]
        assert self.conn_factory is not None
        with self.conn_factory() as c:
            rows = c.execute(f'SELECT "{fk_col}"::text, "{t.pk[0]}"::text FROM "{table}" LIMIT 500000').fetchall()
        g: dict[str, list[str]] = {}
        for cid, pid in rows:
            g.setdefault(cid, []).append(pid)
        out = {k: np.array(v, dtype=object) for k, v in g.items()}
        self._refs[key] = out
        return out


# ---------------------------------------------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------------------------------------------
_LOC_W: dict[str, tuple[np.ndarray, np.ndarray]] = {}


def _name_w(n: int) -> np.ndarray:
    w = 1.0 / np.arange(1, n + 1) ** 0.6
    return w / w.sum()


def _locale_arrays(code: str) -> dict[str, Any]:
    loc = L.LOCALES[code]
    if code not in _LOC_W:
        _LOC_W[code] = (_name_w(len(loc.first)), _name_w(len(loc.last)))
    return {"loc": loc, "wf": _LOC_W[code][0], "wl": _LOC_W[code][1]}


def locale_rows(scn_locales: dict[str, float], rng: np.random.Generator, n: int) -> tuple[list[str], np.ndarray]:
    codes = list(scn_locales)
    w = np.array([scn_locales[c] for c in codes], dtype=np.float64)
    return codes, rng.choice(len(codes), size=n, p=w / w.sum())


def person_parts(codes: list[str], idx: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(idx)
    first, last, first_l, last_l = (np.empty(n, dtype=object) for _ in range(4))
    for k, code in enumerate(codes):
        m = np.where(idx == k)[0]
        if len(m) == 0:
            continue
        a = _locale_arrays(code)
        loc: L.Locale = a["loc"]
        fi = rng.choice(len(loc.first), size=len(m), p=a["wf"])
        li = rng.choice(len(loc.last), size=len(m), p=a["wl"])
        first[m] = np.array(loc.first, dtype=object)[fi]
        last[m] = np.array(loc.last, dtype=object)[li]
        first_l[m] = np.array([fold_ascii(x) for x in loc.first_latin], dtype=object)[fi]
        last_l[m] = np.array([fold_ascii(x) for x in loc.last_latin], dtype=object)[li]
    return first, last, first_l, last_l


def full_names(codes: list[str], idx: np.ndarray, first: np.ndarray, last: np.ndarray) -> np.ndarray:
    fam = np.array([L.LOCALES[c].family_first for c in codes])[idx]
    return np.array([(l + f) if fm else (f + " " + l) for f, l, fm in zip(first, last, fam)], dtype=object)


def apply_typos(arr: np.ndarray, rng: np.random.Generator, mask: np.ndarray) -> np.ndarray:
    out = arr.copy()
    for i in np.where(mask)[0]:
        s = out[i]
        if s is None or len(s) < 3:
            continue
        op = rng.integers(0, 5)
        j = int(rng.integers(1, len(s) - 1))
        if op == 0:
            s = s[:j] + s[j + 1] + s[j] + s[j + 2:]
        elif op == 1:
            s = s[:j] + s[j + 1:]
        elif op == 2:
            s = s[:j] + s[j] + s[j:]
        elif op == 3:
            s = s.upper() if rng.random() < 0.5 else s.lower()
        else:
            s = " " + s if rng.random() < 0.5 else s + " "
        out[i] = s
    return out


# ---------------------------------------------------------------------------------------------------------------
# numeric bounds from CHECK constraints
# ---------------------------------------------------------------------------------------------------------------
def check_bounds(t: Table, col: str) -> tuple[float | None, float | None]:
    lo = hi = None
    c = re.escape(col)
    for chk in t.checks:
        for m in re.finditer(rf'\(?"?{c}"?\)?\s*(>=|>|<=|<)\s*\(?(-?\d+(?:\.\d+)?)\)?(?![\w.])', chk):
            op, v = m.group(1), float(m.group(2))
            if op in (">=", ">"):
                lo = v + (1 if op == ">" and float(v).is_integer() else 0) if lo is None else max(lo, v)
            else:
                hi = v if hi is None else min(hi, v)
        m = re.search(rf'"?{c}"?\s+BETWEEN\s+(-?\d+(?:\.\d+)?)\s+AND\s+(-?\d+(?:\.\d+)?)', chk, re.I)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
    return lo, hi


# ---------------------------------------------------------------------------------------------------------------
# the batch generator
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class BatchResult:
    columns: list[str]
    text: bytes
    rows: int
    staged: bool
    dups: int = 0
    nulls: int = 0


def unique_cols(t: Table) -> set[str]:
    return {c for u in t.uniques if len(u) == 1 for c in u}


def needs_staging(t: Table) -> bool:
    comp = [u for u in t.uniques if len(u) > 1] + ([t.pk] if len(t.pk) > 1 else [])
    return any(any(t.fk_for(c) for c in u) for u in comp)


def find_companions(t: Table) -> dict[str, list[str]]:
    """companion status col (enum with unknown+not_applicable) -> nullable columns it describes."""
    out: dict[str, list[str]] = {}
    for c in t.columns.values():
        ev = c.enum_values or []
        if "unknown" in ev and any("applicable" in e for e in ev):
            stem = re.sub(r"_(disclosure|status|availability|state|flag)$", "", c.name)
            toks = [x for x in stem.split("_") if x]
            members = [m.name for m in t.columns.values() if m.nullable and m.name != c.name
                       and any(tok in m.name.split("_") for tok in toks)]
            if members:
                out[c.name] = members
    return out


def generate_batch(ctx: RunContext, scn: Scenario, ts: TableSpec, batch: int, n: int, token_base: int,
                   only_cols: list[str] | None = None) -> tuple[dict[str, np.ndarray], list[str], dict[str, int]]:
    """Return (stringified column arrays, column order, stats)."""
    t = ctx.schema.tables[ts.table]
    stats = {"dups": 0, "nulls": 0}
    ordinals = np.arange(batch * ctx.batch_size, batch * ctx.batch_size + n, dtype=np.int64)
    tokens = token_base + ordinals
    uniq = unique_cols(t)
    pairs = ordered_pairs(t)
    companions = find_companions(t)
    comp_members = {m for ms in companions.values() for m in ms}
    r_meta = rng_for(scn.seed, ts.table, batch, STREAM_DATA, "__meta")
    dr0 = np.datetime64(scn.date_range.start, "s").astype(np.int64)
    dr1 = (np.datetime64(scn.date_range.end, "s") + np.timedelta64(86399, "s")).astype(np.int64)
    codes, lidx = locale_rows(scn.locale_mix, rng_for(scn.seed, ts.table, batch, STREAM_DATA, "__locale"), n)
    row_locs = [L.LOCALES[c] for c in codes]
    row_country = np.array([l.country for l in row_locs], dtype=object)[lidx]
    row_lang = np.array([l.language for l in row_locs], dtype=object)[lidx]

    vals: dict[str, np.ndarray] = {}          # raw values (float / int64 / object str)
    nullmask: dict[str, np.ndarray] = {}
    skip: set[str] = set()                     # columns omitted from COPY (DB default applies)
    country_of_row: np.ndarray | None = None  # country ids (str) per row, once known
    names_cache: dict[str, Any] = {}

    def person() -> tuple:
        if "p" not in names_cache:
            names_cache["p"] = person_parts(codes, lidx, rng_for(scn.seed, ts.table, batch, STREAM_DATA, "__person"))
        return names_cache["p"]

    pk_cols = t.pk
    single_pk = pk_cols[0] if len(pk_cols) == 1 else None
    fk_cols = [c for c in t.columns if t.fk_for(c) and len(t.fk_for(c).columns) == 1]  # type: ignore[union-attr]
    country_fk_cols = [c for c in fk_cols if re.search(r"country", t.fk_for(c).ref_table)]  # type: ignore[union-attr]
    order = ([c for c in t.columns if c in country_fk_cols] + [c for c in t.columns if c not in country_fk_cols])
    wanted = set(only_cols) if only_cols else None

    for cname in order:
        if wanted is not None and cname not in wanted:
            continue
        col = t.columns[cname]
        spec = ts.fields.get(cname)
        rng = rng_for(scn.seed, ts.table, batch, STREAM_DATA, cname)
        # ---- constants
        if cname in ts.constants:
            vals[cname] = np.full(n, str(ts.constants[cname]), dtype=object)
            continue
        # ---- primary key
        if cname == single_pk and cname not in ts.fields and not t.fk_for(cname):
            if col.kind in ("uuid", "int", "text"):
                vals[cname] = gen_ids(col.kind, scn.seed, ts.table, batch, n)
                continue
        # ---- foreign keys
        fk = t.fk_for(cname)
        if fk is not None and len(fk.columns) == 1 and fk.ref_table in ctx.schema.tables:
            vals[cname] = _gen_fk(ctx, scn, ts, t, cname, fk.ref_table, ts.fk.get(cname), batch, n, ordinals, rng,
                                  row_country, vals, country_fk_cols, uniq, row_lang)
            if re.search(r"country", fk.ref_table) and country_of_row is None:
                country_of_row = vals[cname]
            continue
        # ---- audit columns with defaults => let DB default apply
        if cname in AUDIT_DEFAULT_COLS and col.has_default and spec is None:
            skip.add(cname)
            continue
        # ---- explicit distribution
        if spec is not None and not (wanted is not None):
            vals[cname] = _gen_from_spec(col, spec, rng, n, dr0, dr1, t)
            continue
        vals[cname] = _gen_heuristic(col, t, ts, scn, rng, n, tokens, codes, lidx, row_locs, row_country, row_lang,
                                     person, dr0, dr1, uniq, vals, ctx)

    # ---- composite UNIQUE/PK over FKs: resolve residual within-batch collisions by resampling the non-partner columns
    for u in ([t.pk] if len(t.pk) > 1 else []) + [x for x in t.uniques if len(x) > 1]:
        if not all(c in vals and t.fk_for(c) is not None for c in u):
            continue
        cols_u = [c for c in t.columns if c in u]
        for it in range(30):
            seen: set = set()
            dup = []
            for i, key in enumerate(zip(*[vals[c] for c in cols_u])):
                if key in seen:
                    dup.append(i)
                else:
                    seen.add(key)
            if not dup:
                break
            di = np.array(dup)
            part_ = _composite_group(ctx, scn, ts, t, cols_u[0])[1]  # type: ignore[index]
            for c in [x for x in cols_u if x != part_]:
                fk_ = t.fk_for(c)
                new_ = _gen_fk(ctx, scn, ts, t, c, fk_.ref_table, ts.fk.get(c), batch, len(di), ordinals[di],  # type: ignore[union-attr]
                               rng_for(scn.seed, ts.table, batch, STREAM_DATA, f"__resample_{c}_{it}"), row_country[di], {}, [], set(),
                               row_lang[di])
                vals[c][di] = new_

    if wanted is not None:
        cols = [c for c in order if c in wanted]
        return _stringify(t, vals, {}, cols, n), cols, stats

    # ---- check-constraint pair repair (lo <= hi)
    for lo, hi in pairs:
        if lo in vals and hi in vals and vals[lo].dtype != object and vals[hi].dtype != object:
            a, b = vals[lo], vals[hi]
            if t.columns[hi].kind in ("timestamp", "date"):
                vals[hi] = np.maximum(a, b)
                if t.columns[lo].kind == "timestamp":  # make a sensible spread
                    spread = rng_for(scn.seed, ts.table, batch, STREAM_DATA, "__spread").integers(86400 * 3, 86400 * 90, n)
                    vals[hi] = a + spread
            else:
                rr = rng_for(scn.seed, ts.table, batch, STREAM_DATA, f"__pair_{lo}_{hi}")
                f = rr.uniform(1.1, 1.7, n)
                if lo in ts.fields and hi not in ts.fields:      # honour the explicitly specified column; derive the other
                    vals[hi] = a * f if t.columns[hi].kind != "int" else np.ceil(a * f)
                elif hi in ts.fields and lo not in ts.fields:
                    vals[lo] = b / f if t.columns[lo].kind != "int" else np.floor(b / f)
                else:
                    vals[lo], vals[hi] = np.minimum(a, b), np.maximum(a, b)
                for c_ in (lo, hi):
                    if t.columns[c_].kind == "numeric":
                        vals[c_] = _fit_numeric(t.columns[c_], vals[c_])

    # ---- nulls (unknown vs not applicable) for nullable columns
    nrate = ts.null_rate if ts.null_rate is not None else scn.null_rate
    comp_modes: dict[str, np.ndarray] = {}
    for cname, col in t.columns.items():
        if cname not in vals or not col.nullable or cname in uniq or cname in pk_cols or t.fk_for(cname) and False:
            continue
        spec = ts.fields.get(cname)
        rate = spec.null_rate if spec and spec.null_rate is not None else (nrate if not re.fullmatch(r"deleted_at", cname) else 0.02)
        if cname.endswith("_at") and cname not in ("deleted_at",):
            rate = rate if spec and spec.null_rate is not None else min(rate, 0.1)
        if rate <= 0:
            continue
        rn = rng_for(scn.seed, ts.table, batch, STREAM_DATA, "__null_" + cname)
        if cname in comp_members:
            continue  # handled at group level
        nullmask[cname] = rn.random(n) < rate
    for comp, members in companions.items():
        rn = rng_for(scn.seed, ts.table, batch, STREAM_DATA, "__comp_" + comp)
        spec_rate = next((ts.fields[m].null_rate for m in members if m in ts.fields and ts.fields[m].null_rate is not None), None)
        rate = spec_rate if spec_rate is not None else nrate
        mask = rn.random(n) < rate
        na_share = 0.3
        forced = next((ts.fields[m].null_mode for m in members if m in ts.fields and ts.fields[m].null_mode), None)
        if forced:
            na_share = 1.0 if forced == "not_applicable" else 0.0
        is_na = rn.random(n) < na_share
        ev = t.columns[comp].enum_values or []
        unk = "unknown"
        nap = next(e for e in ev if "applicable" in e)
        known = next((e for e in ("disclosed", "known", "available", "provided") if e in ev),
                     next(e for e in ev if e not in (unk, nap)))
        comp_vals = np.where(mask, np.where(is_na, nap, unk), known).astype(object)
        vals[comp] = comp_vals
        for m in members:
            nullmask[m] = mask.copy()
    stats["nulls"] = int(sum(int(m.sum()) for m in nullmask.values()))

    # ---- duplicates & typos on text identity columns
    drate = ts.duplicate_rate if ts.duplicate_rate is not None else scn.duplicate_rate
    protected = set(pk_cols) | uniq | set(fk_cols) | set(companions) | {"created_at", "updated_at", "deleted_at"} | comp_members
    rd = rng_for(scn.seed, ts.table, batch, STREAM_DATA, "__dups")
    if drate > 0 and n > 2:
        dmask = rd.random(n) < drate
        dmask[0] = False
        pos = np.where(dmask)[0]
        src = (rd.random(len(pos)) * pos).astype(np.int64)  # earlier row
        for cname in vals:
            if cname in protected or cname in ts.constants:
                continue
            arr = vals[cname]
            arr[pos] = arr[src]
            if arr.dtype == object and t.columns[cname].kind == "text" and not t.columns[cname].enum_values \
                    and re.search(r"name|title|headline|description|resume", cname):
                tm = np.zeros(n, dtype=bool)
                tm[pos] = rd.random(len(pos)) < 0.5
                vals[cname] = apply_typos(arr, rd, tm)
        stats["dups"] = int(len(pos))
    if scn.typo_rate > 0:
        for cname, col in t.columns.items():
            if cname in vals and col.kind == "text" and not col.enum_values and cname not in protected \
                    and re.search(r"name|title|headline", cname):
                tm = rd.random(n) < scn.typo_rate
                vals[cname] = apply_typos(vals[cname], rd, tm)
    cols = [c for c in order if c in vals and c not in skip]
    return _stringify(t, vals, nullmask, cols, n), cols, stats


# ---------------------------------------------------------------------------------------------------------------
def _gen_fk(ctx: RunContext, scn: Scenario, ts: TableSpec, t: Table, cname: str, parent: str, fks: FkSpec | None,
            batch: int, n: int, ordinals: np.ndarray, rng: np.random.Generator, row_country: np.ndarray,
            vals: dict[str, np.ndarray], country_fk_cols: list[str], uniq: set[str],
            row_lang: np.ndarray) -> np.ndarray:
    in_scn = parent in scn.target_tables
    fks = fks or FkSpec(scope="scenario" if in_scn else "any")
    if fks.scope == "scenario" and not in_scn:
        fks = FkSpec(scope="any", skew=fks.skew, alpha=fks.alpha, pool_size=fks.pool_size)
    unique_fk = cname in uniq
    pt = ctx.schema.tables[parent]
    # --- country / language: align with the row's locale
    if parent in ("country", "language") and "code" in pt.columns:
        refname = "country_by_code" if parent == "country" else "language_by_code"
        m = ctx.ref(refname)
        keys = row_country if parent == "country" else row_lang
        ids = np.array([m.get(str(k).lower() if parent == "language" else k) for k in keys], dtype=object)
        miss = np.array([x is None for x in ids])
        if miss.any():
            pool = ctx.any_pool(parent)
            ids[miss] = pool[rng.integers(0, len(pool), int(miss.sum()))]
        return ids
    # --- child of country (city): condition on this row's country FK when present
    parent_country_fk = next((f for f in pt.fks if re.search(r"country", f.ref_table) and len(f.columns) == 1), None)
    if parent_country_fk and country_fk_cols and country_fk_cols[0] in vals and fks.scope == "any":
        grouped = ctx.grouped_by_country(parent, parent_country_fk.columns[0])
        cvals = vals[country_fk_cols[0]]
        out = np.empty(n, dtype=object)
        fallback = ctx.any_pool(parent)
        for cid in np.unique(cvals):
            m = np.where(cvals == cid)[0]
            pool = grouped.get(cid)
            if pool is None or len(pool) == 0:
                out[m] = fallback[rng.integers(0, len(fallback), len(m))]
            else:
                out[m] = pool[skew_indices(fks, rng, len(m), len(pool))]
        return out
    # --- bijective / unique FK: ordinal -> parent ordinal
    if unique_fk and fks.scope == "scenario":
        return ctx.scenario_ids(scn, parent, ordinals % max(1, ctx._count(scn, parent)))
    if unique_fk:
        pool = ctx.any_pool(parent, fks.pool_size)
        return pool[ordinals % len(pool)]
    if fks.scope == "scenario":
        cap = fks.pool_size or 300_000
        pool = ctx.scenario_pool(scn, parent, cap)
    else:
        pool = ctx.any_pool(parent, fks.pool_size)
    g = _composite_group(ctx, scn, ts, t, cname)
    if g and g[1] == cname:
        # composite UNIQUE/PK over several FKs: this column (largest pool) is split into one residue class per batch, so
        # rows of different batches can never collide; within-batch collisions are repaired in generate_batch.
        nb = max(1, math.ceil(ctx._count(scn, ts.table) / ctx.batch_size))
        pe = len(pool) // nb
        if pe >= 1:
            return pool[skew_indices(fks, rng, n, pe) * nb + batch % nb]
    return pool[skew_indices(fks, rng, n, len(pool))]


def _pool_len(ctx: RunContext, scn: Scenario, ts: TableSpec, t: Table, c: str) -> int:
    fk = t.fk_for(c)
    spec = ts.fk.get(c)
    in_scn = fk.ref_table in scn.target_tables  # type: ignore[union-attr]
    scope = spec.scope if spec else ("scenario" if in_scn else "any")
    if scope == "scenario" and in_scn:
        return min(spec.pool_size if spec and spec.pool_size else 300_000, ctx._count(scn, fk.ref_table))  # type: ignore[union-attr]
    return len(ctx.any_pool(fk.ref_table, spec.pool_size if spec else None))  # type: ignore[union-attr]


def _composite_group(ctx: RunContext, scn: Scenario, ts: TableSpec, t: Table, col: str) -> tuple[list[str], str] | None:
    """If `col` belongs to a composite unique/PK made only of single-column FKs: (all group cols, column to partition per batch)."""
    for u in ([t.pk] if len(t.pk) > 1 else []) + [x for x in t.uniques if len(x) > 1]:
        if col in u and all(t.fk_for(c) is not None and len(t.fk_for(c).columns) == 1 for c in u):  # type: ignore[union-attr]
            cols = [c for c in t.columns if c in u]
            part = max(cols, key=lambda c: _pool_len(ctx, scn, ts, t, c))
            return cols, part
    return None


def _gen_from_spec(col: Column, spec: FieldSpec, rng: np.random.Generator, n: int, dr0: int, dr1: int, t: Table) -> np.ndarray:
    k = col.kind
    if k in ("int", "numeric", "float"):
        lo, hi = check_bounds(t, col.name)
        dflo, dfhi = (lo if lo is not None else 0.0), (hi if hi is not None else 1000.0)
        x = sample_numeric(spec, rng, n, dflo, dfhi)
        if lo is not None:
            x = np.maximum(x, lo)
        if hi is not None:
            x = np.minimum(x, hi)
        if k == "int":
            return np.round(x).astype(np.int64)
        return _fit_numeric(col, x)
    if k in ("timestamp", "date"):
        lo = _to_epoch(spec.min, dr0)
        hi = _to_epoch(spec.max, dr1)
        u = sample_unit(spec, rng, n)
        secs = (lo + u * (hi - lo)).astype(np.int64)
        return secs if k == "timestamp" else secs // 86400
    if k == "bool":
        s = spec if spec.dist != "uniform" else FieldSpec(dist="categorical", weights={"t": 0.5, "f": 0.5})
        if s.dist == "categorical":
            s = FieldSpec(dist="categorical", weights={("t" if str(a).lower() in ("true", "t", "1") else "f"): w
                                                       for a, w in (s.weights or {}).items()})  # type: ignore[union-attr]
        return sample_categorical(s, rng, n)
    if spec.dist in ("categorical", "weights", "constant"):
        return sample_categorical(spec, rng, n)
    # numeric dist on text column -> stringified number
    return np.array([str(int(v)) for v in sample_numeric(spec, rng, n)], dtype=object)


def _to_epoch(v: Any, default: int) -> int:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return int(v)
    return int(np.datetime64(str(v), "s").astype(np.int64))


def _fit_numeric(col: Column, x: np.ndarray) -> np.ndarray:
    if col.precision:
        lim = 10 ** (col.precision - (col.scale or 0)) - 1
        x = np.clip(x, -lim, lim)
    if col.kind == "numeric":
        x = np.round(x, col.scale if col.scale is not None else 2)
    return x


def _bool_arr(rng: np.random.Generator, n: int, p_true: float) -> np.ndarray:
    return np.where(rng.random(n) < p_true, "t", "f").astype(object)


def _gen_heuristic(col: Column, t: Table, ts: TableSpec, scn: Scenario, rng: np.random.Generator, n: int,
                   tokens: np.ndarray, codes: list[str], lidx: np.ndarray, row_locs: list[L.Locale],
                   row_country: np.ndarray, row_lang: np.ndarray, person: Callable, dr0: int, dr1: int, uniq: set[str],
                   vals: dict[str, np.ndarray], ctx: RunContext) -> np.ndarray:
    name = col.name.lower()
    k = col.kind
    # ----- enums from CHECK constraints
    if col.enum_values:
        ev = col.enum_values
        w = 1.0 / np.arange(1, len(ev) + 1) ** 0.8
        if name in ("role",):
            w = np.array([0.8, 0.1, 0.08, 0.02][:len(ev)] + [0.01] * max(0, len(ev) - 4))
        if name == "gender" and "undisclosed" in ev:
            w = np.array([0.45, 0.5, 0.01, 0.04][:len(ev)])
        return choose_weighted(list(ev), list(w[:len(ev)]), rng, n)
    if k == "text":
        if re.search(r"e?mail", name):
            first, last, fl, ll = person()
            doms = np.array([L.LOCALES[c].email_domains for c in codes], dtype=object)
            dom = np.array([doms[i][int(j)] for i, j in zip(lidx, rng.integers(0, 4, n))], dtype=object)
            if name.startswith("contact") or "company" in name or t.name == "employer":
                return np.array([f"hr{tk}@{fold_ascii(f)}{ll_}.com".lower() for tk, f, ll_ in zip(tokens, fl, ll)], dtype=object)
            return np.array([f"{a}.{b}{tk}@{d}" for a, b, tk, d in zip(fl, ll, tokens, dom)], dtype=object)
        if re.search(r"phone|mobile|msisdn|e164", name) and "country" not in name and "code" not in name:
            return _phones(codes, lidx, tokens, rng, n, name in uniq or col.name in uniq)
        if re.search(r"(^|_)(full|display|person|contact)_?name$|^name$", name) and not _is_org(t):
            first, last, _, _ = person()
            return full_names(codes, lidx, first, last)
        if name in ("first_name", "given_name"):
            return person()[0]
        if name in ("last_name", "surname", "family_name"):
            return person()[1]
        if _is_org(t) and name in ("name", "legal_name", "company_name", "employer_name"):
            stem = rng.integers(0, len(L.COMPANY_STEM), n)
            suf = rng.integers(0, len(L.COMPANY_SUFFIX), n)
            first, last, fl, ll = person()
            return np.array([f"{L.COMPANY_STEM[a]} {L.COMPANY_SUFFIX[b]} {tk % 997}" for a, b, tk in zip(stem, suf, tokens)], dtype=object)
        if name in ("title", "job_title", "position", "role_title"):
            tt = rng.integers(0, len(L.JOB_TITLES), n)
            sn = rng.integers(0, len(L.SENIORITY), n)
            return np.array([f"{L.SENIORITY[b]} {L.JOB_TITLES[a]}".strip() for a, b in zip(tt, sn)], dtype=object)
        if name in ("headline", "tagline"):
            tt = rng.integers(0, len(L.JOB_TITLES), n)
            ph = [row_locs[i].phrases for i in lidx]
            return np.array([f"{L.JOB_TITLES[a]} | {p[int(j) % len(p)]}"[:190] for a, p, j in zip(tt, ph, rng.integers(0, 99, n))],
                            dtype=object)
        if re.search(r"description|resume|bio|summary|notes|about|body", name):
            return _long_text(row_locs, lidx, rng, n)
        if re.search(r"website|url|domain", name):
            return np.array([f"https://www.{fold_ascii(L.COMPANY_STEM[tk % len(L.COMPANY_STEM)])}{tk}.com" for tk in tokens], dtype=object)
        if re.search(r"registration|tax|gst|pan|vat|license|licence|reg_no|national_id", name):
            return np.array([f"{row_country[i]}-{tk:010d}" for i, tk in enumerate(tokens)], dtype=object)
        if re.search(r"currency", name):
            return np.array([COUNTRY_CURRENCY.get(c, "USD") for c in row_country], dtype=object)
        if re.search(r"language_code|lang_code|locale", name) or name in ("language", "lang"):
            return row_lang.astype(object)
        if re.search(r"country_code|iso2", name):
            return row_country.astype(object)
        if re.search(r"timezone|time_zone", name):
            return np.full(n, "UTC", dtype=object)
        if col.max_len and col.max_len <= 6:
            return np.array([f"{tk % (10 ** col.max_len):0{col.max_len}d}" for tk in tokens], dtype=object)
        base = re.sub(r"[^a-z0-9]", "", name)[:12] or "v"
        return np.array([f"{base}_{tk}" for tk in tokens], dtype=object)
    if k == "uuid":
        return gen_ids("uuid", scn.seed, ts.table + "." + col.name, int(tokens[0] // 1) if len(tokens) else 0, n)
    if k == "bool":
        p = 0.95 if re.search(r"active|enabled|verified|open", name) else (0.03 if re.search(r"deleted|blocked|banned|spam", name) else 0.5)
        if "open_to_work" in name:
            p = 0.8
        return _bool_arr(rng, n, p)
    if k in ("timestamp", "date"):
        u = rng.random(n)
        if name in ("updated_at",) and "created_at" in vals:
            base = vals["created_at"]
            off = rng.exponential(86400 * 20, n).astype(np.int64)
            return np.minimum(base + off, dr1) if col.kind == "timestamp" else np.minimum(base + off // 86400, dr1 // 86400)
        if name in ("date_of_birth", "dob", "birth_date"):
            age = np.clip(rng.normal(31, 8, n), 18, 64)
            return (dr1 // 86400 - (age * 365.25).astype(np.int64))
        if name.endswith("_at") or k == "timestamp":
            # recent-heavy: beta skews towards later dates
            u = rng.beta(2.0, 1.2, n) if re.search(r"created|applied|posted", name) else u
        secs = (dr0 + u * (dr1 - dr0)).astype(np.int64)
        if name == "deleted_at":
            secs = np.minimum(dr1, secs)
        return secs if k == "timestamp" else secs // 86400
    if k in ("int", "numeric", "float"):
        lo, hi = check_bounds(t, col.name)
        cur_mu = None
        if re.search(r"salary|amount|price|cost|fee|budget|wage|pay|ctc", name):
            cur = np.array([COUNTRY_CURRENCY.get(c, "USD") for c in row_country], dtype=object)
            mu = np.array([CURRENCY_SALARY_MU.get(c, 11.0) for c in cur])
            x = np.exp(rng.normal(mu, 0.55, n))
            if re.search(r"max|upper|to$", name):
                x = x * rng.uniform(1.15, 1.7, n)
            return _fit_numeric(col, x)
        if re.search(r"year|experience|tenure", name):
            x = np.round(rng.lognormal(1.3, 0.75, n), 1)
            hi_ = hi if hi is not None else min(45.0, 10 ** (col.precision - (col.scale or 0)) - 1 if col.precision else 45.0)
            x = np.clip(x, lo if lo is not None else 0, hi_)
            if re.search(r"max", name):
                x = np.minimum(x + rng.uniform(1, 6, n), hi_)
            return x if k != "int" else x.astype(np.int64)
        if re.search(r"score|percent|ratio|pct|match", name):
            return _fit_numeric(col, np.clip(rng.normal(68, 17, n), lo if lo is not None else 0, hi if hi is not None else 100))
        if re.search(r"latitude|^lat$", name):
            c = np.array([COUNTRY_CENTROID.get(c, (0.0, 0.0)) for c in row_country])
            return _fit_numeric(col, np.clip(c[:, 0] + rng.normal(0, 1.8, n), -89.9, 89.9))
        if re.search(r"longitude|^lon|^lng", name):
            c = np.array([COUNTRY_CENTROID.get(c, (0.0, 0.0)) for c in row_country])
            return _fit_numeric(col, np.clip(c[:, 1] + rng.normal(0, 2.2, n), -179.9, 179.9))
        if re.search(r"notice|days", name):
            return rng.choice([0, 15, 30, 45, 60, 90], n, p=[.1, .15, .4, .1, .2, .05]).astype(np.int64)
        if re.search(r"opening|count|quantity|qty|number", name):
            return np.maximum(1, rng.poisson(3, n)).astype(np.int64) if k == "int" else rng.poisson(3, n).astype(np.float64)
        lo_ = lo if lo is not None else 0.0
        hi_ = hi if hi is not None else 1000.0
        if k == "int":
            return rng.integers(int(lo_), int(hi_) + 1, n).astype(np.int64)
        return _fit_numeric(col, rng.uniform(lo_, hi_, n))
    if k == "json":
        return np.full(n, "{}", dtype=object)
    return np.full(n, "", dtype=object)


def _is_org(t: Table) -> bool:
    return bool(re.search(r"employer|company|organi[sz]ation|agency|firm|client", t.name))


def _long_text(row_locs: list[L.Locale], lidx: np.ndarray, rng: np.random.Generator, n: int) -> np.ndarray:
    k = rng.integers(1, 5, n)
    sel = rng.integers(0, 1000, (n, 4))
    out = []
    for i in range(n):
        ph = row_locs[lidx[i]].phrases
        out.append(" ".join(ph[int(sel[i, j]) % len(ph)] for j in range(int(k[i]))))
    return np.array(out, dtype=object)


def _phones(codes: list[str], lidx: np.ndarray, tokens: np.ndarray, rng: np.random.Generator, n: int, unique: bool) -> np.ndarray:
    out = np.empty(n, dtype=object)
    for ci, code in enumerate(codes):
        loc = L.LOCALES[code]
        m = np.where(lidx == ci)[0]
        if len(m) == 0:
            continue
        firsts = loc.phone_first
        k = len(firsts)
        rest = loc.phone_len - 1
        if unique:
            tk = tokens[m]
            body = ((tk // k) * 2654435761) % (10 ** rest)
            fd = np.array([firsts[int(x % k)] for x in tk])
            out[m] = [f"+{loc.phone_cc}{f}{int(b):0{rest}d}" for f, b in zip(fd, body)]
        else:
            fd = rng.choice(list(firsts), len(m))
            body = rng.integers(0, 10 ** rest, len(m))
            out[m] = [f"+{loc.phone_cc}{f}{int(b):0{rest}d}" for f, b in zip(fd, body)]
    return out


# ---------------------------------------------------------------------------------------------------------------
def _stringify(t: Table, vals: dict[str, np.ndarray], nullmask: dict[str, np.ndarray], cols: list[str], n: int) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for c in cols:
        col = t.columns[c]
        v = vals[c]
        k = col.kind
        if v.dtype == object:
            s = v
            if k == "text" or k == "other":
                # COPY text escaping for free text only
                if any(("\\" in x or "\t" in x or "\n" in x or "\r" in x) for x in s[: min(n, 64)]):
                    s = np.array([x.replace("\\", "\\\\").replace("\t", " ").replace("\n", "\\n").replace("\r", "") for x in s], dtype=object)
        elif k == "timestamp":
            s = (np.datetime64(0, "s") + v.astype(np.int64).astype("timedelta64[s]")).astype("datetime64[s]").astype(str)
            s = np.char.add(np.char.replace(s, "T", " "), "+00").astype(object)
        elif k == "date":
            s = (np.datetime64(0, "D") + v.astype(np.int64).astype("timedelta64[D]")).astype(str).astype(object)
        elif k == "int":
            s = v.astype(np.int64).astype(str).astype(object)
        elif k in ("numeric", "float"):
            sc = col.scale if col.scale is not None else 2
            fmt = f"%.{sc}f" if k == "numeric" else "%.6g"
            s = np.array([fmt % x for x in v.tolist()], dtype=object)
        else:
            s = v.astype(str).astype(object)
        m = nullmask.get(c)
        if m is not None and m.any():
            s = s.copy()
            s[m] = NULL
        out[c] = s
    return out


def to_copy_bytes(arrs: dict[str, np.ndarray], cols: list[str]) -> bytes:
    lists = [arrs[c].tolist() for c in cols]
    return ("\n".join(["\t".join(r) for r in zip(*lists)]) + "\n").encode("utf-8")

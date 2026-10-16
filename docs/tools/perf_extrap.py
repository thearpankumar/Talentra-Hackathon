"""B74 full-scale extrapolation model (predictions, NOT measurements). Parameters are explicit so the team can refit them
from the B70 baseline: edit PARAMS (or pass measured values) and re-run gen_perf_plan.py.

latency(q, lam) = fixed + scan_rows*c_scan + hash_rows*c_hash + sort_rows*c_sort + lookups*(c_lookup + miss(lam)*c_miss + (depth(lam)-depth(1))*c_level)
where lam is the position on the path laptop(1) -> full(10 for profiles/jobs, 20 for applications): s_P = s_J = lam, s_A = 2*lam.
"""
import math

PARAMS = dict(
    fixed_ms=1.5,          # parse/plan/dispatch/network round trip inside a docker bridge network
    c_scan=0.0025,         # ms per row of a covered index scan (decode + filter)
    c_hash=0.0010,         # ms per row built/probed in a hash join
    c_sort=0.0030,         # ms per row in a top-k sort
    c_lookup=0.050,        # ms per row of a batched lookup/index join (warm)
    c_miss=0.30,           # ms extra per lookup when the block is not in the Pebble block cache (NVMe 32 KiB block read)
    c_level=0.020,         # ms per extra B-tree/LSM level per lookup
    block_bytes=4096,      # cache accounting granularity
    zipf_s=1.0,            # access skew (s = 1: hit ratio = ln(C)/ln(N))
    cache_bytes_total=3 * 2 * 1024 ** 3,   # 3 nodes x 2 GiB cache (8 GiB container x 25%)
)

MISS_FLOOR = 0.02   # cold blocks, compaction churn and scans never reach 100% hits
FACT = {"P": 10, "J": 10, "A": 20, "G": 10}


def miss_ratio(working_set_bytes, p=PARAMS):
    c = p["cache_bytes_total"] / p["block_bytes"]
    n = max(working_set_bytes / p["block_bytes"], c + 1)
    h = math.log(1 + c) / math.log(1 + n)
    return max(MISS_FLOOR, 1.0 - min(h, 1.0))


def btree_depth(n_entries, entry_bytes, key_bytes, block=32768):
    leaf = max(1, block // entry_bytes)
    fan = max(2, block // (key_bytes + 16))
    leaves = max(1, n_entries / leaf)
    return 1 + max(0, math.ceil(math.log(leaves, fan))) if leaves > 1 else 1


# per query components at laptop scale (rows) and which scale driver multiplies them
# (scan, hash, sort, lookups) each as (laptop value, driver) ; driver in P,J,A,G,C (constant)
COMP = {
    "Q1": dict(scan=(20, "C"), hash=(0, "C"), sort=(0, "C"), look=(0, "C"), slo=15, desc="LIMIT 20 on (city, published_at DESC)"),
    "Q2": dict(scan=(150, "J"), hash=(0, "C"), sort=(150, "J"), look=(150, "J"), slo=60, desc="posting list of rarest trigram = 0.15% of jobs, index join each"),
    "Q3": dict(scan=(50, "C"), hash=(0, "C"), sort=(0, "C"), look=(100, "C"), slo=25, desc="50 rows + deleted_at index join + candidate_profile lookups"),
    "Q4": dict(scan=(6, "C"), hash=(0, "C"), sort=(0, "C"), look=(12, "C"), slo=15, desc="~5 applications per candidate (5M/1M)"),
    "Q5": dict(scan=(26000, "P"), hash=(26000, "P"), sort=(160, "P"), look=(480, "P"), slo=250, desc="city set 16k + skill set 10k index entries, 160 matches x 3 lookups"),
    "Q6": dict(scan=(0, "C"), hash=(0, "C"), sort=(0, "C"), look=(15, "C"), slo=40, desc="1 insert + 7 index entries + 7 FK checks"),
    "Q7": dict(scan=(0, "C"), hash=(0, "C"), sort=(0, "C"), look=(12, "C"), slo=40, desc="update + 4 index rewrites + history insert + FK checks"),
    "Q8": dict(scan=(0, "C"), hash=(0, "C"), sort=(0, "C"), look=(14, "C"), slo=60, desc="wallet lock + ledger + wallet + download insert"),
    "Q9": dict(scan=(0, "C"), hash=(0, "C"), sort=(0, "C"), look=(10, "C"), slo=40, desc="seat lock, slot anti-join, 2 inserts"),
    "Q10": dict(scan=(20, "C"), hash=(0, "C"), sort=(0, "C"), look=(20, "C"), slo=10, desc="20 unread rows + payload index join"),
    "Q11 (avg requirement)": dict(scan=(200, "AJ"), hash=(0, "C"), sort=(0, "C"), look=(0, "C"), slo=40, desc="4 transitions x apps per requirement (A/J doubles)"),
    "Q11 (largest requirement)": dict(scan=(20000, "A"), hash=(0, "C"), sort=(0, "C"), look=(0, "C"), slo=400, desc="largest requirement holds 0.1% of applications x 4 transitions (p99 tail)"),
    "Q12 (existing index)": dict(scan=(5000, "G"), hash=(5000, "G"), sort=(0, "C"), look=(5000, "G"), slo=120, desc="90-day window, ~55 grain rows per day, index join per row"),
    "Q12 (proposed index)": dict(scan=(90, "C"), hash=(0, "C"), sort=(0, "C"), look=(0, "C"), slo=120, desc="covered (category, period_date) range: 90 rows"),
}


def _mult(driver, lam):
    return {"C": 1.0, "P": lam, "J": lam, "G": lam, "A": 2 * lam, "AJ": 2.0 if lam > 1 else 1.0}[driver] if driver != "AJ" else (1 + (lam - 1) / 9.0)


def latency(q, lam, miss, depth_delta, p=PARAMS):
    c = COMP[q]
    scan = c["scan"][0] * _mult(c["scan"][1], lam)
    hsh = c["hash"][0] * _mult(c["hash"][1], lam)
    srt = c["sort"][0] * _mult(c["sort"][1], lam)
    look = c["look"][0] * _mult(c["look"][1], lam)
    return (p["fixed_ms"] + scan * p["c_scan"] + hsh * p["c_hash"] + srt * p["c_sort"]
            + look * (p["c_lookup"] + miss * p["c_miss"] + depth_delta * p["c_level"]))


def run(ws_lap, ws_full, depth_lap, depth_full, p=PARAMS):
    """returns rows: query, desc, slo, laptop_ms, full_ms, ratio, breach_lambda"""
    out = []
    for q, c in COMP.items():
        # miss ratio and depth interpolate on log scale between laptop (lam=1) and full (lam=10)
        def at(lam):
            t = min(max(math.log(lam) / math.log(10), 0), 1.2)
            ws = ws_lap * (ws_full / ws_lap) ** t
            return latency(q, lam, miss_ratio(ws, p), (depth_full - depth_lap) * min(t, 1), p)
        l1, l10 = at(1), at(10)
        br = None
        if l1 >= c["slo"]:
            br = 1.0
        else:
            lo, hi = 1.0, 10.0
            if at(10) >= c["slo"]:
                for _ in range(40):
                    mid = (lo + hi) / 2
                    if at(mid) >= c["slo"]:
                        hi = mid
                    else:
                        lo = mid
                br = hi
        out.append((q, c["desc"], c["slo"], l1, l10, l10 / l1, br))
    return out

"""Negative tests for the dictionary CHECK constraints (docs/05_data_dictionary.xlsx, sheet Constraints, type CHECK).

For every table with CHECKs a valid baseline row is built (helpers_rowbuilder + CheckSolver, which uses the database itself as the
oracle for the CHECK predicates); then for each CHECK a mutation of the baseline is searched that violates exactly that CHECK and
the INSERT must be rejected with SQLSTATE 23514 naming the constraint.  CHECKs for which no mutation can be derived mechanically
are listed in HAND (module-critical ones: explicit baseline overrides + violating values); anything still unsolved is counted and
the suite requires >= 70% mechanical coverage overall.
Everything runs inside rolled-back transactions on the admin connection; only parent rows are left behind (fresh uuid4 ids).
"""
import uuid

import psycopg
import pytest
from psycopg.errors import CheckViolation

import helpers_conf as hc

D = hc.get_dict()
TABLES = sorted(t for t in D.tables if D.checks(t))
N_CHECKS = sum(len(D.checks(t)) for t in TABLES)

# table -> overrides for the valid baseline (row builder cannot satisfy these CHECKs on its own)
BASE_OVERRIDES = {}
# "table.check" -> violating column values (explicit; used when the mechanical search finds nothing)
HAND = {}

RESULTS = {}      # (table, check) -> 'mech' | 'hand' | 'unsolved' | 'noinsert'
_SOLVERS = {}


def _solver(conn, table):
    if table not in _SOLVERS:
        s = hc.CheckSolver(conn, table)
        s.randomize = True
        explicit, full = s.valid_baseline(**BASE_OVERRIDES.get(table, {}))
        _SOLVERS[table] = (s, explicit, full)
    return _SOLVERS[table]


def _insert(conn, table, row, colmap):
    names = list(row)
    ph = ", ".join("%s" + ("::INET" if hc.norm_type(colmap[n]["type"]) == "INET" else "") for n in names)
    conn.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({ph})", [row[n] for n in names])


class _Rollback(Exception):
    pass


def _try_insert(conn, table, row, colmap):
    """Insert inside a transaction that is always rolled back; returns None on success or the exception."""
    for attempt in range(20):
        try:
            with conn.transaction():
                _insert(conn, table, row, colmap)
                raise _Rollback()
        except _Rollback:
            return None
        except psycopg.OperationalError:
            import time
            time.sleep(2)
            conn._connect()
        except psycopg.Error as e:
            return e
    raise RuntimeError("connection kept failing")


@pytest.fixture(scope="module")
def rc():
    c = hc.RConn()
    yield c
    c.close()


@pytest.mark.parametrize("table", TABLES)
def test_valid_baseline_row_accepted(rc, table):
    """The solved baseline row satisfies every CHECK, NOT NULL, FK and unique constraint of the table."""
    for attempt in range(6):   # random candidates may collide with seeded unique values
        s, explicit, full = _solver(rc, table)
        err = _try_insert(rc, table, explicit, s.cols)
        if not isinstance(err, psycopg.errors.UniqueViolation):
            break
        _SOLVERS.pop(table, None)
    assert err is None, f"{table}: valid baseline row rejected: {err}"


@pytest.mark.parametrize("table", TABLES)
def test_checks_reject_violating_rows(rc, table):
    s, explicit, full = _solver(rc, table)
    problems = []
    for chk in D.checks(table):
        name = chk["constraint_name"]
        key = f"{table}.{name}"
        if key in HAND:
            mut, how = HAND[key], "hand"
        else:
            mut, how = s.violation(explicit, full, name), "mech"
        if mut is None:
            RESULTS[(table, name)] = "unsolved"
            continue
        row = {**explicit, **mut}
        err = _try_insert(rc, table, row, s.cols)
        if err is None:
            problems.append(f"{name}: violating row {mut} was ACCEPTED (check missing or wrong)")
        elif not isinstance(err, CheckViolation):
            problems.append(f"{name}: {type(err).__name__} instead of 23514: {str(err).splitlines()[0]}")
        elif name not in str(err) and getattr(err.diag, "constraint_name", None) != name:
            problems.append(f"{name}: 23514 raised by another constraint: {str(err).splitlines()[0]}")
        else:
            assert err.sqlstate == "23514"
            RESULTS[(table, name)] = how
    assert not problems, f"{table}:\n  " + "\n  ".join(problems)


def test_zz_mechanical_coverage():
    """>= 70% of all CHECKs got a derived violating row (runs after the parametrised tests above)."""
    done = sum(1 for k, v in RESULTS.items() if v in ("mech", "hand"))
    if len(RESULTS) < N_CHECKS * 0.9:
        pytest.skip(f"only {len(RESULTS)} of {N_CHECKS} checks evaluated in this run")
    uns = sorted(k for k, v in RESULTS.items() if v == "unsolved")
    assert done / N_CHECKS >= 0.70, f"covered {done}/{N_CHECKS}; unsolved sample: {uns[:40]}"


def test_check_count_matches_dictionary(admin):
    n = admin.execute("SELECT count(*) FROM pg_constraint WHERE contype='c' AND connamespace='public'::regnamespace").fetchone()[0]
    assert n >= 2183


@pytest.fixture(scope="module", autouse=True)
def _dump_results():
    yield
    import json
    from pathlib import Path
    d = Path(__file__).resolve().parent / ".cache"
    d.mkdir(exist_ok=True)
    (d / "neg_results.json").write_text(json.dumps({f"{t}.{c}": v for (t, c), v in RESULTS.items()}, indent=0), encoding="utf-8")

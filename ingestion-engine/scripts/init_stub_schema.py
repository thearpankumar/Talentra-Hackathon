"""Create the `talentra` database in the local CockroachDB and apply the stub schema + master seed.

Usage: uv run python scripts/init_stub_schema.py [--reset] [--schema-dir DIR]
With --schema-dir the real DDL files (*.sql, sorted) are applied instead of the stub.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from ingestion_engine.settings import get_settings

HERE = Path(__file__).resolve().parent.parent / "tests"


def main() -> None:
    s = get_settings()
    base, _, rest = s.db_url.rpartition("/")
    dbname, _, query = rest.partition("?")
    admin_url = f"{base}/defaultdb" + (f"?{query}" if query else "")
    files: list[Path] = [HERE / "stub_schema.sql", HERE / "stub_seed.sql"]
    if "--schema-dir" in sys.argv:
        d = Path(sys.argv[sys.argv.index("--schema-dir") + 1])
        files = sorted(d.glob("*.sql"))
    with psycopg.connect(admin_url, autocommit=True) as c:
        if "--reset" in sys.argv:
            c.execute(f"DROP DATABASE IF EXISTS {dbname} CASCADE")
        c.execute(f"CREATE DATABASE IF NOT EXISTS {dbname}")
    with psycopg.connect(s.db_url, autocommit=True) as c:
        for f in files:
            c.execute(f.read_text(encoding="utf-8"))  # type: ignore[arg-type]
            print(f"applied {f.name}")
        n = c.execute("select count(*) from information_schema.tables where table_schema='public'").fetchone()[0]
        print("tables:", n)


if __name__ == "__main__":
    main()

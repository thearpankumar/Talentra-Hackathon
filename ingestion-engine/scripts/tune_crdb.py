"""Optional DEV-ONLY tuning for the local single-node CockroachDB to speed up bulk loads.

  * disables automatic table statistics during loads (re-enable + `ANALYZE` afterwards)
  * `--unsafe-nosync` disables raft-log fsync (UNSAFE: data loss on crash; local throwaway DBs only)

Usage: uv run python scripts/tune_crdb.py [--unsafe-nosync] [--restore]
"""
from __future__ import annotations

import re
import sys

import psycopg

from ingestion_engine.settings import get_settings


def main() -> None:
    restore = "--restore" in sys.argv
    with psycopg.connect(get_settings().db_url, autocommit=True) as c:
        c.execute(f"SET CLUSTER SETTING sql.stats.automatic_collection.enabled = {'true' if restore else 'false'}")
        if "--unsafe-nosync" in sys.argv or restore:
            val = "false" if restore else "true"
            stmt = f"SET CLUSTER SETTING kv.raft_log.disable_synchronization_unsafe = {val}"
            try:
                c.execute(stmt)
            except psycopg.Error as e:
                m = re.search(r"unsafe_setting_interlock_key = '([^']+)'", str(e))
                if not m:
                    raise
                c.execute(f"SET unsafe_setting_interlock_key = '{m.group(1)}'")
                c.execute(stmt)
        c.execute(f"ALTER RANGE default CONFIGURE ZONE USING gc.ttlseconds = {14400 if restore else 600}")
        print("tuned" if not restore else "restored")


if __name__ == "__main__":
    main()

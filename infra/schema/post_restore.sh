#!/usr/bin/env bash
# Post-restore repair.  CockroachDB RESTORE gives every restored object to the restoring user and does not bring back
# the explicit grants, and a fresh cluster (e.g. the backup cluster) does not know our roles yet.  This script
#   1. creates roles / memberships (00_roles.sql)            2. re-assigns ownership to schema_owner
#   3. re-applies database privileges, grants (45_, 70_) and views' grants
# usage: post_restore.sh <database> [sql_port=26267] [host=localhost]      (insecure local clusters; for secure ones set SCHEMA_DSN)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB="${1:?database}"; PORT="${2:-26267}"; HOST="${3:-localhost}"
DSN="${SCHEMA_DSN:-postgresql://root@${HOST}:${PORT}/${DB}?sslmode=disable}"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in "$HERE/../../tests/.venv/Scripts/python" "$HERE/../../tests/.venv/bin/python" python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import psycopg' 2>/dev/null; then PY="$c"; break; fi
  done
fi
"$PY" "$HERE/apply.py" --dsn "$DSN" --only 00_ --role ""
"$PY" - "$DSN" <<'PYEOF'
import sys, psycopg
c = psycopg.connect(sys.argv[1], autocommit=True)
c.execute("REASSIGN OWNED BY root TO schema_owner")
c.execute("ALTER DATABASE %s OWNER TO schema_owner" % c.info.dbname)
PYEOF
"$PY" "$HERE/apply.py" --dsn "$DSN" --only 45_,70_,75_ --role schema_owner --soft

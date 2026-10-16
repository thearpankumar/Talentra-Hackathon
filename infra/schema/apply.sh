#!/usr/bin/env bash
# Apply the Talentra schema (infra/schema/ddl + parts) to a CockroachDB cluster.  Idempotent.
#   bash schema/apply.sh local [DB]        -> insecure local main cluster via HAProxy write port 26267 (default DB talentra)
#   bash schema/apply.sh prod              -> uses SCHEMA_DSN (required), e.g. postgresql://talentra_admin:PW@db.talentra.thearpanhq.in:26257/talentra?sslmode=verify-full&sslrootcert=main-db/certs-public/ca.crt
# Options via env: SCHEMA_WORKERS (parallel connections, default 6), PYTHON (interpreter with psycopg 3), ONLY (file-prefix filter)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-local}"; DB="${2:-${TALENTRA_DB:-talentra}}"
case "$MODE" in
  local) DSN="postgresql://root@${TALENTRA_HOST:-localhost}:${TALENTRA_PORT:-26267}/${DB}?sslmode=disable" ;;
  prod)  : "${SCHEMA_DSN:?set SCHEMA_DSN to the prod write endpoint (never run casually)}"; DSN="$SCHEMA_DSN" ;;
  *) echo "usage: apply.sh local|prod [db]" >&2; exit 2 ;;
esac
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in "$HERE/../../tests/.venv/Scripts/python" "$HERE/../../tests/.venv/bin/python" python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import psycopg' 2>/dev/null; then PY="$c"; break; fi
  done
fi
[ -n "$PY" ] || { echo "no python with psycopg: pip install 'psycopg[binary]' or: uv venv tests/.venv && uv pip install -p tests/.venv 'psycopg[binary]'" >&2; exit 1; }
if [ "$MODE" = local ]; then
  # create the target database (root, insecure) and the owner role before the first run
  "$PY" - "$DSN" <<'PYEOF'
import sys, psycopg
from urllib.parse import urlparse
u = urlparse(sys.argv[1]); db = u.path.lstrip("/")
c = psycopg.connect(sys.argv[1].replace("/" + db + "?", "/defaultdb?"), autocommit=True)
c.execute(f"CREATE DATABASE IF NOT EXISTS {db}")
c.execute("CREATE ROLE IF NOT EXISTS schema_owner NOLOGIN")
c.execute(f"GRANT ALL ON DATABASE {db} TO schema_owner")
c.execute(f"ALTER DATABASE {db} OWNER TO schema_owner")
PYEOF
fi
exec "$PY" "$HERE/apply.py" --dsn "$DSN" ${ONLY:+--only "$ONLY"} --workers "${SCHEMA_WORKERS:-6}"

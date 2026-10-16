#!/usr/bin/env bash
# Start local CockroachDB and apply the stub schema (add --reset to recreate the database, --tune for dev-only speedups)
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d --wait
[[ "$*" == *--tune* ]] && uv run python scripts/tune_crdb.py --unsafe-nosync
uv run python scripts/init_stub_schema.py $(echo "$@" | sed "s/--tune//")

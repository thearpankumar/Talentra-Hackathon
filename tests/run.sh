#!/usr/bin/env bash
# run the schema test suite:  tests/run.sh [pytest args]   (TALENTRA_DB selects the database, default talentra)
cd "$(dirname "${BASH_SOURCE[0]}")"
PY=.venv/Scripts/python; [ -x "$PY" ] || PY=.venv/bin/python; [ -x "$PY" ] || PY=python
exec "$PY" -m pytest "$@"

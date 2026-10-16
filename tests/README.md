# Schema test suite

Live-database pytest suite for the Talentra CockroachDB schema (catalog conformance, constraints, business rules incl. concurrency, RBAC, masking, audit, TTL/retention, seeds, idempotent apply, backup/restore).

```
cd tests
uv venv .venv && uv pip install -p .venv -r <(echo) -e .       # or: python -m venv .venv && .venv/Scripts/pip install -e .
TALENTRA_DB=talentra .venv/Scripts/python -m pytest -q          # fast + slow tests (slow: TTL wait, backup/restore, ~10+ min)
TALENTRA_DB=talentra .venv/Scripts/python -m pytest -q -m "not slow"
make -C ../infra test-schema DB=talentra
```
Environment (defaults = local insecure stack): `TALENTRA_HOST=localhost TALENTRA_PORT=26267 TALENTRA_READ_PORT=26269 TALENTRA_BACKUP_PORT=26268 TALENTRA_DB=talentra TALENTRA_USER=root TALENTRA_SSLMODE=disable`.
Prerequisite: `make -C infra up-local STACK=backup-db && make -C infra up-local STACK=main-db` and `make -C infra schema-local`.
Tests create their own rows (fresh uuid4 ids) and are re-runnable. `helpers_rowbuilder.py` builds valid rows for any table from the data dictionary. The backup/restore test needs the local `backup-db` stack and the `talentra-main-local-backup-handler-1` container and writes its measurements to `docs/20_backup_restore_log.xlsx`.

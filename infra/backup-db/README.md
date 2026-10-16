# backup-db

Standalone CockroachDB receiving RESTOREs from main-db's handler. Local: SQL 26268, Console 8181 (insecure). Prod: secure, SQL 26258, Console at backup.talentra.thearpanhq.in via Caddy.
Mounts the shared `talentra_backup_share` volume at `extern/` (created by `make`/stack.sh). Roles mirror main-db; `backup_admin` is used by the handler. Prod needs `.env` (see `.env.example`). See ../README.md.


## Licensing, monitor role, restore
- License: same `CRDB_LICENSE_KEY` / `CRDB_ORG` as main-db in `backup-db/.env` (see main-db/README.md "Licensing"); bootstrap applies it idempotently, `make license-check` verifies.
- `talentra_monitor` is created here too (least privilege, no table SELECT; CONNECT on `defaultdb` because `talentra` does not exist until the first sync). Set `TALENTRA_MONITOR_PASSWORD` in `.env`.
- The `talentra` database here is dropped and re-created by the handler's sync (stable name, then roles/grants re-applied); do not keep anything in it. See main-db/README.md "Backup handler".
- `certs-public/ca.crt` is always a file: `gen-certs.sh` repairs a directory left by an old single-file bind mount; mount the `certs-public` directory, never the file.

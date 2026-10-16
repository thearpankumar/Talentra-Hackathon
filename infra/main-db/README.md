# main-db

Three-node CockroachDB + HAProxy + backup-handler. See ../README.md for design, ports, roles, licensing.

- Local: `docker compose up -d --build` (insecure; write 26267, read 26269, Console 8180). Prod: `docker compose -f docker-compose.prod.yml up -d --build` with `.env` from `.env.example`.
- Startup order is deliberate: crdb1 -> init -> crdb2 -> crdb3 (so node ids are 1,2,3) -> bootstrap -> handler.
- `scripts/`: init.sh, bootstrap.sh (idempotent; rerun with `docker compose run --rm bootstrap`), gen-certs.sh (prod). `haproxy/`: local and prod configs. `handler/`: backup scheduler. `logs/backup_timing.csv`: timing log.
- A lease can take ~1 min to return to node1 after it recovers (replicate queue).


## Licensing (CockroachDB Enterprise Free)
Without a license key the cluster is throttled after the 7-day grace period (this laptop cluster: **2026-10-22**).
1. Create a free account at https://www.cockroachlabs.com/, open Console -> Licenses and create an **Enterprise Free** license. Note the key (`crl-0-...`) and the organization name.
2. Put `CRDB_LICENSE_KEY=` and `CRDB_ORG=` (organization exactly as in the Console) in `infra/main-db/.env` and `infra/backup-db/.env` (gitignored; local compose reads the optional `.env` next to `local.env`, prod reads `.env`). Never commit the key.
3. Re-run the bootstrap (`docker compose run --rm bootstrap`, or `make up-local`). It is idempotent: it logs `LICENSE: ... skipping` when the key is empty, `already set` when unchanged, `applied` otherwise, and never fails the bootstrap.
4. Check: `make license-check` (licensed / days left; the key is never printed). Prometheus alerts on `seconds_until_enterprise_license_expiry`.

## HAProxy failover design
`be_write` = node1, nodes 2/3 as `backup`; `be_read` = round-robin node2/3, node1 as backup. There is no `on-marked-down shutdown-sessions` any more: a slow (not dead) node used to lose all its healthy sessions after 2 missed checks. Now a node needs 4 failed checks (5s interval, ~20s) to go DOWN, 3 passes + 20s slowstart to come back, and open sessions are left alone (a really dead node resets its TCP connections anyway). Native Prometheus metrics: `main-haproxy:8405/metrics` (docker network only, not published). Prod certs include the SAN `main-haproxy`.

## Roles
`talentra_monitor` (created by the bootstrap, password `TALENTRA_MONITOR_PASSWORD`): LOGIN, not admin, `VIEWACTIVITY VIEWCLUSTERMETADATA VIEWJOB VIEWCLUSTERSETTING`, CONNECT on the DB, no SELECT on any table (verified: `SELECT` on a user table is denied). Used by the SQL exporter.

## Backup handler
- `logs/backup_timing.csv` columns: `ts_utc,op,kind,status,start_utc,end_utc,duration_s,detail,size_bytes`. `size_bytes` = size of the latest backup layer (`SHOW BACKUP`), filled for `backup` rows, empty otherwise. An old 8-column file is migrated in place on start.
- **sync** (restore into the backup cluster) now restores under the stable name: pre-flight `SHOW BACKUP`, drop the old copy, `RESTORE DATABASE talentra FROM LATEST`, then `sync/acl` re-applies roles, memberships, owners and grants mirrored from main's catalog. Reasons (all verified on CockroachDB v26.2.8): RESTORE loses explicit grants, non-root owners and default privileges; `RESTORE ... WITH new_db_name` rewrites view definitions to the new name and `ALTER DATABASE ... RENAME` then fails with "cannot rename database because relation depends on it" whenever a view exists, so the old `_next` + rename swap could never work. Trade-off: the backup copy is briefly absent during a restore; if the restore fails it is retried once and logged as `sync restore fail`. Default privileges and function EXECUTE grants are not mirrored.
- Manual repair from the schema repo (roles + ownership + 45/70/75 grants): `infra/schema/post_restore.sh <db> [port] [host]` (tested on a restored small DB: owner and tables end up owned by `schema_owner`; grants for objects absent from the DB are skipped with warnings).

# infra: Talentra compose stacks

Four independent compose projects share one external docker network (`talentra-edge`) and, for backups, one external volume (`talentra_backup_share`).

| Stack | Dir | Local (`docker-compose.yml`) | Prod (`docker-compose.prod.yml`) |
|---|---|---|---|
| backup-db | `backup-db/` | 1 insecure node, SQL :26268, Console :8181 | 1 secure node (own CA + passwords), SQL :26258 |
| main-db | `main-db/` | 3 insecure nodes + HAProxy (write :26267, read :26269, Console :8180, stats :8404) + init + handler | 3 secure nodes, HAProxy write :26257 / read :26259, init, bootstrap, backup-handler; limits from `aws-scripts/EC2_SIZING.md` |
| caddy | `caddy/` | HTTP only on :8081, Host-routed `*.localhost` | Let's Encrypt for `talentra.`, `db.`, `backup.`, `monitoring.` `thearpanhq.in` |
| monitoring | `monitoring/` | owned by another agent (Prometheus/Grafana); wired into the Makefile only | same |

Start order (enforced by `scripts/stack.sh`): `talentra-edge` network + volume -> backup-db -> main-db -> monitoring -> caddy.

## Commands (run in `infra/`, Git Bash)
```
make up-local-all            # or: make up-local STACK=main-db
make ps-local / logs-local / down-local-all
make test-local              # acceptance tests (routing, read-only, backup->sync counts, caddy, node1 failover)
make deploy-prod             # ssh -> git clone/pull on the host into /opt/talentra/repo, copies gitignored .env files; deploy-prod-up also starts all stacks
make up-prod [STACK=..] / down-prod / ps-prod / logs-prod     # runs compose over SSH on EC2
```
Prod needs `.env` in backup-db, main-db and caddy (copy `.env.example`, fill passwords; `BACKUP_ADMIN_PASSWORD` must match in both DB stacks).
Local uses committed dummy `local.env` files; the local clusters are insecure, so passwords are ignored.

## Decisions
**SQL path.** pgwire is not proxied by Caddy (no caddy-l4 custom build). HAProxy publishes 26257 (write) and 26259 (read); the backup node publishes 26258. TLS is terminated by CockroachDB itself.
**TLS strategy: Cockroach internal CA + password auth, `sslmode=verify-full`.** Each stack's `certs` one-shot job creates a CA, one node cert whose SANs cover the service names, the public hostname and the EIP, and a root client cert. Only `ca.crt` is copied to `<stack>/certs-public/` (give it to SQL clients, Caddy and the handler). We rejected sharing Caddy's Let's Encrypt cert: Cockroach reuses the node cert for inter-node client auth (Let's Encrypt dropped the client-auth EKU), reload/sync sidecars are fragile, and the DB Console proxy works fine with a pinned CA.
**DB Console**: Caddy -> `https://main-crdb{1,2,3}:8080` (first healthy, trusts the internal CA) with Caddy basic auth on top of the Console's own SQL login. `monitoring.` is a plain `reverse_proxy grafana:3000`.
**Leaseholder / reads.** Localities `region=..,zone=a|b|c`; zone config on `RANGE default`, `meta`, `liveness`, `system` and database `talentra`: `num_replicas=3, lease_preferences=[[+zone=a]]` so node1 holds leases. HAProxy write backend: node1 primary, nodes 2/3 `backup` (used only when node1 fails `GET /health?ready=1`). Read backend: round-robin node2/node3, node1 as last resort.
**How clients connect**
```
write: postgresql://talentra_app_rw:PW@db.talentra.thearpanhq.in:26257/talentra?sslmode=verify-full&sslrootcert=ca.crt
read : postgresql://talentra_app_ro:PW@db.talentra.thearpanhq.in:26259/talentra?sslmode=verify-full&sslrootcert=ca.crt
backup: postgresql://backup_admin:PW@backup.talentra.thearpanhq.in:26258/talentra?sslmode=verify-full&sslrootcert=backup-ca.crt
```
`talentra_app_ro`, `talentra_analyst`, `talentra_audit_reader` have `default_transaction_read_only=on` and `default_transaction_use_follower_reads=on` set at role level (follower reads are about 5 s stale, so a row written to the write port is visible on the read port after ~5 s). Other users can use `AS OF SYSTEM TIME follower_read_timestamp()`. Important: the read port is read-only by role, not by port. A read-write role connected to 26259 is forwarded to the leaseholder and can write.
**Roles** (placeholders, schema team adds grants): `talentra_admin` (admin), `talentra_app_rw` (ALL on db + default privileges), `talentra_app_ro` / `talentra_analyst` (SELECT via default privileges for tables created by root, talentra_admin, talentra_app_rw), `talentra_audit_reader` (connect only), `talentra_backup` (admin; used by the handler). Passwords must be alphanumeric.
**Cluster settings** applied by `bootstrap`: `kv.rangefeed.enabled`, `sql.stats.automatic_collection.enabled`, `sql.log.admin_audit.enabled` (admin statements audited), SCRAM-SHA-256 with cost 10610, min password length 12 (prod), idle-in-txn timeout 5 min.

## Licensing (CockroachDB v26.2, verified in the Cockroach docs, Oct 2026)
- Core was retired in v24.3; every cluster runs under one license. **No license: 7 day grace, then throttled to 5 concurrent open SQL transactions.** Single-node dev (`start-single-node`, `demo`) is exempt, but our backup-db is run in production mode, so treat it as licensed too.
- **Enterprise Free**: same functionality as Enterprise, for companies under $10M revenue (affiliates counted), non-government, on your own hardware or cloud account; telemetry must keep flowing (throttled after 7 days without it, so leave `diagnostics.reporting.enabled=true` and allow outbound HTTPS); renew yearly; community support. Create at cloud.cockroachlabs.com > Organization > Enterprise Licenses, then put the key in `.env` (`CRDB_LICENSE_KEY`, `CRDB_ORG`); bootstrap runs `SET CLUSTER SETTING enterprise.license=...`.
- BACKUP/RESTORE (full + incremental), scheduled backups, follower reads, changefeeds and PCR are Enterprise-tier features under this model, so they run inside the grace period and afterwards need the free license. The docs do not publish a per-feature list on the licensing page; we use plain BACKUP/RESTORE and follower reads only. PCR was not chosen: its standby is offline while replicating, whereas the backup cluster must stay queryable.

## Backup design
`backup-handler` (bash loop, image built from the cockroach image): every `INCR_EVERY_MIN` incremental, every `FULL_EVERY_MIN` full `BACKUP DATABASE talentra INTO 'nodelocal://self/main'`; `nodelocal://self` resolves to each node's `extern` dir, and all main nodes plus the backup node mount the same volume there, so the backup node can restore without copying. Every `SYNC_EVERY_MIN`: fresh backup, pre-flight `SHOW BACKUP`, drop the old copy on the backup cluster, `RESTORE DATABASE talentra FROM LATEST IN ...` under the stable name (a `new_db_name` restore plus rename-swap is impossible once views exist, see `main-db/README.md`), re-apply roles/owners/grants mirrored from main (`acl`), then row-count verification per table. The backup copy is briefly absent during a restore. Main stays up throughout. Set `BACKUP_S3_URI` to additionally back up to S3 (instance role: `AUTH=implicit`). Manual: `docker exec <handler> handler.sh backup full|incr | sync | verify`.
Timing log (feeds `docs/20_backup_restore_log.xlsx`): `main-db/logs/backup_timing.csv` with `ts_utc,op,kind,status,start_utc,end_utc,duration_s,detail` (ops: backup, sync restore/acl/verify).
Sync drops and restores the whole database each cycle, so cost grows with data size; raise `SYNC_EVERY_MIN` when loaded with tens of GB. A real disaster restore from the volume: `RESTORE DATABASE talentra FROM LATEST IN 'nodelocal://self/main'` on any cluster that mounts it.

## Security groups
Open to the owner IP: 22, 26257 (write), **26259 (read, new)**, 26258 (backup). Public: 80, 443. Console ports (8080) are never published on the host.


## Local vs prod, checks (added)
Every stack has `docker-compose.yml` (LOCAL, insecure, laptop) and `docker-compose.prod.yml` (PROD, EC2). Makefile: `make up-local|down-local STACK=...`, `make schema-local`, `make test-schema`, `make license-check` (licensed? days left), `make config-check` (`docker compose config -q` for all files). Licensing: see main-db/README.md. `certs-public/ca.crt` must be a file; `scripts/stack.sh` repairs a leftover directory before a local `up`, `gen-certs.sh` does the same in the certs job.

## Known deviations from docs/15 and docs/20 (tracked in docs/21_alignment_report.md)
| Doc statement | As built | Status |
|---|---|---|
| docs/20: 15-minute incrementals (laptop), 5-minute (prod), daily full | `INCR_EVERY_MIN` default 15 local / 5 prod, `FULL_EVERY_MIN` 1440; Prometheus `talentra:backup_rpo_target_seconds` = 1800 | aligned |
| docs/20, docs/15 s13: `revision_history` (PITR), encrypted backups (KMS or passphrase), 35-day retention + monthly fulls, object-lock bucket | plain `BACKUP DATABASE ... INTO` to the shared volume, optional `BACKUP_S3_URI`; no `revision_history`, no encryption option, no pruning job | gap, deferred to EC2 hardening |
| docs/20, docs/15: backups run by `backup_operator` (BACKUP privilege, no SELECT) | handler logs in as `talentra_backup` (member of `admin`); `backup_operator` exists in the schema (`45_db_privileges.sql`) but no login is wired to the handler | gap |
| docs/15 s3: certificate-only logins, host-based auth, 1-year node / 90-day client certs | password (SCRAM) login with `verify-full`, 5-year node/root certs, no HBA rule | accepted deviation, see docs/15 section 3 |
| docs/15 s14: no secrets in the repo | `.env` is gitignored; `local.env` files hold throw-away values for insecure local clusters | aligned |

## Backup schedule and manual runs (prod, verified on EC2)
`talentra-main-backup-handler-1` (`restart: unless-stopped`, healthcheck) runs forever: incremental backup of `talentra` every `INCR_EVERY_MIN` (5), full every `FULL_EVERY_MIN` (1440), restore into the backup cluster every `SYNC_EVERY_MIN` (360) with main staying up, then ACL mirror (roles, owners, grants, in batched transactions) and a one-stream row-count verification. Timing and `size_bytes` go to `main-db/logs/backup_timing.csv` and from there to Prometheus (`talentra_backup_*`, Grafana backup-health panel). Measured with the 1306-table empty schema: full backup 9 s, restore 15 to 22 s (up to 15 min when the cluster is busy), ACL mirror 286 s, verify 6 s.
Manual: `make backup-now KIND=full|incr`, `make sync-now` (background on the host), `docker exec talentra-main-backup-handler-1 handler.sh acl|verify`. When the ingestion engine fills main, the next scheduled sync carries the data across; tune `SYNC_EVERY_MIN` in `main-db/.env` as data grows (a sync drops and restores the whole database). Operations runbook: `PROD_RUNBOOK.md`; psql/DBeaver access: `DBEAVER_CONNECT.md`.

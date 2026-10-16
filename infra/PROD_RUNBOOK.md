# Talentra production runbook (EC2, single host)

Everything needed to rebuild or debug prod is in this repo; only secrets are not. Secrets live in gitignored `infra/*/.env` (laptop and host) and `.aws-creds/prod-credentials.txt` (laptop only). **Back that file up** (password manager or an encrypted copy): it is the only readable copy of the generated passwords. This repo is public: never commit `.env`, the PEM, or credentials.

## 1. Host state
Ubuntu 24.04.5 LTS, m6i.2xlarge (8 vCPU, 30 GiB usable), 500 GB gp3 (6000 IOPS / 300 MB/s), Elastic IP 34.235.96.187, us-east-1, kernel 7.0.0-1014-aws. Docker Engine 29.9.1 + Compose v5.6.0 from Docker's apt repo (deb822 `docker.sources`), chrony on the Amazon time source, fail2ban (sshd jail), ufw (22, 80, 443, 26257-26259, plus 172.16.0.0/12 to 9100/9323 for Prometheus), swap off, sysctl `vm.max_map_count=262144`, `vm.swappiness=1`, nofile 1048576. Exact commands: `aws-scripts/user-data.sh` and `aws-scripts/README.md`. Python venv `/opt/talentra/venv` (psycopg, pytest) and tmux for long jobs.
Note: `metrics-addr` and `live-restore` in `daemon.json` come from user-data on new hosts; on this host they are applied only after a docker restart (not done, to avoid a DB blip). The Prometheus `docker-daemon` target stays down until then.

## 2. Layout on the server
```
/opt/talentra/repo                              git clone (public https); deploy = git fetch + reset --hard
/opt/talentra/repo/infra/<stack>/.env           GITIGNORED, mode 600, copied from the laptop by `make deploy-prod`
/opt/talentra/repo/infra/{main-db,backup-db}/certs-public/ca.crt   GITIGNORED, produced by the `certs` one-shot job
/opt/talentra/repo/infra/main-db/logs/backup_timing.csv           GITIGNORED, handler timing log (has size_bytes)
/opt/talentra/schema.env                        outside the repo, exports SCHEMA_DSN for apply.py (talentra_admin, verify-full)
/opt/talentra/venv, /opt/talentra/logs          python env, apply and sync logs
docker volumes: per-stack named volumes (CRDB data, certs, caddy_data = Let's Encrypt state), talentra_backup_share
```
Stack order: network `talentra-edge` and volume, then backup-db, main-db, monitoring, caddy (`infra/scripts/stack.sh`).

## 3. Rebuild from a fresh EC2
1. `bash infra/aws-scripts/provision.sh` (key pair to `.aws-creds/`, SG, instance, EIP; user-data does section 1). Wait for `/var/lib/talentra-bootstrap.done`.
2. DNS A records (DNS only, not proxied) to the EIP: `talentra.`, `db.talentra.`, `backup.talentra.`, `monitoring.talentra.` plus `thearpanhq.in`.
3. Create `infra/{main-db,backup-db,caddy,monitoring}/.env` from the `.env.example` files (every variable is listed there; alphanumeric passwords only). Generate a password with `python -c "import secrets,string;print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(24)))"`. The Caddy hash comes from `docker run --rm caddy:2-alpine caddy hash-password --plaintext PW`. `CRDB_LICENSE_KEY` is the Enterprise Free key from cockroachlabs.com, `CRDB_ORG=Talentra`. The same `TALENTRA_*` and `BACKUP_ADMIN_PASSWORD` values go into both main-db and backup-db; `TALENTRA_MONITOR_PASSWORD` is also inside both `SQL_EXPORTER_*_DSN` in monitoring/.env. Keep every value in `.aws-creds/prod-credentials.txt`.
4. `make -C infra deploy-prod-up` (git clone on the host, copy .env, start all stacks). `make -C infra prod-env-check` must print `ALL REQUIRED VARIABLES SET`.
5. Public CA: `infra/certs/main-ca.crt` and `backup-ca.crt` are committed copies; they change only if the `certs` volumes are recreated (refresh with scp, see DBEAVER_CONNECT.md).
6. Schema: create `/opt/talentra/schema.env` on the host (`export SCHEMA_DSN="postgresql://talentra_admin:<pw>@127.0.0.1:26257/talentra?sslmode=verify-full&sslrootcert=/opt/talentra/repo/infra/main-db/certs-public/ca.crt"`, mode 600). Create the owner role once: `CREATE ROLE IF NOT EXISTS schema_owner NOLOGIN; GRANT ALL ON DATABASE talentra TO schema_owner; ALTER DATABASE talentra OWNER TO schema_owner;`. Then `make -C infra schema-prod-remote` (tmux; about 10 min first run, 4 to 6 min re-apply). Then `make -C infra sync-now` to copy it to the backup cluster.

## 4. Ports, security group, URLs
SG open: 22, 80, 443, 26257 (write), 26258 (backup SQL), 26259 (read). HAProxy: 26257 write (node1 primary, nodes 2/3 standby), 26259 read (nodes 2/3; `talentra_app_ro` is read-only by role). Console ports are not published; Caddy proxies them.
URLs: https://monitoring.talentra.thearpanhq.in (Grafana, user `admin`), https://db.talentra.thearpanhq.in and https://backup.talentra.thearpanhq.in (CockroachDB Console behind Caddy basic auth user `talentra`, then SQL login), https://talentra.thearpanhq.in (placeholder). SQL clients: `DBEAVER_CONNECT.md`.

## 5. Users and where the passwords are
Variable names in `main-db/.env` and `backup-db/.env`: `talentra_admin` TALENTRA_ADMIN_PASSWORD (CockroachDB admin), `talentra_app_rw` TALENTRA_APP_RW_PASSWORD, `talentra_app_ro` TALENTRA_APP_RO_PASSWORD, `talentra_analyst` TALENTRA_ANALYST_PASSWORD, `talentra_audit_reader` TALENTRA_AUDIT_READER_PASSWORD, `talentra_backup` TALENTRA_BACKUP_PASSWORD (handler login on main, currently admin; a least-privilege `backup_operator` login is a known gap), `talentra_monitor` TALENTRA_MONITOR_PASSWORD, `backup_admin` BACKUP_ADMIN_PASSWORD (backup cluster). Grafana: GRAFANA_ADMIN_PASSWORD (monitoring/.env). Caddy: BASIC_AUTH_USER and BASIC_AUTH_HASH (caddy/.env; plaintext only in prod-credentials.txt). The 60+ schema group and persona roles come from `schema/ddl/00_roles.sql` and have no password.

## 6. Certificates
CockroachDB TLS uses an internal CA (10-year); node certs list the public host names and the EIP. Clients use `sslmode=verify-full` with the CA file. Caddy uses Let's Encrypt for the four host names and renews automatically; state is in volume `caddy_data` (do not delete it: rate limits).

## 7. Backups
`talentra-main-backup-handler-1` loops forever (`restart: unless-stopped`): incremental every 5 min (INCR_EVERY_MIN), full every 1440 min, restore-sync into the backup cluster every 360 min (SYNC_EVERY_MIN), under the stable database name `talentra` (no rename). After each restore it mirrors roles, owners and grants (batched transactions) and verifies row counts per table in one stream. Log: `infra/main-db/logs/backup_timing.csv`. Manual: `make -C infra backup-now KIND=full|incr`, `make -C infra sync-now` (runs in the background on the host; watch the csv). Disaster restore of main from the shared volume: `RESTORE DATABASE talentra FROM LATEST IN 'nodelocal://self/main'`.

## 8. Monitoring
Grafana datasources: Prometheus, Loki, Alertmanager; dashboards are provisioned from `monitoring/grafana/dashboards`. Targets: `docker exec talentra-monitoring-prometheus-1 wget -qO- localhost:9090/api/v1/targets`. Alert delivery (Slack, SMTP, webhook) is empty by default; set the variables in `monitoring/.env`.

## 9. Rotating secrets
Edit the value in `.aws-creds/prod-credentials.txt` and in the stack `.env` files, run `make -C infra deploy-prod`, then re-run the bootstrap on both clusters (`docker compose -f docker-compose.prod.yml run --rm bootstrap` in `main-db` and in `backup-db`; it does CREATE/ALTER USER) and restart dependants. Monitor password: also the DSNs in `monitoring/.env`, then `up -d --force-recreate sql-exporter-main sql-exporter-backup`. App or backup roles: `up -d backup-handler`. Grafana password: also change it in the UI (the env var only seeds the first start). Caddy basic auth: new hash in caddy/.env, then `up -d --force-recreate caddy`. License: re-run bootstrap.

## 10. Known issues and fixes already applied
| Symptom | Cause and fix |
|---|---|
| Git Bash ssh: `error in libcrypto` | Git's ssh cannot read the ed25519 key; `aws-scripts/common.sh` uses Windows OpenSSH and converts the key path |
| Caddy unhealthy though serving | healthcheck used `localhost` (IPv6); now `127.0.0.1` |
| Alertmanager crash loop, `yaml: did not find expected key` | inline `# comments` in `.env` were read as values; keep comments on their own lines |
| Passwords visible in bootstrap logs | bootstrap scripts now mask `PASSWORD '...'`; the affected password was rotated |
| Handler process list shows the URL with password | `cockroach sql --url` carries it in argv; visible only to root or docker users on the host; tracked for a PGPASSFILE change |
| Prometheus `node` target down | ufw blocked docker0 to 9100; rule added (also in user-data) |
| `docker-daemon` target down | needs `metrics-addr` in daemon.json and a docker restart |
| `schema/parts` missing on the host | root `.gitignore` had `parts/`; negated for `infra/schema/parts` |
| Rule functions failed to compile | `sha256(bytes)` replaced by `digest(...,'sha256')`, hash helper STABLE not IMMUTABLE, `deleted_at` filter removed for tables without that column |
| Re-apply error `function fn_recruiter_job_requirement already exists` | generator now emits `CREATE OR REPLACE FUNCTION` for view helpers |
| Sync ran over an hour at 1306 tables | ACL mirror did one schema change at a time and verify opened 2600 connections; now batched and single stream |
| `grafana/dashboards/Logs` missing on the host | `**/logs/` ignore; negated in `infra/.gitignore` |

## 11. Troubleshooting
| Check | Command |
|---|---|
| containers | `make -C infra ps-prod` or `bash aws-scripts/ssh.sh 'docker ps -a'` |
| env complete | `make -C infra prod-env-check` |
| drift host vs repo | `make -C infra prod-snapshot`, then diff `infra/prod-snapshot/` |
| cluster health | Console via Caddy |
| license | the Console, or `seconds_until_enterprise_license_expiry` in `_status/vars` |
| TLS to SQL fails | wrong CA file, or an IP instead of the host name (hostname/PKIX errors) |
| schema apply log | `/opt/talentra/logs/apply-*.log`; re-apply is idempotent |

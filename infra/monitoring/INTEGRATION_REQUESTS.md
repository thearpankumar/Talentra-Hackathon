# Integration requests (other stacks / host) from the monitoring stack

Nothing here edits files outside `infra/monitoring/`. Items are ordered by importance. "Works today" means the monitoring stack already runs without the change.

## 1. Caddy (infra/caddy/Caddyfile) - REQUIRED for request/latency/status metrics
Caddy v2.11 only exports `caddy_http_*` series when metrics are enabled. The admin API (`localhost:2019` in prod) is correctly NOT reachable from other containers, so expose metrics on a dedicated internal port:

```caddyfile
{
	email {$ACME_EMAIL}
	admin localhost:2019
	metrics            # <- add (Caddy >= 2.10 global option; replaces `servers { metrics }`)
}

# internal-only metrics endpoint for Prometheus (port 9180 is NOT published in docker-compose.prod.yml)
:9180 {
	metrics /metrics
}
```
Optional, for the Logs dashboard: add `log { output stdout \n format json }` to the `{$DB_HOST}`, `{$BACKUP_HOST}`, `{$APEX_HOST}` and `{$MONITORING_HOST}` blocks (access logs then flow to Loki via Alloy).
Local (`Caddyfile.local`): `admin :2019` already serves `/metrics` (Go/process/admin series, scraped as `caddy:2019`). Adding `metrics` to its global block also gives per-request series locally.
Works today without it: `caddy_reverse_proxy_upstreams_healthy`, `caddy_config_*`, admin metrics (local only), blackbox HTTPS probes + cert expiry (prod).

## 2. CockroachDB monitoring SQL role (infra/main-db/scripts/bootstrap.sh and infra/backup-db/scripts/bootstrap.sh) - REQUIRED in prod for SQL-level metrics
The SQL exporter (jobs/backup status, table row counts, sessions, slow queries, contention) logs in as `talentra_monitor`. Add to both bootstraps (idempotent, same style as the other users):
```sql
CREATE USER IF NOT EXISTS talentra_monitor WITH LOGIN PASSWORD '<TALENTRA_MONITOR_PASSWORD>';   -- ALTER USER ... on re-run
GRANT SYSTEM VIEWACTIVITY, VIEWCLUSTERMETADATA, VIEWJOB, VIEWCLUSTERSETTING TO talentra_monitor;
GRANT CONNECT ON DATABASE talentra TO talentra_monitor;
```
New env var `TALENTRA_MONITOR_PASSWORD` (main-db/.env, backup-db/.env); the same value goes into `infra/monitoring/.env` inside `SQL_EXPORTER_*_DSN`. It is read-only and has no table SELECT (only optimizer statistics and `SHOW JOBS`). Local insecure stacks need nothing (exporter connects as `root`).
Works today without it: every native `/_status/vars` metric (nodes, ranges, SQL latency, storage...).

## 3. certs-public/ca.crt must be a FILE inside a directory (both DB stacks)
Monitoring mounts the **directories** `infra/main-db/certs-public` and `infra/backup-db/certs-public` and reads `ca.crt` from them (Prometheus scrape TLS, blackbox, SQL exporter use `verify-full`). On this laptop `main-db/certs-public/ca.crt` was observed as a *directory* (`certs-public/ca.crt/ca.crt`): Docker creates a directory when a missing file is bind-mounted (the caddy prod compose mounts `../main-db/certs-public/ca.crt` as a single file). Run `gen-certs` before Caddy starts (already the documented order) and never start Caddy first.

## 4. Certificate SANs (CERT_HOSTS)
Current SANs already include `main-crdb1..3` and `backup-crdb` - monitoring pins `server_name` to those, so nothing is needed. Optional: add `main-haproxy` to main CERT_HOSTS if you want the SQL exporter to connect through HAProxy (automatic failover of the monitoring connection). Today it connects to `main-crdb1` directly.

## 5. Backup handler CSV (infra/main-db/handler/handler.sh -> logs/backup_timing.csv)
Parsed by `backup-metrics/export.py`. Contract I rely on - please keep stable:
`ts_utc,op,kind,status,start_utc,end_utc,duration_s,detail` with `status` in `ok|fail`; `op` in `backup|sync|verify|...`; `kind` = `<full|incr>-<local|s3>` for backups (other kinds are exported as-is). Unknown ops/kinds appear as extra label values, nothing breaks.
Requests: (a) optional 9th column `size_bytes` (I will surface it as `talentra_backup_last_size_bytes`; today I export total bytes under the shared `talentra_backup_share` volume instead); (b) rotate the CSV (e.g. keep 5000 lines) - `talentra_backup_runs_in_log` resets on rotation, alerts do not depend on it; (c) keep `./logs` bind-mounted at `infra/main-db/logs` (monitoring mounts it read-only; in prod the path is `/opt/talentra/infra/main-db/logs`).
Observation on the local run: `sync/restore` ends with `fail` (`RESTORE ... Failed running 'sql'`) while a later `swap` succeeds - the BackupSyncFailed alert will fire on the last-run state; confirm the intended semantics of the three-step sync.

## 6. HAProxy (optional upgrade, works today)
Monitoring uses `prom/haproxy-exporter` on `http://main-haproxy:8404/stats;csv` (the existing `stats` frontend) - no change needed. For native metrics (more series, no exporter) add to `frontend monitor`: `http-request use-service prometheus-exporter if { path /metrics }` and I will repoint the scrape job.

## 7. Docker daemon metrics (infra/aws-scripts/user-data.sh) - optional
Add `"metrics-addr": "172.17.0.1:9323"` to `/etc/docker/daemon.json` (alongside the log rotation already set there) and restart Docker. Target `host.docker.internal:9323` is already in `prometheus/targets/prod/docker-daemon.yml`; it is marked `optional` so it never pages while absent.

## 8. Containerd image store / Docker version (informational)
Docker 28/29 with the containerd image store (this laptop, and fresh Docker 29 installs) breaks cAdvisor <= v0.52 (`failed to identify the read-write layer ID`, no per-container series). The stack pins cAdvisor v0.55.1 which works. Don't downgrade it.

## 9. Memory budget (infra/aws-scripts/EC2_SIZING.md)
Monitoring limits sum to ~3.3 GiB (Prometheus 1.25g, Loki 0.5g, Grafana 0.4g, Alloy 0.25g, cAdvisor 0.25g, rest < 0.7g); typical resident 1.8-2.2 GiB with 15d retention at this scrape volume. EC2_SIZING.md budgets ~3 GiB for OS+Docker+page cache and ~28-29 GiB total, so the box is ~1-2 GiB over. Suggested: lower `N1_CACHE` 3GiB->2GiB (or backup `BK_CACHE` 4GiB->3GiB) before the 10M load. Monitoring limits are tunable via `PROM_MEM_LIMIT`, `LOKI_MEM_LIMIT`, `GRAFANA_MEM_LIMIT`.

## 10. Ingestion engine
To light up the "Ingestion Engine" dashboard: expose Prometheus metrics on `:8000/metrics` (names in README), run it as a container with alias `ingestion-engine` on `talentra-edge` (prod) - or on the laptop host (scraped as `host.docker.internal:8000`).

## 11. Makefile / stack.sh
`infra/scripts/stack.sh` already brings up `monitoring` between the DB stacks and Caddy. For prod it needs `infra/monitoring/.env` on the host (not synced by git) - include it in `deploy.sh` or copy by hand; it contains the Grafana admin password and SQL exporter DSNs.


## 12. Infra-alignment changes (agent C) - what changed under monitoring/ and what it needs
- **sql_exporter made light**: `crdb_internal.tables` (loads every table descriptor, 30-50 s with 1300+ tables) is gone. `crdb_tables` now only exports `crdb_user_databases` (metric `crdb_database_tables` removed; the old query is in `sql_exporter/optional/crdb_tables_full.collector.yml.example`, opt-in). SHOW JOBS queries moved into their own collector `crdb_jobs` (cached 300 s). Dashboard panel "User databases" and METRICS_INVENTORY updated. Measured: backup exporter scrape 0.04-0.5 s; main exporter was 33 s before; the main cluster is currently saturated by schema applies, so `crdb_jobs` (SHOW JOBS, ~6700 jobs) still times out there (partial metrics are returned) - re-measure on a quiet cluster / EC2.
- Local exporters now connect as `talentra_monitor` (compose default DSN). Role exists in both bootstraps (local and prod), env var `TALENTRA_MONITOR_PASSWORD`.
- **Caddy**: `metrics` global option and `:9180 { metrics /metrics }` are in Caddyfile and Caddyfile.local. Local target file still scrapes `caddy:2019`; point it to `caddy:9180` to get `caddy_http_*`.
- **HAProxy**: native exporter `main-haproxy:8405/metrics` exists (internal only); `main-haproxy` added to prod cert SANs, so the exporter DSN may use it.
- **Backup CSV**: 9th column `size_bytes` added (empty for non-backup rows); header migrated in place. `sync` ops are now `restore`, `acl`, `verify` (no `swap`).
- **License**: `seconds_until_enterprise_license_expiry` is exposed natively by every node (/_status/vars); the existing alert covers expiry < 30 d. It does NOT fire when there is no license (value 0): suggest an alert on `seconds_until_enterprise_license_expiry == 0` (no license, throttling after the grace period). No SQL role needed.

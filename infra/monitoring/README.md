# Talentra monitoring stack

Prometheus + Alertmanager + Grafana + Loki/Alloy + exporters, everything provisioned as code. Own compose project (`talentra-monitoring` / `talentra-monitoring-local`), joins the shared `talentra-edge` network so it can reach CockroachDB, HAProxy and Caddy, and so Caddy can reach Grafana (`grafana:3000`). **Grafana is the only UI**: `https://monitoring.talentra.thearpanhq.in` (prod), `http://localhost:3300` or `http://monitoring.localhost:8081` (local).

```
infra/monitoring/
  docker-compose.yml / docker-compose.prod.yml   local (Docker Desktop) / EC2
  .env.example  INTEGRATION_REQUESTS.md  METRICS_INVENTORY.md
  prometheus/   prometheus.yml, scrape.d/{common,local,prod}/, targets/{local,prod}/ (file_sd), rules/ (recording + alerts)
  alertmanager/ alertmanager.base.yml + entrypoint.sh (receivers built from env)
  grafana/      provisioning/{datasources,dashboards,alerting}, dashboards/<Folder>/*.json  (26 dashboards)
  loki/ alloy/  log storage (15d) and collection (docker.alloy; journal.alloy prod-only)
  blackbox/ sql_exporter/ backup-metrics/ cloudwatch/    probes, SQL queries, backup textfile exporter, opt-in YACE
  tools/        dashboard generators (gen_*.py), vendored community JSON (tools/vendor), check_dashboards.py
  scripts/      promtool-check.sh
```

## Run

Local (laptop):
```bash
docker network create talentra-edge        # once (make net does it)
docker compose up -d                        # no .env needed
docker compose --profile demo-target up -d  # + throwaway CockroachDB single node with a running `movr` workload (cluster="demo")
# Grafana http://localhost:3300 admin/admin   Prometheus :9090   Alertmanager :9093
```
Prod (EC2; started by `make up-prod STACK=monitoring` after main-db/backup-db, before caddy):
```bash
cp .env.example .env && $EDITOR .env        # GRAFANA_ADMIN_PASSWORD, SQL_EXPORTER_*_DSN, alert receiver
docker compose -f docker-compose.prod.yml config -q && docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml --profile cloudwatch up -d yace   # optional CloudWatch
```
Prometheus/Alertmanager/Loki listen on `127.0.0.1` only (`ssh -L 9090:127.0.0.1:9090 ...`). No new public ports; Caddy's existing `monitoring.` block proxies Grafana.

## What is scraped
| Job | Target | Notes |
|---|---|---|
| `cockroachdb` | main-crdb1..3:8080, backup-crdb:8080 `/_status/vars` | labels `cluster=main|backup`; job label normalised so the official dashboards work |
| `node` | node_exporter (prod: host network on docker0 `172.17.0.1:9100`) | + systemd, processes, tcpstat, ethtool (ENA), buddyinfo, textfile |
| `cadvisor` | cadvisor:8080 (v0.55.1) | per-container CPU/mem/IO/net/OOM/processes |
| `haproxy` | haproxy_exporter -> `main-haproxy:8404/stats;csv` | |
| `caddy` | local `caddy:2019`, prod `caddy:9180` | needs INTEGRATION_REQUESTS #1 |
| `sql_exporter` | two instances (main, backup) | jobs/backups, rows, sessions, slow queries, contention |
| `blackbox-http` / `blackbox-tcp` | public HTTPS hostnames + SQL ports (internal) | TLS expiry, handshake timing |
| backup textfile | `backup-metrics` -> node_exporter textfile | from `infra/main-db/logs/backup_timing.csv` |
| `docker-daemon`, `ingestion-engine`, `cloudwatch` | optional (never page while absent) | |
| self | prometheus, alertmanager, grafana, loki, alloy, blackbox | |

### CockroachDB in secure mode (researched and verified on v26.2)
`GET https://<node>:8080/_status/vars` returns the Prometheus exposition **without a login/session cookie** (HTTP 200 from another container; the DB Console pages themselves need auth). So no API token or client cert is needed - Prometheus only verifies the server certificate: `scheme: https`, `tls_config.ca_file` = each cluster's own `certs-public/ca.crt` (main and backup have different CAs, hence two jobs), `server_name` pinned to a SAN present in the node cert (`main-crdb1`, `backup-crdb`; `CERT_HOSTS` already lists them). If a future version starts to require auth for this endpoint, generate an API token for a user with the VIEWCLUSTERMETADATA privilege (`cockroach auth-session login <user> --certs-dir=... --only-cookie`, or a service-account token) and add `authorization: {type: Bearer, credentials_file: /secrets/crdb-token}` (or a `Cookie` header via `http_headers`) to the job; `/metrics` on port 8080 also answered 200 on v26.2 as an alternative path. SQL-level metrics (which `/_status/vars` cannot provide) go through pgwire with the read-only `talentra_monitor` role (INTEGRATION_REQUESTS #2).

## Dashboards (folder / uid)
- **Talentra**: Overview (`talentra-overview`, home), Alerts Overview (`talentra-alerts`), Capacity and Forecast (`talentra-capacity`: predict_linear disk-full, PSI, EBS 6000 IOPS / 300 MB/s saturation, memory budget).
- **Host and Containers**: Node Exporter Full (community 1860, vendored), Host Extras (systemd, TCP states, processes, PSI, hwmon, entropy, FDs, clock, ENA), Docker Containers (cAdvisor + daemon metrics).
- **CockroachDB**: Overview (Talentra), SQL/Queries/Latency percentiles, Storage/LSM/Compaction/Admission, Replication/Ranges/Leaseholders (1W-2R: leaseholder share on node 1), Hot ranges and Contention, Jobs and Backups (RPO/RTO) + the 9 official `CRDB Console:` dashboards (vendored from cockroachdb/cockroach `monitoring/grafana-dashboards/by-cluster`).
- **Edge and Uptime**: HAProxy, Caddy, Uptime and SSL (Blackbox).
- **Logs**: Logs Explorer (Loki: containers, journald, CRDB, backup-handler, Caddy).
- **Applications**: Ingestion Engine (placeholder).
All have tags, links dropdown, alert/restart/backup annotations, variables, thresholds and units. Regenerate with `python tools/gen_all.py`; verify with `python tools/check_dashboards.py` (runs every panel query through Grafana's `/api/ds/query`).

## Alerts
Prometheus rules (`prometheus/rules/`, routed by Alertmanager): instance down; host CPU/memory/PSI/disk (<20%/<8%), **disk fills in 24h** (predict_linear), inodes, EBS IOPS/throughput near the gp3 limit, clock skew, FDs, OOM kills, containers (restarts, OOM, memory near limit, throttling), CockroachDB (node down, live nodes, unavailable/under-replicated ranges, clock offset, p99 latency, connections near limit, node/CA cert and license expiry, store disk, L0 sublevels, restarts, slow requests, write stalls), **backups** (stale vs RPO 2h, very stale 4x, never succeeded, last run failed, sync failed, slower than RTO 30m, exporter stale, SHOW JOBS failures), HAProxy (backend/server down, errors, sessions), Caddy (5xx, latency), blackbox (probe failed, SQL port unreachable, **TLS < 14d / < 5d**, slow), monitoring self-checks, ingestion stalled. `Watchdog` always fires. Tune RPO/RTO/limits in `rules/00-recording.yml`.
Receivers: set `SLACK_WEBHOOK_URL`, `ALERT_WEBHOOK_URL` or `SMTP_*` + `ALERT_EMAIL_TO` in `.env` (see `alertmanager/entrypoint.sh`); with none set alerts stay visible in Alertmanager/Grafana and a warning is logged. Grafana's own contact point forwards Grafana-managed alerts (one log-error-burst rule) to the same Alertmanager.

## Retention / memory
Prometheus 15d **and** 8 GB cap (WAL compressed), Loki 15d (compactor), Alertmanager default. Prod memory limits total ~3.3 GiB (typical ~2 GiB); see INTEGRATION_REQUESTS #9 for the tight EC2 budget. Cardinality is trimmed: cAdvisor metric groups disabled/dropped, node filesystem/mount excludes, sql_exporter results cached 45-120s.

## Per-table row counts (opt-in)
`sql_exporter` turns *any* failing query into HTTP 500 for the whole target, so the default collectors only run queries that cannot fail (verified against a secure cluster with the least-privilege `talentra_monitor` role). Row counts need to name tables (error while a table does not exist yet) and `crdb_internal.table_row_statistics` / `pg_class.reltuples` do not contain user tables on v26.2, so they are opt-in: see `sql_exporter/optional/crdb_table_rows.collector.yml.example`. Default parity/growth panels use native `livecount` / `livebytes`.

## Backup health metrics
`backup-metrics/export.py` tails `infra/main-db/logs/backup_timing.csv` and writes `talentra_backup_*` (last success timestamp, last run success, last successful duration, consecutive failures, runs by status, bytes under the shared backup volume) to node_exporter's textfile directory. Complemented by `SHOW JOBS` (`crdb_job_*`, `crdb_jobs_*`) so a backup that the CSV missed is still visible, and by main-vs-backup row-count parity.

## Ingestion engine metrics expected (placeholder dashboard `ingestion-engine`)
`ingest_rows_total{table}`, `ingest_batches_total{status}`, `ingest_errors_total{type}`, `ingest_retries_total`, `ingest_batch_duration_seconds` (histogram), `ingest_batch_size_rows` (histogram), `ingest_rows_inflight`, `ingest_queue_depth`, `ingest_last_success_timestamp_seconds`, plus default `process_*`. Serve on `:8000/metrics`.

## Verification
`bash scripts/promtool-check.sh` (config + rules, both envs), `docker compose -f docker-compose.prod.yml config -q`, `python tools/check_dashboards.py`, Prometheus `/targets` and `/rules`.
Gotchas found while building: cAdvisor < v0.53 cannot see containers on the containerd image store; Docker Desktop has no systemd/hwmon/ENA data (those panels are prod-only); `crdb_internal.*` needs `allow_unsafe_internals` in the SQL exporter DSN; node_exporter 1.9.1 has no `swap`/`tainted` collectors (swap comes from `meminfo`/`vmstat`).

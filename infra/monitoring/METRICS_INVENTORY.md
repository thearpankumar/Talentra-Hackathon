# Metrics inventory

Research basis: node_exporter 1.9.1 `--help` and README collector tables, cAdvisor v0.55 `docs/storage/prometheus.md`, HAProxy `addons/promex/README`, Caddy `/docs/metrics`, Docker daemon Prometheus docs, YACE README, and the **live** CockroachDB v26.2 `/_status/vars` output (2,807 series names; family counts below) plus `SHOW JOBS` / `crdb_internal` probing on a running cluster. "Enabled" = collected in prod (L = also local / Docker Desktop). Dashboards: `HostX` Host Extras, `NEF` Node Exporter Full, `Cap` Capacity, `Dock` Docker Containers, `Ovw` Overview, `Alerts` = Prometheus rule.

## 1. node_exporter (v1.9.1)
| Source | Collector | Tells us | Enabled | Why / why not | Used by |
|---|---|---|---|---|---|
| node | cpu, loadavg, stat, schedstat | CPU modes, load, forks, ctx switches, run-queue wait | yes (L) | core | NEF, HostX, Cap; HostHighCPU, HostLoadHigh |
| node | meminfo, vmstat | memory, swap, page faults, reclaim, OOM kills | yes (L) | memory-tight host | NEF, Cap, HostX; HostHighMemory, HostOOMKill |
| node | pressure | PSI cpu/mem/io | yes | early saturation signal | Cap, HostX; HostMemoryPressure, HostCPUPressure, HostIOPressure |
| node | diskstats | IOPS, throughput, latency, queue, busy | yes (L) | EBS saturation | Cap, NEF; HostEBS*, HostDiskBusy |
| node | filesystem | space, inodes | yes (L) | disk-full forecast | Cap, NEF; HostDiskSpace*, HostDiskWillFillIn24h, HostInodesLow |
| node | netdev, netstat, sockstat, softnet | traffic, errors, TCP/UDP counters, drops | yes (L) | | NEF, HostX, Cap; HostNetworkErrors |
| node | conntrack | nf_conntrack entries/limit | yes | Docker NAT | HostX |
| node | filefd, entropy | FD usage, entropy | yes | | HostX; HostFileDescriptorsHigh |
| node | hwmon, thermal_zone | temperature | yes | absent on most VMs, harmless | HostX; HostTemperatureHigh |
| node | timex, time | clock offset/sync | yes | Cockroach needs <400ms | HostX; HostClockSkew |
| node | systemd | unit states, restarts | prod only | no systemd in Docker Desktop; unit allow-list limits cardinality | HostX; HostSystemdUnitFailed |
| node | processes | procs by state, threads, pids | yes | opt-in collector | HostX |
| node | tcpstat | TCP connection states | yes | opt-in; slower under load but fine here | HostX |
| node | ethtool | ENA allowance-exceeded (bw/pps/conntrack), queue stats | prod only | EC2 network throttle visibility; `ens*` only | Cap, HostX |
| node | buddyinfo | memory fragmentation | yes | cheap, opt-in | HostX |
| node | textfile | backup health metrics | yes | custom | Jobs & Backups, HostX; Backup* alerts |
| node | uname, os, time, boot_time (stat), cpufreq, rapl, edac, nvme, dmi, netclass, udp_queues, kernel_hung(n/a in 1.9.1) | static info / hardware | default-on, kept | negligible cost | NEF (info panels) |
| node | interrupts, softirqs, ksmd, mountstats, cgroups, logind, meminfo_numa, slabinfo, sysctl, zoneinfo, qdisc, network_route, perf, wifi, drm, drbd, lnstat, xfrm, cpu_vulnerabilities, ntp, runit, supervisord, pcidevice(n/a) | | **no** | high cardinality (interrupts: 8 cpus x ~100 IRQ), no NFS/KSM/NUMA on a single-socket VM, or needs extra privileges; chrony covered by timex | - |
| node | arp, bcache, bonding, btrfs, fibrechannel, infiniband, ipvs, mdadm, nfs, nfsd, powersupplyclass, tapestats, xfs, zfs | | **no** (explicitly disabled) | hardware/filesystems not present; saves scrape time and series | - |
| node | swap, tainted | | not available in 1.9.1 | swap is in meminfo/vmstat | Cap |

## 2. cAdvisor (v0.55.1)
| Group (`--enable_metrics`) | Series | Tells us | Enabled | Used by |
|---|---|---|---|---|
| cpu | `container_cpu_usage_seconds_total`, `_cfs_throttled_*`, user/system | CPU cores, throttling | yes | Dock, Cap; ContainerCPUThrottled |
| memory | working_set, rss, cache, usage, kernel, mapped_file, failures, max_usage, swap | memory vs limit | yes | Dock, Cap; ContainerMemoryNearLimit |
| oom_event | `container_oom_events_total` | OOM kills | yes | Dock, Cap; ContainerOOMKilled |
| diskIO / disk | `container_fs_*` | IO bytes/time, fs usage, inodes | yes | Dock |
| network | rx/tx bytes, packets, errors, drops | | yes | Dock |
| process | processes, threads, sockets, file_descriptors, ulimits | | yes | Dock |
| always | `container_start_time_seconds`, `_last_seen`, `spec_*` | restarts, limits, uptime | yes | Dock, annotations; ContainerRestarting |
| cpuLoad, sched, percpu, tcp, udp, advtcp, hugetlb, referenced_memory, cpu_topology, resctrl, memory_numa, cpuset, perf_event | | | **no** | high cardinality or intrusive (referenced_memory hurts page reclaim), irrelevant on this VM |
Dropped by relabel: `container_spec_*` (except limits via metric_relabel keep on spec_memory in alerts: kept), `container_tasks_state`, `container_cpu_load_average_10s`, `container_fs_inodes_*`, tcp/udp usage.
Flags: `--docker_only`, `--housekeeping_interval=15s|30s`, `--store_container_labels=false` + whitelisted compose labels, v0.55.1 mandatory for the containerd image store.

## 3. Docker daemon (`metrics-addr`)
`engine_daemon_container_states_containers`, `engine_daemon_container_actions_seconds`, `engine_daemon_events_total`, `engine_daemon_health_checks_total`, `engine_daemon_network_actions_seconds`, `engine_daemon_engine_cpus_cpus`, `engine_daemon_engine_memory_bytes`, `builder_*`, `swarm_*` (unused). Enabled as optional target (needs `daemon.json`); shown on Docker Containers dashboard; not paged. Swarm/builder series ignored (not used).

## 4. CockroachDB native (`/_status/vars`, 2,807 series names)
Family sizes: jobs 348, admission 337, sql 165, storage 148, distsender 141, queue 109, kv 106, rpc 68, raft 63, sys 55, changefeed 52, txn 44, range 39, kvflowcontrol 34, mma 32, schedules 22, storeliveness 19, security 18, logical 18, rocksdb 17, tenant 13, leases 12, replicas 11, ranges 11, gossip 11, cloud 11, rebalancing 10, log 8, obs 7, liveness 5, clock 4, capacity 4 and ~60 single-metric families (livebytes, keybytes, valbytes, intentage, ...). **All of it is scraped** (cost ~5 MB/day/node); dashboards surface the useful subset, the rest is available to ad-hoc Explore and to the 9 official dashboards.
| Area | Key metrics | Dashboard | Alert |
|---|---|---|---|
| SQL | `sql_{select,insert,update,delete,ddl,misc,copy}_count`, `sql_conns`, `sql_new_conns`, `sql_conn_failures`, `sql_bytesin/out`, `sql_txns_open`, `sql_statements_active`, `sql_failure_count`, `sql_txn_{commit,rollback,abort}_count`, `sql_distsql_*`, `sql_mem_*_current`, `sql_stats_*` | SQL, Overview, official SQL | CockroachSQLConnections*, CockroachSQLLatencyHigh |
| SQL latency | `sql_service_latency_bucket` (+`_consistent`, `_historical`), `sql_exec_latency_bucket`, `sql_txn_latency_bucket`, `sql_distsql_service_latency_bucket` | SQL latency | CockroachSQLLatencyHigh (recording p50/95/99) |
| Txn / KV | `txn_commits*`, `txn_restarts_*` (8 causes), `txn_aborts`, `txn_refresh_*`, `intents_*`, `distsender_*`, `kv_*` | Hot ranges and contention, SQL | - |
| Contention | `sql_distsql_contended_queries_count`, `sql_txn_contended_count`, `sql_contention_*`, `kv_concurrency_*` (locks, waiters, latch wait), `txnwaitqueue_*` | Hot and contention | CockroachSlowRequests |
| Storage / Pebble | `capacity*`, `livebytes/keybytes/valbytes`, `rocksdb_*` (read amp, compactions, flushes, sstables, block cache, memtable), `storage_l0_*`, `storage_wal_*`, `storage_disk_*`, `storage_write_stalls`, `storage_compactions_*`, `storage_flush_*` | Storage | CockroachStoreDisk*, CockroachL0SublevelsHigh, CockroachWriteStalls |
| Admission control | `admission_wait_durations_*`, `admission_io_overload`, `admission_granter_*`, `admission_admitted_*`, `admission_errored_*`, `kvflowcontrol_*` | Storage | - |
| Replication | `ranges*`, `replicas*`, `replicas_leaseholders`, `leases_*`, `range_{splits,merges,adds,removes,snapshots_*}`, `queue_replicate_*`, `rebalancing_*`, `raft_*`, `kv_allocator_*` | Replication, Hot | CockroachUnavailableRanges, CockroachUnderReplicatedRanges |
| Liveness / clock | `liveness_*`, `clock_offset_*`, `round_trip_latency_bucket`, `gossip_*` | Replication, Overview | CockroachLiveNodesBelowExpected, CockroachClockOffsetHigh |
| Jobs / backup | `jobs_*` (348), `jobs_backup_*`, `schedules_BACKUP_*`, `backup_last_failed_time_kms_inaccessible` | Jobs and Backups | CockroachBackupJobFailures |
| Changefeeds | `changefeed_*` (52), `schedules_CHANGEFEED_*` | official dashboards only | **not alerted** - changefeeds are not used by Talentra |
| Security | `security_certificate_ttl_*`, `security_certificate_expiration_*`, `seconds_until_enterprise_license_expiry` | Overview | Cockroach*CertExpiring, CockroachLicenseExpiring |
| Runtime | `sys_cpu_*`, `sys_rss`, `sys_goroutines`, `sys_gc_*`, `sys_fd_*`, `sys_uptime`, `sys_host_disk_*` | Overview, official Runtime/Hardware | CockroachNodeRestarted |
| Multi-tenant / logical / physical replication / mma / storeliveness / tenant_* | | official dashboards | **not alerted**: single-tenant, no LDR/PCR |

## 5. CockroachDB SQL-queryable internals (sql_exporter; read-only role)
| Query | Source | Metric | Used by |
|---|---|---|---|
| liveness | `SELECT 1` | `crdb_sql_up` | CockroachSQLExporterBlind |
| job counts | `SHOW JOBS` (24h by type/status; running) | `crdb_jobs_recent`, `crdb_jobs_running` | Jobs, CockroachBackupJobFailures |
| last success + duration | `SHOW JOBS` | `crdb_job_last_success_*` | Jobs |
| sessions, queries, slow | `crdb_internal.cluster_sessions`, `cluster_queries` | `crdb_sessions`, `crdb_queries_running[_over_10s]`, `crdb_oldest_query_age_seconds` | SQL, CockroachLongRunningQueries |
| conn limit | `crdb_internal.cluster_settings` | `crdb_max_connections_per_gateway` | CockroachSQLConnectionsNearLimit |
| contention | `crdb_internal.transaction_contention_events` | `crdb_contention_events_5m`, `crdb_contention_seconds_5m` | Hot |
| database inventory | `crdb_internal.databases` | `crdb_user_databases` | Jobs |
| per-table rows | `SHOW STATISTICS FOR TABLE ...` | `crdb_table_estimated_rows` | **opt-in** (`sql_exporter/optional/`): a query naming a missing table errors, and sql_exporter turns any error into HTTP 500 for the whole target. `crdb_internal.table_row_statistics` and `pg_class.reltuples` were tested and do NOT contain user tables on v26.2. Parity/growth use native `livecount`/`livebytes` instead. |
`SHOW SCHEDULES` was dropped (needs SELECT on system tables; native `schedules_*` metrics cover it). Left out on purpose: `count(*)` row counts (full scans on 100M rows), `crdb_internal.ranges` / `hot_ranges` (expensive; per-node QPS/CPU/write rates from `rebalancing_*` give the same signal), `statement_statistics` top-N (cardinality; use the DB Console behind Caddy for statement drill-down), `kv_store_status` (duplicated by native `capacity*`).

## 6. HAProxy
Chosen: `prom/haproxy-exporter` on the stats CSV (works with the current config, no edit of the other agent's file). Series: `haproxy_{frontend,backend,server}_*` (sessions, rate, bytes, errors, queue, up, check duration/failures, downtime, weight, connect/response time averages) + exporter health. Native `promex` (`USE_PROMEX`, `/metrics` via `http-request use-service prometheus-exporter`) would add `haproxy_process_*`, stick tables, listener and per-code HTTP counters - **not enabled**, needs a config line (INTEGRATION_REQUESTS #6). Dashboard: HAProxy. Alerts: HAProxyBackendDown/ServerDown/ConnectionErrors/SessionsNearLimit/ExporterCannotScrape.

## 7. Caddy
`caddy_http_requests_total`, `_requests_in_flight`, `_request_errors_total`, `caddy_http_request_duration_seconds` (histogram, `code` label), `caddy_http_response_duration_seconds` (TTFB), `_request_size_bytes`, `_response_size_bytes`, `caddy_reverse_proxy_upstreams_healthy`, `caddy_admin_http_*`, `caddy_config_last_reload_*`, Go/process. Enabled: all; request-level series need the `metrics` global option (INTEGRATION_REQUESTS #1). Left out: `per_host` label (cardinality, 4 hosts) - can be turned on later. Dashboard Caddy; alerts CaddyHighServerErrorRate, CaddyHighLatency.

## 8. blackbox_exporter
Modules: `http_2xx`, `http_tls_any` (401/403 OK, TLS required), `http_crdb_main|backup` (internal CA), `tcp_connect`, `pgwire_tls` (SSLRequest -> expects `S`; defined, not scheduled because the public SQL ports are SG-restricted and unreachable from the host itself). Series: `probe_success`, `probe_duration_seconds`, `probe_http_duration_seconds{phase}`, `probe_ssl_earliest_cert_expiry`, `probe_tls_version_info`, `probe_http_status_code`, `probe_http_version`, `probe_dns_lookup_time_seconds`. Not used: ICMP (needs CAP_NET_RAW), DNS prober, gRPC.

## 9. Logs
Alloy `loki.source.docker` (all containers, labels container/compose_project/service/level), `loki.source.journal` (prod: unit, level, host). Not used: file tails of `/var/log/*` (journald covers them), Docker `json-file` raw path (API path is simpler and rotation-safe), CockroachDB `cockroach-data/logs` files (stdout stream already carries them), OpenTelemetry/Tempo traces (no app instrumentation).

## 10. EC2 / EBS via CloudWatch (opt-in profile `cloudwatch`, YACE)
`AWS/EC2`: CPUUtilization, Network{In,Out,PacketsIn,PacketsOut}, EBS{Read,Write}{Ops,Bytes}, StatusCheckFailed{,_System,_Instance}. `AWS/EBS`: Volume{Read,Write}{Ops,Bytes}, VolumeQueueLength, VolumeThroughputPercentage, VolumeConsumedReadWriteOps, VolumeIdleTime, VolumeTotal{Read,Write}Time. Used by Capacity (EBS panel) - not alerted (5-minute resolution; node_exporter gives 15s). Not included: `BurstBalance` (gp2 only), `EBSIOBalance%`/`EBSByteBalance%` (burstable/baseline families, not m6i), CPUCreditBalance (t-family), billing metrics. Not run during acceptance (no AWS credentials in the sandbox by design).

## 11. Stack self-monitoring
Prometheus (`prometheus_tsdb_*`, rule evaluation), Alertmanager (`alertmanager_*`), Loki (`loki_request_duration_seconds`), Alloy, Grafana (`grafana_*`, scraped), exporters' `up`. Alerts: PrometheusRuleEvaluationFailures, PrometheusTSDBNearSizeCap, AlertmanagerNotificationsFailing, LokiRequestErrors, InstanceDown, Watchdog. Grafana/Alloy internals beyond `up` are collected but not charted.

## Deliberate omissions (summary)
interrupts/softirqs/slabinfo/perf (cardinality or privilege), NFS/ZFS/RAID/bonding/IB collectors (no such hardware), cAdvisor perf/resctrl/referenced_memory/percpu (cost/intrusive), Docker swarm/builder metrics, CockroachDB changefeed/LDR/PCR/tenant families (unused features; still scraped), `count(*)` and `crdb_internal.ranges` SQL probes (expensive), native HAProxy promex (needs their config edit), public SQL-port probes from the host (SG hairpin), CloudWatch burst-balance metrics (n/a for gp3/m6i).

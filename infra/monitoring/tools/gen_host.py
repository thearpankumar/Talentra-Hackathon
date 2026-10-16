"""Talentra overview, alerts, capacity, host extras, docker containers dashboards."""
from dashlib import *

FS = 'fstype!~"tmpfs|overlay|squashfs|ramfs|nsfs|fuse.*|vfat|iso9660",mountpoint!~"/(boot|run|sys|dev|proc|host/(run|sys|dev|proc)).*|/var/lib/docker/.+|/etc/(hosts|hostname|resolv.conf)"'
DEV = 'device!~"loop.*|ram.*|dm-.*|sr.*|fd.*"'
NET = 'device!~"lo|veth.*|docker.*|br-.*|flannel.*|cali.*"'


def overview():
    d = Dash("talentra-overview", "Talentra Overview", ["overview", "home"],
             "One-screen health summary: targets, alerts, CockroachDB, backups, edge, host.", refresh="30s", time_from="now-3h")
    d.std_annotations(backups=True)
    d.var_query("cluster", 'label_values(up{job="cockroachdb"}, cluster)', label="CockroachDB cluster")
    d.row("Health at a glance")
    d.stat("Targets up", 'sum(up{optional!="true"})', w=3, h=4, thresholds=steps((None, GREEN)), desc="Scrape targets currently UP (optional integrations excluded).", graph=False)
    d.stat("Targets down", 'count(up{optional!="true"} == 0) or vector(0)', w=3, h=4, thresholds=BADOK, graph=False,
           desc="Non-optional scrape targets that are DOWN. See Alerts dashboard / Prometheus /targets.")
    d.stat("Critical alerts", 'count(ALERTS{alertstate="firing",severity="critical"}) or vector(0)', w=3, h=4, thresholds=BADOK, graph=False,
           links=[{"title": "Alerts overview", "url": "/d/talentra-alerts"}])
    d.stat("Warning alerts", 'count(ALERTS{alertstate="firing",severity="warning"}) or vector(0)', w=3, h=4, thresholds=steps((None, GREEN), (1, YELLOW)), graph=False)
    d.stat("CRDB live nodes", 'max(liveness_livenodes{job="cockroachdb",cluster=~"$cluster"})', w=3, h=4, thresholds=steps((None, RED), (2, YELLOW), (3, GREEN)), graph=False,
           desc="Nodes in the liveness range (main expects 3, backup 1).", links=[{"title": "CockroachDB overview", "url": "/d/crdb-talentra-overview"}])
    d.stat("Unavailable / under-replicated ranges", 'sum(ranges_unavailable{job="cockroachdb",cluster=~"$cluster"}) + sum(ranges_underreplicated{job="cockroachdb",cluster=~"$cluster"})', w=3, h=4,
           thresholds=BADOK, graph=False, links=[{"title": "Replication", "url": "/d/crdb-replication"}])
    d.stat("Newest backup age", 'time() - max(talentra_backup_last_success_timestamp_seconds{op="backup"})', unit="s", w=3, h=4, graph=False,
           thresholds=steps((None, GREEN), (3600, YELLOW), (7200, RED)), desc="RPO target 2h (talentra:backup_rpo_target_seconds).",
           links=[{"title": "Jobs & backups", "url": "/d/crdb-jobs-backups"}])
    d.stat("Min TLS cert validity", 'min(probe_ssl_earliest_cert_expiry{job="blackbox-http"} - time())', unit="s", w=3, h=4, graph=False,
           thresholds=steps((None, RED), (14 * 86400, YELLOW), (30 * 86400, GREEN)), desc="Soonest public TLS expiry (blackbox).", links=[{"title": "Uptime", "url": "/d/blackbox-uptime"}])
    d.row("Traffic and performance")
    d.ts("SQL statements / s", [('sum by (cluster) (rate(sql_select_count{job="cockroachdb",cluster=~"$cluster"}[$__rate_interval]))', "{{cluster}} select"),
                                ('sum by (cluster) (rate(sql_insert_count{job="cockroachdb",cluster=~"$cluster"}[$__rate_interval]))', "{{cluster}} insert"),
                                ('sum by (cluster) (rate(sql_update_count{job="cockroachdb",cluster=~"$cluster"}[$__rate_interval]))', "{{cluster}} update"),
                                ('sum by (cluster) (rate(sql_delete_count{job="cockroachdb",cluster=~"$cluster"}[$__rate_interval]))', "{{cluster}} delete")], unit="ops", w=8, stack=True)
    d.ts("SQL service latency p50 / p99", [('cluster:crdb_sql_service_latency_seconds:p50{cluster=~"$cluster"}', "{{cluster}} p50"),
                                          ('cluster:crdb_sql_service_latency_seconds:p99{cluster=~"$cluster"}', "{{cluster}} p99")], unit="s", w=8,
         thresholds=steps((None, GREEN), (1, RED)))
    d.ts("HTTP probe availability", ['probe_success{job="blackbox-http"}'], unit="none", w=8, minv=0, maxv=1.1, legend="none")
    d.row("Host")
    d.gauge("Host CPU", 'instance:node_cpu_utilisation:ratio', unit="percentunit", maxv=1, thresholds=steps((None, GREEN), (0.7, YELLOW), (0.85, RED)), w=4)
    d.gauge("Host memory used", 'instance:node_memory_utilisation:ratio', unit="percentunit", maxv=1, thresholds=steps((None, GREEN), (0.8, YELLOW), (0.92, RED)), w=4)
    d.gauge("Fullest filesystem used", f'max(1 - node_filesystem_avail_bytes{{{FS}}} / node_filesystem_size_bytes{{{FS}}})', unit="percentunit", maxv=1,
            thresholds=steps((None, GREEN), (0.7, YELLOW), (0.85, RED)), w=4,
            desc="Worst real filesystem. Prod has one 500GB gp3 root volume; Cockroach wants >30% free.")
    d.gauge("EBS IOPS vs 6000", 'instance:node_disk_iops:rate5m / scalar(talentra:ebs_iops_limit)', unit="percentunit", maxv=1.2, thresholds=steps((None, GREEN), (0.7, YELLOW), (0.9, RED)), w=4)
    d.gauge("EBS throughput vs 300MB/s", 'instance:node_disk_throughput_bytes:rate5m / scalar(talentra:ebs_throughput_limit_bytes)', unit="percentunit", maxv=1.2, thresholds=steps((None, GREEN), (0.7, YELLOW), (0.9, RED)), w=4)
    d.gauge("Containers up", 'count(time() - container_last_seen{name!=""} < 60)', unit="none", maxv=40, w=4, thresholds=steps((None, GREEN)))
    d.row("Firing alerts")
    d.table("Currently firing", [('ALERTS{alertstate="firing",alertname!="Watchdog"}', "")], w=24, h=8,
            hide=["Time", "__name__", "alertstate", "Value", "job"], sort_by="severity")
    return d


def alerts():
    d = Dash("talentra-alerts", "Alerts Overview", ["alerts"], "Prometheus alert state, history, Alertmanager health.", time_from="now-24h")
    d.std_annotations(restarts=False)
    d.row("Now")
    d.stat("Critical firing", 'count(ALERTS{alertstate="firing",severity="critical"}) or vector(0)', w=4, thresholds=BADOK, graph=False)
    d.stat("Warning firing", 'count(ALERTS{alertstate="firing",severity="warning"}) or vector(0)', w=4, thresholds=steps((None, GREEN), (1, YELLOW)), graph=False)
    d.stat("Pending", 'count(ALERTS{alertstate="pending"}) or vector(0)', w=4, thresholds=steps((None, GREEN), (1, BLUE)), graph=False)
    d.stat("Watchdog (pipeline heartbeat)", 'max(ALERTS{alertname="Watchdog",alertstate="firing"}) or vector(0)', w=4, thresholds=OKBAD, graph=False,
           mappings=[{"type": "value", "options": {"0": {"text": "NOT FIRING", "color": "red"}, "1": {"text": "OK", "color": "green"}}}],
           desc="Must always be firing. If 0, rule evaluation is broken.")
    d.stat("Notifications failed (1h)", 'sum(increase(alertmanager_notifications_failed_total[1h])) or vector(0)', w=4, thresholds=BADOK, graph=False)
    d.stat("Silences active", 'sum(alertmanager_silences{state="active"}) or vector(0)', w=4, thresholds=steps((None, GREEN), (1, BLUE)), graph=False)
    d.row("Firing now")
    d.table("Alerts", [('ALERTS{alertstate=~"firing|pending",alertname!="Watchdog"}', "")], w=24, h=9, hide=["Time", "__name__", "Value", "job"], sort_by="severity")
    d.row("History")
    d.ts("Firing alerts by severity", ['count by (severity) (ALERTS{alertstate="firing",alertname!="Watchdog"})'], unit="short", w=12, stack=True,
         overrides=[{"matcher": {"id": "byName", "options": "critical"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
                    {"matcher": {"id": "byName", "options": "warning"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "yellow"}}]}])
    d.ts("Firing alerts by name", ['sum by (alertname) (ALERTS{alertstate="firing",alertname!="Watchdog"})'], unit="short", w=12, stack=True, legend="table")
    d.row("Alertmanager and rule engine")
    d.ts("Notifications sent / failed per integration", ['sum by (integration) (rate(alertmanager_notifications_total[$__rate_interval]))',
                                                         'sum by (integration) (rate(alertmanager_notifications_failed_total[$__rate_interval]))'], unit="ops", w=8)
    d.ts("Alerts received by Alertmanager", ['sum by (state) (alertmanager_alerts)'], unit="short", w=8)
    d.ts("Rule evaluation duration & failures", ['sum by (rule_group) (prometheus_rule_group_last_duration_seconds)', 'sum(rate(prometheus_rule_evaluation_failures_total[$__rate_interval]))'], unit="s", w=8)
    d.table("Alert rules inventory (Prometheus)", [('count by (alertname) (ALERTS_FOR_STATE)', "")], w=24, h=6, hide=["Time", "__name__"])
    return d


def capacity():
    d = Dash("talentra-capacity", "Capacity and Forecast", ["capacity", "forecast"],
             "Disk-full prediction, memory pressure, EBS IOPS/throughput saturation vs gp3 6000 IOPS / 300 MB/s, memory budget.", time_from="now-24h")
    d.std_annotations()
    d.var_custom("horizon", ["1d", "3d", "7d", "14d", "30d"], label="Forecast horizon", default="7d")
    d.var_custom("lookback", ["1h", "6h", "12h", "1d", "3d"], label="Trend lookback", default="6h")
    d.row("Disk")
    d.stat("Days until the first filesystem is full (linear trend)",
           f'min(node_filesystem_avail_bytes{{{FS}}} / clamp_min(-deriv(node_filesystem_avail_bytes{{{FS}}}[$lookback]), 1e-9)) / 86400',
           unit="d", w=6, thresholds=steps((None, RED), (2, YELLOW), (7, GREEN)), decimals=1, graph=False,
           desc="avail / consumption rate over the trend lookback, worst filesystem. Very large = not shrinking. Empty = no filesystem is shrinking.")
    d.ts("Free space and predict_linear forecast", [
        (f'node_filesystem_avail_bytes{{{FS}}}', "free {{mountpoint}}"),
        (f'predict_linear(node_filesystem_avail_bytes{{{FS}}}[$lookback], 86400)', "in 24h {{mountpoint}}"),
        (f'predict_linear(node_filesystem_avail_bytes{{{FS}}}[$lookback], 7*86400)', "in 7d {{mountpoint}}")], unit="bytes", w=18, h=8,
         desc="Dotted projections below zero mean the filesystem fills inside that horizon (alert: HostDiskWillFillIn24h).")
    d.ts("Filesystem used %", [(f'1 - node_filesystem_avail_bytes{{{FS}}} / node_filesystem_size_bytes{{{FS}}}', "{{mountpoint}}")], unit="percentunit", w=8, minv=0, maxv=1,
         thresholds=steps((None, GREEN), (0.7, YELLOW), (0.85, RED)))
    d.ts("Inodes used %", [(f'1 - node_filesystem_files_free{{{FS}}} / (node_filesystem_files{{{FS}}} > 0)', "{{mountpoint}}")], unit="percentunit", w=8, minv=0, maxv=1)
    d.ts("CockroachDB store used % (per node)", [('instance:crdb_store_used_ratio', "{{cluster}} {{instance}}")], unit="percentunit", w=8, minv=0, maxv=1,
         thresholds=steps((None, GREEN), (0.6, YELLOW), (0.7, RED)), desc="Guidance: stay under ~60% full.")
    d.ts("Store bytes growth per node (CockroachDB)", [('deriv(capacity_used{job="cockroachdb"}[$lookback]) * 3600', "{{cluster}} {{instance}}")], unit="bytes", w=12, desc="Bytes per hour.")
    d.ts("Live key growth per hour (CockroachDB)", [('deriv(sum by (cluster) (livecount{job="cockroachdb"})[$lookback:1m]) * 3600', "{{cluster}}")], unit="short", w=12, desc="Keys per hour across all replicas.")
    d.row("EBS saturation (gp3: 6000 IOPS, 300 MB/s provisioned)")
    d.ts("IOPS vs limit", [('instance:node_disk_iops:rate5m', "IOPS (read+write)"), ('rate(node_disk_reads_completed_total{%s}[$__rate_interval])' % DEV, "read {{device}}"),
                           ('rate(node_disk_writes_completed_total{%s}[$__rate_interval])' % DEV, "write {{device}}"), ('scalar(talentra:ebs_iops_limit)', "gp3 limit")], unit="iops", w=12,
         overrides=[{"matcher": {"id": "byName", "options": "gp3 limit"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}, {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}}, {"id": "custom.fillOpacity", "value": 0}]}])
    d.ts("Throughput vs limit", [('instance:node_disk_throughput_bytes:rate5m', "total"), ('sum(rate(node_disk_read_bytes_total{%s}[$__rate_interval]))' % DEV, "read"),
                                 ('sum(rate(node_disk_written_bytes_total{%s}[$__rate_interval]))' % DEV, "write"), ('scalar(talentra:ebs_throughput_limit_bytes)', "gp3 limit")], unit="Bps", w=12,
         overrides=[{"matcher": {"id": "byName", "options": "gp3 limit"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}, {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}}, {"id": "custom.fillOpacity", "value": 0}]}])
    d.ts("Disk busy % (io_time)", [('rate(node_disk_io_time_seconds_total{%s}[$__rate_interval])' % DEV, "{{device}}")], unit="percentunit", w=8, minv=0, maxv=1.05)
    d.ts("Avg queue depth", [('rate(node_disk_io_time_weighted_seconds_total{%s}[$__rate_interval])' % DEV, "{{device}}")], unit="short", w=8)
    d.ts("Avg IO latency (read / write)", [('rate(node_disk_read_time_seconds_total{%s}[$__rate_interval]) / clamp_min(rate(node_disk_reads_completed_total{%s}[$__rate_interval]), 1e-9)' % (DEV, DEV), "read {{device}}"),
                                           ('rate(node_disk_write_time_seconds_total{%s}[$__rate_interval]) / clamp_min(rate(node_disk_writes_completed_total{%s}[$__rate_interval]), 1e-9)' % (DEV, DEV), "write {{device}}")], unit="s", w=8)
    d.ts("CockroachDB disk stalls / slow I/O", [('storage_disk_slow{job="cockroachdb"}', "slow {{instance}}"), ('increase(storage_write_stalls{job="cockroachdb"}[$__rate_interval])', "write stalls {{instance}}"),
                                                ('storage_disk_stalled{job="cockroachdb"}', "stalled {{instance}}")], unit="short", w=12)
    d.ts("CloudWatch: EBS volume IOPS / throughput % (opt-in profile)", [('aws_ebs_volume_read_ops_sum + aws_ebs_volume_write_ops_sum', "ops/5m {{dimension_VolumeId}}"),
                                                                         ('aws_ebs_volume_throughput_percentage_average', "throughput% {{dimension_VolumeId}}")], unit="short", w=12,
         desc="Empty unless the cloudwatch compose profile is running (yet-another-cloudwatch-exporter).")
    d.row("Memory (32 GiB host, tight budget)")
    d.ts("Memory breakdown", [('node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes', "used (total - available)"), ('node_memory_Cached_bytes', "page cache"),
                              ('node_memory_MemAvailable_bytes', "available"), ('node_memory_SwapCached_bytes + (node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes)', "swap used")], unit="bytes", w=12)
    d.ts("Memory pressure (PSI)", [('rate(node_pressure_memory_waiting_seconds_total[$__rate_interval])', "some (waiting)"), ('rate(node_pressure_memory_stalled_seconds_total[$__rate_interval])', "full (stalled)")],
         unit="percentunit", w=12, thresholds=steps((None, GREEN), (0.1, RED)), desc="Share of time tasks were stalled on memory. >10% sustained = alert HostMemoryPressure.")
    d.ts("Container memory (working set) vs 32 GiB budget", [('sort_desc(container:memory_working_set_bytes)', "{{name}}")], unit="bytes", w=12, stack=True, legend="table", h=10)
    d.table("Container memory vs limit", [('container:memory_working_set_bytes', ""), ('max by (name) (container_spec_memory_limit_bytes{name!=""} > 0)', "")], w=12, h=10, unit="bytes",
            hide=["Time", "__name__"], rename={"Value #A": "working set", "Value #B": "limit"}, sort_by="working set")
    d.ts("OOM kills (kernel) and container OOM events", [('increase(node_vmstat_oom_kill[$__rate_interval])', "kernel OOM kills"), ('sum by (name) (increase(container_oom_events_total{name!=""}[$__rate_interval]))', "container OOM {{name}}")], unit="short", w=12)
    d.ts("Swap in/out (should be zero: swap off per Cockroach guidance)", [('rate(node_vmstat_pswpin[$__rate_interval])', "pages in"), ('rate(node_vmstat_pswpout[$__rate_interval])', "pages out")], unit="short", w=12)
    d.row("CPU")
    d.ts("CPU utilisation and PSI", [('instance:node_cpu_utilisation:ratio', "utilisation"), ('rate(node_pressure_cpu_waiting_seconds_total[$__rate_interval])', "PSI waiting")], unit="percentunit", w=12, minv=0)
    d.ts("Container CPU cores (top)", [('topk(10, container:cpu_usage_cores:rate5m)', "{{name}}")], unit="short", w=12, stack=True, legend="table")
    d.row("Network and EC2 limits (ENA)")
    d.ts("Network throughput", [('instance:node_network_receive_bytes:rate5m', "rx"), ('instance:node_network_transmit_bytes:rate5m', "tx")], unit="Bps", w=12)
    d.ts("ENA allowance exceeded (ethtool): packets dropped by EC2 limits", [('rate(node_ethtool_bw_in_allowance_exceeded[$__rate_interval])', "bw in {{device}}"), ('rate(node_ethtool_bw_out_allowance_exceeded[$__rate_interval])', "bw out {{device}}"),
                                                                       ('rate(node_ethtool_pps_allowance_exceeded[$__rate_interval])', "pps {{device}}"), ('rate(node_ethtool_conntrack_allowance_exceeded[$__rate_interval])', "conntrack {{device}}"),
                                                                       ('rate(node_ethtool_linklocal_allowance_exceeded[$__rate_interval])', "linklocal {{device}}")], unit="pps", w=12,
         desc="Non-zero means the instance hit an ENA network limit. Prod only (ethtool collector).")
    return d


def host_extras():
    d = Dash("host-extras", "Host Extras", ["host", "node-exporter"],
             "Node exporter collectors not shown on Node Exporter Full: systemd, TCP states, processes, PSI, hwmon, entropy, FDs, clock, backups textfile.", time_from="now-3h")
    d.std_annotations()
    d.row("systemd")
    d.table("Units not active", [('node_systemd_unit_state{state=~"failed|activating|deactivating"} == 1', "")], w=12, h=7, hide=["Time", "__name__", "job", "Value"],
            desc="Prod only (systemd collector is not available on Docker Desktop).")
    d.ts("systemd units by state", [('count by (state) (node_systemd_unit_state == 1)', "{{state}}")], unit="short", w=6)
    d.ts("systemd socket connections / services restarts", [('rate(node_systemd_service_restart_total[$__rate_interval])', "restarts {{name}}"), ('node_systemd_system_running', "system running")], unit="short", w=6)
    d.row("TCP / sockets")
    d.ts("TCP connection states (tcpstat)", [('node_tcp_connection_states', "{{state}}")], unit="short", w=8, stack=True, legend="right")
    d.ts("Sockets in use (sockstat)", [('node_sockstat_sockets_used', "all"), ('node_sockstat_TCP_inuse', "tcp inuse"), ('node_sockstat_TCP_tw', "tcp time_wait"), ('node_sockstat_UDP_inuse', "udp")], unit="short", w=8)
    d.ts("TCP retransmits / resets / listen overflows", [('rate(node_netstat_Tcp_RetransSegs[$__rate_interval])', "retrans seg/s"), ('rate(node_netstat_TcpExt_TCPTimeouts[$__rate_interval])', "RTO timeouts/s"), ('rate(node_netstat_Tcp_ActiveOpens[$__rate_interval])', "active opens/s"), ('rate(node_netstat_Tcp_PassiveOpens[$__rate_interval])', "passive opens/s"),
                                                         ('rate(node_netstat_TcpExt_ListenOverflows[$__rate_interval])', "listen overflows/s"), ('rate(node_netstat_TcpExt_ListenDrops[$__rate_interval])', "listen drops/s")], unit="short", w=8)
    d.ts("Network errors and drops", [('rate(node_network_receive_errs_total{%s}[$__rate_interval])' % NET, "rx err {{device}}"), ('rate(node_network_transmit_errs_total{%s}[$__rate_interval])' % NET, "tx err {{device}}"),
                                       ('rate(node_network_receive_drop_total{%s}[$__rate_interval])' % NET, "rx drop {{device}}"), ('rate(node_softnet_dropped_total[$__rate_interval])', "softnet drop")], unit="short", w=12)
    d.ts("conntrack", [('node_nf_conntrack_entries', "entries"), ('node_nf_conntrack_entries_limit', "limit")], unit="short", w=12)
    d.row("Processes and kernel")
    d.ts("Processes by state (processes collector)", [('node_processes_state', "{{state}}"), ], unit="short", w=8, stack=True)
    d.ts("Threads and PIDs", [('node_processes_threads', "threads"), ('node_processes_pids', "pids"), ('node_processes_max_processes', "max processes"), ('node_processes_max_threads', "max threads")], unit="short", w=8)
    d.ts("Forks / context switches / interrupts", [('rate(node_forks_total[$__rate_interval])', "forks/s"), ('rate(node_context_switches_total[$__rate_interval])', "ctx switches/s"), ('rate(node_intr_total[$__rate_interval])', "interrupts/s")], unit="ops", w=8)
    d.ts("File descriptors", [('node_filefd_allocated', "allocated"), ('node_filefd_maximum', "max")], unit="short", w=8)
    d.ts("Entropy available", [('node_entropy_available_bits', "bits")], unit="short", w=8, thresholds=steps((None, RED), (256, GREEN)))
    d.ts("Scheduler: run queue wait", [('rate(node_schedstat_waiting_seconds_total[$__rate_interval])', "waiting cpu{{cpu}}"), ], unit="s", w=8, legend="none")
    d.ts("Pressure stall information (all resources)", [('rate(node_pressure_cpu_waiting_seconds_total[$__rate_interval])', "cpu some"), ('rate(node_pressure_memory_waiting_seconds_total[$__rate_interval])', "memory some"),
                                                        ('rate(node_pressure_memory_stalled_seconds_total[$__rate_interval])', "memory full"), ('rate(node_pressure_io_waiting_seconds_total[$__rate_interval])', "io some"),
                                                        ('rate(node_pressure_io_stalled_seconds_total[$__rate_interval])', "io full")], unit="percentunit", w=12)
    d.ts("Page faults, reclaim, THP (vmstat)", [('rate(node_vmstat_pgmajfault[$__rate_interval])', "major faults/s"), ('rate(node_vmstat_pgfault[$__rate_interval])', "minor faults/s"), ('node_memory_AnonHugePages_bytes', "AnonHugePages bytes")], unit="short", w=12)
    d.ts("Memory fragmentation (buddyinfo, free blocks per zone)", [('sum by (node, zone) (node_buddyinfo_blocks)', "{{node}}/{{zone}}"), ], unit="short", w=12, legend="none")
    d.row("Hardware, time")
    d.ts("Temperature (hwmon / thermal_zone) - n/a on most VMs", [('node_hwmon_temp_celsius', "{{chip}} {{sensor}}"), ('node_thermal_zone_temp', "zone {{zone}}")], unit="celsius", w=12, thresholds=steps((None, GREEN), (75, YELLOW), (85, RED)), legend="none")
    d.ts("Clock: offset and sync (timex)", [('node_timex_offset_seconds', "offset"), ('node_timex_sync_status', "synced"), ('node_timex_maxerror_seconds', "max error")], unit="s", w=12,
         desc="Cockroach nodes self-terminate at ~400ms mean offset.")
    d.ts("Boot time / uptime", [('time() - node_boot_time_seconds', "uptime")], unit="s", w=6, legend="none")
    d.ts("Exporter health: collector duration / success", [('node_scrape_collector_duration_seconds', "{{collector}}"), ], unit="s", w=9, legend="none")
    d.ts("Collector failures", [('node_scrape_collector_success == 0', "failed {{collector}}"), ('node_textfile_scrape_error', "textfile scrape error")], unit="short", w=9, legend="none")
    d.row("Backup-handler textfile metrics (see Jobs & Backups)")
    d.ts("Backup exporter freshness", [('time() - talentra_backup_exporter_last_scan_timestamp_seconds', "seconds since last scan"), ('talentra_backup_csv_present', "csv present")], unit="short", w=12)
    d.ts("Backup volume size", [('talentra_backup_share_bytes', "bytes under /backups")], unit="bytes", w=12, legend="none")
    return d


def docker_containers():
    d = Dash("docker-containers", "Docker Containers", ["docker", "cadvisor"], "Per-container CPU, memory, IO, network, restarts, OOM (cAdvisor) and Docker daemon metrics.", time_from="now-3h")
    d.std_annotations()
    d.var_query("container", 'label_values(container_last_seen{name!=""}, name)', label="Container")
    C = 'name!="",name=~"$container"'
    d.row("Summary")
    d.stat("Running containers", 'count(time() - container_last_seen{name!=""} < 60)', w=4, graph=False)
    d.stat("OOM kills (24h)", 'sum(increase(container_oom_events_total{name!=""}[24h])) or vector(0)', w=4, thresholds=BADOK, graph=False)
    d.stat("Restarts (24h)", 'sum(changes(container_start_time_seconds{name!=""}[24h])) or vector(0)', w=4, thresholds=steps((None, GREEN), (1, YELLOW), (10, RED)), graph=False)
    d.stat("Total CPU cores", 'sum(container:cpu_usage_cores:rate5m)', w=4, unit="short", decimals=2, graph=True)
    d.stat("Total memory (working set)", 'sum(container:memory_working_set_bytes)', w=4, unit="bytes")
    d.stat("Container FS usage", 'sum(container_fs_usage_bytes{name!=""})', w=4, unit="bytes")
    d.row("CPU")
    d.ts("CPU usage (cores)", [(f'sum by (name) (rate(container_cpu_usage_seconds_total{{{C}}}[$__rate_interval]))', "{{name}}")], unit="short", w=12, stack=True, legend="table")
    d.ts("CPU throttling (% of periods)", [(f'rate(container_cpu_cfs_throttled_periods_total{{{C}}}[$__rate_interval]) / rate(container_cpu_cfs_periods_total{{{C}}}[$__rate_interval])', "{{name}}")], unit="percentunit", w=12, minv=0, maxv=1,
         desc="Only containers with a CPU limit (prod).")
    d.row("Memory")
    d.ts("Working set", [(f'container_memory_working_set_bytes{{{C}}}', "{{name}}")], unit="bytes", w=12, legend="table", stack=True)
    d.ts("Memory % of limit", [(f'container_memory_working_set_bytes{{{C}}} / (container_spec_memory_limit_bytes{{{C}}} > 0)', "{{name}}")], unit="percentunit", w=12, minv=0, maxv=1.05,
         thresholds=steps((None, GREEN), (0.8, YELLOW), (0.92, RED)))
    d.ts("RSS vs cache vs kernel", [(f'sum(container_memory_rss{{{C}}})', "rss"), (f'sum(container_memory_cache{{{C}}})', "page cache"), (f'sum(container_memory_kernel_usage{{{C}}})', "kernel"),
                                   (f'sum(container_memory_mapped_file{{{C}}})', "mapped file")], unit="bytes", w=12, stack=True)
    d.ts("Memory failures (page faults) and OOM events", [(f'sum by (name) (rate(container_memory_failures_total{{{C}}}[$__rate_interval]))', "fail {{name}}"), (f'sum by (name) (increase(container_oom_events_total{{{C}}}[$__rate_interval]))', "OOM {{name}}")], unit="short", w=12)
    d.row("Disk IO")
    d.ts("Read bytes/s", [(f'sum by (name) (rate(container_fs_reads_bytes_total{{{C}}}[$__rate_interval]))', "{{name}}")], unit="Bps", w=12, legend="table")
    d.ts("Write bytes/s", [(f'sum by (name) (rate(container_fs_writes_bytes_total{{{C}}}[$__rate_interval]))', "{{name}}")], unit="Bps", w=12, legend="table")
    d.ts("IO time % (busy)", [(f'sum by (name) (rate(container_fs_io_time_seconds_total{{{C}}}[$__rate_interval]))', "{{name}}")], unit="percentunit", w=12)
    d.ts("Container filesystem usage", [(f'sum by (name) (container_fs_usage_bytes{{{C}}})', "{{name}}")], unit="bytes", w=12)
    d.row("Network")
    d.ts("Receive", [(f'sum by (name) (rate(container_network_receive_bytes_total{{{C}}}[$__rate_interval]))', "{{name}}")], unit="Bps", w=12, legend="table")
    d.ts("Transmit", [(f'sum by (name) (rate(container_network_transmit_bytes_total{{{C}}}[$__rate_interval]))', "{{name}}")], unit="Bps", w=12, legend="table")
    d.ts("Packet drops and errors", [(f'sum by (name) (rate(container_network_receive_packets_dropped_total{{{C}}}[$__rate_interval]) + rate(container_network_transmit_packets_dropped_total{{{C}}}[$__rate_interval]))', "drop {{name}}"),
                                    (f'sum by (name) (rate(container_network_receive_errors_total{{{C}}}[$__rate_interval]) + rate(container_network_transmit_errors_total{{{C}}}[$__rate_interval]))', "err {{name}}")], unit="pps", w=12)
    d.row("Processes")
    d.ts("Processes / threads / sockets / FDs", [(f'sum by (name) (container_processes{{{C}}})', "procs {{name}}"), (f'sum by (name) (container_threads{{{C}}})', "threads {{name}}"),
                                                (f'sum by (name) (container_sockets{{{C}}})', "sockets {{name}}"), (f'sum by (name) (container_file_descriptors{{{C}}})', "fds {{name}}")], unit="short", w=12, legend="none")
    d.ts("Container age (uptime) and last seen", [(f'time() - container_start_time_seconds{{{C}}}', "up {{name}}")], unit="s", w=12, legend="none")
    d.row("Docker daemon (optional: daemon.json metrics-addr)")
    d.ts("Containers by state", [('engine_daemon_container_states_containers', "{{state}}")], unit="short", w=8, desc="Empty until the daemon exposes metrics (see README).")
    d.ts("Daemon actions (rate)", [('sum by (action) (rate(engine_daemon_container_actions_seconds_count[$__rate_interval]))', "{{action}}")], unit="ops", w=8)
    d.ts("Daemon events / health checks / network actions", [('rate(engine_daemon_events_total[$__rate_interval])', "events"), ('sum(rate(engine_daemon_health_checks_total[$__rate_interval]))', "health checks"),
                                                            ('sum by (action) (rate(engine_daemon_network_actions_seconds_count[$__rate_interval]))', "net {{action}}")], unit="ops", w=8)
    return d


def build_all(out):
    for fn, folder in [(overview, "Talentra"), (alerts, "Talentra"), (capacity, "Talentra"), (host_extras, "Host and Containers"), (docker_containers, "Host and Containers")]:
        fn().save(os.path.join(out, folder))

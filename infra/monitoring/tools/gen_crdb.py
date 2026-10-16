"""Custom CockroachDB dashboards (complementing the vendored official 'CRDB Console' set)."""
from dashlib import *

J = 'job="cockroachdb"'
SEL = f'{J},cluster=~"$cluster",instance=~"$node"'
R = "$__rate_interval"


def _vars(d):
    d.var_query("cluster", 'label_values(up{job="cockroachdb"}, cluster)', label="Cluster")
    d.var_query("node", 'label_values(sys_uptime{job="cockroachdb",cluster=~"$cluster"}, instance)', label="Node")


def overview():
    d = Dash("crdb-talentra-overview", "CockroachDB Overview (Talentra)", ["cockroachdb", "overview"],
             "Cluster health, throughput, latency, capacity, certificates. Official detailed dashboards live in the same folder.", time_from="now-3h")
    d.std_annotations(backups=True)
    _vars(d)
    d.row("Cluster health")
    d.stat("Nodes scraped up", f'count(up{{{J},cluster=~"$cluster"}} == 1)', w=3, thresholds=steps((None, RED), (1, GREEN)), graph=False)
    d.stat("Live nodes (liveness)", f'max(liveness_livenodes{{{J},cluster=~"$cluster"}})', w=3, thresholds=steps((None, RED), (2, YELLOW), (3, GREEN)), graph=False)
    d.stat("Unavailable ranges", f'sum(ranges_unavailable{{{J},cluster=~"$cluster"}})', w=3, thresholds=BADOK, graph=False)
    d.stat("Under-replicated", f'sum(ranges_underreplicated{{{J},cluster=~"$cluster"}})', w=3, thresholds=steps((None, GREEN), (1, YELLOW)), graph=False)
    d.stat("Total ranges", f'max(ranges{{{J},cluster=~"$cluster"}})', w=3, graph=False, color_mode="value")
    d.stat("Max clock offset", f'max(clock_offset_meannanos{{{SEL}}}) / 1e9', unit="s", w=3, thresholds=steps((None, GREEN), (0.1, YELLOW), (0.25, RED)), decimals=3, graph=False)
    d.stat("Open SQL connections", f'sum(sql_conns{{{SEL}}})', w=3, graph=True, color_mode="value")
    d.stat("Min node cert TTL", f'min(security_certificate_ttl_node{{{SEL}}} > 0)', unit="s", w=3, thresholds=steps((None, RED), (14 * 86400, YELLOW), (30 * 86400, GREEN)), graph=False)
    d.row("Throughput and latency")
    d.ts("QPS by statement type", [(f'sum(rate(sql_select_count{{{SEL}}}[{R}]))', "select"), (f'sum(rate(sql_insert_count{{{SEL}}}[{R}]))', "insert"),
                                   (f'sum(rate(sql_update_count{{{SEL}}}[{R}]))', "update"), (f'sum(rate(sql_delete_count{{{SEL}}}[{R}]))', "delete"), (f'sum(rate(sql_ddl_count{{{SEL}}}[{R}]))', "ddl")], unit="ops", w=8, stack=True)
    d.ts("SQL service latency (p50/p95/p99)", [(f'histogram_quantile({q}, sum by (le) (rate(sql_service_latency_bucket{{{SEL}}}[{R}])))', f"p{int(q*100)}") for q in (0.5, 0.95, 0.99)], unit="ns", w=8)
    d.ts("Txn commits / rollbacks / aborts", [(f'sum(rate(sql_txn_commit_count{{{SEL}}}[{R}]))', "commit"), (f'sum(rate(sql_txn_rollback_count{{{SEL}}}[{R}]))', "rollback"),
                                              (f'sum(rate(sql_txn_abort_count{{{SEL}}}[{R}]))', "abort"), (f'sum(rate(sql_failure_count{{{SEL}}}[{R}]))', "stmt failures")], unit="ops", w=8)
    d.ts("QPS per node (balance across gateways)", [(f'sum by (instance) (rate(sql_select_count{{{SEL}}}[{R}]) + rate(sql_insert_count{{{SEL}}}[{R}]) + rate(sql_update_count{{{SEL}}}[{R}]) + rate(sql_delete_count{{{SEL}}}[{R}]))', "{{instance}}")], unit="ops", w=12)
    d.ts("SQL connections per node", [(f'sql_conns{{{SEL}}}', "{{instance}}"), (f'rate(sql_new_conns{{{SEL}}}[{R}])', "new/s {{instance}}"), (f'rate(sql_conn_failures{{{SEL}}}[{R}])', "failures/s {{instance}}")], unit="short", w=12)
    d.row("Resources")
    d.ts("CPU % (cockroach process, normalized)", [(f'sys_cpu_combined_percent_normalized{{{SEL}}}', "{{instance}}")], unit="percentunit", w=8, minv=0)
    d.ts("RSS memory", [(f'sys_rss{{{SEL}}}', "{{instance}}")], unit="bytes", w=8)
    d.ts("Goroutines / GC pause / FDs", [(f'sys_goroutines{{{SEL}}}', "goroutines {{instance}}"), (f'sys_fd_open{{{SEL}}} / sys_fd_softlimit{{{SEL}}}', "fd ratio {{instance}}"),
                                         (f'rate(sys_gc_pause_ns{{{SEL}}}[{R}]) / 1e9', "gc pause s/s {{instance}}")], unit="short", w=8)
    d.ts("Store capacity used", [(f'capacity_used{{{SEL}}}', "used {{instance}}"), (f'capacity_available{{{SEL}}}', "avail {{instance}}")], unit="bytes", w=12)
    d.ts("Network bytes in/out (SQL) & RPC", [(f'sum(rate(sql_bytesin{{{SEL}}}[{R}]))', "sql in"), (f'sum(rate(sql_bytesout{{{SEL}}}[{R}]))', "sql out"),
                                              (f'sum(rate(rpc_connection_heartbeats{{{SEL}}}[{R}]))', "rpc heartbeats/s")], unit="Bps", w=12)
    d.row("Security and licensing")
    d.table("Certificate and license TTLs", [(f'security_certificate_ttl_node{{{SEL}}}', ""), (f'security_certificate_ttl_ca{{{SEL}}}', ""), (f'security_certificate_ttl_client{{{SEL}}}', ""),
                                             (f'seconds_until_enterprise_license_expiry{{{SEL}}}', "")], w=12, h=7, unit="s", hide=["Time", "__name__", "job", "cluster"],
            rename={"Value #A": "node cert", "Value #B": "CA cert", "Value #C": "client cert", "Value #D": "license"},
            desc="0 = not applicable / no license set (Core/trial).")
    d.table("Node info", [(f'sys_uptime{{{SEL}}}', ""), (f'build_timestamp{{{SEL}}}', "")], w=12, h=7, hide=["Time", "__name__", "job"],
            rename={"Value #A": "uptime (s)", "Value #B": "build ts"}, unit="short")
    return d


def sql_latency():
    d = Dash("crdb-sql-latency", "CockroachDB SQL, Queries and Latency", ["cockroachdb", "sql", "latency"],
             "Latency percentiles (service/exec/txn), statement mix, failures, restarts, active statements, sessions by user and long-running queries.", time_from="now-3h")
    d.std_annotations()
    _vars(d)
    d.row("Latency percentiles")
    for name, metric in [("SQL service latency (client-observed)", "sql_service_latency_bucket"), ("SQL exec latency", "sql_exec_latency_bucket"), ("Txn latency", "sql_txn_latency_bucket")]:
        d.ts(name, [(f'histogram_quantile({q}, sum by (le, cluster) (rate({metric}{{{SEL}}}[{R}])))', f"p{q*100:g} {{{{cluster}}}}") for q in (0.5, 0.9, 0.99, 0.999)], unit="ns", w=8,
             thresholds=steps((None, GREEN), (1e9, RED)))
    d.ts("p99 service latency per node", [(f'histogram_quantile(0.99, sum by (le, instance) (rate(sql_service_latency_bucket{{{SEL}}}[{R}])))', "{{instance}}")], unit="ns", w=8)
    d.ts("p99 DistSQL / KV (txn coord) latency", [(f'histogram_quantile(0.99, sum by (le) (rate(sql_distsql_service_latency_bucket{{{SEL}}}[{R}])))', "distsql service p99"),
                                                 (f'histogram_quantile(0.99, sum by (le) (rate(txn_durations_bucket{{{SEL}}}[{R}])))', "kv txn duration p99")], unit="ns", w=8)
    d.ts("Statement latency by consistency (p99)", [(f'histogram_quantile(0.99, sum by (le) (rate(sql_service_latency_consistent_bucket{{{SEL}}}[{R}])))', "consistent"),
                                                    (f'histogram_quantile(0.99, sum by (le) (rate(sql_service_latency_historical_bucket{{{SEL}}}[{R}])))', "historical (follower/AOST)")], unit="ns", w=8,
         desc="Historical = AS OF SYSTEM TIME / follower reads (1W-2R read pool).")
    d.row("Statement mix and failures")
    d.ts("Statements by type", [(f'sum(rate({m}{{{SEL}}}[{R}]))', n) for m, n in [("sql_select_count", "select"), ("sql_insert_count", "insert"), ("sql_update_count", "update"), ("sql_delete_count", "delete"),
                                                                                    ("sql_ddl_count", "ddl"), ("sql_misc_count", "misc"), ("sql_copy_count", "copy")]], unit="ops", w=8, stack=True)
    d.ts("Failures and txn restarts", [(f'sum(rate(sql_failure_count{{{SEL}}}[{R}]))', "statement failures"), (f'sum(rate(sql_txn_abort_count{{{SEL}}}[{R}]))', "txn aborts"),
                                       (f'sum(rate(txn_restarts_serializable{{{SEL}}}[{R}]))', "restart serializable"), (f'sum(rate(txn_restarts_writetooold{{{SEL}}}[{R}]))', "restart write too old"),
                                       (f'sum(rate(txn_restarts_txnpush{{{SEL}}}[{R}]))', "restart txn push"), (f'sum(rate(txn_restarts_readwithinuncertainty{{{SEL}}}[{R}]))', "restart uncertainty"),
                                       (f'sum(rate(txn_restarts_txnaborted{{{SEL}}}[{R}]))', "restart aborted"), (f'sum(rate(txn_restarts_unknown{{{SEL}}}[{R}]))', "restart unknown")], unit="ops", w=8)
    d.ts("Txn commits, 1PC, parallel commits, rollbacks", [(f'sum(rate(txn_commits{{{SEL}}}[{R}]))', "commits"), (f'sum(rate(txn_commits1PC{{{SEL}}}[{R}]))', "1PC commits"), (f'sum(rate(txn_parallelcommits{{{SEL}}}[{R}]))', "parallel commits"),
                                                           (f'sum(rate(sql_txn_rollback_count{{{SEL}}}[{R}]))', "sql rollbacks"), (f'sum(rate(txn_refresh_success{{{SEL}}}[{R}]))', "refresh ok"), (f'sum(rate(txn_refresh_fail{{{SEL}}}[{R}]))', "refresh fail")], unit="ops", w=8)
    d.row("Active work")
    d.ts("Active statements and open txns", [(f'sum by (instance) (sql_statements_active{{{SEL}}})', "active stmts {{instance}}"), (f'sum by (instance) (sql_txns_open{{{SEL}}})', "open txns {{instance}}")], unit="short", w=8)
    d.ts("DistSQL flows / queries", [(f'sum(sql_distsql_flows_active{{{SEL}}})', "flows active"), (f'sum(rate(sql_distsql_queries_total{{{SEL}}}[{R}]))', "dist queries/s"),
                                     (f'sum(rate(sql_distsql_queries_spilled{{{SEL}}}[{R}]))', "spilled/s"), (f'sum(sql_distsql_queries_active{{{SEL}}})', "dist queries active")], unit="short", w=8)
    d.ts("SQL memory (current)", [(f'sql_mem_root_current{{{SEL}}}', "root {{instance}}"), (f'sql_mem_distsql_current{{{SEL}}}', "distsql {{instance}}"), (f'sql_mem_sql_session_current{{{SEL}}}', "sessions {{instance}}"),
                                  (f'sql_mem_bulk_current{{{SEL}}}', "bulk {{instance}}")], unit="bytes", w=8)
    d.row("Sessions and slow statements (SQL exporter: SHOW queries)")
    d.ts("Sessions by SQL user", [('sum by (cluster, sql_user) (crdb_sessions{cluster=~"$cluster"})', "{{cluster}} {{sql_user}}")], unit="short", w=8, stack=True)
    d.ts("Running queries by user", [('sum by (cluster, sql_user) (crdb_queries_running{cluster=~"$cluster"})', "{{cluster}} {{sql_user}}")], unit="short", w=8)
    d.ts("Slow statements: running >10s, oldest age", [('crdb_queries_running_over_10s{cluster=~"$cluster"}', "over 10s {{cluster}}"), ('crdb_oldest_query_age_seconds{cluster=~"$cluster"}', "oldest age s {{cluster}}")], unit="short", w=8,
         thresholds=steps((None, GREEN), (10, YELLOW), (60, RED)))
    d.ts("Connection limit headroom", [(f'sum by (cluster) (sql_conns{{{SEL}}})', "open {{cluster}}"), ('max by (cluster) (crdb_max_connections_per_gateway{cluster=~"$cluster"} > 0)', "limit per gateway {{cluster}}")], unit="short", w=12,
         desc="Limit shown only when server.max_connections_per_gateway is set (-1 = unlimited).")
    d.ts("SQL stats subsystem", [(f'sum(rate(sql_stats_flushes_successful{{{SEL}}}[{R}]))', "flushes ok"), (f'sum(rate(sql_stats_flushes_failed{{{SEL}}}[{R}]))', "flushes failed"),
                                 (f'sum(sql_stats_ingester_queue_size{{{SEL}}})', "ingester queue")], unit="short", w=12)
    return d


def storage():
    d = Dash("crdb-storage", "CockroachDB Storage, LSM and Compaction", ["cockroachdb", "storage", "pebble"],
             "Pebble LSM health: L0 sublevels, read/write amplification, compactions, WAL fsync, block cache, disk, admission control.", time_from="now-3h")
    d.std_annotations()
    _vars(d)
    d.row("Capacity")
    d.ts("Store used / available / reserved", [(f'capacity_used{{{SEL}}}', "used {{instance}}"), (f'capacity_available{{{SEL}}}', "available {{instance}}")], unit="bytes", w=8, stack=False)
    d.ts("Used % per store", [('instance:crdb_store_used_ratio{cluster=~"$cluster",instance=~"$node"}', "{{instance}}")], unit="percentunit", w=8, minv=0, maxv=1, thresholds=steps((None, GREEN), (0.6, YELLOW), (0.7, RED)))
    d.ts("Logical data: live / key / value bytes", [(f'sum by (instance) (livebytes{{{SEL}}})', "live {{instance}}"), (f'sum by (instance) (keybytes{{{SEL}}})', "keys {{instance}}"), (f'sum by (instance) (valbytes{{{SEL}}})', "values {{instance}}")], unit="bytes", w=8)
    d.row("LSM health")
    d.ts("L0 sublevels (admission control kicks in around 10-20)", [(f'storage_l0_sublevels{{{SEL}}}', "{{instance}}")], unit="short", w=8, thresholds=steps((None, GREEN), (10, YELLOW), (20, RED)))
    d.ts("L0 files", [(f'storage_l0_num_files{{{SEL}}}', "{{instance}}")], unit="short", w=8)
    d.ts("Read amplification", [(f'rocksdb_read_amplification{{{SEL}}}', "{{instance}}")], unit="short", w=8, thresholds=steps((None, GREEN), (30, YELLOW), (50, RED)))
    d.ts("SSTables count", [(f'rocksdb_num_sstables{{{SEL}}}', "{{instance}}")], unit="short", w=8)
    d.ts("Estimated pending compaction bytes", [(f'rocksdb_estimated_pending_compaction{{{SEL}}}', "{{instance}}")], unit="bytes", w=8)
    d.ts("Write amplification (compacted bytes written / flushed)", [(f'rate(rocksdb_compacted_bytes_written{{{SEL}}}[{R}]) / clamp_min(rate(rocksdb_flushed_bytes{{{SEL}}}[{R}]), 1)', "{{instance}}")], unit="short", w=8)
    d.row("Compaction, flush, WAL")
    d.ts("Compactions / flushes per second", [(f'rate(rocksdb_compactions{{{SEL}}}[{R}])', "compactions {{instance}}"), (f'rate(rocksdb_flushes{{{SEL}}}[{R}])', "flushes {{instance}}")], unit="ops", w=8)
    d.ts("Compaction I/O (read / written / ingested)", [(f'sum(rate(rocksdb_compacted_bytes_read{{{SEL}}}[{R}]))', "compaction read"), (f'sum(rate(rocksdb_compacted_bytes_written{{{SEL}}}[{R}]))', "compaction write"),
                                                       (f'sum(rate(rocksdb_flushed_bytes{{{SEL}}}[{R}]))', "flushed"), (f'sum(rate(rocksdb_ingested_bytes{{{SEL}}}[{R}]))', "ingested")], unit="Bps", w=8)
    d.ts("WAL fsync latency p99", [(f'histogram_quantile(0.99, sum by (le, instance) (rate(storage_wal_fsync_latency_bucket{{{SEL}}}[{R}])))', "{{instance}}")], unit="ns", w=8,
         thresholds=steps((None, GREEN), (1e7, YELLOW), (1e8, RED)), desc="EBS gp3 fsync latency; sustained >100ms = disk trouble.")
    d.ts("WAL bytes written / in", [(f'rate(storage_wal_bytes_written{{{SEL}}}[{R}])', "written {{instance}}"), (f'rate(storage_wal_bytes_in{{{SEL}}}[{R}])', "in {{instance}}")], unit="Bps", w=8)
    d.ts("Memtable size / block cache usage", [(f'rocksdb_memtable_total_size{{{SEL}}}', "memtable {{instance}}"), (f'rocksdb_block_cache_usage{{{SEL}}}', "block cache {{instance}}"), (f'rocksdb_table_readers_mem_estimate{{{SEL}}}', "table readers {{instance}}")], unit="bytes", w=8)
    d.ts("Block cache hit ratio", [(f'rate(rocksdb_block_cache_hits{{{SEL}}}[{R}]) / clamp_min(rate(rocksdb_block_cache_hits{{{SEL}}}[{R}]) + rate(rocksdb_block_cache_misses{{{SEL}}}[{R}]), 1)', "{{instance}}")], unit="percentunit", w=8, minv=0, maxv=1)
    d.row("Disk (as seen by Cockroach)")
    d.ts("Disk read / write bytes", [(f'rate(storage_disk_read_bytes{{{SEL}}}[{R}])', "read {{instance}}"), (f'rate(storage_disk_write_bytes{{{SEL}}}[{R}])', "write {{instance}}")], unit="Bps", w=8)
    d.ts("Disk IOPS in progress / io time", [(f'storage_disk_iopsinprogress{{{SEL}}}', "in-flight {{instance}}"), (f'rate(storage_disk_io_time{{{SEL}}}[{R}])', "io time {{instance}}")], unit="short", w=8)
    d.ts("Disk slow / stalled / write stalls", [(f'storage_disk_slow{{{SEL}}}', "slow {{instance}}"), (f'storage_disk_stalled{{{SEL}}}', "stalled {{instance}}"), (f'increase(storage_write_stalls{{{SEL}}}[{R}])', "write stalls {{instance}}")], unit="short", w=8)
    d.row("Admission control (overload protection)")
    d.ts("IO overload score (>= 1 means throttling)", [(f'admission_io_overload{{{SEL}}}', "{{instance}}")], unit="short", w=8, thresholds=steps((None, GREEN), (0.5, YELLOW), (1, RED)))
    d.ts("KV admission wait p99", [(f'histogram_quantile(0.99, sum by (le, instance) (rate(admission_wait_durations_kv_bucket{{{SEL}}}[{R}])))', "kv {{instance}}"),
                                   (f'histogram_quantile(0.99, sum by (le, instance) (rate(admission_wait_durations_sql_kv_response_bucket{{{SEL}}}[{R}])))', "sql-kv response {{instance}}")], unit="ns", w=8)
    d.ts("Admission requests admitted / errored", [(f'sum(rate(admission_admitted_kv{{{SEL}}}[{R}]))', "kv admitted"), (f'sum(rate(admission_errored_kv{{{SEL}}}[{R}]))', "kv errored"),
                                                   (f'sum(rate(admission_admitted_sql_kv_response{{{SEL}}}[{R}]))', "sql-kv resp admitted")], unit="ops", w=8)
    d.ts("IO tokens exhausted (share of time)", [(f'rate(admission_granter_io_tokens_exhausted_duration_kv{{{SEL}}}[{R}]) / 1e6', "{{instance}}")], unit="percentunit", w=12)
    d.ts("GC queue and MVCC garbage", [(f'sum(rate(queue_gc_process_success{{{SEL}}}[{R}]))', "gc ok/s"), (f'sum(queue_gc_pending{{{SEL}}})', "gc pending"), (f'sum(rate(queue_gc_info_numkeysaffected{{{SEL}}}[{R}]))', "keys gc'd/s")], unit="short", w=12)
    return d


def replication():
    d = Dash("crdb-replication", "CockroachDB Replication, Ranges and Leaseholders", ["cockroachdb", "replication", "leaseholders"],
             "1 writer / 2 reader topology: leaseholders must concentrate on node 1 (zone=a); reads served by followers on nodes 2/3.", time_from="now-3h")
    d.std_annotations()
    _vars(d)
    d.row("Leaseholder distribution (1W-2R)")
    d.stat("Leaseholders on node 1 (preferred)", f'sum(replicas_leaseholders{{{J},cluster=~"$cluster",instance=~"main-crdb1.*|crdb1.*"}}) / sum(replicas_leaseholders{{{J},cluster=~"$cluster"}})', unit="percentunit", w=6, h=5,
           thresholds=steps((None, RED), (0.6, YELLOW), (0.9, GREEN)), graph=False, desc="Lease preference is +zone=a (node 1). Should be near 100% in steady state.")
    d.stat("Leases violating preferences", f'sum(leases_preferences_violating{{{J},cluster=~"$cluster"}})', w=4, h=5, thresholds=steps((None, GREEN), (1, YELLOW)), graph=False)
    d.stat("Leases less preferred", f'sum(leases_preferences_less_preferred{{{J},cluster=~"$cluster"}})', w=4, h=5, thresholds=steps((None, GREEN), (1, YELLOW)), graph=False)
    d.pie("Leaseholders per node", [(f'replicas_leaseholders{{{SEL}}}', "{{instance}}")], w=10, h=5)
    d.ts("Leaseholders per node", [(f'replicas_leaseholders{{{SEL}}}', "{{instance}}")], unit="short", w=8, stack=True)
    d.ts("Raft leaders per node", [(f'replicas_leaders{{{SEL}}}', "{{instance}}")], unit="short", w=8, stack=True)
    d.ts("Leaders that are not leaseholders (should be ~0)", [(f'replicas_leaders_not_leaseholders{{{SEL}}}', "{{instance}}")], unit="short", w=8)
    d.table("Per-node summary", [(f'replicas{{{SEL}}}', ""), (f'replicas_leaseholders{{{SEL}}}', ""), (f'replicas_leaders{{{SEL}}}', ""),
                                 (f'sum by (instance) (rate(sql_select_count{{{SEL}}}[5m]))', ""), (f'sum by (instance) (rate(sql_insert_count{{{SEL}}}[5m]) + rate(sql_update_count{{{SEL}}}[5m]) + rate(sql_delete_count{{{SEL}}}[5m]))', "")],
            w=24, h=6, hide=["Time", "__name__", "job"], rename={"Value #A": "replicas", "Value #B": "leaseholders", "Value #C": "raft leaders", "Value #D": "select/s (reads)", "Value #E": "writes/s"},
            desc="Reads should land on nodes 2/3 (follower reads via the HAProxy read port); writes on node 1.")
    d.row("Range health")
    d.ts("Ranges: total / unavailable / under / over-replicated", [(f'max(ranges{{{J},cluster=~"$cluster"}})', "total"), (f'sum(ranges_unavailable{{{J},cluster=~"$cluster"}})', "unavailable"),
                                                                  (f'sum(ranges_underreplicated{{{J},cluster=~"$cluster"}})', "under-replicated"), (f'sum(ranges_overreplicated{{{J},cluster=~"$cluster"}})', "over-replicated")], unit="short", w=8)
    d.ts("Replicas per node (incl. non-leaseholders)", [(f'replicas{{{SEL}}}', "{{instance}}"), (f'replicas_quiescent{{{SEL}}}', "quiescent {{instance}}"), (f'replicas_uninitialized{{{SEL}}}', "uninitialized {{instance}}")], unit="short", w=8)
    d.ts("Range ops: splits / merges / adds / removes", [(f'sum(rate(range_splits{{{SEL}}}[{R}]))', "splits"), (f'sum(rate(range_merges{{{SEL}}}[{R}]))', "merges"), (f'sum(rate(range_adds{{{SEL}}}[{R}]))', "adds"),
                                                       (f'sum(rate(range_removes{{{SEL}}}[{R}]))', "removes"), (f'sum(rate(range_raftleadertransfers{{{SEL}}}[{R}]))', "raft leader transfers")], unit="ops", w=8)
    d.row("Rebalancing and snapshots")
    d.ts("Lease transfers", [(f'sum(rate(leases_transfers_success{{{SEL}}}[{R}]))', "success"), (f'sum(rate(leases_transfers_error{{{SEL}}}[{R}]))', "error"), (f'sum(rate(rebalancing_lease_transfers{{{SEL}}}[{R}]))', "load-based")], unit="ops", w=8)
    d.ts("Snapshots (bytes sent/received)", [(f'sum(rate(range_snapshots_rcvd_bytes{{{SEL}}}[{R}]))', "received"), (f'sum(rate(range_snapshots_rebalancing_sent_bytes{{{SEL}}}[{R}]))', "rebalancing sent"),
                                            (f'sum(rate(range_snapshots_recovery_sent_bytes{{{SEL}}}[{R}]))', "recovery sent")], unit="Bps", w=8)
    d.ts("Replicate / rebalance queue", [(f'sum(queue_replicate_pending{{{SEL}}})', "replicate pending"), (f'sum(rate(queue_replicate_process_failure{{{SEL}}}[{R}]))', "replicate failures/s"),
                                         (f'sum(rate(queue_replicate_addreplica_success{{{SEL}}}[{R}]))', "adds ok/s")], unit="short", w=8)
    d.row("Liveness, Raft, clocks")
    d.ts("Live nodes and heartbeat failures", [(f'max by (cluster) (liveness_livenodes{{{J},cluster=~"$cluster"}})', "live nodes {{cluster}}"), (f'sum(rate(liveness_heartbeatfailures{{{SEL}}}[{R}]))', "heartbeat failures/s"),
                                              (f'sum(rate(liveness_epochincrements{{{SEL}}}[{R}]))', "epoch increments/s")], unit="short", w=8)
    d.ts("Raft: commands applied / proposed, dropped", [(f'sum(rate(raft_commandsapplied{{{SEL}}}[{R}]))', "applied"), (f'sum(rate(raft_commands_proposed{{{SEL}}}[{R}]))', "proposed"), (f'sum(rate(raft_dropped{{{SEL}}}[{R}]))', "dropped")], unit="ops", w=8)
    d.ts("Raft commit latency p99 / handleready p99", [(f'histogram_quantile(0.99, sum by (le) (rate(raft_process_commandcommit_latency_bucket{{{SEL}}}[{R}])))', "command commit"),
                                                      (f'histogram_quantile(0.99, sum by (le) (rate(raft_process_handleready_latency_bucket{{{SEL}}}[{R}])))', "handleready"),
                                                      (f'histogram_quantile(0.99, sum by (le) (rate(raft_process_logcommit_latency_bucket{{{SEL}}}[{R}])))', "log commit")], unit="ns", w=8)
    d.ts("Clock offset (mean) per node", [(f'clock_offset_meannanos{{{SEL}}}', "{{instance}}")], unit="ns", w=8, thresholds=steps((None, GREEN), (2.5e8, YELLOW), (4e8, RED)), desc="Nodes self-terminate near 400ms.")
    d.ts("Inter-node round-trip latency p99", [(f'histogram_quantile(0.99, sum by (le, instance) (rate(round_trip_latency_bucket{{{SEL}}}[{R}])))', "{{instance}}")], unit="ns", w=8)
    d.ts("DistSender: NotLeaseHolder errors / range cache", [(f'sum(rate(distsender_errors_notleaseholder{{{SEL}}}[{R}]))', "NotLeaseHolderError/s"), (f'sum(rate(distsender_rpc_sent_nextreplicaerror{{{SEL}}}[{R}]))', "next-replica errors/s"),
                                                           (f'sum(rate(distsender_batches_partial{{{SEL}}}[{R}]))', "partial batches/s")], unit="ops", w=8)
    d.ts("Follower reads", [(f'sum by (instance) (rate(follower_reads_success_count{{{SEL}}}[{R}]))', "{{instance}}")], unit="ops", w=8)
    return d


def hot_contention():
    d = Dash("crdb-hot-contention", "CockroachDB Hot Ranges and Contention", ["cockroachdb", "hotspots", "contention"],
             "Load per node/range, lock & latch waits, txn pushes, deadlocks, contended queries.", time_from="now-3h")
    d.std_annotations()
    _vars(d)
    d.row("Load distribution (hot spots)")
    d.ts("Store QPS / write QPS (rebalancing signals)", [(f'rebalancing_queriespersecond{{{SEL}}}', "qps {{instance}}"), (f'rebalancing_writespersecond{{{SEL}}}', "writes/s {{instance}}")], unit="ops", w=8)
    d.ts("Store CPU (ns/s) per node", [(f'rebalancing_cpunanospersecond{{{SEL}}} / 1e9', "{{instance}}"), (f'replicas_cpunanospersecond{{{SEL}}} / 1e9', "replica CPU {{instance}}")], unit="short", w=8, desc="CPU-seconds per second spent serving replicas.")
    d.ts("Read / write bytes per second (store)", [(f'rebalancing_readbytespersecond{{{SEL}}}', "read {{instance}}"), (f'rebalancing_writebytespersecond{{{SEL}}}', "write {{instance}}")], unit="Bps", w=8)
    d.ts("Load-based splits & rebalance decisions", [(f'sum(rate(range_splits{{{SEL}}}[{R}]))', "splits"), (f'sum(rate(rebalancing_range_rebalances{{{SEL}}}[{R}]))', "range rebalances"), (f'sum(rate(rebalancing_lease_transfers{{{SEL}}}[{R}]))', "lease transfers"),
                                                    (f'sum(rate(kv_allocator_load_based_lease_transfers_should_transfer{{{SEL}}}[{R}]))', "lease should-transfer")], unit="ops", w=12)
    d.ts("Request imbalance: max/avg ratio per metric", [(f'max(rebalancing_requestspersecond{{{SEL}}}) / clamp_min(avg(rebalancing_requestspersecond{{{SEL}}}), 1e-9)', "requests"),
                                                         (f'max(replicas_cpunanospersecond{{{SEL}}}) / clamp_min(avg(replicas_cpunanospersecond{{{SEL}}}), 1e-9)', "cpu")], unit="short", w=12, desc="1.0 = perfectly even. With 1W-2R pinning this is expected to be high for writes.")
    d.row("Contention (KV and SQL)")
    d.ts("Contended queries / txns (SQL)", [(f'sum(rate(sql_distsql_contended_queries_count{{{SEL}}}[{R}]))', "contended queries/s"), (f'sum(rate(sql_txn_contended_count{{{SEL}}}[{R}]))', "contended txns/s"),
                                           (f'sum(rate(sql_distsql_cumulative_contention_nanos{{{SEL}}}[{R}])) / 1e9', "cumulative contention s/s")], unit="short", w=8)
    d.ts("Contention events (SQL exporter, last 5m)", [('crdb_contention_events_5m{cluster=~"$cluster"}', "events {{cluster}}"), ('crdb_contention_seconds_5m{cluster=~"$cluster"}', "seconds {{cluster}}")], unit="short", w=8)
    d.ts("Lock table: waiters, locks, max wait", [(f'sum(kv_concurrency_lock_wait_queue_waiters{{{SEL}}})', "waiters"), (f'sum(kv_concurrency_locks{{{SEL}}})', "locks"), (f'sum(kv_concurrency_locks_with_wait_queues{{{SEL}}})', "locks with queues"),
                                                  (f'max(kv_concurrency_max_lock_wait_duration_nanos{{{SEL}}}) / 1e9', "max lock wait s")], unit="short", w=8)
    d.ts("Latch conflict wait p99", [(f'histogram_quantile(0.99, sum by (le, instance) (rate(kv_concurrency_latch_conflict_wait_durations_bucket{{{SEL}}}[{R}])))', "{{instance}}")], unit="ns", w=8)
    d.ts("Txn wait queue / deadlocks / pushes", [(f'sum(txnwaitqueue_pusher_waiting{{{SEL}}})', "pushers waiting"), (f'sum(txnwaitqueue_pushee_waiting{{{SEL}}})', "pushees waiting"),
                                                 (f'sum(rate(txnwaitqueue_deadlocks_total{{{SEL}}}[{R}]))', "deadlocks/s"), (f'sum(txnwaitqueue_pusher_slow{{{SEL}}})', "slow pushers")], unit="short", w=8)
    d.ts("Txn restarts by cause", [(f'sum(rate({m}{{{SEL}}}[{R}]))', n) for m, n in [("txn_restarts_serializable", "serializable"), ("txn_restarts_writetooold", "write too old"), ("txn_restarts_txnpush", "txn push"),
                                                                                     ("txn_restarts_readwithinuncertainty", "read within uncertainty"), ("txn_restarts_txnaborted", "aborted"), ("txn_restarts_asyncwritefailure", "async write failure"),
                                                                                     ("txn_restarts_commitdeadlineexceeded", "commit deadline"), ("txn_restarts_exclusionviolation", "exclusion violation")]], unit="ops", w=8, stack=True)
    d.ts("Intent resolution", [(f'sum(rate(intents_resolve_attempts{{{SEL}}}[{R}]))', "resolve attempts"), (f'sum(rate(intents_abort_attempts{{{SEL}}}[{R}]))', "abort attempts"), (f'sum(rate(intents_poison_attempts{{{SEL}}}[{R}]))', "poison attempts")], unit="ops", w=8)
    d.ts("Slow requests (stuck in raft / latch / lease)", [(f'sum by (instance) (requests_slow_raft{{{SEL}}})', "raft {{instance}}"), (f'sum by (instance) (requests_slow_latch{{{SEL}}})', "latch {{instance}}"),
                                                          (f'sum by (instance) (requests_slow_lease{{{SEL}}})', "lease {{instance}}")], unit="short", w=8)
    d.row("Logs (Loki): contention, slow, retry")
    d.logs("CockroachDB log lines matching slow / contention / retry / deadlock", '{container=~".*crdb.*|.*cockroach.*"} |~ "(?i)slow|contention|deadlock|retry|restart|txn aborted|lease"', h=10)
    return d


def jobs_backups():
    d = Dash("crdb-jobs-backups", "Jobs and Backups (RPO / RTO)", ["cockroachdb", "backup", "rpo", "rto"],
             "Time since last backup vs RPO target (2h), duration vs RTO budget (30m), handler run history, CockroachDB jobs, main-vs-backup row parity.", time_from="now-24h")
    d.std_annotations(backups=True)
    d.var_custom("rpo_h", ["1", "2", "4", "6", "24"], label="RPO target (h) shown on gauges", default="2")
    d.row("RPO / RTO")
    d.gauge("Time since last successful backup", 'time() - max(talentra_backup_last_success_timestamp_seconds{op="backup"})', unit="s", w=5, h=6, maxv=14400, thresholds=steps((None, GREEN), (3600, YELLOW), (7200, RED)),
            desc="RPO = data you would lose if main died now. Target 2h (recording rule talentra:backup_rpo_target_seconds).")
    d.stat("RPO headroom", 'talentra:backup_rpo_headroom_seconds', unit="s", w=4, h=6, thresholds=steps((None, RED), (0, YELLOW), (1800, GREEN)), graph=False, desc="Seconds left before the RPO target is breached (negative = breached).")
    d.gauge("Last backup duration", 'max(talentra_backup_last_duration_seconds{op="backup"})', unit="s", w=5, h=6, maxv=3600, thresholds=steps((None, GREEN), (900, YELLOW), (1800, RED)),
            desc="Duration of the last successful backup (RTO budget 30m: a restore is at least this slow).")
    d.stat("Last run status", 'min(talentra_backup_last_run_success{op="backup"})', w=3, h=6, thresholds=OKBAD, graph=False,
           mappings=[{"type": "value", "options": {"0": {"text": "FAILED", "color": "red"}, "1": {"text": "OK", "color": "green"}}}])
    d.stat("Consecutive failures", 'max(talentra_backup_consecutive_failures{op="backup"})', w=3, h=6, thresholds=steps((None, GREEN), (1, YELLOW), (3, RED)), graph=False)
    d.stat("Backup data on volume", 'talentra_backup_share_bytes', unit="bytes", w=4, h=6, graph=True, color_mode="value")
    d.row("Backup handler history (from handler CSV)")
    d.ts("Age of last success, per op/type/dest", [('time() - talentra_backup_last_success_timestamp_seconds', "{{op}} {{type}} {{dest}}"), ('scalar(talentra:backup_rpo_target_seconds)', "RPO target")], unit="s", w=12,
         overrides=[{"matcher": {"id": "byName", "options": "RPO target"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}, {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}}, {"id": "custom.fillOpacity", "value": 0}]}])
    d.ts("Last duration per op/type/dest vs RTO budget", [('talentra_backup_last_duration_seconds', "{{op}} {{type}} {{dest}}"), ('scalar(talentra:backup_rto_target_seconds)', "RTO budget")], unit="s", w=12,
         overrides=[{"matcher": {"id": "byName", "options": "RTO budget"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}, {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}}, {"id": "custom.fillOpacity", "value": 0}]}])
    d.statetimeline("Last run success per op/type/dest (1 ok / 0 failed)", [('talentra_backup_last_run_success', "{{op}} {{type}} {{dest}}")], w=12, h=7)
    d.ts("Runs recorded in handler log (ok vs fail)", [('talentra_backup_runs_in_log', "{{op}} {{type}} {{dest}} {{status}}")], unit="short", w=12, h=7)
    d.table("Handler status table", [('talentra_backup_last_run_success', ""), ('talentra_backup_last_run_timestamp_seconds * 1000', ""), ('talentra_backup_last_duration_seconds', ""), ('talentra_backup_consecutive_failures', "")],
            w=24, h=7, hide=["Time", "__name__", "job", "instance", "host"], rename={"Value #A": "last ok?", "Value #B": "last run", "Value #C": "last ok duration (s)", "Value #D": "consecutive failures"},
            overrides=[{"matcher": {"id": "byName", "options": "last run"}, "properties": [{"id": "unit", "value": "dateTimeAsIso"}]}])
    d.row("CockroachDB jobs (SHOW JOBS via SQL exporter)")
    d.ts("Newest successful job per type: age", [('time() - crdb_job_last_success_timestamp_seconds', "{{cluster}} {{job_type}}")], unit="s", w=8)
    d.ts("Last successful BACKUP / RESTORE / IMPORT duration", [('crdb_job_last_success_duration_seconds', "{{cluster}} {{job_type}}")], unit="s", w=8)
    d.ts("Jobs created in last 24h by type/status", [('sum by (cluster, job_type, status) (crdb_jobs_recent)', "{{cluster}} {{job_type}} {{status}}")], unit="short", w=8, stack=True)
    d.ts("Running jobs", [('sum by (cluster, job_type) (crdb_jobs_running)', "{{cluster}} {{job_type}}")], unit="short", w=8)
    d.ts("Native job metrics: backup running / failed / PTS age", [('sum(jobs_backup_currently_running{job="cockroachdb"})', "backup running"), ('sum(increase(jobs_backup_resume_failed{job="cockroachdb"}[1h]))', "resume failed (1h)"),
                                                                    ('max(jobs_backup_protected_age_sec{job="cockroachdb"})', "oldest PTS age s"), ('sum(increase(jobs_backup_fail_or_cancel_completed{job="cockroachdb"}[1h]))', "fail/cancel (1h)")], unit="short", w=8)
    d.row("Main vs backup cluster parity (restore-sync health)")
    d.ts("Live MVCC keys: main (per replica) vs backup", [('sum(livecount{job="cockroachdb",cluster="main"}) / 3', "main / RF3"),
                                                          ('sum(livecount{job="cockroachdb",cluster="backup"})', "backup (RF1)")], unit="short", w=12,
         desc="Cheap data-parity proxy without scanning tables: live key count. Main is RF=3 so its total is divided by 3. Per-table counts are an opt-in collector (README).")
    d.ts("Logical size: main (per replica) vs backup", [('sum(livebytes{job="cockroachdb",cluster="main"}) / 3', "main / RF3"), ('sum(livebytes{job="cockroachdb",cluster="backup"})', "backup (RF1)")], unit="bytes", w=12)
    d.ts("User databases (SQL exporter)", [('crdb_user_databases', "{{cluster}}")], unit="short", w=12)
    d.ts("Row-count optional collector", [('crdb_table_estimated_rows', "{{cluster}} {{tbl}}")], unit="short", w=12, desc="Empty unless the opt-in collector is enabled (sql_exporter/optional).")
    d.ts("Native schedule metrics (if CockroachDB schedules are used)", [('sum(schedules_BACKUP_succeeded{job="cockroachdb"})', "succeeded"), ('sum(schedules_BACKUP_failed{job="cockroachdb"})', "failed"), ('sum(schedules_BACKUP_started{job="cockroachdb"})', "started"),
                                                                       ('max(time() - schedules_BACKUP_last_completed_time{job="cockroachdb"})', "age of last completed (s)")], unit="short", w=24)
    d.row("Handler logs (Loki)")
    d.logs("backup-handler output", '{container=~".*backup-handler.*"}', h=9)
    return d


def build_all(out):
    for fn in (overview, sql_latency, storage, replication, hot_contention, jobs_backups):
        fn().save(os.path.join(out, "CockroachDB"))

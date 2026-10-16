"""HAProxy, Caddy, Blackbox/Uptime, Logs explorer, Ingestion engine dashboards."""
from dashlib import *

R = "$__rate_interval"


def haproxy():
    d = Dash("haproxy", "HAProxy (SQL load balancer)", ["haproxy", "edge", "sql"],
             "main-db HAProxy: write port 26257 -> node1 (others backup), read port 26259 -> nodes 2/3. Source: haproxy_exporter on the stats CSV.", time_from="now-3h")
    d.std_annotations()
    d.var_query("backend", 'label_values(haproxy_backend_up, proxy)', label="Backend")
    d.var_query("frontend", 'label_values(haproxy_frontend_current_sessions, proxy)', label="Frontend")
    d.row("Health")
    d.stat("Exporter can read stats", 'min(haproxy_up)', w=4, thresholds=OKBAD, graph=False, mappings=[{"type": "value", "options": {"0": {"text": "NO", "color": "red"}, "1": {"text": "YES", "color": "green"}}}])
    d.stat("Backends up", 'sum(haproxy_backend_up{proxy=~"$backend"})', w=4, thresholds=steps((None, RED), (1, GREEN)), graph=False)
    d.stat("Servers up / total", 'sum(haproxy_server_up{backend=~"$backend"}) / count(haproxy_server_up{backend=~"$backend"})', unit="percentunit", w=4, thresholds=steps((None, RED), (0.5, YELLOW), (0.99, GREEN)), graph=False,
           desc="Note: in be_write nodes 2/3 are 'backup' servers and count as up while healthy.")
    d.stat("Current sessions", 'sum(haproxy_frontend_current_sessions{proxy=~"$frontend",proxy!="monitor"})', w=4, graph=True, color_mode="value")
    d.stat("Backend queue", 'sum(haproxy_backend_current_queue{proxy=~"$backend"})', w=4, thresholds=steps((None, GREEN), (1, YELLOW), (10, RED)), graph=False)
    d.stat("Connection errors (1h)", 'sum(increase(haproxy_backend_connection_errors_total{proxy=~"$backend"}[1h]))', w=4, thresholds=steps((None, GREEN), (1, YELLOW)), graph=False)
    d.statetimeline("Server health (1 up / 0 down)", [('haproxy_server_up{backend=~"$backend"}', "{{backend}}/{{server}}")], w=12, h=7)
    d.ts("Backend up", [('haproxy_backend_up{proxy=~"$backend"}', "{{proxy}}")], unit="short", w=12, h=7, minv=0, maxv=1.2)
    d.row("Frontends (SQL ports)")
    d.ts("Current sessions", [('haproxy_frontend_current_sessions{proxy=~"$frontend"}', "{{proxy}}"), ('haproxy_frontend_limit_sessions{proxy=~"$frontend"}', "limit {{proxy}}")], unit="short", w=8)
    d.ts("New sessions / s and session rate", [('rate(haproxy_frontend_sessions_total{proxy=~"$frontend"}[%s])' % R, "sessions/s {{proxy}}"), ('rate(haproxy_frontend_connections_total{proxy=~"$frontend"}[%s])' % R, "conns/s {{proxy}}"),
                                              ('haproxy_frontend_max_session_rate{proxy=~"$frontend"}', "max rate {{proxy}}")], unit="ops", w=8)
    d.ts("Frontend traffic", [('rate(haproxy_frontend_bytes_in_total{proxy=~"$frontend"}[%s])' % R, "in {{proxy}}"), ('rate(haproxy_frontend_bytes_out_total{proxy=~"$frontend"}[%s])' % R, "out {{proxy}}")], unit="Bps", w=8)
    d.ts("Session utilisation (current / limit)", [('haproxy_frontend_current_sessions{proxy=~"$frontend"} / haproxy_frontend_limit_sessions{proxy=~"$frontend"}', "{{proxy}}")], unit="percentunit", w=8, minv=0, maxv=1,
         thresholds=steps((None, GREEN), (0.7, YELLOW), (0.8, RED)))
    d.ts("Denied / request errors / HTTP responses (stats frontend)", [('rate(haproxy_frontend_requests_denied_total{proxy=~"$frontend"}[%s])' % R, "denied {{proxy}}"),
                                                                      ('rate(haproxy_frontend_request_errors_total{proxy=~"$frontend"}[%s])' % R, "request errors {{proxy}}"),
                                                                      ('sum by (proxy, code) (rate(haproxy_frontend_http_responses_total{proxy=~"$frontend"}[%s]))' % R, "http {{code}} {{proxy}}")], unit="ops", w=16)
    d.row("Backends")
    d.ts("Backend current sessions and queue", [('haproxy_backend_current_sessions{proxy=~"$backend"}', "sessions {{proxy}}"), ('haproxy_backend_current_queue{proxy=~"$backend"}', "queue {{proxy}}")], unit="short", w=8)
    d.ts("Backend errors", [('rate(haproxy_backend_connection_errors_total{proxy=~"$backend"}[%s])' % R, "conn errors {{proxy}}"), ('rate(haproxy_backend_response_errors_total{proxy=~"$backend"}[%s])' % R, "resp errors {{proxy}}"),
                            ('rate(haproxy_backend_retry_warnings_total{proxy=~"$backend"}[%s])' % R, "retries {{proxy}}"), ('rate(haproxy_backend_redispatch_warnings_total{proxy=~"$backend"}[%s])' % R, "redispatch {{proxy}}"),
                            ('rate(haproxy_backend_client_aborts_total{proxy=~"$backend"}[%s])' % R, "client aborts {{proxy}}"), ('rate(haproxy_backend_server_aborts_total{proxy=~"$backend"}[%s])' % R, "server aborts {{proxy}}")], unit="ops", w=8)
    d.ts("Connect time (avg, last 1024 conns)", [('haproxy_backend_http_connect_time_average_seconds{proxy=~"$backend"}', "{{proxy}}"), ('haproxy_backend_http_total_time_average_seconds{proxy=~"$backend"}', "total {{proxy}}")], unit="s", w=8)
    d.ts("Backend traffic", [('rate(haproxy_backend_bytes_in_total{proxy=~"$backend"}[%s])' % R, "in {{proxy}}"), ('rate(haproxy_backend_bytes_out_total{proxy=~"$backend"}[%s])' % R, "out {{proxy}}")], unit="Bps", w=8)
    d.ts("Sessions served per server (shows write vs read distribution)", [('rate(haproxy_server_sessions_total{backend=~"$backend"}[%s])' % R, "{{backend}}/{{server}}")], unit="ops", w=8, stack=True)
    d.ts("Backend selected server rate", [('rate(haproxy_backend_server_selected_total{proxy=~"$backend"}[%s])' % R, "{{proxy}}")], unit="ops", w=8)
    d.row("Servers (CockroachDB nodes)")
    d.ts("Server current sessions", [('haproxy_server_current_sessions{backend=~"$backend"}', "{{backend}}/{{server}}")], unit="short", w=8)
    d.ts("Health-check duration and failures", [('haproxy_server_check_duration_seconds{backend=~"$backend"}', "duration {{backend}}/{{server}}"), ('increase(haproxy_server_check_failures_total{backend=~"$backend"}[%s])' % R, "failures {{backend}}/{{server}}")], unit="s", w=8)
    d.ts("Server downtime and weight", [('haproxy_server_downtime_seconds_total{backend=~"$backend"}', "downtime {{backend}}/{{server}}"), ('haproxy_server_weight{backend=~"$backend"}', "weight {{backend}}/{{server}}")], unit="short", w=8)
    d.ts("Server errors", [('rate(haproxy_server_connection_errors_total{backend=~"$backend"}[%s])' % R, "conn {{backend}}/{{server}}"), ('rate(haproxy_server_response_errors_total{backend=~"$backend"}[%s])' % R, "resp {{backend}}/{{server}}")], unit="ops", w=12)
    d.ts("Server queue and session rate", [('haproxy_server_current_queue{backend=~"$backend"}', "queue {{backend}}/{{server}}"), ('haproxy_server_current_session_rate{backend=~"$backend"}', "rate {{backend}}/{{server}}")], unit="short", w=12)
    d.row("Exporter and logs")
    d.ts("Exporter scrapes / parse failures", [('rate(haproxy_exporter_scrapes_total[%s])' % R, "scrapes/s"), ('rate(haproxy_exporter_csv_parse_failures_total[%s])' % R, "csv parse failures/s")], unit="ops", w=8)
    d.logs("HAProxy logs", '{container=~".*haproxy.*",container!~".*exporter.*"}', w=16, h=8)
    return d


def caddy():
    d = Dash("caddy", "Caddy (HTTPS edge)", ["caddy", "edge", "tls"],
             "Requests, status codes, latency, upstream health, config reloads, certificate expiry. Per-request metrics need the global `metrics` option (see INTEGRATION_REQUESTS.md).", time_from="now-3h")
    d.std_annotations()
    d.var_query("server", 'label_values(caddy_http_requests_total, server)', label="Server", regex="")
    S = 'server=~"$server"'
    d.row("Traffic")
    d.stat("Requests / s", f'sum(rate(caddy_http_request_duration_seconds_count{{{S}}}[5m]))', unit="reqps", w=4, color_mode="value")
    d.stat("5xx ratio", f'sum(rate(caddy_http_request_duration_seconds_count{{{S},code=~"5.."}}[5m])) / sum(rate(caddy_http_request_duration_seconds_count{{{S}}}[5m]))', unit="percentunit", w=4,
           thresholds=steps((None, GREEN), (0.01, YELLOW), (0.05, RED)), graph=False)
    d.stat("In flight", f'sum(caddy_http_requests_in_flight{{{S}}})', w=4, color_mode="value")
    d.stat("p95 latency", f'histogram_quantile(0.95, sum by (le) (rate(caddy_http_request_duration_seconds_bucket{{{S}}}[5m])))', unit="s", w=4, thresholds=steps((None, GREEN), (1, YELLOW), (2, RED)), graph=False)
    d.stat("Healthy upstreams", 'sum(caddy_reverse_proxy_upstreams_healthy)', w=4, thresholds=steps((None, RED), (1, GREEN)), graph=False)
    d.stat("Config reload ok", 'caddy_config_last_reload_successful', w=4, thresholds=OKBAD, graph=False, mappings=[{"type": "value", "options": {"0": {"text": "FAILED", "color": "red"}, "1": {"text": "OK", "color": "green"}}}])
    d.ts("Requests by status class", [(f'sum by (code) (rate(caddy_http_request_duration_seconds_count{{{S}}}[{R}]))', "{{code}}")], unit="reqps", w=12, stack=True)
    d.ts("Requests by handler", [(f'sum by (handler) (rate(caddy_http_requests_total{{{S}}}[{R}]))', "{{handler}}")], unit="reqps", w=12, stack=True,
         desc="Handlers chain (don't add them up): subroute / reverse_proxy / static_response / authentication.")
    d.row("Latency")
    d.ts("Request duration percentiles", [(f'histogram_quantile({q}, sum by (le) (rate(caddy_http_request_duration_seconds_bucket{{{S}}}[{R}])))', f"p{q*100:g}") for q in (0.5, 0.9, 0.95, 0.99)], unit="s", w=12)
    d.ts("Time to first byte percentiles", [(f'histogram_quantile({q}, sum by (le) (rate(caddy_http_response_duration_seconds_bucket{{{S}}}[{R}])))', f"p{q*100:g}") for q in (0.5, 0.95, 0.99)], unit="s", w=12)
    d.ts("Request / response size (avg)", [(f'sum(rate(caddy_http_request_size_bytes_sum{{{S}}}[{R}])) / clamp_min(sum(rate(caddy_http_request_size_bytes_count{{{S}}}[{R}])), 1e-9)', "request avg"),
                                           (f'sum(rate(caddy_http_response_size_bytes_sum{{{S}}}[{R}])) / clamp_min(sum(rate(caddy_http_response_size_bytes_count{{{S}}}[{R}])), 1e-9)', "response avg")], unit="bytes", w=12)
    d.ts("Handler errors", [(f'sum by (handler) (rate(caddy_http_request_errors_total{{{S}}}[{R}]))', "{{handler}}")], unit="ops", w=12)
    d.row("Upstreams, admin, process")
    d.ts("Reverse proxy upstream health", [('caddy_reverse_proxy_upstreams_healthy', "{{upstream}}")], unit="short", w=8, minv=0, maxv=1.2)
    d.ts("Admin API requests", [('sum by (path, code) (rate(caddy_admin_http_requests_total[%s]))' % R, "{{path}} {{code}}")], unit="ops", w=8)
    d.ts("Process memory / goroutines / fds", [('process_resident_memory_bytes{job="caddy"}', "rss"), ('go_goroutines{job="caddy"}', "goroutines"), ('process_open_fds{job="caddy"}', "open fds")], unit="short", w=8)
    d.row("TLS certificates (blackbox, public hostnames)")
    d.table("Certificate expiry", [('probe_ssl_earliest_cert_expiry{job="blackbox-http"} - time()', "")], w=12, h=7, unit="s", hide=["Time", "__name__", "job", "Value"] if False else ["Time", "__name__", "job"],
            rename={"Value": "time left"}, defaults={"thresholds": steps((None, RED), (14 * 86400, YELLOW), (30 * 86400, GREEN)), "color": {"mode": "thresholds"}},
            overrides=[{"matcher": {"id": "byName", "options": "time left"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}}]}])
    d.ts("Days until certificate expiry", [('(probe_ssl_earliest_cert_expiry{job="blackbox-http"} - time()) / 86400', "{{instance}}")], unit="d", w=12, thresholds=steps((None, RED), (14, YELLOW), (30, GREEN)))
    d.row("Access logs (Loki)")
    d.logs("Caddy logs", '{container=~".*caddy.*"}', h=9)
    d.ts("Access-log requests by status (from logs)", [('sum by (status) (count_over_time({container=~".*caddy.*"} | json | status != "" [$__auto]))', "{{status}}")], unit="short", w=24, ds=LOKI, stack=True)
    return d


def blackbox():
    d = Dash("blackbox-uptime", "Uptime and SSL (Blackbox)", ["blackbox", "uptime", "tls"],
             "HTTPS/HTTP probes of public hostnames, TLS handshake/cert expiry, TCP probes of SQL ports.", time_from="now-24h")
    d.std_annotations(restarts=False)
    d.var_query("instance", 'label_values(probe_success, instance)', label="Target")
    I = 'instance=~"$instance"'
    d.row("Status")
    d.stat("Probes up", f'sum(probe_success{{{I}}})', w=4, thresholds=steps((None, GREEN)), graph=False)
    d.stat("Probes failing", f'count(probe_success{{{I}}} == 0) or vector(0)', w=4, thresholds=BADOK, graph=False)
    d.stat("Uptime 24h", f'avg(avg_over_time(probe_success{{{I},job="blackbox-http"}}[24h]))', unit="percentunit", w=4, thresholds=steps((None, RED), (0.99, YELLOW), (0.999, GREEN)), decimals=3, graph=False)
    d.stat("Uptime 7d", f'avg(avg_over_time(probe_success{{{I},job="blackbox-http"}}[7d]))', unit="percentunit", w=4, thresholds=steps((None, RED), (0.99, YELLOW), (0.999, GREEN)), decimals=3, graph=False)
    d.stat("Min cert days left", f'min(probe_ssl_earliest_cert_expiry{{{I}}} - time()) / 86400', unit="d", w=4, thresholds=steps((None, RED), (14, YELLOW), (30, GREEN)), graph=False)
    d.stat("Slowest probe", f'max(probe_duration_seconds{{{I}}})', unit="s", w=4, thresholds=steps((None, GREEN), (1, YELLOW), (3, RED)), graph=False)
    d.statetimeline("Probe success (HTTP + TCP)", [(f'probe_success{{{I}}}', "{{job}} {{instance}}")], w=24, h=9)
    d.row("HTTP probes")
    d.table("HTTP probe summary", [(f'probe_success{{{I},job="blackbox-http"}}', ""), (f'probe_http_status_code{{{I}}}', ""), (f'probe_duration_seconds{{{I},job="blackbox-http"}}', ""),
                                   (f'probe_ssl_earliest_cert_expiry{{{I}}} - time()', ""), (f'probe_http_version{{{I}}}', "")], w=24, h=8,
            hide=["Time", "__name__", "job", "host"], rename={"Value #A": "ok", "Value #B": "status", "Value #C": "duration (s)", "Value #D": "cert time left (s)", "Value #E": "HTTP ver"},
            overrides=[{"matcher": {"id": "byName", "options": "cert time left (s)"}, "properties": [{"id": "unit", "value": "s"}]}])
    d.ts("Probe duration by phase", [(f'probe_http_duration_seconds{{{I}}}', "{{instance}} {{phase}}")], unit="s", w=12, stack=True, legend="none")
    d.ts("DNS lookup time and total duration", [(f'probe_dns_lookup_time_seconds{{{I}}}', "dns {{instance}}"), (f'probe_duration_seconds{{{I},job="blackbox-http"}}', "total {{instance}}")], unit="s", w=12, legend="none")
    d.ts("HTTP status codes", [(f'probe_http_status_code{{{I}}}', "{{instance}}")], unit="short", w=8, legend="none")
    d.ts("Redirects and content length", [(f'probe_http_redirects{{{I}}}', "redirects {{instance}}"), (f'probe_http_content_length{{{I}}}', "content length {{instance}}")], unit="short", w=8, legend="none")
    d.ts("Probe via TLS (1) / plain (0) and TLS version", [(f'probe_http_ssl{{{I}}}', "ssl {{instance}}"), (f'probe_tls_version_info{{{I}}}', "{{version}} {{instance}}")], unit="short", w=8, legend="none")
    d.row("TLS certificates")
    d.ts("Days until expiry", [(f'(probe_ssl_earliest_cert_expiry{{{I}}} - time()) / 86400', "{{instance}}")], unit="d", w=12, thresholds=steps((None, RED), (14, YELLOW), (30, GREEN)), legend="table")
    d.ts("Certificate lifetime / last chain issuance", [(f'probe_ssl_last_chain_expiry_timestamp_seconds{{{I}}} - time()', "chain {{instance}}")], unit="s", w=12, legend="none")
    d.row("TCP probes (SQL ports)")
    d.ts("TCP connect success", [(f'probe_success{{{I},job="blackbox-tcp"}}', "{{service}} {{instance}}")], unit="short", w=12, minv=0, maxv=1.2)
    d.ts("TCP connect time", [(f'probe_duration_seconds{{{I},job="blackbox-tcp"}}', "{{service}} {{instance}}")], unit="s", w=12)
    return d


def logs():
    d = Dash("logs-explorer", "Logs Explorer", ["loki", "logs"], "Container and host (systemd journal) logs from Loki via Grafana Alloy.", time_from="now-1h", refresh="30s")
    d.annotate("Alerts firing", 'ALERTS{alertstate="firing",severity=~"warning|critical",alertname!="Watchdog"}', "red", title="{{alertname}}", text="{{summary}}")
    d.var_query("container", 'label_values(container)', label="Container", ds=LOKI)
    d.var_query("level", 'label_values(level)', label="Level", ds=LOKI)
    d.var_textbox("search", "", label="Contains")
    d.var_textbox("exclude", "^$", label="Exclude (regex; default matches only empty lines)")
    d.row("Volume")
    d.ts("Log volume by container", [('sum by (container) (count_over_time({container=~"$container"} |~ "(?i)$search" !~ "(?i)$exclude" [$__auto]))', "{{container}}")], unit="short", w=12, ds=LOKI, stack=True, draw="bars", fill=60)
    d.ts("Log volume by level", [('sum by (level) (count_over_time({container=~"$container", level=~"$level"} [$__auto]))', "{{level}}")], unit="short", w=12, ds=LOKI, stack=True, draw="bars", fill=60,
         overrides=[{"matcher": {"id": "byName", "options": "error"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
                    {"matcher": {"id": "byName", "options": "warn"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "yellow"}}]}])
    d.stat("Errors in range", 'sum(count_over_time({container=~"$container", level=~"error|fatal"} [$__range]))', w=4, thresholds=steps((None, GREEN), (1, YELLOW), (100, RED)), ds=LOKI, graph=False)
    d.stat("Warnings in range", 'sum(count_over_time({container=~"$container", level="warn"} [$__range]))', w=4, thresholds=steps((None, GREEN), (1, YELLOW)), ds=LOKI, graph=False)
    d.bargauge("Top containers by error lines", [('topk(10, sum by (container) (count_over_time({level=~"error|fatal"} [$__range])))', "{{container}}")], w=8, h=4, ds=LOKI, thresholds=steps((None, GREEN), (10, YELLOW), (100, RED)))
    d.row("Container logs")
    d.logs("Logs", '{container=~"$container"} |~ "(?i)$search" !~ "(?i)$exclude"', h=14)
    d.row("Host (systemd journal; prod only)")
    d.logs("Journal: warnings and above", '{job="systemd-journal", level=~"emerg|alert|crit|err|warning"}', h=8)
    d.logs("Journal: docker / sshd / kernel / chrony", '{job="systemd-journal", unit=~"docker.service|ssh.service|sshd.service|chrony.service|chronyd.service|systemd-timesyncd.service|containerd.service"}', h=8)
    d.row("CockroachDB, backups, edge")
    d.logs("CockroachDB errors and warnings", '{container=~".*crdb.*|.*cockroach.*"} |~ "^[EWF]\\\\d{6}|(?i)\\"severity\\":\\"(ERROR|WARNING|FATAL)\\""', h=8)
    d.logs("backup-handler", '{container=~".*backup-handler.*"}', h=8)
    d.logs("Caddy access/errors", '{container=~".*caddy.*"}', h=8)
    return d


def ingestion():
    d = Dash("ingestion-engine", "Ingestion Engine (placeholder)", ["ingestion", "app"],
             "Placeholder dashboard fed by a Prometheus /metrics endpoint the ingestion engine may expose later. Expected metric names are documented in README.md.", time_from="now-3h")
    d.std_annotations()
    d.text("**Expected metrics** (prometheus_client naming): `ingest_rows_total{table}` counter, `ingest_batches_total{status}` counter, `ingest_errors_total{type}` counter, "
           "`ingest_batch_duration_seconds` histogram, `ingest_rows_inflight` gauge, `ingest_last_success_timestamp_seconds` gauge, `ingest_queue_depth` gauge, "
           "`ingest_batch_size_rows` histogram, `ingest_retries_total` counter. Panels stay empty until the engine serves /metrics on port 8000 (targets/*/ingestion-engine.yml).", h=3)
    d.row("Throughput")
    d.stat("Rows / s", 'sum(rate(ingest_rows_total[1m]))', unit="rps", w=4, color_mode="value")
    d.stat("Rows total", 'sum(ingest_rows_total)', unit="short", w=4, color_mode="value")
    d.stat("Error rate", 'sum(rate(ingest_errors_total[5m]))', unit="ops", w=4, thresholds=steps((None, GREEN), (0.01, YELLOW), (1, RED)), graph=False)
    d.stat("In flight rows", 'sum(ingest_rows_inflight)', w=4, color_mode="value")
    d.stat("Last success age", 'time() - max(ingest_last_success_timestamp_seconds)', unit="s", w=4, thresholds=steps((None, GREEN), (300, YELLOW), (900, RED)), graph=False)
    d.stat("Engine up", 'max(up{job="ingestion-engine"})', w=4, thresholds=OKBAD, graph=False, mappings=[{"type": "value", "options": {"0": {"text": "NOT SCRAPED", "color": "orange"}, "1": {"text": "UP", "color": "green"}}}])
    d.ts("Rows/s by table", [('sum by (table) (rate(ingest_rows_total[$__rate_interval]))', "{{table}}")], unit="rps", w=12, stack=True)
    d.ts("Batches/s by status", [('sum by (status) (rate(ingest_batches_total[$__rate_interval]))', "{{status}}")], unit="ops", w=12, stack=True)
    d.row("Latency and errors")
    d.ts("Batch duration percentiles", [('histogram_quantile(%s, sum by (le) (rate(ingest_batch_duration_seconds_bucket[$__rate_interval])))' % q, "p%g" % (q * 100)) for q in (0.5, 0.95, 0.99)], unit="s", w=12)
    d.ts("Errors by type / retries", [('sum by (type) (rate(ingest_errors_total[$__rate_interval]))', "{{type}}"), ('sum(rate(ingest_retries_total[$__rate_interval]))', "retries")], unit="ops", w=12)
    d.ts("Queue depth / in-flight", [('ingest_queue_depth', "queue"), ('ingest_rows_inflight', "in flight")], unit="short", w=12)
    d.ts("Average batch size", [('sum(rate(ingest_batch_size_rows_sum[$__rate_interval])) / clamp_min(sum(rate(ingest_batch_size_rows_count[$__rate_interval])), 1e-9)', "rows/batch")], unit="short", w=12)
    d.row("Target database impact (CockroachDB main)")
    d.ts("Live keys in main (per replica)", [('sum(livecount{job="cockroachdb",cluster="main"}) / 3', "keys")], unit="short", w=12)
    d.ts("Write latency while ingesting (p99)", [('cluster:crdb_sql_service_latency_seconds:p99{cluster="main"}', "SQL p99")], unit="s", w=12)
    d.row("Process (if the engine exports default process metrics)")
    d.ts("Engine CPU / RSS", [('rate(process_cpu_seconds_total{job="ingestion-engine"}[$__rate_interval])', "cpu cores"), ('process_resident_memory_bytes{job="ingestion-engine"}', "rss")], unit="short", w=12)
    d.logs("Engine logs", '{container=~".*ingest.*"}', w=12, h=8)
    return d


def build_all(out):
    for fn, folder in [(haproxy, "Edge and Uptime"), (caddy, "Edge and Uptime"), (blackbox, "Edge and Uptime"), (logs, "Logs"), (ingestion, "Applications")]:
        fn().save(os.path.join(out, folder))

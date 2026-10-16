#!/usr/bin/env bash
# Local acceptance tests (stacks must be up: make up-local-all). Exits non-zero if any check fails.
set -u
INFRA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MSYS_NO_PATHCONV=1
EX=talentra-main-local-crdb2-1
FAIL=0
ok()   { echo "PASS  $*"; }
bad()  { echo "FAIL  $*"; FAIL=1; }
chk()  { if [ "$1" = "$2" ]; then ok "$3 ($1)"; else bad "$3 (got '$1', want '$2')"; fi; }
q()    { # port user sql
  docker exec $EX cockroach sql --insecure --host=haproxy --port="$1" --user="$2" --format=csv -e "$3" 2>&1; }
gw()   { q "$1" root "SET allow_unsafe_internals=true; SELECT crdb_internal.node_id()" | tail -1; }
retry_gw() { local i r; for i in $(seq 1 20); do r=$(gw "$1"); case "$r" in [0-9]*) echo "$r"; return;; esac; sleep 1; done; echo "$r"; }

lease() { q 26257 root "SELECT lease_holder FROM [SHOW RANGES FROM TABLE talentra.public.t_accept WITH DETAILS]" | tail -1; }
wait_lease() { local i r t0=$(date +%s); for i in $(seq 1 30); do r=$(lease); [ "$r" = 1 ] && break; sleep 6; done; echo "(lease -> node $r after $(( $(date +%s)-t0 ))s; replicate queue applies lease_preferences lazily)"; echo "$r"; }

echo "== 1. routing: write port -> node1, read port -> node2/3"
chk "$(gw 26257)" 1 "write port gateway node"
seen=""; for i in 1 2 3 4 5 6; do seen="$seen $(gw 26259)"; done
echo "read port gateways:$seen"
echo "$seen" | grep -qw 1 && bad "read port reached node1" || ok "read port never hits node1"
echo "$seen" | grep -qw 2 && echo "$seen" | grep -qw 3 && ok "read port alternates node2/node3" || bad "read port not alternating"

echo "== 2. leaseholder + read-only enforcement"
q 26257 root "CREATE TABLE IF NOT EXISTS talentra.public.t_accept (id INT PRIMARY KEY, v STRING); UPSERT INTO talentra.public.t_accept SELECT i, 'x' FROM generate_series(1,5000) i" >/dev/null
w=$(wait_lease); echo "$w" | head -1; chk "$(echo "$w" | tail -1)" 1 "leaseholder of test table"
sleep 6
MAINN=$(q 26257 root "SELECT count(*) FROM talentra.public.t_accept" | tail -1)
chk "$(q 26259 talentra_app_ro "SELECT count(*) FROM talentra.public.t_accept" | tail -1)" "$MAINN" "app_ro reads same count via read port (follower read)"
r=$(q 26259 talentra_app_ro "INSERT INTO talentra.public.t_accept VALUES (999999,'no')"); echo "$r" | grep -qi "error" && ok "app_ro insert via read port rejected" || bad "app_ro insert succeeded"
r=$(q 26259 talentra_app_ro "SHOW default_transaction_read_only" | tail -1); chk "$r" on "app_ro default_transaction_read_only"
r=$(q 26259 talentra_app_ro "SHOW default_transaction_use_follower_reads" | tail -1); chk "$r" on "app_ro default_transaction_use_follower_reads"

echo "== 3. backup -> sync -> counts"
H=$(docker ps --format '{{.Names}}' | grep backup-handler)
M=$(q 26257 root "SELECT count(*) FROM talentra.public.t_accept" | tail -1)
docker exec "$H" handler.sh sync 2>&1 | tail -6
B=$(docker exec talentra-backup-local-backup-crdb-1 cockroach sql --insecure --format=csv -e "SELECT count(*) FROM talentra.public.t_accept" | tail -1)
chk "$B" "$M" "backup cluster row count equals main"
echo "timing log tail:"; tail -4 "$INFRA/main-db/logs/backup_timing.csv"

echo "== 4. caddy (local)"
code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: db.localhost' http://localhost:8081/); chk "$code" 401 "db.localhost without basic auth"
code=$(curl -s -o /dev/null -w '%{http_code}' -u local:local -H 'Host: db.localhost' http://localhost:8081/); chk "$code" 200 "db.localhost with basic auth -> main console"
code=$(curl -s -o /dev/null -w '%{http_code}' -u local:local -H 'Host: backup.localhost' http://localhost:8081/); chk "$code" 200 "backup.localhost -> backup console"
code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: monitoring.localhost' http://localhost:8081/); echo "monitoring.localhost -> HTTP $code (502 until infra/monitoring grafana joins talentra-edge)"

echo "== 5. failover: stop node1"
docker stop -t 30 talentra-main-local-crdb1-1 >/dev/null; t0=$(date +%s)
g=$(retry_gw 26257); echo "write port after node1 stop -> gateway node $g ($(( $(date +%s)-t0 ))s)"
case "$g" in 2|3) ok "write port failed over to node $g";; *) bad "write port did not fail over";; esac
r=$(q 26257 root "UPSERT INTO talentra.public.t_accept VALUES (100001,'during-failover')" | tail -1); echo "$r" | grep -qi "error" && bad "write during failover: $r" || ok "write succeeds during node1 outage"
g2=$(retry_gw 26259); case "$g2" in 2|3) ok "read port still serves (node $g2)";; *) bad "read port down";; esac
docker start talentra-main-local-crdb1-1 >/dev/null
for i in $(seq 1 40); do [ "$(gw 26257)" = 1 ] && break; sleep 2; done
chk "$(gw 26257)" 1 "write port returns to node1 after restart"
w=$(wait_lease); echo "$w" | head -1; chk "$(echo "$w" | tail -1)" 1 "lease returned to node1"

echo; [ $FAIL -eq 0 ] && echo "ALL PASSED" || echo "SOME FAILED"; exit $FAIL
